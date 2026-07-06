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
