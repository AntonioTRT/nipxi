"""
Measurement data storage.
Writes samples to:
    1. SQLite database  (nipxi.db)
    2. CSV files        (one per channel per test run)

MiniSQL replacement path:
    When MiniSQL becomes available, create a MiniSQLStorage class that
    implements the same StorageBackend interface defined below.
    Swap it in place of DataStorage without changing any caller code.
"""

import csv
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime

from config.settings import Settings


# -----------------------------------------------------------------------------
# StorageBackend interface  (MiniSQL compatibility layer)
# -----------------------------------------------------------------------------

class StorageBackend(ABC):
    """
    Abstract interface for measurement persistence.
    DataStorage (SQLite) and future MiniSQLStorage both implement this.
    Callers depend only on this interface.
    """

    @abstractmethod
    def open(self):
        """Open / initialize the storage backend."""

    @abstractmethod
    def close(self):
        """Flush and close the storage backend."""

    @abstractmethod
    def record(self, channel: int, sample: dict):
        """
        Persist one measurement sample.
        sample keys: elapsed_s, phase, voltage_v, current_a, temp_c
        """

    @abstractmethod
    def query(self, run_id: str = None, channel: int = None) -> list:
        """
        Return a list of measurement dicts matching the given filters.
        Returns all records when both filters are None.
        """

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


# -----------------------------------------------------------------------------
# SQLite implementation
# -----------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    channel     INTEGER NOT NULL,
    timestamp   TEXT    NOT NULL,
    elapsed_s   REAL,
    phase       TEXT,
    voltage_v   REAL,
    current_a   REAL,
    temp_c      REAL
);
"""

_COLUMNS = ["run_id", "channel", "timestamp", "elapsed_s", "phase",
            "voltage_v", "current_a", "temp_c"]

# Station/execution state -- one row per relay processed, used to display
# "previous execution found" at startup (Proto Test Execution, Milestone 2,
# and the future cycle/state recovery engine this anticipates -- see
# docs/DATABASE_ROADMAP.md Section 4 / docs/TODO.md's previously-unwired
# "station_state" table). Deliberately a separate table from `measurements`
# above (a different concern: station/execution position, not a per-sample
# battery reading) rather than overloading that schema's columns.
CREATE_STATION_STATE_SQL = """
CREATE TABLE IF NOT EXISTS station_state (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                        TEXT    NOT NULL,
    timestamp                     TEXT    NOT NULL,
    relay                         INTEGER,
    state                         TEXT,
    commanded_v                   REAL,
    commanded_current_limit_a     REAL,
    smu_readback_v                REAL,
    smu_readback_current_limit_a  REAL,
    smu_measured_v                REAL,
    smu_measured_i                REAL,
    dmm_measured_v                REAL
);
"""

_STATION_STATE_COLUMNS = [
    "relay", "state", "timestamp", "commanded_v", "commanded_current_limit_a",
    "smu_readback_v", "smu_readback_current_limit_a", "smu_measured_v",
    "smu_measured_i", "dmm_measured_v",
]


class DataStorage(StorageBackend):
    """SQLite + CSV storage. Implements StorageBackend."""

    def __init__(self, settings):
        self.s = settings
        self.log = logging.getLogger("nipxi.storage")
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._db: sqlite3.Connection | None = None
        self._csv_writers: dict = {}
        self._csv_files: dict = {}

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def open(self):
        try:
            os.makedirs(self.s.DATA_DIR, exist_ok=True)
            os.makedirs(self.s.CSV_DIR, exist_ok=True)
            self._db = sqlite3.connect(self.s.DATABASE_FILE)
            self._db.execute(CREATE_TABLE_SQL)
            self._db.execute(CREATE_STATION_STATE_SQL)
            self._db.commit()
            self.log.info("Storage opened. run_id=%s", self.run_id)
        except (OSError, sqlite3.Error) as e:
            self.log.error("Failed to open storage: %s", e)
            raise

    def close(self):
        for ch, f in list(self._csv_files.items()):
            try:
                f.close()
            except OSError as e:
                self.log.warning("Error closing CSV for channel %d: %s", ch, e)
        self._csv_files.clear()
        self._csv_writers.clear()

        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error as e:
                self.log.warning("Error closing database: %s", e)
            self._db = None

        self.log.info("Storage closed.")

    def record(self, channel: int, sample: dict):
        """Persist one measurement sample (dict with voltage_v, current_a, etc.)."""
        now = datetime.now().isoformat()

        if self._db is not None:
            try:
                self._db.execute(
                    "INSERT INTO measurements "
                    "(run_id, channel, timestamp, elapsed_s, phase, voltage_v, current_a, temp_c) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        self.run_id,
                        channel,
                        now,
                        sample.get("elapsed_s"),
                        sample.get("phase"),
                        sample.get("voltage_v"),
                        sample.get("current_a"),
                        sample.get("temp_c"),
                    ),
                )
                self._db.commit()
            except sqlite3.Error as e:
                self.log.error("DB write failed (channel=%d): %s", channel, e)
                raise

        writer = self._get_csv_writer(channel)
        writer.writerow({"run_id": self.run_id, "channel": channel,
                         "timestamp": now, **sample})

    def query(self, run_id: str = None, channel: int = None) -> list:
        """Return measurement rows as list of dicts."""
        if self._db is None:
            return []
        conditions, params = [], []
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if channel is not None:
            conditions.append("channel = ?")
            params.append(channel)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        try:
            cur = self._db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM measurements {where}", params
            )
            return [dict(zip(_COLUMNS, row)) for row in cur.fetchall()]
        except sqlite3.Error as e:
            self.log.error("DB query failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Station/execution state (Proto Test Execution, Milestone 2) -- NOT
    # part of the StorageBackend interface above: this is a separate
    # concern (station/execution position, not a per-sample measurement),
    # so a future MiniSQLStorage need not implement it unless it also wants
    # this feature. Same connection/table-lifetime as the measurements
    # table (opened in open(), closed in close()).
    # ------------------------------------------------------------------

    def record_execution_state(self, relay: int, state: str, **fields) -> int:
        """
        Persist one station-state row: which relay was being processed,
        what state it was in, and (optionally) the SMU configuration/
        readback, SMU measurements, and DMM measurement at that point.

        `state` is a plain string -- reuse utils/stop_reason.py's
        StopReason constants for terminal states (COMPLETED/FAILED/
        SAFETY_VIOLATION/CANCELLED) plus "ACTIVE" for an in-progress relay,
        rather than inventing a second vocabulary.

        `fields` accepts any of: commanded_v, commanded_current_limit_a,
        smu_readback_v, smu_readback_current_limit_a, smu_measured_v,
        smu_measured_i, dmm_measured_v -- any field omitted is stored NULL.
        Unknown keys are ignored (not written) rather than raising, so a
        caller can pass through a dict built for another purpose safely.

        Raises if the storage backend is not open -- unlike record()'s
        silent no-op, a caller relying on this for recovery/display data
        should know immediately if it silently didn't persist.
        """
        if self._db is None:
            raise RuntimeError("DataStorage.record_execution_state() called before open()")
        row = {
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "relay": relay,
            "state": state,
        }
        for key in _STATION_STATE_COLUMNS:
            if key in fields and key not in row:
                row[key] = fields[key]
        for key in _STATION_STATE_COLUMNS:
            row.setdefault(key, None)
        cols = ["run_id"] + _STATION_STATE_COLUMNS
        placeholders = ", ".join("?" for _ in cols)
        cursor = self._db.execute(
            f"INSERT INTO station_state ({', '.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )
        self._db.commit()
        return cursor.lastrowid

    def get_last_execution_state(self):
        """
        Return the most recently recorded station-state row as a dict
        (relay/state/timestamp/commanded_v/.../dmm_measured_v), or None if
        none has ever been recorded. Reads across run_ids deliberately --
        "the previous execution's last known position" means the last row
        in the table regardless of which run wrote it, since this is
        queried at the START of a new run (a new run_id) specifically to
        show what the PREVIOUS run left off at. No automatic resume is
        implied or performed here -- display only (see
        test.py::run_proto_test_execution()).
        """
        if self._db is None:
            return None
        cur = self._db.execute(
            f"SELECT {', '.join(_STATION_STATE_COLUMNS)} FROM station_state "
            f"ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(_STATION_STATE_COLUMNS, row))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_csv_writer(self, channel: int):
        if channel not in self._csv_writers:
            path = os.path.join(self.s.CSV_DIR, f"{self.run_id}_ch{channel:02d}.csv")
            f = open(path, "w", newline="", encoding="utf-8")
            w = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
            w.writeheader()
            self._csv_files[channel] = f
            self._csv_writers[channel] = w
        return self._csv_writers[channel]
