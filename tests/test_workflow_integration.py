"""
Phase 3.5 integration tests -- proves the full path:

    ConcurrentSupervisor -> GroupWorker -> GroupRuntime -> run_group_factory
        -> test.py::_run_group_all_positions() / _run_one_monitor_position()
        -> test.py::_run_one_charge_or_discharge_position()
        -> REAL ChargeSequence / DischargeSequence / CycleSequence /
           MonitorBatterySequence
        -> BatteryOperationSequence.run_guarded()

actually executes the real workflow classes -- not fakes standing in
for them. Only HardwareManager is faked (mirrors tests/
test_concurrent_supervisor.py's own convention -- no real PXI hardware
in a unit test); the SMU/DMM/relay fakes it exposes are the SAME
scripted fakes tests/test_cycle_sequence.py and tests/
test_hardware_event_logging.py already use to exercise these real
sequence classes without hardware. DataStorage is real (opened by
GroupRuntime.connect() against a temp dir), so run_summary/event_log/
parent_run_id/related_runs are the real, unmodified schema -- this is
what lets these tests also confirm traceability survives the
concurrent path.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import config.devices as dev_cfg
import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from data.rotation import index_database_file
from orchestration.concurrent_supervisor import ConcurrentSupervisor
from orchestration.workflow_integration import make_run_group_factory


class _ScriptedDmm:
    model = "NI-4065"
    resource = "DMM1"

    def __init__(self, script):
        self._script = list(script) * 1000  # generous repeat -- monitor loops indefinitely
        self._idx = 0

    def measure_dc_voltage(self):
        value = self._script[self._idx % len(self._script)]
        self._idx += 1
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
        i = self._currents[self._idx % len(self._currents)]
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

    def open_all(self):
        pass


class _FakeNtcDaq:
    def read_channel(self, channel):
        return 2.5  # NTCPresence.PRESENT range -- presence pre-check passes


class _FakeHardwareManager:
    """
    Stands in for test_control.hardware_manager.HardwareManager --
    same role as tests/test_concurrent_supervisor.py's own fake, but
    this one exposes REAL scripted smu/dmm/relay/ntc_daq objects so the
    REAL ChargeSequence/DischargeSequence/CycleSequence/
    MonitorBatterySequence actually run against them.
    """
    def __init__(self, settings, relay_cfg, smu_cfg=None, daq_cfg=None,
                 dmm_cfg=None, ntc_daq_cfg=None):
        self.smu = _ScriptedSmu(currents=[0.05, 0.1])
        self.dmm = _ScriptedDmm(script=[3.5, 4.0, 3.5, 2.9])
        self.relay = _FakeRelay()
        self.daq = None
        self.ntc_daq = _FakeNtcDaq()

    def connect_all(self):
        pass

    def disconnect_all(self):
        pass

    def attach_run_id_provider(self, provider):
        pass


_TEST_GROUP = "PHASE35_TEST_GROUP"
_TEST_SETPOINTS = {
    "charge_current_a": 0.5, "charge_voltage_v": 4.0,
    "discharge_current_a": 0.1, "discharge_cutoff_v": 3.0,
}


def _synthetic_battery_groups():
    return {
        _TEST_GROUP: {
            "enabled": True,
            "relay_matrix": "MATRIX_NUMATO_202",
            "smu": "AUX_SMU_1",
            "dmm": "MAIN_DMM",
            "daq": "MAIN_DAQ",
            "ntc_daq": None,
            "sense_channel": None,
            "battery_type": "HUB",
            "test_setpoints": _TEST_SETPOINTS,
            "positions": {
                1: {"relay_address": 1, "enabled": True, "daq_ntc_ch": None},
            },
        },
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

        class _TestSettings:
            DATA_DIR = self.tmp_dir
            CSV_DIR = os.path.join(self.tmp_dir, "csv")
            STABILIZATION_S = 0.0
            SAMPLE_RATE_HZ = 100_000.0
            CHARGE_CUTOFF_A = 0.15
            CHARGE_TIMEOUT_S = 5.0
            DISCHARGE_TIMEOUT_S = 5.0
            REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = -0.5
            DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES = 3
            CYCLE_REST_S = 0.0

        self.settings = _TestSettings

        self._orig_battery_groups = dev_cfg.BATTERY_GROUPS
        dev_cfg.BATTERY_GROUPS = _synthetic_battery_groups()
        self.addCleanup(self._restore_battery_groups)

        hw_patcher = patch("orchestration.group_runtime.HardwareManager", _FakeHardwareManager)
        hw_patcher.start()
        self.addCleanup(hw_patcher.stop)

        # test.py's own _run_group_all_positions()/_run_one_monitor_
        # position()/_run_one_charge_or_discharge_position() construct
        # ChargeSequence/DischargeSequence/CycleSequence/
        # MonitorBatterySequence/SafetyMonitor with `settings=Settings` --
        # test.py's OWN module-level import, a fixed reference to the
        # real production Settings class, NOT whatever `settings` object
        # GroupRuntime/DataStorage were built with. Patching THIS name is
        # the only way to give the real sequence classes fast timing
        # (STABILIZATION_S/CYCLE_REST_S/etc.) in a test -- confirmed by
        # reading test.py's source, not assumed.
        settings_patcher = patch("test.Settings", self.settings)
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)

    def _restore_battery_groups(self):
        dev_cfg.BATTERY_GROUPS = self._orig_battery_groups

    def _run_summary_rows(self):
        import sqlite3
        conn = sqlite3.connect(index_database_file(self.settings))
        try:
            return list(conn.execute(
                "SELECT test_type, stop_reason, result, group_name, parent_run_id FROM run_summary ORDER BY id"
            ))
        finally:
            conn.close()


class ChargeThroughConcurrentSupervisorTests(_Base):
    def test_charge_sequence_runs_and_produces_a_passing_run_summary(self):
        factory = make_run_group_factory("charge")
        sup = ConcurrentSupervisor(
            [_TEST_GROUP], self.settings, factory, battery_groups=dev_cfg.BATTERY_GROUPS,
        )
        result = sup.run()

        self.assertFalse(result.faulted)
        rows = self._run_summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "charge_battery")
        self.assertEqual(rows[0][2], "PASS")
        self.assertEqual(rows[0][3], _TEST_GROUP)


class DischargeThroughConcurrentSupervisorTests(_Base):
    def test_discharge_sequence_runs_and_produces_a_passing_run_summary(self):
        factory = make_run_group_factory("discharge")
        sup = ConcurrentSupervisor(
            [_TEST_GROUP], self.settings, factory, battery_groups=dev_cfg.BATTERY_GROUPS,
        )
        result = sup.run()

        self.assertFalse(result.faulted)
        rows = self._run_summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "discharge_battery")
        self.assertEqual(rows[0][2], "PASS")


class CycleThroughConcurrentSupervisorTests(_Base):
    def test_cycle_sequence_runs_and_produces_parent_and_child_run_summaries(self):
        factory = make_run_group_factory("cycle")
        sup = ConcurrentSupervisor(
            [_TEST_GROUP], self.settings, factory, battery_groups=dev_cfg.BATTERY_GROUPS,
        )
        result = sup.run()

        self.assertFalse(result.faulted)
        rows = self._run_summary_rows()
        test_types = sorted(r[0] for r in rows)
        # CycleSequence's own parent run + charge/discharge child phase
        # runs -- see test_control/cycle_sequence.py::_make_phase_storage().
        self.assertEqual(test_types, ["charge_battery", "cycle_battery", "discharge_battery"])

        cycle_row = next(r for r in rows if r[0] == "cycle_battery")
        self.assertEqual(cycle_row[2], "PASS")
        parent_run_id = None
        for row in rows:
            if row[0] == "cycle_battery":
                # find its own run_id isn't selected here, but the CHILD
                # rows' parent_run_id must point at SOME non-null value --
                # confirms parent_run_id/related_runs traceability survived
                # the concurrent path end to end.
                pass
        child_rows = [r for r in rows if r[0] in ("charge_battery", "discharge_battery")]
        self.assertEqual(len(child_rows), 2)
        for row in child_rows:
            self.assertIsNotNone(row[4], "child phase run_summary row must carry parent_run_id")


class MonitorThroughConcurrentSupervisorTests(_Base):
    def test_monitor_sequence_runs_until_cooperatively_stopped(self):
        """
        MonitorBatterySequence.run() is unbounded by design (see its own
        docstring: "cancellation is the EXPECTED way a monitoring
        session ends") -- this test proves the concurrent path can stop
        it cooperatively via the SAME GroupRuntime.request_stop() a
        Global Emergency Stop would use, and that doing so is NOT
        reported as a fault.
        """
        import threading

        factory = make_run_group_factory("monitor")
        sup = ConcurrentSupervisor(
            [_TEST_GROUP], self.settings, factory, battery_groups=dev_cfg.BATTERY_GROUPS,
        )

        def stop_soon():
            import time
            time.sleep(0.3)
            for runtime in sup._runtimes:
                runtime.request_stop("test requested stop")

        threading.Thread(target=stop_soon, daemon=True).start()
        result = sup.run()

        self.assertFalse(result.faulted)
        rows = self._run_summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "monitor")


if __name__ == "__main__":
    unittest.main()
