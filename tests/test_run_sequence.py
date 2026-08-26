"""
Tests for Phase A Items 2-3 (run_sequence allocator, run_summary
extension) and Phase B (telemetry/index database split, telemetry_db
column) -- see docs/architecture.md "Global Run Sequence" / "Telemetry /
Index Database Split" / "telemetry_db Lookup Strategy".

Uses a real, temp-directory DataStorage -- no mocking of sqlite3
internals -- mirroring tests/test_storage_measurement_scoping.py's
established convention.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

import config.devices as dev_cfg
from data.rotation import index_database_file, telemetry_database_file
from data.storage import DataStorage


class _TempSettings:
    def __init__(self, base_dir):
        self.DATA_DIR = base_dir
        self.CSV_DIR = os.path.join(base_dir, "csv")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.settings = _TempSettings(self.tmp_dir)


class SequenceAllocationTests(_Base):
    def test_first_allocation_is_one(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            self.assertEqual(storage._sequence_number, 1)
        finally:
            storage.close()

    def test_sequence_never_reuses_a_number_within_one_process(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            first = storage._sequence_number
            storage.begin_new_run_id()
            second = storage._sequence_number
            storage.begin_new_run_id()
            third = storage._sequence_number
            self.assertEqual([first, second, third], [1, 2, 3])
        finally:
            storage.close()

    def test_run_id_format_is_station_prefixed_with_no_timestamp(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            station_id = dev_cfg.STATION_INFO["station_id"]
            self.assertEqual(storage.run_id, f"{station_id}-00000001")
        finally:
            storage.close()

    def test_run_id_is_unique_across_group_all_style_reallocation(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            seen = {storage.run_id}
            for _ in range(5):
                storage.begin_new_run_id()
                self.assertNotIn(storage.run_id, seen)
                seen.add(storage.run_id)
        finally:
            storage.close()


class SequencePersistenceTests(_Base):
    """Survives restart (a new DataStorage/process) and monthly rotation
    (a new telemetry file) -- run_sequence lives only in the index db."""

    def test_sequence_survives_a_new_datastorage_instance_same_settings(self):
        first = DataStorage(settings=self.settings)
        first.open()
        try:
            self.assertEqual(first._sequence_number, 1)
        finally:
            first.close()

        second = DataStorage(settings=self.settings)
        second.open()
        try:
            self.assertEqual(second._sequence_number, 2)
        finally:
            second.close()

    def test_sequence_committed_directly_to_the_index_database(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        storage.close()

        conn = sqlite3.connect(index_database_file(self.settings))
        try:
            next_value = conn.execute("SELECT next_value FROM run_sequence WHERE id = 1").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(next_value, 2)  # one allocation happened (id=1), next is 2

    def test_sequence_survives_a_simulated_monthly_rotation(self):
        # Open once against "January" telemetry (real Settings.DATABASE_FILE
        # override simulates a specific month without needing to mock
        # datetime -- see tests/test_rotation.py for the real month-
        # resolution behavior).
        self.settings.DATABASE_FILE = os.path.join(self.tmp_dir, "nipxi_2026_01.db")
        first = DataStorage(settings=self.settings)
        first.open()
        try:
            self.assertEqual(first._sequence_number, 1)
        finally:
            first.close()

        # "Rotate" -- point telemetry at a different (February) file. The
        # index database (and therefore run_sequence) is untouched by this.
        self.settings.DATABASE_FILE = os.path.join(self.tmp_dir, "nipxi_2026_02.db")
        second = DataStorage(settings=self.settings)
        second.open()
        try:
            self.assertEqual(second._sequence_number, 2)
            self.assertEqual(second._telemetry_db_name, "nipxi_2026_02.db")
        finally:
            second.close()


class RunSummaryExtensionTests(_Base):
    def test_run_summary_row_carries_sequence_and_station_identity(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.start_run_summary(test_type="charge")
            row = storage.get_run_summary(storage.run_id)
            self.assertEqual(row["sequence_number"], 1)
            self.assertEqual(row["station_id"], dev_cfg.STATION_INFO["station_id"])
            self.assertEqual(row["station_name"], dev_cfg.STATION_INFO["station_name"])
        finally:
            storage.close()

    def test_telemetry_db_column_records_the_exact_filename_used(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.start_run_summary(test_type="charge")
            row = storage.get_run_summary(storage.run_id)
            expected_name = os.path.basename(telemetry_database_file(self.settings))
            self.assertEqual(row["telemetry_db"], expected_name)
        finally:
            storage.close()

    def test_caller_cannot_override_identity_fields_via_fields_kwargs(self):
        # sequence_number/station_id/station_name/telemetry_db are
        # DataStorage-owned facts -- never caller-suppliable.
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.start_run_summary(
                test_type="charge", sequence_number=999999, station_id="NOT_REAL",
            )
            row = storage.get_run_summary(storage.run_id)
            self.assertEqual(row["sequence_number"], 1)
            self.assertEqual(row["station_id"], dev_cfg.STATION_INFO["station_id"])
        finally:
            storage.close()

    def test_run_summary_migration_adds_new_columns_to_a_legacy_index_database(self):
        # A pre-existing index database (or the old combined database, in
        # its role as the future index database) whose run_summary table
        # predates sequence_number/station_id/station_name/telemetry_db/
        # parent_run_id. Realistic pre-Phase-A schema: every column
        # run_summary already had (see data/storage.py's own pre-this-
        # change shape), just missing those 5 new ones -- NOT an
        # artificially bare table, which would misrepresent what a real
        # legacy database actually looks like (every one of these columns
        # has existed since Milestone II, except parent_run_id which
        # predates CycleSequence).
        index_path = index_database_file(self.settings)
        os.makedirs(self.settings.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(index_path)
        conn.execute(
            "CREATE TABLE run_summary ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE NOT NULL,"
            "    test_type TEXT, start_time TEXT, end_time TEXT, duration_s REAL,"
            "    stop_reason TEXT, result TEXT, battery_type TEXT,"
            "    battery_voltage_max_v REAL, battery_voltage_min_v REAL,"
            "    battery_charge_current_limit_a REAL, battery_discharge_current_limit_a REAL,"
            "    capacity_ah REAL, energy_wh REAL, cycle_count INTEGER,"
            "    start_voltage REAL, end_voltage REAL, min_voltage REAL, max_voltage REAL,"
            "    average_voltage REAL, sample_count INTEGER,"
            "    smu_name TEXT, smu_resource TEXT, smu_model TEXT,"
            "    dmm_name TEXT, dmm_resource TEXT, dmm_model TEXT,"
            "    daq_name TEXT, daq_resource TEXT, daq_model TEXT,"
            "    relay_matrix_name TEXT, relay_matrix_resource TEXT, relay_matrix_model TEXT,"
            "    group_name TEXT, position_in_group INTEGER, analysis_result TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO run_summary (run_id, test_type, start_time) VALUES (?, ?, ?)",
            ("LEGACY-0001", "charge", "2026-01-15T10:00:00"),
        )
        conn.commit()
        conn.close()

        storage = DataStorage(settings=self.settings)
        storage.open()  # must not raise, must not drop the legacy row
        try:
            row = storage.get_run_summary("LEGACY-0001")
            self.assertIsNotNone(row, "pre-existing legacy row must survive migration")
            self.assertEqual(row["test_type"], "charge")
            self.assertIsNone(row["sequence_number"], "legacy row has no historical sequence_number to backfill")
            self.assertIsNone(row["station_id"])
            self.assertIsNone(row["telemetry_db"])
            self.assertIsNone(row["parent_run_id"], "legacy row predates CycleSequence -- no parent to backfill")
        finally:
            storage.close()


class DualDatabaseRoutingTests(_Base):
    """External callers must remain unchanged -- routing is entirely
    internal to DataStorage (see docs/architecture.md "Telemetry / Index
    Database Split")."""

    def test_index_and_telemetry_are_different_files(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            self.assertNotEqual(index_database_file(self.settings), telemetry_database_file(self.settings))
        finally:
            storage.close()

    def test_run_summary_and_station_state_land_in_the_index_file(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.start_run_summary(test_type="charge")
            storage.record_execution_state(relay=1, state="ACTIVE")
        finally:
            storage.close()

        conn = sqlite3.connect(index_database_file(self.settings))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_summary").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM station_state").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_sequence").fetchone()[0], 1)
        finally:
            conn.close()

    def test_measurements_and_event_log_land_in_the_telemetry_file_not_the_index_file(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.record_measurement(test_type="charge", channel=1, voltage_v=3.8)
            storage.log_event(level="INFO", source="test", message="hello")
        finally:
            storage.close()

        telemetry_conn = sqlite3.connect(telemetry_database_file(self.settings))
        try:
            self.assertEqual(telemetry_conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 1)
            self.assertEqual(telemetry_conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], 1)
        finally:
            telemetry_conn.close()

        index_conn = sqlite3.connect(index_database_file(self.settings))
        try:
            tables = {r[0] for r in index_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            index_conn.close()
        self.assertNotIn("measurements", tables)
        self.assertNotIn("event_log", tables)

    def test_public_read_write_methods_are_unchanged_no_call_site_refactoring_needed(self):
        # Every one of these is the exact same call shape every existing
        # caller across the codebase already uses -- proof the split is
        # entirely internal.
        storage = DataStorage(settings=self.settings)
        storage.open()
        try:
            storage.record(channel=1, sample={"voltage_v": 3.7})
            storage.record_measurement(test_type="charge", channel=1)
            storage.log_event(level="INFO", source="test", message="m")
            storage.record_execution_state(relay=1, state="ACTIVE")
            storage.start_run_summary(test_type="charge")
            storage.finish_run_summary(stop_reason="COMPLETED", result="PASS")
            self.assertIsNotNone(storage.get_run_summary(storage.run_id))
            self.assertIsNotNone(storage.get_last_execution_state())
            self.assertTrue(storage.get_measurements(run_id=storage.run_id))
        finally:
            storage.close()


if __name__ == "__main__":
    unittest.main()
