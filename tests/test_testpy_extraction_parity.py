"""
Parity regression tests for the test.py extraction (see docs/
architecture.md "Preparation Phase: Six Resolved Decisions Before
worker_runtime.py" and "Remaining Helper Extraction Before
worker_runtime.py"): _resolve_group_hardware(), _open_storage_guarded(),
_select_battery_position()'s bounds-check, _ntc_group_snapshot(),
_hardware_snapshot_fields(), and _start_run_summary_guarded() were all
turned into thin wrappers over utils/group_hardware.py and
test_control/storage_session.py/ntc_snapshot.py.

These tests exist specifically to prove test.py's own operator-facing
behavior (exact printed messages, exact return values, exact call
sequence) did not change -- not to re-test the extracted modules'
internal logic (already covered by test_group_hardware.py,
test_storage_session.py, test_ntc_snapshot.py).

No hardware access anywhere in this file.
"""

import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from config.settings import Settings


class ResolveGroupHardwareParityTests(unittest.TestCase):
    def test_matches_shared_implementation_on_success(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = test_module._resolve_group_hardware("B1")
        self.assertIsNotNone(result)
        self.assertEqual(buf.getvalue(), "", "must print nothing on a successful resolution")
        hw, battery_type, battery_cfg = result
        self.assertEqual(battery_type, "HUB")

    def test_prints_fail_message_with_missing_role_name(self):
        original = test_module.dev_cfg.hardware_for_group

        def _patched(group):
            hw = dict(original(group))
            hw["dmm_cfg"] = None
            return hw

        with patch.object(test_module.dev_cfg, "hardware_for_group", _patched):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = test_module._resolve_group_hardware("B1")
        self.assertIsNone(result)
        printed = buf.getvalue()
        self.assertIn("[FAIL]", printed)
        self.assertIn("dmm", printed)
        self.assertIn("Aborting, no hardware activated", printed)


class SelectBatteryPositionParityTests(unittest.TestCase):
    def test_valid_position_returns_int_and_prints_only_the_prompt(self):
        with patch("builtins.input", return_value="3"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = test_module._select_battery_position("B1")
        self.assertEqual(result, 3)
        self.assertNotIn("out of range", buf.getvalue())
        self.assertNotIn("Invalid selection", buf.getvalue())

    def test_out_of_range_position_prints_the_exact_original_message(self):
        size = test_module.dev_cfg.group_size("B1")
        with patch("builtins.input", return_value=str(size + 1)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = test_module._select_battery_position("B1")
        self.assertIsNone(result)
        self.assertIn(f"Position {size + 1} out of range (1-{size}).", buf.getvalue())

    def test_non_numeric_input_prints_invalid_selection(self):
        with patch("builtins.input", return_value="not-a-number"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = test_module._select_battery_position("B1")
        self.assertIsNone(result)
        self.assertIn("Invalid selection.", buf.getvalue())

    def test_delegates_bounds_check_to_shared_helper(self):
        """Confirms the extraction actually delegates (not a parallel
        reimplementation that happens to agree today)."""
        calls = []
        original = test_module._shared_validate_position_in_group

        def _spy(group, position):
            calls.append((group, position))
            return original(group, position)

        with patch.object(test_module, "_shared_validate_position_in_group", _spy):
            with patch("builtins.input", return_value="1"):
                test_module._select_battery_position("B1")
        self.assertEqual(calls, [("B1", 1)])


class OpenStorageGuardedParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self._orig_data_dir = Settings.DATA_DIR
        self._orig_csv_dir = Settings.CSV_DIR
        # DATABASE_FILE no longer exists on real Settings by default (see
        # data/rotation.py -- telemetry paths are computed live from
        # DATA_DIR/current month) -- it is set here only as a test-local
        # override (honored by data/rotation.py::telemetry_database_file()),
        # so it must not exist afterward either.
        self._had_db_file = hasattr(Settings, "DATABASE_FILE")
        self._orig_db_file = getattr(Settings, "DATABASE_FILE", None)
        self.addCleanup(self._restore_settings)

    def _restore_settings(self):
        Settings.DATA_DIR = self._orig_data_dir
        Settings.CSV_DIR = self._orig_csv_dir
        if self._had_db_file:
            Settings.DATABASE_FILE = self._orig_db_file
        elif hasattr(Settings, "DATABASE_FILE"):
            del Settings.DATABASE_FILE

    def test_success_path_returns_opened_storage_prints_nothing(self):
        Settings.DATA_DIR = self.tmp_dir
        Settings.CSV_DIR = os.path.join(self.tmp_dir, "csv")
        Settings.DATABASE_FILE = os.path.join(self.tmp_dir, "nipxi_parity_test.db")

        buf = io.StringIO()
        with redirect_stdout(buf):
            storage = test_module._open_storage_guarded()
        self.addCleanup(storage.close)
        self.assertIsNotNone(storage)
        self.assertEqual(buf.getvalue(), "")

    def test_failure_path_prints_identical_fail_message(self):
        bad_path = os.path.join(self.tmp_dir, "a_directory_not_a_db_file")
        os.makedirs(bad_path)
        Settings.DATA_DIR = self.tmp_dir
        Settings.CSV_DIR = os.path.join(self.tmp_dir, "csv")
        Settings.DATABASE_FILE = bad_path

        buf = io.StringIO()
        with redirect_stdout(buf):
            storage = test_module._open_storage_guarded()
        self.assertIsNone(storage)
        printed = buf.getvalue()
        self.assertIn("[FAIL] Database unavailable", printed)
        self.assertIn("Aborting, no hardware activated", printed)


class NtcGroupSnapshotParityTests(unittest.TestCase):
    def test_delegates_to_shared_implementation_with_same_arguments(self):
        calls = []

        def _spy(storage, daq, group, size, source, phase_detail=None, log_summary=False):
            calls.append((storage, daq, group, size, source, phase_detail, log_summary))
            return ["sentinel-result"]

        with patch.object(test_module, "_shared_ntc_group_snapshot", _spy):
            result = test_module._ntc_group_snapshot(
                "storage-sentinel", "daq-sentinel", "B1", 4, "charge_battery",
                phase_detail="NTC_PRECHECK", log_summary=True,
            )
        self.assertEqual(result, ["sentinel-result"])
        self.assertEqual(calls, [
            ("storage-sentinel", "daq-sentinel", "B1", 4, "charge_battery", "NTC_PRECHECK", True),
        ])

    def test_none_daq_is_still_a_no_op_through_the_wrapper(self):
        result = test_module._ntc_group_snapshot(None, None, "B1", 4, "charge_battery")
        self.assertEqual(result, [])


class HardwareSnapshotFieldsParityTests(unittest.TestCase):
    _RELAY_CFG = {"name": "MATRIX_NUMATO_202", "driver": "RELAY32ETHRL00",
                  "type": "ethernet", "ip": "169.254.1.202", "port": 23}

    def test_delegates_to_shared_implementation_with_same_arguments(self):
        calls = []

        def _spy(smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg):
            calls.append((smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg))
            return {"sentinel": True}

        with patch.object(test_module, "_shared_hardware_snapshot_fields", _spy):
            result = test_module._hardware_snapshot_fields(
                "AUX_SMU_1", {"resource": "PXI1Slot7"}, "MAIN_DMM", {"resource": "PXI1Slot3"},
                "MAIN_DAQ", {"resource": "PXI1Slot2"}, self._RELAY_CFG,
            )
        self.assertEqual(result, {"sentinel": True})
        self.assertEqual(calls, [(
            "AUX_SMU_1", {"resource": "PXI1Slot7"}, "MAIN_DMM", {"resource": "PXI1Slot3"},
            "MAIN_DAQ", {"resource": "PXI1Slot2"}, self._RELAY_CFG,
        )])

    def test_real_output_matches_pre_extraction_shape(self):
        fields = test_module._hardware_snapshot_fields(
            "AUX_SMU_1", {"resource": "PXI1Slot7", "model": "PXI-4130"},
            "MAIN_DMM", {"resource": "PXI1Slot3", "model": "PXI-4065"},
            "MAIN_DAQ", {"resource": "PXI1Slot2", "model": "PXIe-6363"},
            self._RELAY_CFG,
        )
        self.assertEqual(fields["smu_name"], "AUX_SMU_1")
        self.assertEqual(fields["relay_matrix_resource"], "169.254.1.202:23")
        self.assertEqual(fields["dmm_model"], "PXI-4065")


class StartRunSummaryGuardedParityTests(unittest.TestCase):
    class _FakeStorage:
        def __init__(self, raise_error=False):
            self.calls = []
            self._raise_error = raise_error

        def start_run_summary(self, test_type, **fields):
            self.calls.append((test_type, fields))
            if self._raise_error:
                raise sqlite3.OperationalError("simulated database unavailability")

    def test_success_prints_nothing_and_returns_true(self):
        storage = self._FakeStorage()
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = test_module._start_run_summary_guarded(storage, "charge_battery", battery_type="HUB")
        self.assertTrue(result)
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(storage.calls, [("charge_battery", {"battery_type": "HUB"})])

    def test_failure_prints_identical_fail_message_and_returns_false(self):
        storage = self._FakeStorage(raise_error=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = test_module._start_run_summary_guarded(storage, "charge_battery")
        self.assertFalse(result)
        printed = buf.getvalue()
        self.assertIn("[FAIL] Database unavailable -- could not start run_summary", printed)
        self.assertIn("Aborting, no hardware activated", printed)

    def test_delegates_to_shared_implementation_with_on_fail_print(self):
        calls = []

        def _spy(storage, test_type, on_fail=None, **fields):
            calls.append((storage, test_type, on_fail, fields))
            return True

        storage = self._FakeStorage()
        with patch.object(test_module, "_shared_start_run_summary_guarded", _spy):
            result = test_module._start_run_summary_guarded(storage, "discharge_battery", battery_type="SB")
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        called_storage, called_test_type, called_on_fail, called_fields = calls[0]
        self.assertIs(called_storage, storage)
        self.assertEqual(called_test_type, "discharge_battery")
        self.assertIs(called_on_fail, print)
        self.assertEqual(called_fields, {"battery_type": "SB"})


if __name__ == "__main__":
    unittest.main()
