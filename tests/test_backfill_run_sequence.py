"""
Tests for scripts/backfill_run_sequence.py -- the one-time, manual
migration that assigns sequence_number to pre-existing run_summary rows
and seeds run_sequence -- see docs/architecture.md "Global Run Sequence".
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_run_sequence import backfill, main


def _make_legacy_db(path, run_ids):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE run_summary (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_id TEXT UNIQUE NOT NULL, sequence_number INTEGER, "
        "station_id TEXT, station_name TEXT, telemetry_db TEXT)"
    )
    for run_id in run_ids:
        conn.execute("INSERT INTO run_summary (run_id) VALUES (?)", (run_id,))
    conn.commit()
    conn.close()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.db_path = os.path.join(self.tmp_dir, "nipxi_index.db")

    def _rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT id, run_id, sequence_number FROM run_summary ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def _next_value(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT next_value FROM run_sequence WHERE id = 1").fetchone()[0]
        finally:
            conn.close()


class BackfillOrderingTests(_Base):
    def test_assigns_sequence_numbers_in_insertion_order(self):
        _make_legacy_db(self.db_path, ["A", "B", "C"])
        backfill(self.db_path)
        rows = self._rows()
        self.assertEqual([r[2] for r in rows], [1, 2, 3])
        self.assertEqual([r[1] for r in rows], ["A", "B", "C"])

    def test_seeds_run_sequence_correctly_after_backfill(self):
        _make_legacy_db(self.db_path, ["A", "B", "C"])
        backfill(self.db_path)
        self.assertEqual(self._next_value(), 4)

    def test_starts_after_the_highest_existing_sequence_number_if_any(self):
        _make_legacy_db(self.db_path, ["A", "B"])
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE run_summary SET sequence_number = 100 WHERE run_id = 'A'")
        conn.commit()
        conn.close()
        backfill(self.db_path)
        rows = {r[1]: r[2] for r in self._rows()}
        self.assertEqual(rows["A"], 100)  # untouched -- already had a value
        self.assertEqual(rows["B"], 101)
        self.assertEqual(self._next_value(), 102)  # next value still available to allocate


class BackfillIdempotencyTests(_Base):
    def test_running_twice_does_not_reassign_or_duplicate(self):
        _make_legacy_db(self.db_path, ["A", "B"])
        backfill(self.db_path)
        first_pass = self._rows()
        backfill(self.db_path)
        second_pass = self._rows()
        self.assertEqual(first_pass, second_pass)
        self.assertEqual(self._next_value(), 3)


class BackfillDryRunTests(_Base):
    def test_dry_run_makes_no_changes(self):
        _make_legacy_db(self.db_path, ["A", "B"])
        backfill(self.db_path, dry_run=True)
        rows = self._rows()
        self.assertEqual([r[2] for r in rows], [None, None])
        conn = sqlite3.connect(self.db_path)
        try:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='run_sequence'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(exists, "dry-run must not even create run_sequence")


class BackfillNothingPendingTests(_Base):
    def test_no_pending_rows_is_a_safe_no_op(self):
        _make_legacy_db(self.db_path, [])
        backfill(self.db_path)  # must not raise
        self.assertEqual(self._next_value(), 1)


class MainLegacyFileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_missing_index_and_missing_legacy_file_reports_nothing_to_do(self):
        argv = ["backfill_run_sequence.py", "--db", os.path.join(self.tmp_dir, "nipxi_index.db")]
        old_argv = sys.argv
        sys.argv = argv
        try:
            exit_code = main()
        finally:
            sys.argv = old_argv
        self.assertEqual(exit_code, 1)

    def test_legacy_file_without_migrate_flag_is_left_alone(self):
        index_path = os.path.join(self.tmp_dir, "nipxi_index.db")
        legacy_path = os.path.join(self.tmp_dir, "nipxi_dev.db")
        _make_legacy_db(legacy_path, ["A"])
        argv = ["backfill_run_sequence.py", "--db", index_path]
        old_argv, old_data_dir = sys.argv, None
        from config.settings import Settings
        old_data_dir = Settings.DATA_DIR
        Settings.DATA_DIR = self.tmp_dir
        sys.argv = argv
        try:
            exit_code = main()
        finally:
            sys.argv = old_argv
            Settings.DATA_DIR = old_data_dir
        self.assertEqual(exit_code, 1)
        self.assertFalse(os.path.exists(index_path), "must not rename without --migrate-legacy-file")
        self.assertTrue(os.path.exists(legacy_path))


if __name__ == "__main__":
    unittest.main()
