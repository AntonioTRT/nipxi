"""
Tests for Change 1 ("Escalate failed shutdown verification to
STATION_FAULT") and its Change 5/6/7 persistence wiring -- see
docs/architecture.md "Safety Fault Lifecycle".

Before this change, a failed emergency_output_off() at the end of a
Charge/Discharge Sequence was only logged (CRITICAL) -- the sequence
still returned normally and the position was reported PASS. These tests
pin the new behavior: a verification failure now raises
SMUStateVerificationError (an existing utils/errors.py::SMUError
subclass -- no new fault-classification system), which
test.py::_classify_position_exception() already classifies as
STATION_FAULT, and a SAFETY_FAULT_RAISED/SAFETY_FAULT_ACKNOWLEDGED pair
is persisted with the OutputVerificationResult distinction preserved.

Scripted fake hardware, no real hardware/sleeps/stdin blocking -- mirrors
the harness established in tests/test_hardware_event_logging.py.
"""

import unittest
from unittest import mock

from config.settings import Settings
from test_control.charge_sequence import ChargeSequence
from test_control.discharge_sequence import DischargeSequence
from test_control.safety_monitor import SafetyStatus
from utils.errors import SMUStateVerificationError


class _FailingSmu:
    """Always fails shutdown verification -- emits the exact on_event
    message hardware/smu.py::SMU.emergency_output_off() emits, so
    extract_verification_result() has something real to parse."""
    model = "PXI-4130"
    resource = "SMU1"
    name = "SMU_PXI1Slot5"

    def __init__(self, currents, verification_result="still_enabled"):
        self._currents = list(currents)
        self._idx = 0
        self.verification_result = verification_result

    def set_charge_mode(self, current_a, voltage_limit_v):
        pass

    def set_discharge_mode(self, current_a, voltage_limit_v):
        pass

    def output_enable(self):
        pass

    def measure(self):
        i = self._currents[self._idx]
        self._idx += 1
        return {"voltage_v": 0.0, "current_a": i}

    def emergency_output_off(self, reason, on_event=None):
        if on_event is not None:
            on_event(f"emergency_output_off requested ({reason})")
            on_event(
                f"output disabled verification result: {self.verification_result} "
                f"(exhausted 3 attempt(s))"
            )
        return False

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        return True


class _ScriptedDmm:
    model = "NI-4065"
    resource = "DMM1"

    def __init__(self, script):
        self._script = list(script)
        self._idx = 0

    def measure_dc_voltage(self):
        value = self._script[self._idx]
        self._idx += 1
        return value


class _FakeRelay:
    name = "TEST_RELAY_MATRIX"

    def __init__(self):
        self.open_all_calls = 0

    def close(self, channel):
        pass

    def open(self, channel):
        pass

    def open_all(self):
        self.open_all_calls += 1


class _RecordingSafety:
    def set_battery_limits(self, battery_cfg):
        pass

    def check(self, v, i, t_c, mode=None):
        return SafetyStatus(safe=True)

    def emergency_stop(self, smu, relay, reason, on_event=None):
        pass

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        pass


class _RecordingStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.events = []
        self.finish_calls = []
        self.execution_states = []

    def get_run_summary(self, run_id):
        return None

    def log_event(self, **kwargs):
        self.events.append(kwargs)
        return len(self.events)

    def record_execution_state(self, **kwargs):
        self.execution_states.append(kwargs)

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


def _patch_input():
    # display_safety_fault_screen() calls input() -- never let a test
    # depend on stdin/EOF timing behavior; always resolve immediately.
    return mock.patch("builtins.input", return_value="")


class ChargeSequenceEscalationTests(unittest.TestCase):
    def test_failed_verification_raises_smu_state_verification_error(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _FailingSmu([0.05])
        relay = _FakeRelay()
        seq = ChargeSequence(
            smu=smu, dmm=dmm, relay=relay, safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=5, relay_address=5, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)

    def test_all_relays_forced_open_before_raising(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _FailingSmu([0.05])
        relay = _FakeRelay()
        seq = ChargeSequence(
            smu=smu, dmm=dmm, relay=relay, safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=5, relay_address=5, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        self.assertGreaterEqual(relay.open_all_calls, 1)

    def test_safety_fault_raised_and_acknowledged_are_persisted_with_verification_result(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _FailingSmu([0.05], verification_result="still_enabled")
        relay = _FakeRelay()
        seq = ChargeSequence(
            smu=smu, dmm=dmm, relay=relay, safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=5, relay_address=5, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)

        messages = storage.event_messages()
        raised = [m for m in messages if "EVENT_TYPE=SAFETY_FAULT_RAISED" in m]
        acked = [m for m in messages if "EVENT_TYPE=SAFETY_FAULT_ACKNOWLEDGED" in m]
        self.assertEqual(len(raised), 1)
        self.assertEqual(len(acked), 1)
        self.assertIn("DEVICE_NAME=SMU_PXI1Slot5", raised[0])
        self.assertIn("DEVICE_TYPE=SMU", raised[0])
        self.assertIn("POSITION=5", raised[0])
        self.assertIn("VERIFICATION_RESULT=still_enabled", raised[0])
        # Correlation (Change 7) -- same fault_id in both records.
        raised_fault_id = [p for p in raised[0].split() if p.startswith("FAULT_ID=")][0].split("=", 1)[1]
        self.assertIn(f"ACKNOWLEDGES_FAULT_ID={raised_fault_id}", acked[0])

    def test_distinguishes_comm_failure_from_still_enabled(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])
        smu = _FailingSmu([0.05], verification_result="verification_comm_failure")
        relay = _FakeRelay()
        seq = ChargeSequence(
            smu=smu, dmm=dmm, relay=relay, safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=5, relay_address=5, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        messages = storage.event_messages()
        raised = [m for m in messages if "EVENT_TYPE=SAFETY_FAULT_RAISED" in m]
        self.assertIn("VERIFICATION_RESULT=verification_comm_failure", raised[0])

    def test_successful_verification_does_not_raise_or_report_a_fault(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 4.0])

        class _OkSmu(_FailingSmu):
            def emergency_output_off(self, reason, on_event=None):
                if on_event is not None:
                    on_event(f"emergency_output_off requested ({reason})")
                    on_event("output disabled verification result: disabled")
                return True

        smu = _OkSmu([0.05])
        seq = ChargeSequence(
            smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        result = seq.run(channel=5, relay_address=5, battery_cfg=BATTERY_CFG, test_setpoints=CHARGE_SETPOINTS)
        self.assertTrue(result)
        self.assertFalse(any("SAFETY_FAULT_RAISED" in m for m in storage.event_messages()))


class DischargeSequenceEscalationTests(unittest.TestCase):
    def test_failed_verification_raises_smu_state_verification_error(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 2.9])
        smu = _FailingSmu([0.1])
        seq = DischargeSequence(
            smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=2, relay_address=2, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)

    def test_safety_fault_persisted(self):
        storage = _RecordingStorage()
        dmm = _ScriptedDmm([3.5, 2.9])
        smu = _FailingSmu([0.1], verification_result="still_enabled")
        seq = DischargeSequence(
            smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(), storage=storage,
            settings=_FastSettings, group_name="B1",
        )
        seq.log.disabled = True
        with _patch_input():
            with self.assertRaises(SMUStateVerificationError):
                seq.run(channel=2, relay_address=2, battery_cfg=BATTERY_CFG, test_setpoints=DISCHARGE_SETPOINTS)
        messages = storage.event_messages()
        self.assertTrue(any("EVENT_TYPE=SAFETY_FAULT_RAISED" in m and "POSITION=2" in m for m in messages))
        self.assertTrue(any("EVENT_TYPE=SAFETY_FAULT_ACKNOWLEDGED" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
