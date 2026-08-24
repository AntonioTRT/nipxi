"""
Tests for DB-backed "SHUTDOWN: ..." event_log tracing -- see
docs/architecture.md "Shutdown Trace Logging".

Root cause this covers: prior to this work, every shutdown-path log line
in hardware/smu.py / test_control/safety_monitor.py (including all
"[SHUTDOWN-TRACE]" lines) went ONLY through Python's `logging` module
(self.log.warning/critical/...), never through storage.log_event() (the
SQLite event_log table get_recent_events() reads from, i.e. what the
operator-facing "Recent Events" panel shows). The only storage.log_event()
call anywhere in the cancellation path was run_guarded()'s own
cancel_message line (e.g. "Charging stopped by operator") -- exactly the
single event_log row observed after a real Ctrl+C test that otherwise
showed no trace of emergency_output_off()/relay open/setpoint-zeroing
having run. This file proves the new on_event-callback wiring closes that
gap: BatteryOperationSequence._shutdown_trace_logger() (the one place with
both a `storage` handle and per-call channel/relay context) is forwarded
through run_guarded() -> SafetyMonitor.emergency_stop()/
safe_cancel_shutdown() -> hardware/smu.py's own methods, and every step
along the way records a "SHUTDOWN: <message>" event_log row.

No hardware, no real DataStorage -- fake doubles throughout, mirroring the
existing pattern in tests/test_sense_routing_live_wiring.py.
"""

import unittest

from config.settings import Settings
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.safety_monitor import SafetyMonitor
from utils.errors import (
    NIPXITimeoutError, OperationCancelledError, RelayError, SafetyViolationError,
)


class _FakeStorage:
    def __init__(self, log_event_raises=False):
        self.run_id = "test-run"
        self.events = []
        self._log_event_raises = log_event_raises

    def log_event(self, **kwargs):
        if self._log_event_raises:
            raise RuntimeError("simulated event_log write failure")
        self.events.append(kwargs)

    def get_run_summary(self, run_id):
        return None

    def record_execution_state(self, **kwargs):
        pass

    def finish_run_summary(self, **kwargs):
        pass


class _FakeSmu:
    """Real SMU driver's emergency_output_off()/zero_output_setpoint_best_effort()
    are unit-tested separately (test_smu_emergency_shutdown.py,
    test_smu_post_isolation_zeroing.py) -- this fake only needs to prove
    on_event is received and forwarded, matching the real contract."""

    def emergency_output_off(self, reason, on_event=None):
        if on_event is not None:
            on_event(f"emergency_output_off requested ({reason})")
        return True

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        if on_event is not None:
            on_event("current_level zeroed (was 0.500 A)")
            on_event("voltage_limit zeroed (was 4.000 V)")
        return True


class _FakeRelay:
    def open_all(self):
        pass


class _RecordingSafety:
    """Records the on_event it was called with (for assertions), and
    invokes it with a couple of representative messages -- exercising the
    same real call shape SafetyMonitor.emergency_stop()/
    safe_cancel_shutdown() use, without needing the real class here (that
    real wiring is covered separately by test_safety_monitor_shutdown.py)."""

    def __init__(self):
        self.emergency_stop_calls = []
        self.safe_cancel_shutdown_calls = []

    def emergency_stop(self, smu, relay, reason, on_event=None):
        self.emergency_stop_calls.append(on_event)
        if on_event is not None:
            on_event("relay_matrix.open_all executed")
            on_event("shutdown completed")

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        self.safe_cancel_shutdown_calls.append(on_event)
        if on_event is not None:
            on_event("relay_matrix.open_all executed")
            on_event("shutdown completed")


def _make_sequence(storage=None):
    return BatteryOperationSequence(
        smu=_FakeSmu(), relay=_FakeRelay(), safety=_RecordingSafety(),
        storage=storage or _FakeStorage(), settings=Settings, source="charge_battery",
    )


class ShutdownTraceLoggerTests(unittest.TestCase):
    """Direct tests of _shutdown_trace_logger() itself."""

    def test_on_event_writes_a_prefixed_event_log_row(self):
        storage = _FakeStorage()
        seq = _make_sequence(storage)
        on_event = seq._shutdown_trace_logger(channel=3, relay_address=7)
        on_event("cancellation detected")
        self.assertEqual(len(storage.events), 1)
        row = storage.events[0]
        self.assertEqual(row["level"], "INFO")
        self.assertEqual(row["source"], "charge_battery")
        self.assertEqual(row["channel"], 3)
        self.assertEqual(row["relay"], 7)
        self.assertEqual(row["message"], "SHUTDOWN: cancellation detected")

    def test_each_call_writes_its_own_row(self):
        storage = _FakeStorage()
        seq = _make_sequence(storage)
        on_event = seq._shutdown_trace_logger(channel=1, relay_address=1)
        on_event("current_level zeroed (was 0.500 A)")
        on_event("voltage_limit zeroed (was 4.000 V)")
        messages = [e["message"] for e in storage.events]
        self.assertEqual(
            messages,
            ["SHUTDOWN: current_level zeroed (was 0.500 A)", "SHUTDOWN: voltage_limit zeroed (was 4.000 V)"],
        )

    def test_a_failing_storage_write_does_not_raise(self):
        seq = _make_sequence(_FakeStorage(log_event_raises=True))
        on_event = seq._shutdown_trace_logger(channel=1, relay_address=1)
        on_event("cancellation detected")  # must not raise


class RunGuardedShutdownTraceTests(unittest.TestCase):
    """
    End-to-end (fakes only): run_guarded() -> the SHUTDOWN: trace appears
    in storage.events for every one of its five handled exception types,
    and the SafetyMonitor call it makes receives a real, callable on_event
    (never None) that itself deposits further SHUTDOWN: rows.
    """

    def _run_and_get_messages(self, exc, expected_detected_message):
        storage = _FakeStorage()
        seq = _make_sequence(storage)

        def _raise():
            raise exc

        with self.assertRaises(type(exc)):
            seq.run_guarded(
                _raise, channel=1, relay_address=1, label="Charge Battery",
                verb="charging", cancel_message="Charging stopped by operator",
            )
        messages = [e["message"] for e in storage.events]
        self.assertIn(f"SHUTDOWN: {expected_detected_message}", messages)
        self.assertIn("SHUTDOWN: relay_matrix.open_all executed", messages)
        self.assertIn("SHUTDOWN: shutdown completed", messages)
        # The "detected" trace line must be written before shutdown is
        # invoked -- otherwise an operator reading the event log in order
        # would see the shutdown's own steps before knowing why it started.
        self.assertLess(
            messages.index(f"SHUTDOWN: {expected_detected_message}"),
            messages.index("SHUTDOWN: relay_matrix.open_all executed"),
        )
        return storage, seq

    def test_operator_cancellation_produces_detected_and_shutdown_trace(self):
        storage, seq = self._run_and_get_messages(
            OperationCancelledError("operator stop"), "cancellation detected",
        )
        # The pre-existing human-readable cancel_message must still be
        # present too -- this is additive, not a replacement.
        messages = [e["message"] for e in storage.events]
        self.assertIn("Charging stopped by operator", messages)
        on_event = seq.safety.safe_cancel_shutdown_calls[-1]
        self.assertTrue(callable(on_event))

    def test_safety_violation_produces_detected_and_shutdown_trace(self):
        storage, seq = self._run_and_get_messages(
            SafetyViolationError("overvoltage"), "safety violation detected",
        )
        self.assertTrue(callable(seq.safety.emergency_stop_calls[-1]))

    def test_relay_error_produces_detected_and_shutdown_trace(self):
        self._run_and_get_messages(RelayError("relay fault"), "relay fault detected")

    def test_timeout_produces_detected_and_shutdown_trace(self):
        self._run_and_get_messages(NIPXITimeoutError("charge timeout"), "timeout detected")

    def test_unexpected_error_produces_detected_and_shutdown_trace(self):
        self._run_and_get_messages(RuntimeError("something unexpected"), "unexpected error detected")


if __name__ == "__main__":
    unittest.main()
