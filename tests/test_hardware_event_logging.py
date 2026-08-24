"""
Integration tests for "Standardized Hardware Event Logging" (see
docs/architecture.md) as wired into ChargeSequence/DischargeSequence --
SMU_OUTPUT_ENABLED/_DISABLED, RELAY_OPEN/_CLOSE, MATRIX_ROUTE_APPLIED/
_CLEARED, DMM_MEASUREMENT_FAILED/_RECOVERED (bounded tolerance), and
SAFETY_MONITOR_TRIGGERED.

Scripted fake hardware, no real hardware/sleeps -- mirrors the harness
established in tests/test_battery_removal_during_charge.py and
tests/test_sense_routing_live_wiring.py.
"""

import unittest

from config.settings import Settings
from test_control.charge_sequence import ChargeSequence
from test_control.discharge_sequence import DischargeSequence
from test_control.safety_monitor import SafetyStatus
from utils.errors import DMMMeasurementLostError


class _ScriptedDmm:
    """Returns one voltage per call, in order. A "FAIL" sentinel raises
    instead of returning a value, for DMM_MEASUREMENT_FAILED/_RECOVERED
    coverage."""
    model = "NI-4065"
    resource = "DMM1"

    def __init__(self, script):
        self._script = list(script)
        self._idx = 0

    def measure_dc_voltage(self):
        value = self._script[self._idx]
        self._idx += 1
        if value == "FAIL":
            raise RuntimeError("simulated DMM comms failure")
        return value


class _ScriptedSmu:
    model = "PXI-4130"
    resource = "SMU1"

    def __init__(self, currents):
        self._currents = list(currents)
        self._idx = 0
        self.enabled = False

    def set_charge_mode(self, current_a, voltage_limit_v):
        pass

    def set_discharge_mode(self, current_a, voltage_limit_v):
        pass

    def output_enable(self):
        self.enabled = True

    def measure(self):
        i = self._currents[self._idx]
        self._idx += 1
        return {"voltage_v": 0.0, "current_a": i}

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


class _RecordingSafety:
    def __init__(self, unsafe_on_sample=None):
        self.calls = []
        self._unsafe_on_sample = unsafe_on_sample
        self._sample_count = 0

    def set_battery_limits(self, battery_cfg):
        pass

    def check(self, v, i, t_c, mode=None):
        self._sample_count += 1
        if self._unsafe_on_sample is not None and self._sample_count == self._unsafe_on_sample:
            return SafetyStatus(safe=False, reason="Overvoltage: simulated limit violation")
        return SafetyStatus(safe=True)

    def emergency_stop(self, smu, relay, reason, on_event=None):
        self.calls.append(("emergency_stop", reason))

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        self.calls.append(("safe_cancel_shutdown", reason))


class _RecordingStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.events = []
        self.finish_calls = []

    def get_run_summary(self, run_id):
        return None

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def record_execution_state(self, **kwargs):
        pass

    def record_measurement(self, **kwargs):
        pass

    def finish_run_summary(self, **kwargs):
        self.finish_calls.append(kwargs)

    def get_first_measurement(self, **kwargs):
        return None

    def get_measurements(self, **kwargs):
        return []

    def get_recent_events(self, **kwargs):
        return []

    def event_messages(self):
        return [e["message"] for e in self.events]


class _RecordingSenseRouter:
    def __init__(self):
        self.calls = []

    def connect(self, channel):
        self.calls.append(("connect", channel))

    def disconnect(self, channel):
        self.calls.append(("disconnect", channel))


BATTERY_CFG = {
    "voltage_max_v": 4.2, "voltage_min_v": 3.0,
    "max_charge_current_a": 1.5, "max_discharge_current_a": 1.05,
    "max_temp_c": 45.0,
}
CHARGE_SETPOINTS = {"charge_current_a": 1.0, "charge_voltage_v": 4.0}
DISCHARGE_SETPOINTS = {"discharge_current_a": 0.1, "discharge_cutoff_v": 3.0}


class _FastSettings:
    STABILIZATION_S = 0.0
    SAMPLE_RATE_HZ = 100_000.0
    CHARGE_CUTOFF_A = 0.15
    CHARGE_TIMEOUT_S = Settings.CHARGE_TIMEOUT_S
    DISCHARGE_TIMEOUT_S = Settings.DISCHARGE_TIMEOUT_S
    REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V
    DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES = 3


def _make_charge_sequence(dmm, smu, storage=None, sense_router=None, sense_channel=None):
    seq = ChargeSequence(
        smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(),
        storage=storage or _RecordingStorage(), settings=_FastSettings, group_name="B1",
        sense_router=sense_router, sense_channel=sense_channel,
    )
    seq.log.disabled = True
    return seq


def _make_discharge_sequence(dmm, smu, storage=None, sense_router=None, sense_channel=None):
    seq = DischargeSequence(
        smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(),
        storage=storage or _RecordingStorage(), settings=_FastSettings, group_name="B1",
        sense_router=sense_router, sense_channel=sense_channel,
    )
    seq.log.disabled = True
    return seq


class ChargeSequenceEventLoggingTests(unittest.TestCase):
    def test_relay_close_and_smu_output_enabled_logged_on_start(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _ScriptedSmu([0.05])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=RELAY_CLOSE" in m and "RELAY_ADDRESS=1" in m for m in messages))
        self.assertTrue(any(
            "EVENT_TYPE=SMU_OUTPUT_ENABLED" in m and "DEVICE=PXI-4130" in m and "RESOURCE=SMU1" in m
            for m in messages
        ))

    def test_relay_open_and_smu_output_disabled_logged_on_completion(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _ScriptedSmu([0.05])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=RELAY_OPEN" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=SMU_OUTPUT_DISABLED" in m and "VERIFIED=True" in m for m in messages))

    def test_matrix_route_applied_and_cleared_when_sense_channel_configured(self):
        storage = _RecordingStorage()
        router = _RecordingSenseRouter()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _ScriptedSmu([0.05])
        seq = _make_charge_sequence(dmm, smu, storage=storage, sense_router=router, sense_channel=1)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=MATRIX_ROUTE_APPLIED" in m and "SOURCE=DMM" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=MATRIX_ROUTE_CLEARED" in m for m in messages))
        self.assertEqual(router.calls, [("connect", 1), ("disconnect", 1)])

    def test_no_matrix_route_events_when_sense_channel_is_none(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _ScriptedSmu([0.05])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertFalse(any("MATRIX_ROUTE" in m for m in messages))

    def test_dmm_transient_failure_recovers_without_aborting(self):
        # Two consecutive failures (below the max of 3), then recovery --
        # the run must complete normally, not abort.
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, "FAIL", "FAIL", 4.0])
        smu = _ScriptedSmu([1.0, 1.0, 0.05])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        self.assertTrue(result)
        messages = storage.event_messages()
        self.assertEqual(sum("EVENT_TYPE=DMM_MEASUREMENT_FAILED" in m for m in messages), 2)
        self.assertTrue(any("EVENT_TYPE=DMM_MEASUREMENT_RECOVERED" in m and "AFTER_FAILURES=2" in m for m in messages))

    def test_dmm_failure_exceeding_the_bound_raises_and_aborts(self):
        # Three consecutive failures == the configured max -- must raise
        # DMMMeasurementLostError rather than tolerate a fourth attempt.
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, "FAIL", "FAIL", "FAIL"])
        smu = _ScriptedSmu([1.0, 1.0, 1.0])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        with self.assertRaises(DMMMeasurementLostError):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertEqual(sum("EVENT_TYPE=DMM_MEASUREMENT_FAILED" in m for m in messages), 3)
        self.assertFalse(any("EVENT_TYPE=DMM_MEASUREMENT_RECOVERED" in m for m in messages))
        self.assertEqual(storage.finish_calls[-1]["result"], "FAIL")
        self.assertEqual(storage.finish_calls[-1]["stop_reason"], "SAFETY_VIOLATION")

    def test_safety_monitor_triggered_logged_on_a_real_limit_violation(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 3.6])
        smu = _ScriptedSmu([1.0])
        seq = _make_charge_sequence(dmm, smu, storage=storage)
        seq.safety = _RecordingSafety(unsafe_on_sample=1)
        with self.assertRaises(Exception):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=SAFETY_MONITOR_TRIGGERED" in m for m in messages))


class DischargeSequenceEventLoggingTests(unittest.TestCase):
    def test_relay_close_open_and_smu_output_events_logged(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 2.9])
        smu = _ScriptedSmu([0.1])
        seq = _make_discharge_sequence(dmm, smu, storage=storage)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=RELAY_CLOSE" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=RELAY_OPEN" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=SMU_OUTPUT_ENABLED" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=SMU_OUTPUT_DISABLED" in m for m in messages))

    def test_matrix_route_events_when_sense_channel_configured(self):
        storage = _RecordingStorage()
        router = _RecordingSenseRouter()
        dmm = _ScriptedDmm([3.5, 2.9])
        smu = _ScriptedSmu([0.1])
        seq = _make_discharge_sequence(dmm, smu, storage=storage, sense_router=router, sense_channel=1)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=MATRIX_ROUTE_APPLIED" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=MATRIX_ROUTE_CLEARED" in m for m in messages))

    def test_dmm_transient_failure_recovers_without_aborting(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, "FAIL", 2.9])
        smu = _ScriptedSmu([0.1, 0.1])
        seq = _make_discharge_sequence(dmm, smu, storage=storage)
        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)
        self.assertTrue(result)
        messages = storage.event_messages()
        self.assertEqual(sum("EVENT_TYPE=DMM_MEASUREMENT_FAILED" in m for m in messages), 1)
        self.assertTrue(any("EVENT_TYPE=DMM_MEASUREMENT_RECOVERED" in m for m in messages))

    def test_dmm_failure_exceeding_the_bound_raises(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, "FAIL", "FAIL", "FAIL"])
        smu = _ScriptedSmu([0.1, 0.1, 0.1])
        seq = _make_discharge_sequence(dmm, smu, storage=storage)
        with self.assertRaises(DMMMeasurementLostError):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)


if __name__ == "__main__":
    unittest.main()
