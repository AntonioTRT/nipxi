"""
Tests for test_control/storage_session.py -- the extraction of test.py's
_open_storage_guarded() into a shared, on_fail-injectable helper (see
docs/architecture.md "Preparation Phase: Six Resolved Decisions Before
worker_runtime.py").

Uses a real (temp-directory) DataStorage.open() to exercise the true
success/failure paths -- no mocking of sqlite3 internals, no hardware
access anywhere in this file.
"""

import os
import shutil
import tempfile
import unittest

from test_control.storage_session import open_storage_guarded


class _FakeSettings:
    def __init__(self, base_dir, database_file):
        self.DATA_DIR = os.path.join(base_dir, "data")
        self.CSV_DIR = os.path.join(base_dir, "csv")
        self.DATABASE_FILE = database_file


class _FakeHardwareManager:
    def __init__(self, raise_on_disconnect=False):
        self.disconnect_calls = 0
        self._raise_on_disconnect = raise_on_disconnect

    def disconnect_all(self):
        self.disconnect_calls += 1
        if self._raise_on_disconnect:
            raise RuntimeError("simulated shutdown failure, not a real hardware error")


class OpenStorageGuardedSuccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_returns_opened_storage_and_calls_no_on_fail(self):
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test.db"))
        calls = []
        storage = open_storage_guarded(settings, on_fail=calls.append)
        self.addCleanup(storage.close)
        self.assertIsNotNone(storage)
        self.assertEqual(calls, [])

    def test_works_with_on_fail_omitted(self):
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test2.db"))
        storage = open_storage_guarded(settings)
        self.addCleanup(storage.close)
        self.assertIsNotNone(storage)


class OpenStorageGuardedFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        # A directory path where sqlite3 expects a file -- sqlite3.connect()
        # reliably raises sqlite3.OperationalError ("unable to open database
        # file") on this, without needing to mock sqlite3 internals.
        self.bad_db_path = os.path.join(self.tmp_dir, "this_is_a_directory")
        os.makedirs(self.bad_db_path)

    def test_returns_none_and_reports_failure(self):
        settings = _FakeSettings(self.tmp_dir, self.bad_db_path)
        calls = []
        result = open_storage_guarded(settings, on_fail=calls.append)
        self.assertIsNone(result)
        self.assertTrue(any("[FAIL]" in c and "Database unavailable" in c for c in calls))
        self.assertTrue(any("logs for full diagnostic detail" in c for c in calls))

    def test_on_fail_omitted_does_not_raise(self):
        settings = _FakeSettings(self.tmp_dir, self.bad_db_path)
        result = open_storage_guarded(settings)  # must not raise
        self.assertIsNone(result)

    def test_disconnects_hardware_manager_on_failure(self):
        settings = _FakeSettings(self.tmp_dir, self.bad_db_path)
        hw_mgr = _FakeHardwareManager()
        result = open_storage_guarded(settings, hw_mgr=hw_mgr, on_fail=lambda _msg: None)
        self.assertIsNone(result)
        self.assertEqual(hw_mgr.disconnect_calls, 1)

    def test_no_hardware_manager_means_no_disconnect_attempt(self):
        settings = _FakeSettings(self.tmp_dir, self.bad_db_path)
        # Must not raise even though there's nothing to disconnect.
        result = open_storage_guarded(settings, hw_mgr=None, on_fail=lambda _msg: None)
        self.assertIsNone(result)

    def test_hardware_manager_disconnect_failure_is_reported_not_raised(self):
        settings = _FakeSettings(self.tmp_dir, self.bad_db_path)
        hw_mgr = _FakeHardwareManager(raise_on_disconnect=True)
        calls = []
        result = open_storage_guarded(settings, hw_mgr=hw_mgr, on_fail=calls.append)
        self.assertIsNone(result)
        self.assertTrue(any("[CRITICAL]" in c and "Hardware shutdown failed" in c for c in calls))
        self.assertTrue(any("physically disconnect power" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
