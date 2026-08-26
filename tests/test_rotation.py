"""
Tests for Phase C -- data/rotation.py (path resolution + rotation-gate
decision logic) and its integration at test.py's post-workflow idle
checkpoint. See docs/architecture.md "Monthly Telemetry Rotation".
"""

import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from data.rotation import index_database_file, should_rotate, telemetry_database_file
from data.storage import DataStorage


class _TempSettings:
    def __init__(self, base_dir):
        self.DATA_DIR = base_dir
        self.CSV_DIR = os.path.join(base_dir, "csv")


class TelemetryDatabaseFileTests(unittest.TestCase):
    def test_computes_monthly_filename_from_data_dir(self):
        settings = _TempSettings(os.path.join("some", "dir"))
        path = telemetry_database_file(settings, dt=datetime(2026, 1, 31))
        self.assertEqual(os.path.basename(path), "nipxi_2026_01.db")

    def test_different_months_get_different_filenames(self):
        settings = _TempSettings(os.path.join("some", "dir"))
        jan = telemetry_database_file(settings, dt=datetime(2026, 1, 31, 23, 59, 59))
        feb = telemetry_database_file(settings, dt=datetime(2026, 2, 1, 0, 0, 1))
        self.assertNotEqual(jan, feb)
        self.assertEqual(os.path.basename(feb), "nipxi_2026_02.db")

    def test_explicit_database_file_override_wins(self):
        settings = _TempSettings(os.path.join("some", "dir"))
        settings.DATABASE_FILE = os.path.join("custom", "path.db")
        self.assertEqual(
            telemetry_database_file(settings, dt=datetime(2026, 1, 1)),
            os.path.join("custom", "path.db"),
        )


class IndexDatabaseFileTests(unittest.TestCase):
    def test_computed_live_from_data_dir(self):
        settings = _TempSettings(os.path.join("some", "dir"))
        self.assertEqual(index_database_file(settings), os.path.join("some", "dir", "nipxi_index.db"))

    def test_explicit_override_wins(self):
        settings = _TempSettings(os.path.join("some", "dir"))
        settings.INDEX_DATABASE_FILE = os.path.join("custom", "index.db")
        self.assertEqual(index_database_file(settings), os.path.join("custom", "index.db"))


class ShouldRotateDecisionTests(unittest.TestCase):
    def test_same_month_never_rotates_regardless_of_other_flags(self):
        self.assertFalse(should_rotate(
            current_telemetry_month="2026_01", now_month="2026_01",
            group_finished=True, sequence_running=False, scheduler_idle=True,
        ))

    def test_different_month_and_all_conditions_satisfied_rotates(self):
        self.assertTrue(should_rotate(
            current_telemetry_month="2026_01", now_month="2026_02",
            group_finished=True, sequence_running=False, scheduler_idle=True,
        ))

    def test_group_not_finished_blocks_rotation(self):
        self.assertFalse(should_rotate(
            current_telemetry_month="2026_01", now_month="2026_02",
            group_finished=False, sequence_running=False, scheduler_idle=True,
        ))

    def test_sequence_still_running_blocks_rotation(self):
        self.assertFalse(should_rotate(
            current_telemetry_month="2026_01", now_month="2026_02",
            group_finished=True, sequence_running=True, scheduler_idle=True,
        ))

    def test_scheduler_not_idle_blocks_rotation(self):
        self.assertFalse(should_rotate(
            current_telemetry_month="2026_01", now_month="2026_02",
            group_finished=True, sequence_running=False, scheduler_idle=False,
        ))


class MonthBoundaryGroupTests(unittest.TestCase):
    """Never split a group across databases -- see docs/architecture.md
    "Monthly Telemetry Rotation" / "Group starts Jan 31, finishes Feb 1"."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.settings = _TempSettings(self.tmp_dir)

    def test_a_run_opened_in_january_keeps_writing_to_january_after_the_clock_crosses_into_february(self):
        with mock.patch("data.rotation.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 31, 23, 0, 0)
            storage = DataStorage(settings=self.settings)
            storage.open()
            try:
                self.assertEqual(storage._telemetry_db_name, "nipxi_2026_01.db")

                # Clock crosses into February WHILE this instance stays
                # open, simulating a group that starts Jan 31 and finishes
                # Feb 1 -- nothing about this instance's own telemetry
                # connection re-resolves.
                mock_dt.now.return_value = datetime(2026, 2, 1, 0, 30, 0)
                storage.log_event(level="INFO", source="test", message="after boundary")
                storage.record_measurement(test_type="charge", channel=1, voltage_v=3.8)

                self.assertEqual(storage._telemetry_db_name, "nipxi_2026_01.db")
            finally:
                storage.close()

        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "nipxi_2026_02.db")),
                          "no February file should ever have been created by this run")
        conn = sqlite3.connect(os.path.join(self.tmp_dir, "nipxi_2026_01.db"))
        try:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE message = 'after boundary'"
            ).fetchone()[0]
            measurement_count = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(event_count, 1, "the post-boundary event must still be in the January file")
        self.assertEqual(measurement_count, 1, "the post-boundary measurement must still be in the January file")

    def test_a_new_datastorage_opened_after_the_boundary_uses_the_new_month(self):
        with mock.patch("data.rotation.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 1, 0, 30, 0)
            storage = DataStorage(settings=self.settings)
            storage.open()
            try:
                self.assertEqual(storage._telemetry_db_name, "nipxi_2026_02.db")
            finally:
                storage.close()


class DispatchRotationCheckpointTests(unittest.TestCase):
    """test.py::_check_telemetry_rotation() -- the current idle checkpoint
    integration (called from _dispatch_menu_choice(), right after a
    workflow completes and before returning to the Main Menu)."""

    def setUp(self):
        self._orig = test_module._LAST_TELEMETRY_MONTH
        self.addCleanup(self._restore)

    def _restore(self):
        test_module._LAST_TELEMETRY_MONTH = self._orig

    def test_first_call_seeds_without_reporting_a_rotation(self):
        test_module._LAST_TELEMETRY_MONTH = None
        buf = io.StringIO()
        with redirect_stdout(buf):
            test_module._check_telemetry_rotation()
        self.assertNotIn("rotated", buf.getvalue())
        self.assertIsNotNone(test_module._LAST_TELEMETRY_MONTH)

    def test_detects_and_logs_a_month_change_since_the_last_check(self):
        test_module._LAST_TELEMETRY_MONTH = "2020_01"  # guaranteed to differ from "now"
        buf = io.StringIO()
        with redirect_stdout(buf):
            test_module._check_telemetry_rotation()
        self.assertIn("Telemetry database rotated: 2020_01 ->", buf.getvalue())
        self.assertNotEqual(test_module._LAST_TELEMETRY_MONTH, "2020_01")

    def test_same_month_as_last_check_reports_nothing(self):
        current_month = datetime.now().strftime("%Y_%m")
        test_module._LAST_TELEMETRY_MONTH = current_month
        buf = io.StringIO()
        with redirect_stdout(buf):
            test_module._check_telemetry_rotation()
        self.assertNotIn("rotated", buf.getvalue())
        self.assertEqual(test_module._LAST_TELEMETRY_MONTH, current_month)

    def test_dispatch_menu_choice_calls_the_rotation_check(self):
        # Source-presence regression -- mirrors the established convention
        # for hard-to-harness dispatch wiring (see
        # tests/test_post_isolation_zeroing_ordering.py).
        import inspect
        src = inspect.getsource(test_module._dispatch_menu_choice)
        rotation_idx = src.find("_check_telemetry_rotation()")
        pause_idx = src.find("_pause_before_main_menu()")
        self.assertGreater(rotation_idx, -1)
        self.assertGreater(pause_idx, -1)
        self.assertLess(rotation_idx, pause_idx,
                         "rotation must be checked before pausing/returning to the Main Menu")


if __name__ == "__main__":
    unittest.main()
