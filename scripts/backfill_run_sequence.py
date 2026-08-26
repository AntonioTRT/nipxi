"""
One-time backfill: assign a global sequence_number to every pre-existing
run_summary row, and seed run_sequence correctly -- see docs/architecture.md
"Global Run Sequence" and the Phase A/B/C implementation plan.

This is a MANUAL script, deliberately never run automatically at startup
(DataStorage.open() only creates run_sequence and seeds it to 1 on a
brand-new database -- it never backfills existing rows). Run it once,
after upgrading to the sequence_number/station_id/station_name/
telemetry_db columns, before the first new run under the new schema.

Idempotent: rows that already have a non-NULL sequence_number are left
untouched, and re-running after a partial/interrupted run simply resumes
-- safe to run more than once.

Usage:
    python scripts/backfill_run_sequence.py [--db PATH] [--migrate-legacy-file] [--dry-run]

By default, targets data/rotation.py::index_database_file(Settings) -- the
real, mode-specific index database. If that file does not exist yet but a
pre-split legacy database does (the single combined nipxi_dev.db/
nipxi_validation.db/nipxi.db this project used before the telemetry/index
split), pass --migrate-legacy-file to rename it into place first (its
run_summary/station_state rows become the new index database's starting
history -- no row data is copied/transformed, only the file itself is
renamed). Without that flag, a missing legacy file is left alone and the
script prints instructions instead of guessing.

station_id/station_name/telemetry_db are intentionally left NULL for
backfilled rows -- that data does not exist for runs recorded before this
feature existed, and is not fabricated here.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from data.rotation import index_database_file

# The per-mode legacy filename this project used before the telemetry/
# index split (see config/settings.py's removed _MODE_DB_NAME) -- kept
# here, standalone, purely so this one-time script can locate it; nothing
# else in the codebase needs this mapping anymore.
_LEGACY_MODE_DB_NAME = {
    "DEVELOPMENT": "nipxi_dev.db",
    "VALIDATION":  "nipxi_validation.db",
    "PRODUCTION":  "nipxi.db",
}


def _legacy_db_path() -> str:
    name = _LEGACY_MODE_DB_NAME.get(Settings.SYSTEM_MODE, "nipxi_dev.db")
    return os.path.join(Settings.DATA_DIR, name)


def backfill(db_path: str, dry_run: bool = False) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id FROM run_summary WHERE sequence_number IS NULL ORDER BY id ASC"
        )
        pending_ids = [row[0] for row in cur.fetchall()]

        row = conn.execute("SELECT MAX(sequence_number) FROM run_summary").fetchone()
        next_value = (row[0] or 0) + 1

        print(f"Database: {db_path}")
        print(f"Rows needing a sequence_number: {len(pending_ids)}")
        print(f"Starting sequence_number: {next_value}")

        if not pending_ids:
            print("Nothing to backfill.")
        elif dry_run:
            print(f"[dry-run] Would assign sequence_number {next_value}..{next_value + len(pending_ids) - 1} "
                  f"to run_summary ids {pending_ids[0]}..{pending_ids[-1]} (insertion order).")
        else:
            for run_summary_id in pending_ids:
                conn.execute(
                    "UPDATE run_summary SET sequence_number = ? WHERE id = ?",
                    (next_value, run_summary_id),
                )
                next_value += 1
            conn.commit()
            print(f"Backfilled {len(pending_ids)} row(s).")

        final_next_value = (conn.execute(
            "SELECT MAX(sequence_number) FROM run_summary"
        ).fetchone()[0] or 0) + 1 if not dry_run else next_value

        if dry_run:
            print(f"[dry-run] Would seed run_sequence.next_value = {final_next_value}.")
        else:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_sequence ("
                "    id         INTEGER PRIMARY KEY CHECK (id = 1),"
                "    next_value INTEGER NOT NULL"
                ")"
            )
            conn.execute(
                "INSERT INTO run_sequence (id, next_value) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET next_value = "
                "    CASE WHEN excluded.next_value > run_sequence.next_value "
                "         THEN excluded.next_value ELSE run_sequence.next_value END",
                (final_next_value,),
            )
            conn.commit()
            print(f"run_sequence.next_value is now {final_next_value}.")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="Index database path (default: the real, mode-specific index database)")
    parser.add_argument("--migrate-legacy-file", action="store_true",
                         help="Rename the pre-split legacy database into place as the index database, if needed")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    args = parser.parse_args()

    db_path = args.db or index_database_file(Settings)

    if not os.path.exists(db_path):
        legacy_path = _legacy_db_path()
        if not os.path.exists(legacy_path):
            print(f"Neither the index database ({db_path}) nor a legacy database "
                  f"({legacy_path}) exists -- nothing to backfill.")
            return 1
        if not args.migrate_legacy_file:
            print(f"Index database not found at {db_path}.")
            print(f"Found the pre-split legacy database at {legacy_path}.")
            print("Re-run with --migrate-legacy-file to rename it into place as the "
                  "new index database (its run_summary/station_state history becomes "
                  "this rack's starting history -- no row data is copied/transformed, "
                  "only the file itself is renamed), or move/rename it manually first.")
            return 1
        if args.dry_run:
            print(f"[dry-run] Would rename {legacy_path} -> {db_path}")
        else:
            os.rename(legacy_path, db_path)
            print(f"Renamed {legacy_path} -> {db_path}")

    backfill(db_path, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
