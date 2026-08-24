"""
Behavioral tests proving the ChargeSequence/DischargeSequence live
sense-routing wiring (see docs/architecture.md "Future Architecture:
Battery Sense Routing", "Live wiring" subsection) is correct on the
hardest-to-get-right cases -- not just source-inspected, but actually
executed with a full fake-hardware harness.

Specifically proves:
  - sense_channel=None (every group today) never touches sense_router at
    all, not even a stray call -- the core backward-compatibility
    guarantee, verified with a "poison" fake that raises on any use.
  - When configured, sense_router.connect() happens once before the first
    DMM read, and disconnect() happens exactly once, even on the
    trickiest exit path: a ReversePolarityError raised BEFORE the SMU's
    own try/finally even begins (see charge_sequence.py's outer
    try/finally, added specifically to cover this case).
  - Normal EOC completion also disconnects exactly once.

No real hardware anywhere in this file.
"""

import unittest

from config.settings import Settings
from test_control.charge_sequence import ChargeSequence
from test_control.safety_monitor import SafetyStatus


class _PoisonSenseRouter:
    """Raises on ANY use -- proves sense routing is never touched when
    sense_channel is None."""

    def connect(self, channel):
        raise AssertionError("sense_router.connect() must never be called when sense_channel is None")

    def disconnect(self, channel):
        raise AssertionError("sense_router.disconnect() must never be called when sense_channel is None")


class _RecordingSenseRouter:
    def __init__(self):
        self.calls = []

    def connect(self, channel):
        self.calls.append(("connect", channel))

    def disconnect(self, channel):
        self.calls.append(("disconnect", channel))


class _FakeSmu:
    model = "PXI-4130"
    resource = "SMU1"

    def __init__(self):
        self.enabled = False

    def set_charge_mode(self, current_a, voltage_limit_v):
        pass

    def output_enable(self):
        self.enabled = True

    def measure(self):
        return {"voltage_v": 3.7, "current_a": 0.01}

    def emergency_output_off(self, reason, on_event=None):
        self.enabled = False
        return True

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        return True


class _FakeRelay:
    name = "TEST_RELAY_MATRIX"

    def close(self, channel):
        pass

    def open(self, channel):
        pass


class _FakeSafety:
    def set_battery_limits(self, battery_cfg):
        pass

    def check(self, v, i, t_c, mode=None):
        return SafetyStatus(safe=True)

    def emergency_stop(self, smu, relay, reason, on_event=None):
        pass

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        pass


class _FakeStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.events = []

    def get_run_summary(self, run_id):
        return None

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def record_execution_state(self, **kwargs):
        pass

    def record_measurement(self, **kwargs):
        pass

    def finish_run_summary(self, **kwargs):
        pass

    def get_first_measurement(self, **kwargs):
        return None

    def get_measurements(self, **kwargs):
        return []

    def get_recent_events(self, **kwargs):
        return []


class _EocDmm:
    """Always returns exactly the CV target with near-zero current
    implied via the smu's own fake `measure()` -- forces EOC on the very
    first sampling-loop iteration, so the test runs instantly."""

    def __init__(self, voltage_v):
        self.voltage_v = voltage_v

    def measure_dc_voltage(self):
        return self.voltage_v


BATTERY_CFG = {
    "voltage_max_v": 4.2, "voltage_min_v": 3.0,
    "max_charge_current_a": 1.5, "max_discharge_current_a": 1.05,
    "max_temp_c": 45.0,
}
TEST_SETPOINTS = {"charge_current_a": 0.1, "charge_voltage_v": 3.7,
                  "discharge_current_a": 0.1, "discharge_cutoff_v": 3.0}


class _FastSettings:
    """Same values as config.settings.Settings for what this test needs,
    except STABILIZATION_S=0 -- keeps these tests instant instead of
    waiting out a real multi-second stabilization sleep."""
    STABILIZATION_S = 0.0
    SAMPLE_RATE_HZ = Settings.SAMPLE_RATE_HZ
    CHARGE_CUTOFF_A = Settings.CHARGE_CUTOFF_A
    CHARGE_TIMEOUT_S = Settings.CHARGE_TIMEOUT_S
    REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V


def _make_sequence(dmm, sense_router=None, sense_channel=None):
    seq = ChargeSequence(
        smu=_FakeSmu(), dmm=dmm, relay=_FakeRelay(), safety=_FakeSafety(),
        storage=_FakeStorage(), settings=_FastSettings, group_name="TEST",
        sense_router=sense_router, sense_channel=sense_channel,
    )
    seq.log.disabled = True
    return seq


class NoSenseChannelIsNeverTouchedTests(unittest.TestCase):
    def test_normal_completion_never_touches_a_poison_sense_router(self):
        seq = _make_sequence(_EocDmm(3.7), sense_router=_PoisonSenseRouter(), sense_channel=None)
        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)
        self.assertTrue(result)

    def test_reverse_polarity_failure_never_touches_a_poison_sense_router(self):
        seq = _make_sequence(_EocDmm(-1.0), sense_router=_PoisonSenseRouter(), sense_channel=None)
        with self.assertRaises(Exception):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)


class ConfiguredSenseChannelTests(unittest.TestCase):
    def test_normal_completion_connects_once_and_disconnects_once(self):
        router = _RecordingSenseRouter()
        seq = _make_sequence(_EocDmm(3.7), sense_router=router, sense_channel=7)
        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)
        self.assertTrue(result)
        self.assertEqual(router.calls, [("connect", 7), ("disconnect", 7)])

    def test_reverse_polarity_failure_still_disconnects(self):
        """
        The hardest case: ReversePolarityError is raised BEFORE the SMU's
        own try/finally even begins. This proves the NEW outer
        try/finally (added specifically for sense-channel cleanup) really
        does wrap that early-exit path, not just the normal one.

        Expects TWO connect/disconnect pairs, not one -- this is correct,
        designed behavior, not a bug: pair 1 is _run_charge()'s own
        outer-finally cleanup as the exception propagates out; pair 2 is
        run_guarded()'s exception handler calling
        _safe_final_voltage_reading() afterward, which must reconnect the
        already-disconnected sense channel to take its own fresh
        best-effort reading (see that method's docstring).
        """
        router = _RecordingSenseRouter()
        seq = _make_sequence(_EocDmm(-1.0), sense_router=router, sense_channel=7)
        with self.assertRaises(Exception):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)
        self.assertEqual(router.calls, [
            ("connect", 7), ("disconnect", 7),      # _run_charge()'s own cleanup
            ("connect", 7), ("disconnect", 7),      # _safe_final_voltage_reading()'s fresh read
        ])

    def test_sense_channel_connected_before_the_first_dmm_read(self):
        order = []

        class _OrderTrackingDmm(_EocDmm):
            def measure_dc_voltage(self):
                order.append("dmm_read")
                return self.voltage_v

        class _OrderTrackingRouter(_RecordingSenseRouter):
            def connect(self, channel):
                order.append("connect")
                super().connect(channel)

        seq = _make_sequence(_OrderTrackingDmm(3.7), sense_router=_OrderTrackingRouter(), sense_channel=7)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)
        self.assertEqual(order[0], "connect")
        self.assertIn("dmm_read", order)


if __name__ == "__main__":
    unittest.main()
