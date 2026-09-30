"""
Persistent historical record of test.py diagnostic/validation executions --
see the "Test Execution Traceability" design review.

Purely additive, mirrors data/raw_hardware_log.py's design: an independent
SQLite connection to the SAME monthly telemetry file (data/rotation.py::
telemetry_database_file()), its own schema, best-effort writes that never
raise. Deliberately does NOT depend on DataStorage, run_id, run_summary, or
run_sequence -- diagnostic/validation runs (Hardware Discovery, SMU/DMM/DAQ
tests, relay Matrix Scan/RelayEthernetTest/Safety Self-Test/Native Relay
Coexistence Probe, etc.) never open a DataStorage session and must not
consume the battery-run sequence counter or touch measurements/event_log/
run_summary.

test.py's own TestResult objects are duck-typed here (status/module/device/
config_ref/details attributes) -- this module never imports test.py, so
there is no import cycle.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from datetime import datetime

from data.rotation import telemetry_database_file

_log = logging.getLogger("nipxi.test_execution_log")

# Safety guard, not a real-world limit: measured worst-case payloads top out
# around 65 KB (Native Relay Coexistence Probe, a full 32-channel matrix
# split 16/16 -- the product-maximizing case) -- see the "raw_data
# truncation" design review. 200,000 chars gives ~3x headroom over that
# measured worst case so no legitimate diagnostic payload from any of
# these four test.py functions is ever truncated; it exists only to catch
# a genuine bug (e.g. a runaway loop appending observations indefinitely)
# before it writes an unbounded row. See data/raw_hardware_log.py::
# _safe_json() for the equivalent guard on that table's fields.
_MAX_RAW_DATA_LEN = 200_000

CREATE_TEST_EXECUTION_SQL = """
CREATE TABLE IF NOT EXISTS test_execution (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT    NOT NULL,
    test_name      TEXT    NOT NULL,
    config_ref     TEXT,
    duration_s     REAL,
    overall_result TEXT    NOT NULL,
    git_branch     TEXT,
    git_commit     TEXT
);
"""

CREATE_TEST_EXECUTION_STEP_SQL = """
CREATE TABLE IF NOT EXISTS test_execution_step (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL,
    step_order   INTEGER NOT NULL,
    device       TEXT,
    result       TEXT    NOT NULL,
    detail       TEXT,
    raw_data     TEXT
);
"""

CREATE_TEST_EXECUTION_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_test_execution_name      ON test_execution(test_name);",
    "CREATE INDEX IF NOT EXISTS idx_test_execution_timestamp ON test_execution(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_test_execution_step_exec ON test_execution_step(execution_id);",
]

# Additive migration for a test_execution_step table created before raw_data
# existed -- same "ALTER TABLE ... ADD COLUMN, never touch existing rows"
# convention data/storage.py::_migrate_add_missing_columns() already uses.
_TEST_EXECUTION_STEP_MIGRATION_COLUMNS = [("raw_data", "TEXT")]


def _migrate_add_missing_columns(conn: sqlite3.Connection, table: str, columns: list) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _safe_raw_json(raw: dict | None) -> str | None:
    """JSON-encode `raw` for storage, capped at _MAX_RAW_DATA_LEN. Never
    raises -- mirrors data/raw_hardware_log.py::_safe_json()."""
    if raw is None:
        return None
    try:
        text = json.dumps(raw, default=str)
    except Exception:
        try:
            text = repr(raw)
        except Exception:
            text = "<unrepresentable>"
    if len(text) > _MAX_RAW_DATA_LEN:
        text = text[:_MAX_RAW_DATA_LEN] + "...<truncated>"
    return text

# Result severity, worst wins -- same PASS/WARNING/FAIL vocabulary as
# test.py::Status.
_SEVERITY = {"PASS": 0, "WARNING": 1, "FAIL": 2}

# Cached once per process -- git branch/commit never change mid-run.
_GIT_INFO: tuple[str | None, str | None] | None = None


def _git_branch_and_commit() -> tuple[str | None, str | None]:
    """Best-effort `git rev-parse` lookup, cached. Returns (branch, commit),
    each None if git is unavailable or this isn't a git working tree."""
    global _GIT_INFO
    if _GIT_INFO is not None:
        return _GIT_INFO
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    branch = commit = None
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, timeout=2,
        ).stdout.strip() or None
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, timeout=2,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    _GIT_INFO = (branch, commit)
    return _GIT_INFO


def _overall_result(statuses: list) -> str:
    worst = max(statuses, key=lambda s: _SEVERITY.get(s, 0), default="PASS")
    return worst if worst in _SEVERITY else "PASS"


def record_test_executions(settings, results: list, duration_s: float | None = None) -> None:
    """
    Persist one test_execution + N test_execution_step row(s) per distinct
    `module` value found in `results` (a list of test.py::TestResult-like
    objects: status/module/device/config_ref/details attributes).

    Best-effort: any failure (missing DATA_DIR, locked file, disk full) is
    logged as a warning and swallowed -- persistence must never interrupt
    or fail a diagnostic/validation run.
    """
    if not results:
        return
    try:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        path = telemetry_database_file(settings)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=2000")
            conn.execute(CREATE_TEST_EXECUTION_SQL)
            conn.execute(CREATE_TEST_EXECUTION_STEP_SQL)
            for stmt in CREATE_TEST_EXECUTION_INDEXES_SQL:
                conn.execute(stmt)
            _migrate_add_missing_columns(conn, "test_execution_step",
                                          _TEST_EXECUTION_STEP_MIGRATION_COLUMNS)

            groups: dict[str, list] = {}
            for r in results:
                groups.setdefault(getattr(r, "module", "Unknown"), []).append(r)

            timestamp = datetime.now().isoformat()
            branch, commit = _git_branch_and_commit()

            for test_name, steps in groups.items():
                overall = _overall_result([getattr(s, "status", "PASS") for s in steps])
                config_ref = next((getattr(s, "config_ref", None) for s in steps
                                    if getattr(s, "config_ref", None)), None)
                cur = conn.execute(
                    "INSERT INTO test_execution "
                    "(timestamp, test_name, config_ref, duration_s, overall_result, "
                    " git_branch, git_commit) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (timestamp, test_name, config_ref, duration_s, overall, branch, commit),
                )
                execution_id = cur.lastrowid
                conn.executemany(
                    "INSERT INTO test_execution_step "
                    "(execution_id, step_order, device, result, detail, raw_data) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (execution_id, i, getattr(s, "device", None),
                         getattr(s, "status", "PASS"), getattr(s, "details", None),
                         _safe_raw_json(getattr(s, "raw", None)))
                        for i, s in enumerate(steps)
                    ],
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        _log.warning("test_execution_log: write failed -- test run is NOT affected: %s", e)
    except Exception as e:  # pragma: no cover -- defense in depth, mirrors raw_hardware_log.py
        _log.warning("test_execution_log: unexpected logging failure -- test run is NOT affected: %s", e)
