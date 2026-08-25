"""
Automatic, database-backed hardware audit trail -- see docs/architecture.md
"Hardware Audit Trail: raw_hardware_log". Captures every intercepted
hardware driver method call (see hardware/audit_proxy.py) independently
of event_log/measurements, which remain entirely unchanged (this module
is purely additive and is never imported by data/storage.py's existing
StorageBackend/DataStorage callers for anything other than schema
creation -- see CREATE_RAW_HARDWARE_LOG_SQL below).

This module deliberately does NOT depend on DataStorage or require an
open DataStorage session: HardwareManager.connect_all()'s own startup
safety calls (SMU emergency_output_off(), relay open_all()) happen
BEFORE any DataStorage is constructed in every real call site (see
test.py's _run_charge_or_discharge_all_positions()/_run_monitor_battery()
et al -- HardwareManager is always constructed and connected first, then
storage is opened). RawHardwareLogWriter therefore owns its own,
independent SQLite connection to the SAME Settings.DATABASE_FILE,
opened lazily on first write, so audit rows exist even for hardware
calls that happen before -- or entirely without -- an open DataStorage
session (e.g. a diagnostic screen that never constructs DataStorage at
all).

Session vs run: `run_id` (DataStorage.run_id) identifies one logical
test run and can change mid-process (DataStorage.begin_new_run_id() --
see docs/architecture.md "Group -> ALL Support"); `session_id` here
identifies one PROCESS lifetime and never changes once generated,
regardless of how many run_ids or DataStorage/HardwareManager instances
come and go within it. This is what lets a startup/shutdown hardware
call that happens before any run_id exists still be correlated with
whatever run(s) follow in the same process.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime

_log = logging.getLogger("nipxi.raw_hw_log")

# -----------------------------------------------------------------------
# Schema -- imported by data/storage.py::DataStorage.open() too, so the
# table/indexes are guaranteed present as soon as the application opens
# storage the normal way, even before any hardware call has happened in
# that run. Single source of truth for the DDL -- DataStorage and
# RawHardwareLogWriter must never define this independently and risk
# drifting out of sync.
# -----------------------------------------------------------------------
CREATE_RAW_HARDWARE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS raw_hardware_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT    NOT NULL,
    run_id               TEXT,
    session_id           TEXT    NOT NULL,
    position             INTEGER,
    device_type          TEXT    NOT NULL,
    device_name          TEXT    NOT NULL,
    resource             TEXT,
    command              TEXT    NOT NULL,
    command_parameters   TEXT,
    response             TEXT,
    success              INTEGER NOT NULL,
    duration_ms          REAL,
    error_type           TEXT,
    error_message        TEXT,
    additional_metadata  TEXT
);
"""

CREATE_RAW_HARDWARE_LOG_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_raw_hw_log_run     ON raw_hardware_log(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_raw_hw_log_session ON raw_hardware_log(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_raw_hw_log_device  ON raw_hardware_log(device_type, device_name);",
    "CREATE INDEX IF NOT EXISTS idx_raw_hw_log_failure ON raw_hardware_log(success) WHERE success = 0;",
]

# Free-text/JSON fields are capped so one pathological call (a huge
# array argument, a verbose instrument error string) can never balloon a
# single row -- see docs/architecture.md "Database Growth Protection".
_MAX_FIELD_LEN = 1000

# "Generated once per application lifetime" -- a module-level global,
# lazily created on first access, is a process-lifetime singleton by
# construction (re-importing this module returns the same module
# object). NOT tied to any particular RawHardwareLogWriter/settings
# instance, so multiple HardwareManagers (or HardwareManagers built with
# different, e.g. per-test, Settings) within one process still share the
# identical session_id.
_SESSION_ID: str | None = None


def get_session_id() -> str:
    """Return this process's audit session_id, generating it on first call."""
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return _SESSION_ID


def _safe_json(value) -> str | None:
    """
    JSON-encode `value` for storage, capped at _MAX_FIELD_LEN. Never
    raises -- falls back to a capped repr() for anything json.dumps()
    can't handle (e.g. a driver-specific object with no __dict__), since
    a best-effort audit string is far more useful than losing the row
    entirely over a serialization error.
    """
    if value is None:
        return None
    try:
        text = json.dumps(value, default=str)
    except Exception:
        try:
            text = repr(value)
        except Exception:
            text = "<unrepresentable>"
    if len(text) > _MAX_FIELD_LEN:
        text = text[:_MAX_FIELD_LEN] + "...<truncated>"
    return text


class RawHardwareLogWriter:
    """
    Owns one independent SQLite connection to Settings.DATABASE_FILE and
    writes raw_hardware_log rows. Every public method is best-effort and
    NEVER raises -- a database lock, a full disk, or any other storage
    fault degrades to a logged warning, never an interruption of the
    hardware call it was trying to audit (see docs/architecture.md
    "Failure Handling" for the full rationale: an audit mechanism must
    not itself become a new source of test-run failures).

    One instance per HardwareManager (constructed with whatever
    `settings` that HardwareManager was given) -- NOT a process-wide
    singleton, so tests using a throwaway per-test Settings/DATABASE_FILE
    never leak state into each other. `session_id` (module-level, see
    get_session_id()) IS process-wide and is unaffected by this.
    """

    def __init__(self, settings):
        self.s = settings
        self.session_id = get_session_id()
        self._db: sqlite3.Connection | None = None
        self._connect_failed = False  # avoids retrying a hard failure on every single call

    def _ensure_connection(self) -> sqlite3.Connection | None:
        if self._db is not None:
            return self._db
        if self._connect_failed:
            return None
        try:
            os.makedirs(self.s.DATA_DIR, exist_ok=True)
            db = sqlite3.connect(self.s.DATABASE_FILE)
            # WAL mode reduces writer/reader lock contention for this
            # additive, best-effort write path -- see docs/architecture.md
            # "Database Growth Protection". busy_timeout gives a transient
            # lock (e.g. DataStorage's own connection mid-commit) a real
            # chance to clear before this best-effort write gives up,
            # rather than immediately raising "database is locked".
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=2000")
            db.execute(CREATE_RAW_HARDWARE_LOG_SQL)
            for stmt in CREATE_RAW_HARDWARE_LOG_INDEXES_SQL:
                db.execute(stmt)
            db.commit()
            self._db = db
        except (OSError, sqlite3.Error) as e:
            self._connect_failed = True
            _log.warning("raw_hardware_log: could not open/initialize database (%s) -- "
                         "hardware audit logging disabled for this session, hardware "
                         "operation is NOT affected: %s", self.s.DATABASE_FILE, e)
            return None
        return self._db

    def log(self, *, run_id, position, device_type, device_name, resource,
            command, command_parameters, response, success, duration_ms,
            error_type, error_message, additional_metadata=None) -> None:
        """
        Insert one raw_hardware_log row. Never raises -- see class
        docstring. `command_parameters`/`response`/`additional_metadata`
        are arbitrary Python values (JSON-encoded here, not by the
        caller) so hardware/audit_proxy.py never needs to know this is
        even a database.
        """
        try:
            db = self._ensure_connection()
            if db is None:
                return
            db.execute(
                "INSERT INTO raw_hardware_log "
                "(timestamp, run_id, session_id, position, device_type, device_name, "
                " resource, command, command_parameters, response, success, duration_ms, "
                " error_type, error_message, additional_metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(), run_id, self.session_id, position,
                    device_type, device_name, resource, command,
                    _safe_json(command_parameters), _safe_json(response),
                    1 if success else 0, duration_ms, error_type, error_message,
                    _safe_json(additional_metadata),
                ),
            )
            db.commit()
        except (OSError, sqlite3.Error) as e:
            # A DB fault mid-write (disk full, lock never clearing within
            # busy_timeout, etc). Drop the connection so the next call
            # retries a fresh open rather than reusing a possibly-broken one.
            _log.warning("raw_hardware_log: write failed for %s.%s (%s) -- "
                         "hardware operation is NOT affected: %s",
                         device_name, command, self.s.DATABASE_FILE, e)
            self._db = None
        except Exception as e:  # pragma: no cover -- defense in depth, see class docstring
            _log.warning("raw_hardware_log: unexpected logging failure for %s.%s -- "
                         "hardware operation is NOT affected: %s", device_name, command, e)

    def close(self) -> None:
        """Best-effort close -- never raises. Safe to call multiple times."""
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None
