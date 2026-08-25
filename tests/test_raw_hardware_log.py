"""
Tests for data/raw_hardware_log.py -- the independent SQLite-backed
writer for the Hardware Audit Trail (see docs/architecture.md "Hardware
Audit Trail"). Uses a real, temp-directory SQLite database (mirrors
tests/test_storage_measurement_scoping.py's established convention) --
no mocking of sqlite3 internals for the success-path tests; the
resilience tests point DATABASE_FILE at a path sqlite3 reliably cannot
open (a directory), matching tests/test_storage_session.py's own
technique for the identical purpose.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from data.raw_hardware_log import RawHardwareLogWriter, get_session_id


class _TempSettings:
    def __init__(self, base_dir, database_file):
        self.DATA_DIR = base_dir
        self.DATABASE_FILE = database_file


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.db_path = os.path.join(self.tmp_dir, "raw_hw_log_test.db")
        self.settings = _TempSettings(self.tmp_dir, self.db_path)
        self.writer = RawHardwareLogWriter(self.settings)
        self.addCleanup(self.writer.close)

    def _rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT run_id, session_id, position, device_type, device_name, resource, "
                "command, command_parameters, response, success, duration_ms, error_type, "
                "error_message, additional_metadata FROM raw_hardware_log ORDER BY id"
            )
            return cur.fetchall()
        finally:
            conn.close()


class SuccessfulWriteTests(_Base):
    def test_a_successful_call_is_persisted_with_every_field(self):
        self.writer.log(
            run_id="run1", position=3, device_type="SMU", device_name="SMU_PXI1Slot7",
            resource="PXI1Slot7", command="output_enable", command_parameters={"args": (), "kwargs": {}},
            response=True, success=True, duration_ms=1.23, error_type=None, error_message=None,
        )
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[0], "run1")
        self.assertIsNotNone(row[1])  # session_id always populated
        self.assertEqual(row[2], 3)
        self.assertEqual(row[3], "SMU")
        self.assertEqual(row[4], "SMU_PXI1Slot7")
        self.assertEqual(row[5], "PXI1Slot7")
        self.assertEqual(row[6], "output_enable")
        self.assertEqual(row[9], 1)  # success stored as 1/0
        self.assertIsNone(row[11])  # error_type NULL on success
        self.assertIsNone(row[12])  # error_message NULL on success

    def test_table_and_indexes_are_created_lazily_on_first_write(self):
        self.assertFalse(os.path.exists(self.db_path))
        self.writer.log(
            run_id=None, position=None, device_type="DAQ", device_name="DAQ1", resource=None,
            command="read_channel", command_parameters=None, response=1.5, success=True,
            duration_ms=0.5, error_type=None, error_message=None,
        )
        self.assertTrue(os.path.exists(self.db_path))
        conn = sqlite3.connect(self.db_path)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertIn("raw_hardware_log", names)
            index_names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.assertTrue(any("raw_hw_log" in n for n in index_names))
        finally:
            conn.close()

    def test_run_id_none_is_stored_as_null_not_a_string(self):
        # The pre-run-id startup/shutdown case -- see attach_run_id_provider().
        self.writer.log(
            run_id=None, position=None, device_type="RELAY", device_name="RELAY1", resource=None,
            command="open_all", command_parameters=None, response=None, success=True,
            duration_ms=2.0, error_type=None, error_message=None,
        )
        self.assertIsNone(self._rows()[0][0])


class FailureWriteTests(_Base):
    def test_a_failure_is_persisted_with_error_type_and_message(self):
        self.writer.log(
            run_id="run1", position=1, device_type="RELAY", device_name="RELAY1", resource=None,
            command="close", command_parameters={"args": (1,), "kwargs": {}}, response=None,
            success=False, duration_ms=5.0, error_type="RelayError", error_message="comms timeout",
        )
        row = self._rows()[0]
        self.assertEqual(row[9], 0)
        self.assertEqual(row[11], "RelayError")
        self.assertEqual(row[12], "comms timeout")


class LargeValueTruncationTests(_Base):
    def test_oversized_command_parameters_are_truncated_not_dropped(self):
        huge = {"args": ("x" * 5000,), "kwargs": {}}
        self.writer.log(
            run_id="run1", position=None, device_type="DAQ", device_name="DAQ1", resource=None,
            command="read_channel", command_parameters=huge, response=None, success=True,
            duration_ms=0.1, error_type=None, error_message=None,
        )
        stored = self._rows()[0][7]
        self.assertLess(len(stored), 5000)
        self.assertTrue(stored.endswith("...<truncated>"))


class DatabaseFailureResilienceTests(unittest.TestCase):
    """
    Critical requirement: audit logging failures must NEVER raise or
    interrupt the caller. Uses a directory in place of the database file
    -- sqlite3.connect() reliably raises OperationalError on this without
    mocking sqlite3 internals (same technique as
    tests/test_storage_session.py::OpenStorageGuardedFailureTests).
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.bad_db_path = os.path.join(self.tmp_dir, "this_is_a_directory")
        os.makedirs(self.bad_db_path)
        self.settings = _TempSettings(self.tmp_dir, self.bad_db_path)
        self.writer = RawHardwareLogWriter(self.settings)

    def test_log_does_not_raise_when_the_database_cannot_be_opened(self):
        try:
            self.writer.log(
                run_id="run1", position=1, device_type="SMU", device_name="SMU1", resource=None,
                command="output_enable", command_parameters=None, response=None, success=True,
                duration_ms=1.0, error_type=None, error_message=None,
            )
        except Exception as e:  # pragma: no cover -- the test itself fails if this triggers
            self.fail(f"RawHardwareLogWriter.log() raised: {e}")

    def test_repeated_calls_after_a_connect_failure_do_not_raise_either(self):
        for _ in range(3):
            self.writer.log(
                run_id="run1", position=None, device_type="DAQ", device_name="DAQ1", resource=None,
                command="read_channel", command_parameters=None, response=None, success=True,
                duration_ms=1.0, error_type=None, error_message=None,
            )


class SessionIdTests(unittest.TestCase):
    def test_session_id_is_stable_across_multiple_writer_instances(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            settings_a = _TempSettings(tmp_dir, os.path.join(tmp_dir, "a.db"))
            settings_b = _TempSettings(tmp_dir, os.path.join(tmp_dir, "b.db"))
            writer_a = RawHardwareLogWriter(settings_a)
            writer_b = RawHardwareLogWriter(settings_b)
            self.assertEqual(writer_a.session_id, writer_b.session_id)
            self.assertEqual(writer_a.session_id, get_session_id())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
