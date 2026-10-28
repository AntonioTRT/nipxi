"""
Safety shutdown phase: storage-failure shutdown, shutdown ordering, loop
watchdog, relay heartbeat, storage free-space guard, frozen-measurement
detection, process-termination handling, and removal of the menu legend.
"""

import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.frozen_signal import FrozenSignalDetector
from test_control.safety_monitor import SafetyMonitor
from test_control.storage_guard import StorageSpaceGuard, check_start_free_space
from utils import menu_status, termination
from utils.cancellation import CancellationToken, cancel_all_tokens
from utils.errors import (
    MeasurementFrozenError, RelayCommLostError, RelayError, RelayStateVerificationError,
    STATION_HARDWARE_EXCEPTIONS, StorageFailureError, WatchdogTimeoutError,
)
from utils.watchdog import LoopWatchdog

_MB = 1024 * 1024


class FrozenSignalDetectorTests(unittest.TestCase):
    def test_flags_after_window_identical_samples(self):
        d = FrozenSignalDetector(window=3)
        self.assertFalse(d.update(1.0))
        self.assertFalse(d.update(1.0))
        self.assertTrue(d.update(1.0))

    def test_any_change_restarts_the_window(self):
        d = FrozenSignalDetector(window=3)
        d.update(1.0), d.update(1.0)
        self.assertFalse(d.update(1.1))
        self.assertFalse(d.update(1.1))
        self.assertTrue(d.update(1.1))

    def test_epsilon_and_none_and_reset(self):
        d = FrozenSignalDetector(window=3, epsilon=0.01)
        d.update(1.000), d.update(1.005)
        self.assertTrue(d.update(1.008))
        d.reset()
        self.assertFalse(d.update(None))
        self.assertEqual(d.count, 0)


class LoopWatchdogTests(unittest.TestCase):
    def test_not_armed_until_first_pet(self):
        fired = []
        wd = LoopWatchdog(0.1, fired.append)
        time.sleep(0.3)
        self.assertFalse(wd.armed)
        self.assertEqual(fired, [])

    def test_fires_on_stall_and_pet_then_raises(self):
        fired = []
        wd = LoopWatchdog(0.2, fired.append, name="t")
        wd.pet()
        deadline = time.monotonic() + 3
        while not fired and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(fired), 1)
        self.assertTrue(wd.expired)
        with self.assertRaises(WatchdogTimeoutError):
            wd.pet()

    def test_regular_pets_and_stop_prevent_firing(self):
        fired = []
        wd = LoopWatchdog(0.3, fired.append)
        for _ in range(8):
            wd.pet()
            time.sleep(0.1)
        wd.stop()
        time.sleep(0.5)
        self.assertEqual(fired, [])
        self.assertFalse(wd.expired)


class StorageGuardTests(unittest.TestCase):
    def _usage(self, free_mb):
        return lambda _path: SimpleNamespace(free=int(free_mb * _MB))

    def _settings(self, **kw):
        base = dict(DATA_DIR=".", STORAGE_MIN_FREE_MB_START=500, STORAGE_WARN_FREE_MB=300,
                    STORAGE_ABORT_FREE_MB=100, STORAGE_CHECK_INTERVAL_S=30.0)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_start_gate(self):
        ok, free = check_start_free_space(self._settings(), self._usage(400))
        self.assertFalse(ok)
        self.assertAlmostEqual(free, 400)
        self.assertTrue(check_start_free_space(self._settings(), self._usage(600))[0])

    def test_unknown_free_space_is_not_treated_as_low(self):
        def boom(_p):
            raise OSError("no volume")
        self.assertTrue(check_start_free_space(self._settings(), boom)[0])

    def test_runtime_warn_once_then_abort(self):
        now = [0.0]
        warnings = []
        free = [250]
        guard = StorageSpaceGuard(self._settings(), usage_fn=lambda p: SimpleNamespace(free=int(free[0] * _MB)),
                                  clock=lambda: now[0])
        guard.check(on_warn=warnings.append)
        now[0] += 31
        guard.check(on_warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        free[0] = 50
        now[0] += 31
        with self.assertRaises(StorageFailureError):
            guard.check(on_warn=warnings.append)

    def test_interval_gates_the_check(self):
        now = [0.0]
        free = [1000]
        guard = StorageSpaceGuard(self._settings(), usage_fn=lambda p: SimpleNamespace(free=int(free[0] * _MB)),
                                  clock=lambda: now[0])
        guard.check()
        free[0] = 10
        now[0] += 5          # inside the interval -> not re-read
        guard.check()
        now[0] += 30
        with self.assertRaises(StorageFailureError):
            guard.check()


class _FakeSMU:
    model, resource, name = "SMU", "R", "SMU"

    def __init__(self, calls):
        self.calls = calls

    def emergency_output_off(self, reason, on_event=None):
        self.calls.append("smu_off")
        return True

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        self.calls.append("smu_zero")
        return True


class _FakeRelay:
    name = "RELAY"

    def __init__(self, calls, closed=None, error=None):
        self.calls, self._closed, self._error = calls, closed, error

    def open_all(self):
        self.calls.append("relay_open_all")

    def closed_channels(self):
        if self._error:
            raise self._error
        return list(self._closed)


class _FakeStorage:
    run_id = "RUN"

    def __init__(self, calls, fail=False):
        self.calls, self.fail = calls, fail

    def _maybe_fail(self, what):
        self.calls.append(what)
        if self.fail:
            raise sqlite3.OperationalError("database or disk is full")

    def log_event(self, **kw):
        self._maybe_fail("log_event")

    def record_execution_state(self, **kw):
        self._maybe_fail("record_execution_state")

    def finish_run_summary(self, **kw):
        self._maybe_fail("finish_run_summary")

    def record_measurement(self, **kw):
        self._maybe_fail("record_measurement")


def _sequence(calls, *, storage_fails=False, relay=None, **settings):
    s = SimpleNamespace(LOOP_WATCHDOG_TIMEOUT_S=0, RELAY_HEARTBEAT_INTERVAL_S=0,
                        FROZEN_DETECTION_ENABLED=True, **settings)
    safety = SafetyMonitor(mock.MagicMock())
    return BatteryOperationSequence(
        smu=_FakeSMU(calls), relay=relay or _FakeRelay(calls, closed=[1]), safety=safety,
        storage=_FakeStorage(calls, fail=storage_fails), settings=s, source="unit",
    )


def _guarded(seq, fn):
    return seq.run_guarded(fn, channel=1, relay_address=1, label="Unit", verb="testing",
                           cancel_message="cancelled")


class ShutdownOrderingTests(unittest.TestCase):
    def test_hardware_is_made_safe_before_any_bookkeeping(self):
        calls = []
        seq = _sequence(calls)

        def boom():
            raise RuntimeError("instrument exploded")

        with self.assertRaises(RuntimeError):
            _guarded(seq, boom)
        self.assertLess(calls.index("smu_off"), calls.index("relay_open_all"))
        self.assertLess(calls.index("relay_open_all"), calls.index("finish_run_summary"))
        self.assertLess(calls.index("relay_open_all"), calls.index("record_execution_state"))

    def test_storage_failure_cannot_skip_the_shutdown(self):
        calls = []
        seq = _sequence(calls, storage_fails=True)

        def boom():
            raise RuntimeError("original failure")

        with self.assertRaises(RuntimeError) as ctx:   # the ORIGINAL error, not the sqlite one
            _guarded(seq, boom)
        self.assertEqual(str(ctx.exception), "original failure")
        self.assertIn("smu_off", calls)
        self.assertIn("relay_open_all", calls)
        self.assertEqual(sorted(set(seq._bookkeeping_failures)),
                         ["event_log", "execution_state", "run_summary"])

    def test_every_branch_shuts_down(self):
        from utils.errors import NIPXITimeoutError, OperationCancelledError, SafetyViolationError
        for exc in (OperationCancelledError("c"), SafetyViolationError("s"), RelayError("r"),
                    NIPXITimeoutError("t"), ValueError("v")):
            calls = []
            seq = _sequence(calls, storage_fails=True)
            with self.assertRaises(type(exc)):
                _guarded(seq, lambda e=exc: (_ for _ in ()).throw(e))
            self.assertIn("relay_open_all", calls, type(exc).__name__)

    def test_measurement_write_failure_becomes_storage_failure(self):
        seq = _sequence([], storage_fails=True)
        with self.assertRaises(StorageFailureError):
            seq._record_measurement(position_in_group=1, test_type="x", channel=1)

    def test_storage_failure_is_station_level(self):
        self.assertTrue(issubclass(StorageFailureError, STATION_HARDWARE_EXCEPTIONS))
        self.assertTrue(issubclass(WatchdogTimeoutError, STATION_HARDWARE_EXCEPTIONS))
        self.assertTrue(issubclass(MeasurementFrozenError, STATION_HARDWARE_EXCEPTIONS))
        self.assertTrue(issubclass(RelayCommLostError, STATION_HARDWARE_EXCEPTIONS))


class WatchdogIntegrationTests(unittest.TestCase):
    def test_stalled_loop_is_forced_safe_from_the_watchdog_thread(self):
        calls = []
        seq = _sequence(calls)
        seq.s.LOOP_WATCHDOG_TIMEOUT_S = 0.2
        release = threading.Event()

        def hung_loop():
            seq._periodic_safety_checks(channel=1, relay_address=1)
            release.wait(5)          # a blocking instrument call that never returns in time
            seq._periodic_safety_checks(channel=1, relay_address=1)

        timer = threading.Timer(1.0, release.set)
        timer.start()
        try:
            with self.assertRaises(WatchdogTimeoutError):
                _guarded(seq, hung_loop)
        finally:
            timer.cancel()
        self.assertIn("smu_off", calls)
        self.assertIn("relay_open_all", calls)


class RelayHeartbeatTests(unittest.TestCase):
    def _seq(self, relay):
        seq = _sequence([], relay=relay, )
        seq.s.RELAY_HEARTBEAT_INTERVAL_S = 0.01
        return seq

    def test_healthy_relay_passes_and_first_call_only_starts_the_clock(self):
        seq = self._seq(_FakeRelay([], closed=[1]))
        seq._relay_heartbeat(channel=1, relay_address=1)
        time.sleep(0.02)
        seq._relay_heartbeat(channel=1, relay_address=1)

    def test_comm_loss_raises_relay_comm_lost(self):
        seq = self._seq(_FakeRelay([], error=RelayError("link down")))
        seq._relay_heartbeat(channel=1, relay_address=1)
        time.sleep(0.02)
        with self.assertRaises(RelayCommLostError):
            seq._relay_heartbeat(channel=1, relay_address=1)

    def test_unexpected_state_raises_verification_error(self):
        seq = self._seq(_FakeRelay([], closed=[1, 4]))
        seq._relay_heartbeat(channel=1, relay_address=1)
        time.sleep(0.02)
        with self.assertRaises(RelayStateVerificationError):
            seq._relay_heartbeat(channel=1, relay_address=1)

    def test_comm_loss_runs_the_shutdown(self):
        calls = []
        seq = _sequence(calls, relay=_FakeRelay(calls, error=RelayError("link down")))
        seq.s.RELAY_HEARTBEAT_INTERVAL_S = 0.01

        def loop():
            seq._periodic_safety_checks(channel=1, relay_address=1)
            time.sleep(0.02)
            seq._periodic_safety_checks(channel=1, relay_address=1)

        with self.assertRaises(RelayCommLostError):
            _guarded(seq, loop)
        self.assertIn("smu_off", calls)

    def test_disabled_when_interval_zero(self):
        seq = _sequence([], relay=_FakeRelay([], error=RelayError("never read")))
        seq._relay_heartbeat(channel=1, relay_address=1)
        seq._relay_heartbeat(channel=1, relay_address=1)


class FrozenCheckTests(unittest.TestCase):
    def _seq(self):
        return _sequence([], FROZEN_CURRENT_SAMPLES=3, FROZEN_CURRENT_EPSILON_A=0.0,
                         FROZEN_VOLTAGE_SAMPLES=3, FROZEN_VOLTAGE_EPSILON_V=0.0,
                         FROZEN_NTC_SAMPLES=3, FROZEN_NTC_EPSILON_V=0.0,
                         FROZEN_CURRENT_MIN_ACTIVE_A=0.01)

    def test_frozen_current_raises(self):
        seq = self._seq()
        seq._frozen_check_samples(channel=1, relay_address=1, v=3.7, i=1.0)
        seq._frozen_check_samples(channel=1, relay_address=1, v=3.71, i=1.0)
        with self.assertRaises(MeasurementFrozenError):
            seq._frozen_check_samples(channel=1, relay_address=1, v=3.72, i=1.0)

    def test_moving_signals_never_trip(self):
        seq = self._seq()
        for n in range(20):
            seq._frozen_check_samples(channel=1, relay_address=1, v=3.7 + n * 1e-3,
                                      i=1.0 + n * 1e-4, ntc_v=1.0 + n * 1e-3)

    def test_voltage_ignored_without_current_or_during_cv_hold(self):
        seq = self._seq()
        for _ in range(10):   # no current flowing -> only the current detector sees data
            seq._frozen_check_samples(channel=1, relay_address=1, v=3.7, i=0.0 + 1e-6 * _)
        seq = self._seq()
        for n in range(10):   # CV hold: caller marks voltage inactive
            seq._frozen_check_samples(channel=1, relay_address=1, v=4.2, i=0.5 - n * 1e-3,
                                      voltage_active=False)

    def test_frozen_voltage_under_load_raises(self):
        seq = self._seq()
        with self.assertRaises(MeasurementFrozenError):
            for n in range(5):
                seq._frozen_check_samples(channel=1, relay_address=1, v=3.7, i=1.0 + n * 1e-3)

    def test_frozen_ntc_raises_and_disabled_flag_is_respected(self):
        seq = self._seq()
        with self.assertRaises(MeasurementFrozenError):
            for _ in range(5):
                seq._frozen_check_samples(channel=1, relay_address=1, ntc_v=1.234)
        seq = self._seq()
        seq.s.FROZEN_DETECTION_ENABLED = False
        for _ in range(10):
            seq._frozen_check_samples(channel=1, relay_address=1, ntc_v=1.234)


class TerminationTests(unittest.TestCase):
    class _HW:
        def __init__(self, safe=True, cooperative_after=None):
            self.safe, self.forced = safe, []
            self._cooperative_after = cooperative_after
            self._t0 = time.monotonic()

        def is_safe(self):
            if self._cooperative_after is not None and time.monotonic() - self._t0 >= self._cooperative_after:
                self.safe = True
            return self.safe

        def emergency_shutdown(self, reason):
            self.forced.append(reason)
            self.safe = True

    def test_cancel_all_tokens(self):
        a, b = CancellationToken("a"), CancellationToken("b")
        b.request_cancel("already")
        self.assertGreaterEqual(cancel_all_tokens("SIGTERM received"), 1)
        self.assertTrue(a.requested)
        self.assertEqual(b.reason, "already")

    def test_cooperative_shutdown_is_not_forced(self):
        hw = self._HW(safe=False, cooperative_after=0.2)
        token = CancellationToken("op")
        with mock.patch.object(termination, "_registered", []):
            termination.register_hardware(hw)
            self.assertTrue(termination.shutdown_now("test", grace_s=3, poll_s=0.02))
        self.assertTrue(token.requested)
        self.assertEqual(hw.forced, [])

    def test_grace_expiry_forces_emergency_shutdown(self):
        hw = self._HW(safe=False)
        with mock.patch.object(termination, "_registered", []):
            termination.register_hardware(hw)
            self.assertFalse(termination.shutdown_now("console close", grace_s=0.2, poll_s=0.02))
        self.assertEqual(hw.forced, ["console close"])

    def test_terminate_exits_after_safing_even_if_it_raises(self):
        exits = []
        hw = self._HW(safe=False)
        with mock.patch.object(termination, "_registered", []):
            termination.register_hardware(hw)
            termination._terminate("SIGTERM received", 0.1, exits.append, 143)
        self.assertEqual(exits, [143])
        self.assertEqual(len(hw.forced), 1)

    def test_idle_process_exits_immediately(self):
        exits = []
        with mock.patch.object(termination, "_registered", []):
            start = time.monotonic()
            termination._terminate("SIGTERM received", 5, exits.append, 143)
        self.assertEqual(exits, [143])
        self.assertLess(time.monotonic() - start, 1.0)


class MenuLegendRemovedTests(unittest.TestCase):
    def test_legend_is_gone_but_colors_remain(self):
        self.assertFalse(hasattr(menu_status, "legend"))
        with mock.patch.object(menu_status, "color_enabled", lambda: True):
            self.assertEqual(menu_status.style("x", menu_status.VERIFIED), "\033[32mx\033[0m")
            self.assertEqual(menu_status.style("x", menu_status.PARTIAL), "\033[33mx\033[0m")

    def test_no_menu_prints_legend_text(self):
        source = open(test_module.__file__, encoding="utf-8").read()
        self.assertNotIn("verified on hardware", source)
        self.assertNotIn("_menu_status.legend", source)


if __name__ == "__main__":
    unittest.main()
