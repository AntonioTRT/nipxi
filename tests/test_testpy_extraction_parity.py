"""
Parity regression tests for the test.py extraction (see docs/
architecture.md "Preparation Phase: Six Resolved Decisions Before
worker_runtime.py"): _resolve_group_hardware(), _open_storage_guarded(),
_select_battery_position()'s bounds-check, and _ntc_group_snapshot() were
turned into thin wrappers over utils/group_hardware.py,
test_control/storage_session.py, and test_control/ntc_snapshot.py.

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
        self._orig_db_file = Settings.DATABASE_FILE
        self.addCleanup(self._restore_settings)

    def _restore_settings(self):
        Settings.DATA_DIR = self._orig_data_dir
        Settings.CSV_DIR = self._orig_csv_dir
        Settings.DATABASE_FILE = self._orig_db_file

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


if __name__ == "__main__":
    unittest.main()
