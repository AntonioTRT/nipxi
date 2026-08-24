"""
Tests for the Group -> ALL Fault Classification Policy refinement (see
docs/architecture.md "Group -> ALL Fault Classification Policy") --
introduces STATION_FAULT as a third outcome distinct from FAIL/SKIPPED/
PASS/CANCELLED, so a test-station hardware fault (shared relay/SMU/DMM/
DAQ equipment, or any truly unclassified exception) aborts the whole
Group -> ALL run, while a battery-under-test problem (specific to one
position) only fails/skips that one slot and the group continues.

Two levels are tested:
  1. test.py::_classify_position_exception() -- the pure classifier, unit
     tested directly against every exception type named in the policy.
  2. test.py::_run_one_charge_or_discharge_position() -- an integration-
     style test with a scripted fake sequence_cls (mirrors the fake-
     hardware harness in tests/test_hardware_event_logging.py), covering
     the full return-value + GROUP_RUN_ABORTED_STATION_FAULT event-logging
     wiring end to end for each of the 8 scenarios in the design brief.
"""

import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from utils.errors import (
    BatteryRemovedDuringChargeError,
    DAQError,
    DMMError,
    DMMMeasurementLostError,
    NIPXITimeoutError,
    OperationCancelledError,
    RelayError,
    ReversePolarityError,
    SafetyViolationError,
    SMUError,
)
from utils.event_format import EventType


class ClassifyPositionExceptionTests(unittest.TestCase):
    """Direct unit tests for the pure classifier -- one case per exception
    type named in the design brief's Category 1/Category 2 lists, plus
    the "truly unclassified" default."""

    def _classify(self, exc):
        return test_module._classify_position_exception(exc)

    # Category 2 -- test-station hardware failures -> STATION_FAULT.
    def test_relay_error_is_station_fault(self):
        self.assertEqual(self._classify(RelayError("relay comms lost")), "STATION_FAULT")

    def test_smu_error_is_station_fault(self):
        self.assertEqual(self._classify(SMUError("SMU comms lost")), "STATION_FAULT")

    def test_dmm_error_is_station_fault(self):
        self.assertEqual(self._classify(DMMError("DMM comms lost")), "STATION_FAULT")

    def test_daq_error_is_station_fault(self):
        self.assertEqual(self._classify(DAQError("DAQ comms lost")), "STATION_FAULT")

    def test_dmm_measurement_lost_error_is_station_fault_not_battery_fail(self):
        # The critical ordering case: DMMMeasurementLostError IS a
        # SafetyViolationError subclass (see utils/errors.py's own
        # ordering note), so a naive "check SafetyViolationError first"
        # classifier would misclassify this as FAIL. Must be STATION_FAULT.
        self.assertEqual(
            self._classify(DMMMeasurementLostError("DMM communication lost")), "STATION_FAULT"
        )

    def test_unknown_exception_defaults_to_station_fault(self):
        # "Any truly unclassified exception should default to
        # STATION_FAULT" -- unknown system state must be treated as unsafe.
        self.assertEqual(self._classify(ValueError("something never seen before")), "STATION_FAULT")

    def test_bare_exception_defaults_to_station_fault(self):
        self.assertEqual(self._classify(Exception("generic")), "STATION_FAULT")

    # Category 1 -- battery-under-test failures -> FAIL.
    def test_battery_removed_during_charge_is_fail(self):
        self.assertEqual(self._classify(BatteryRemovedDuringChargeError("removed")), "FAIL")

    def test_reverse_polarity_is_fail(self):
        self.assertEqual(self._classify(ReversePolarityError("reversed")), "FAIL")

    def test_generic_safety_violation_is_fail(self):
        self.assertEqual(self._classify(SafetyViolationError("overvoltage")), "FAIL")

    def test_charge_timeout_is_fail(self):
        self.assertEqual(self._classify(NIPXITimeoutError("charge timed out")), "FAIL")

    def test_discharge_timeout_is_fail(self):
        self.assertEqual(self._classify(NIPXITimeoutError("discharge timed out")), "FAIL")


class _FakeStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.events = []
        self.finish_calls = []
        self.execution_states = []
        self.measurements = []

    def start_run_summary(self, **kwargs):
        pass

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def record_execution_state(self, **kwargs):
        self.execution_states.append(kwargs)

    def record_measurement(self, **kwargs):
        self.measurements.append(kwargs)

    def finish_run_summary(self, **kwargs):
        self.finish_calls.append(kwargs)

    def get_run_summary(self, run_id):
        return None

    def event_messages(self):
        return [e["message"] for e in self.events]


class _FakeRelay:
    def close(self, channel):
        pass

    def open(self, channel):
        pass


class _FakeDmm:
    def measure_dc_voltage(self):
        return 3.7  # a present, healthy battery voltage -- precheck passes


class _FakeNtcDaq:
    def read_channel(self, channel):
        return 2.5  # NTCPresence.PRESENT range -- precheck passes


def _make_sequence_cls(exc_to_raise=None):
    """A scripted fake sequence_cls -- accepts every constructor kwarg
    _run_one_charge_or_discharge_position() passes and ignores them
    (mirrors _ScriptedDmm/_ScriptedSmu's role in
    tests/test_hardware_event_logging.py); .run() raises `exc_to_raise`
    (or completes normally, appending "PASS", if None)."""

    class _FakeSequence:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            if exc_to_raise is not None:
                raise exc_to_raise

    return _FakeSequence


class _HwMgr:
    def __init__(self):
        self.smu = object()
        self.dmm = _FakeDmm()
        self.relay = _FakeRelay()
        self.ntc_daq = _FakeNtcDaq()


def _run_position(exc_to_raise=None):
    """Runs _run_one_charge_or_discharge_position() for a single B1
    Position 1, with a scripted sequence_cls that raises `exc_to_raise`
    from .run() (or completes normally if None). Monkeypatches
    config/devices.py::BATTERY_GROUPS exactly as
    tests/test_battery_presence_precheck.py already does, so the Battery
    Presence + NTC Presence pre-check this function calls internally sees
    a single, always-present/readable position and never blocks the run
    before .run() is even reached."""
    import config.devices as dev_cfg

    orig_groups = dev_cfg.BATTERY_GROUPS
    dev_cfg.BATTERY_GROUPS = {
        "B1": {"positions": {1: {"daq_ntc_ch": "Dev1/ai0", "relay_address": 1, "enabled": True}}},
    }
    try:
        storage = _FakeStorage()
        hw_mgr = _HwMgr()
        hw = {
            "smu_name": "SMU1", "smu_cfg": {"model": "PXI-4130", "resource": "SMU1"},
            "dmm_name": "DMM1", "dmm_cfg": {"model": "NI-4065", "resource": "DMM1"},
            "daq_name": "DAQ1", "daq_cfg": {"model": "NI-6210", "resource": "DAQ1"},
            "relay_matrix_cfg": {"name": "RELAY1", "type": "ethernet", "ip": "10.0.0.1", "port": 5000},
            "relay_matrix_name": "RELAY1",
            "ntc_daq_name": "DAQ1",
        }
        battery_cfg = {
            "voltage_max_v": 4.2, "voltage_min_v": 3.0,
            "max_charge_current_a": 1.0, "max_discharge_current_a": 1.0,
            "capacity_ah": 2.0,
        }
        result = test_module._run_one_charge_or_discharge_position(
            operation="Charge Battery", sequence_cls=_make_sequence_cls(exc_to_raise),
            source="charge_battery", group="B1", hw=hw, battery_type="Li-ion",
            battery_cfg=battery_cfg, test_setpoints={}, position=1, channel=1,
            relay_address=1, ch_cfg={"daq_ntc_ch": "Dev1/ai0"}, hw_mgr=hw_mgr,
            storage=storage, safety=None, sense_router=None, sense_channel=None, token=None,
        )
        return result, storage
    finally:
        dev_cfg.BATTERY_GROUPS = orig_groups


class RunOnePositionStationFaultIntegrationTests(unittest.TestCase):
    """Integration coverage for the 8 scenarios in the design brief, at
    the level of _run_one_charge_or_discharge_position() -- the exact
    function Group -> ALL orchestration calls once per position."""

    def test_relay_error_returns_station_fault_and_logs_the_event(self):
        result, storage = _run_position(RelayError("relay comms lost"))
        self.assertEqual(result, "STATION_FAULT")
        messages = storage.event_messages()
        self.assertTrue(any("GROUP_RUN_ABORTED_STATION_FAULT" in m for m in messages))
        self.assertTrue(any("EXCEPTION=RelayError" in m for m in messages))
        self.assertTrue(any("GROUP=B1" in m and "POSITION=1" in m for m in messages))

    def test_dmm_measurement_lost_error_returns_station_fault_and_logs_the_event(self):
        result, storage = _run_position(DMMMeasurementLostError("DMM communication lost"))
        self.assertEqual(result, "STATION_FAULT")
        messages = storage.event_messages()
        self.assertTrue(any("EXCEPTION=DMMMeasurementLostError" in m for m in messages))
        self.assertTrue(any("MESSAGE=DMM communication lost" in m for m in messages))

    def test_daq_communication_failure_returns_station_fault(self):
        result, storage = _run_position(DAQError("DAQ communication failure"))
        self.assertEqual(result, "STATION_FAULT")
        self.assertTrue(any("EXCEPTION=DAQError" in m for m in storage.event_messages()))

    def test_charge_timeout_returns_fail_not_station_fault(self):
        result, storage = _run_position(NIPXITimeoutError("charge timed out"))
        self.assertEqual(result, "FAIL")
        self.assertFalse(any("GROUP_RUN_ABORTED_STATION_FAULT" in m for m in storage.event_messages()))

    def test_discharge_timeout_returns_fail_not_station_fault(self):
        result, storage = _run_position(NIPXITimeoutError("discharge timed out"))
        self.assertEqual(result, "FAIL")

    def test_operator_cancellation_returns_cancelled(self):
        result, _ = _run_position(OperationCancelledError("operator stop"))
        self.assertEqual(result, "CANCELLED")

    def test_keyboard_interrupt_returns_cancelled(self):
        result, _ = _run_position(KeyboardInterrupt())
        self.assertEqual(result, "CANCELLED")

    def test_unknown_exception_returns_station_fault(self):
        result, storage = _run_position(ValueError("never seen before"))
        self.assertEqual(result, "STATION_FAULT")
        self.assertTrue(any("EXCEPTION=ValueError" in m for m in storage.event_messages()))

    def test_normal_completion_returns_pass(self):
        result, storage = _run_position(None)
        self.assertEqual(result, "PASS")
        self.assertFalse(any("GROUP_RUN_ABORTED_STATION_FAULT" in m for m in storage.event_messages()))

    def test_station_fault_overwrites_run_summary_result(self):
        # The DB-persisted run_summary.result must match the orchestration-
        # level classification, not run_guarded()'s own earlier "FAIL"
        # write (see run_guarded()'s RelayError branch) -- this function
        # must overwrite it to "STATION_FAULT" once classified.
        _, storage = _run_position(RelayError("relay comms lost"))
        self.assertTrue(any(c.get("result") == "STATION_FAULT" for c in storage.finish_calls))

    def test_station_fault_overwrites_execution_state_too(self):
        # A final-review fix: run_guarded()'s exception branches also write
        # their own terminal state to the append-only station_state table
        # (record_execution_state()) before re-raising -- that record must
        # be overwritten to STATION_FAULT too, or station_state would
        # permanently disagree with the (correctly overwritten) run_summary
        # row for the same run.
        _, storage = _run_position(RelayError("relay comms lost"))
        self.assertTrue(any(s.get("state") == "STATION_FAULT" for s in storage.execution_states))


if __name__ == "__main__":
    unittest.main()
