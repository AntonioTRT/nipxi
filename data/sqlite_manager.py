"""
SQLite database manager -- minimal foundation for the future database
architecture described in docs/DATABASE_ROADMAP.md.

Intentionally simple: one table (`test_records`), four functions
(create_database, initialize_schema, insert_test_record, get_last_record).
NOT a repository layer, NOT cycle recovery, NOT battery-cycling storage --
those are still on the roadmap (docs/DATABASE_ROADMAP.md Sections 2-4).
This module exists to prove the mode-separated database location
(config/settings.py DATABASE_FILE, driven by SYSTEM_MODE -- see
config/system_mode.py) actually works end to end, on a laptop with no PXI
hardware attached, before anything more complex is built on top of it.

Usage:
    from config.settings import Settings
    from data.sqlite_manager import (
        create_database, initialize_schema, insert_test_record, get_last_record,
    )

    conn = create_database(Settings)
    initialize_schema(conn)
    insert_test_record(conn, label="startup_check", value=1.0)
    last = get_last_record(conn)
    conn.close()
"""

import logging
import os
import sqlite3
from datetime import datetime

log = logging.getLogger("nipxi.sqlite_manager")

CREATE_TEST_RECORDS_SQL = """
CREATE TABLE IF NOT EXISTS test_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    label       TEXT,
    value       REAL
);
"""


def create_database(settings) -> sqlite3.Connection:
    """
    Ensure `settings.DATA_DIR` exists (mode-specific -- see
    config/settings.py and docs/DATABASE_ROADMAP.md Section 1) and open
    `settings.DATABASE_FILE`, creating the file if it does not exist yet.

    Does NOT create any tables -- call initialize_schema() next.
    """
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_FILE)
    log.info("Database opened: %s", settings.DATABASE_FILE)
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create the `test_records` table if it does not already exist. Idempotent."""
    conn.execute(CREATE_TEST_RECORDS_SQL)
    conn.commit()


def insert_test_record(conn: sqlite3.Connection, label: str, value: float) -> int:
    """Insert one row into `test_records`. Returns the new row's id."""
    cursor = conn.execute(
        "INSERT INTO test_records (timestamp, label, value) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), label, value),
    )
    conn.commit()
    return cursor.lastrowid


def get_last_record(conn: sqlite3.Connection):
    """
    Return the most recently inserted `test_records` row as a dict
    (`id`/`timestamp`/`label`/`value`), or None if the table is empty.
    """
    cursor = conn.execute(
        "SELECT id, timestamp, label, value FROM test_records ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "timestamp": row[1], "label": row[2], "value": row[3]}
