"""
Tests for test_control/cycle_sequence.py::CycleSequence -- CURRENT
IMPLEMENTATION of docs/architecture.md Section 67 "CycleSequence -- Final
Design". Uses REAL, temp-directory DataStorage (CycleSequence constructs
real DataStorage instances internally via _make_phase_storage(), so a
fake/mock storage object cannot exercise that path) with scripted fake
SMU/DMM/relay/safety -- mirrors tests/test_hardware_event_logging.py's
established harness, extended with a real temp DATA_DIR/CSV_DIR so
_make_phase_storage() has somewhere real to write.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from config.settings import Settings
from data.rotation import index_database_file
from data.storage import DataStorage
from test_control.cycle_sequence import CycleSequence
from test_control.safety_monitor import SafetyStatus
from utils.cancellation import CancellationToken
from utils.errors import OperationCancelledError


class _ScriptedDmm:
    """Returns one voltage per call, in order -- see
    tests/test_hardware_event_logging.py::_ScriptedDmm (identical)."""
    model = "NI-4065"
    resource = "DMM1"

    def __init__(self, script):
        self._script = list(script)
        self._idx = 0

    def measure_dc_voltage(self):
        value = self._script[self._idx]
        self._idx += 1
        return value


class _ScriptedSmu:
    model = "PXI-4130"
    resource = "SMU1"

    def __init__(self, currents, fail_verification=False):
        self._currents = list(currents)
        self._idx = 0
        self.enabled = False
        self._fail_verification = fail_verification

    def set_charge_mode(self, current_a, voltage_limit_v):
        pass

    def set_discharge_mode(self, current_a, voltage_limit_v):
        pass

    def output_enable(self):
        self.enabled = True

    def measure(self):
        i = self._currents[self._idx % len(self._currents)]
        self._idx += 1
        return {"voltage_v": 0.0, "current_a": i}

    def emergency_output_off(self, reason, on_event=None):
        self.enabled = False
        return not self._fail_verification

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        return True


class _FakeRelay:
    name = "TEST_RELAY_MATRIX"

    def close(self, channel):
        pass

    def open(self, channel):
        pass

    def open_all(self):
        pass


class _RecordingSafety:
    def set_battery_limits(self, battery_cfg):
        pass

    def check(self, v, i, t_c, mode=None):
        return SafetyStatus(safe=True)

    def emergency_stop(self, smu, relay, reason, on_event=None):
        pass

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        pass


BATTERY_CFG = {
    "voltage_max_v": 4.2, "voltage_min_v": 3.0,
    "max_charge_current_a": 1.5, "max_discharge_current_a": 1.05,
    "max_temp_c": 45.0,
}
TEST_SETPOINTS = {
    "charge_current_a": 1.0, "charge_voltage_v": 4.0,
    "discharge_current_a": 0.1, "discharge_cutoff_v": 3.0,
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

        class _FastSettings:
            STABILIZATION_S = 0.0
            SAMPLE_RATE_HZ = 100_000.0
            CHARGE_CUTOFF_A = 0.15
            CHARGE_TIMEOUT_S = Settings.CHARGE_TIMEOUT_S
            DISCHARGE_TIMEOUT_S = Settings.DISCHARGE_TIMEOUT_S
            REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V
            DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES = 3
            CYCLE_REST_S = 0.0
            DATA_DIR = self.tmp_dir
            CSV_DIR = os.path.join(self.tmp_dir, "csv")

        self.settings = _FastSettings
        self.storage = DataStorage(settings=self.settings)
        self.storage.open()
        self.addCleanup(self.storage.close)

    def _index_rows(self, table, columns):
        conn = sqlite3.connect(index_database_file(self.settings))
        try:
            return list(conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"))
        finally:
            conn.close()

    def _make_cycle(self, smu, dmm, channel=1):
        # Mirrors test.py::_run_one_charge_or_discharge_position(), which
        # always calls start_run_summary() on the CALLER's storage before
        # constructing the sequence -- ChargeSequence/DischargeSequence
        # (and, for the same reason, CycleSequence at its own top level)
        # never call it themselves.
        self.storage.start_run_summary(test_type="cycle_battery", group_name="B1", position_in_group=channel)
        seq = CycleSequence(
            smu=smu, dmm=dmm, relay=_FakeRelay(), safety=_RecordingSafety(),
            storage=self.storage, settings=self.settings, group_name="B1",
        )
        seq.log.disabled = True
        return seq


class SuccessfulSingleCycleTests(_Base):
    def test_one_repetition_creates_one_cycle_row_and_two_phase_rows(self):
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9])
        smu = _ScriptedSmu([0.05, 0.1])
        seq = self._make_cycle(smu, dmm)
        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                          test_setpoints={**TEST_SETPOINTS, "cycle_count": 1})
        self.assertTrue(result)

        rows = self._index_rows("run_summary", ["test_type", "stop_reason", "result", "cycle_count"])
        test_types = sorted(r[0] for r in rows)
        self.assertEqual(test_types, ["charge_battery", "cycle_battery", "discharge_battery"])

        cycle_row = next(r for r in rows if r[0] == "cycle_battery")
        self.assertEqual(cycle_row[1], "COMPLETED")
        self.assertEqual(cycle_row[2], "PASS")
        self.assertEqual(cycle_row[3], 1)

    def test_cycle_count_defaults_to_one_when_omitted(self):
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9])
        smu = _ScriptedSmu([0.05, 0.1])
        seq = self._make_cycle(smu, dmm)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)
        rows = self._index_rows("run_summary", ["test_type", "cycle_count"])
        cycle_row = next(r for r in rows if r[0] == "cycle_battery")
        self.assertEqual(cycle_row[1], 1)

    def test_two_repetitions_creates_five_run_summary_rows(self):
        # 1 cycle-level + (2 charge + 2 discharge) phase-level = 5.
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9] * 2)
        smu = _ScriptedSmu([0.05, 0.1])
        seq = self._make_cycle(smu, dmm)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                test_setpoints={**TEST_SETPOINTS, "cycle_count": 2})
        rows = self._index_rows("run_summary", ["test_type"])
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted(r[0] for r in rows),
                          sorted(["cycle_battery", "charge_battery", "charge_battery",
                                  "discharge_battery", "discharge_battery"]))

    def test_each_phase_gets_a_distinct_run_id(self):
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9])
        smu = _ScriptedSmu([0.05, 0.1])
        seq = self._make_cycle(smu, dmm)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                test_setpoints={**TEST_SETPOINTS, "cycle_count": 1})
        rows = self._index_rows("run_summary", ["run_id"])
        run_ids = [r[0] for r in rows]
        self.assertEqual(len(run_ids), len(set(run_ids)), "every row must have a unique run_id")

    def test_rest_events_logged_between_charge_and_discharge(self):
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9])
        smu = _ScriptedSmu([0.05, 0.1])
        seq = self._make_cycle(smu, dmm)
        seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                test_setpoints={**TEST_SETPOINTS, "cycle_count": 1})
        events = self._index_rows("event_log", ["message"]) if False else None
        # event_log lives in the telemetry DB, not the index DB -- query it directly.
        from data.rotation import telemetry_database_file
        conn = sqlite3.connect(telemetry_database_file(self.settings))
        try:
            messages = [r[0] for r in conn.execute(
                "SELECT message FROM event_log WHERE source='cycle_battery' ORDER BY id"
            )]
        finally:
            conn.close()
        self.assertTrue(any("charge phase starting" in m for m in messages))
        self.assertTrue(any("resting" in m for m in messages))
        self.assertTrue(any("discharge phase starting" in m for m in messages))
        self.assertTrue(any("repetition 1/1: complete" in m for m in messages))


class FailureStopsTheCycleTests(_Base):
    def test_charge_phase_failure_prevents_discharge_and_further_repetitions(self):
        dmm = _ScriptedDmm([3.5, 4.0, 3.5, 2.9] * 3)
        smu = _ScriptedSmu([0.05, 0.1], fail_verification=True)  # emergency_output_off() always fails
        seq = self._make_cycle(smu, dmm)
        with self.assertRaises(Exception):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                    test_setpoints={**TEST_SETPOINTS, "cycle_count": 3})

        rows = self._index_rows("run_summary", ["test_type", "result"])
        test_types = [r[0] for r in rows]
        # Exactly one charge-phase row (the failed one) -- no discharge
        # row, no second/third repetition ever attempted.
        self.assertEqual(test_types.count("charge_battery"), 1)
        self.assertEqual(test_types.count("discharge_battery"), 0)
        cycle_row = next(r for r in rows if r[0] == "cycle_battery")
        self.assertEqual(cycle_row[1], "FAIL")


class CancellationTests(_Base):
    def test_cancellation_before_first_repetition_marks_cycle_cancelled(self):
        dmm = _ScriptedDmm([3.5])
        smu = _ScriptedSmu([0.05])
        seq = self._make_cycle(smu, dmm)
        token = CancellationToken(owner="test")
        token.request_cancel()
        with self.assertRaises(OperationCancelledError):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG,
                    test_setpoints={**TEST_SETPOINTS, "cycle_count": 1}, token=token)
        rows = self._index_rows("run_summary", ["test_type", "stop_reason"])
        cycle_row = next(r for r in rows if r[0] == "cycle_battery")
        self.assertEqual(cycle_row[1], "CANCELLED")
        # No phase ever started -- cancellation checkpoint fires before
        # the charge phase is even constructed.
        self.assertEqual(len([r for r in rows if r[0] != "cycle_battery"]), 0)


if __name__ == "__main__":
    unittest.main()
