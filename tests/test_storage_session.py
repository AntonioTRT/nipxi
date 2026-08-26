"""
Tests for test_control/storage_session.py -- the extraction of test.py's
_open_storage_guarded(), _hardware_snapshot_fields(), and
_start_run_summary_guarded() into shared, on_fail-injectable helpers
(see docs/architecture.md "Preparation Phase: Six Resolved Decisions
Before worker_runtime.py" and "Remaining Helper Extraction Before
worker_runtime.py").

Uses a real (temp-directory) DataStorage.open() to exercise the true
open_storage_guarded() success/failure paths -- no mocking of sqlite3
internals there. hardware_snapshot_fields()/start_run_summary_guarded()
are tested with plain dicts/fakes since they don't need a real database.
No hardware access anywhere in this file.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from test_control.storage_session import (
    hardware_snapshot_fields,
    open_storage_guarded,
    start_run_summary_guarded,
)


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


class _FakeHardwareManagerWithAuditWiring(_FakeHardwareManager):
    """Mirrors the real HardwareManager's Hardware Audit Trail surface
    (see test_control/hardware_manager.py) without constructing any real
    hardware -- used to verify open_storage_guarded() wires the run_id
    provider through on success, without needing a real HardwareManager."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attached_providers = []

    def attach_run_id_provider(self, provider):
        self.attached_providers.append(provider)


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

    def test_hw_mgr_without_attach_run_id_provider_is_not_an_error(self):
        # Backward compatibility: a hw_mgr-like object that predates the
        # Hardware Audit Trail (e.g. _FakeHardwareManager, used by every
        # other test in this file) must not break a successful open.
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test3.db"))
        hw_mgr = _FakeHardwareManager()
        storage = open_storage_guarded(settings, hw_mgr=hw_mgr)
        self.addCleanup(storage.close)
        self.assertIsNotNone(storage)

    def test_run_id_provider_is_attached_to_hw_mgr_on_success(self):
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test4.db"))
        hw_mgr = _FakeHardwareManagerWithAuditWiring()
        storage = open_storage_guarded(settings, hw_mgr=hw_mgr)
        self.addCleanup(storage.close)
        self.assertEqual(len(hw_mgr.attached_providers), 1)
        self.assertEqual(hw_mgr.attached_providers[0](), storage.run_id)

    def test_attached_provider_reflects_group_all_run_id_changes(self):
        # Group -> ALL reassigns storage.run_id per position via
        # begin_new_run_id() on the SAME DataStorage instance -- the
        # attached provider must reflect that live, not a value captured
        # once at attach time.
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test5.db"))
        hw_mgr = _FakeHardwareManagerWithAuditWiring()
        storage = open_storage_guarded(settings, hw_mgr=hw_mgr)
        self.addCleanup(storage.close)
        provider = hw_mgr.attached_providers[0]
        first_run_id = provider()
        storage.begin_new_run_id()
        second_run_id = provider()
        self.assertNotEqual(first_run_id, second_run_id)
        self.assertEqual(second_run_id, storage.run_id)

    def test_no_hw_mgr_means_no_provider_attachment_attempted(self):
        settings = _FakeSettings(self.tmp_dir, os.path.join(self.tmp_dir, "nipxi_test6.db"))
        storage = open_storage_guarded(settings, hw_mgr=None)  # must not raise
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


class _FakeStorageForRunSummary:
    """Records start_run_summary() calls; raises sqlite3.Error when
    configured to, to exercise the guarded failure path without a real
    database."""

    def __init__(self, raise_error=False):
        self.calls = []
        self._raise_error = raise_error

    def start_run_summary(self, test_type, **fields):
        self.calls.append((test_type, fields))
        if self._raise_error:
            raise sqlite3.OperationalError("simulated database unavailability")


class HardwareSnapshotFieldsTests(unittest.TestCase):
    _RELAY_CFG_ETHERNET = {
        "name": "MATRIX_NUMATO_202", "driver": "RELAY32ETHRL00",
        "type": "ethernet", "ip": "169.254.1.202", "port": 23,
    }

    def test_full_snapshot_with_dmm(self):
        fields = hardware_snapshot_fields(
            "AUX_SMU_1", {"resource": "PXI1Slot7", "model": "PXI-4130"},
            "MAIN_DMM", {"resource": "PXI1Slot3", "model": "PXI-4065"},
            "MAIN_DAQ", {"resource": "PXI1Slot2", "model": "PXIe-6363"},
            self._RELAY_CFG_ETHERNET,
        )
        self.assertEqual(fields["smu_name"], "AUX_SMU_1")
        self.assertEqual(fields["smu_resource"], "PXI1Slot7")
        self.assertEqual(fields["daq_model"], "PXIe-6363")
        self.assertEqual(fields["relay_matrix_resource"], "169.254.1.202:23")
        self.assertEqual(fields["dmm_name"], "MAIN_DMM")

    def test_dmm_none_omits_dmm_fields_entirely(self):
        fields = hardware_snapshot_fields(
            "AUX_SMU_1", {"resource": "PXI1Slot7", "model": "PXI-4130"},
            None, None,
            "MAIN_DAQ", {"resource": "PXI1Slot2", "model": "PXIe-6363"},
            self._RELAY_CFG_ETHERNET,
        )
        self.assertNotIn("dmm_name", fields)
        self.assertNotIn("dmm_resource", fields)
        self.assertNotIn("dmm_model", fields)

    def test_non_ethernet_relay_uses_port_only_as_resource(self):
        relay_cfg = {"name": "RELAY_SERIAL", "driver": "SOME_DRIVER", "type": "serial", "port": "COM3"}
        fields = hardware_snapshot_fields(
            "AUX_SMU_1", {"resource": "PXI1Slot7", "model": "PXI-4130"},
            None, None, "MAIN_DAQ", {"resource": "PXI1Slot2", "model": "PXIe-6363"},
            relay_cfg,
        )
        self.assertEqual(fields["relay_matrix_resource"], "COM3")


class StartRunSummaryGuardedTests(unittest.TestCase):
    def test_success_returns_true_and_calls_no_on_fail(self):
        storage = _FakeStorageForRunSummary()
        calls = []
        result = start_run_summary_guarded(storage, "charge_battery", on_fail=calls.append,
                                            battery_type="HUB")
        self.assertTrue(result)
        self.assertEqual(calls, [])
        self.assertEqual(storage.calls, [("charge_battery", {"battery_type": "HUB"})])

    def test_failure_returns_false_and_reports_via_on_fail(self):
        storage = _FakeStorageForRunSummary(raise_error=True)
        calls = []
        result = start_run_summary_guarded(storage, "charge_battery", on_fail=calls.append)
        self.assertFalse(result)
        self.assertTrue(any("[FAIL]" in c and "could not start run_summary" in c for c in calls))
        self.assertTrue(any("logs for full diagnostic detail" in c for c in calls))

    def test_on_fail_omitted_does_not_raise_on_failure(self):
        storage = _FakeStorageForRunSummary(raise_error=True)
        result = start_run_summary_guarded(storage, "charge_battery")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
