"""
Measurement data storage.
Writes samples to:
    1. SQLite database  (nipxi.db)
    2. CSV files        (one per channel per test run)

TODO: Call storage.record() from inside charge/discharge loops.
"""

import csv
import logging
import os
import sqlite3
from datetime import datetime
from config.settings import Settings


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


class DataStorage:
    def __init__(self, settings: Settings):
        self.s = settings
        self.log = logging.getLogger("nipxi.storage")
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._db: sqlite3.Connection | None = None
        self._csv_writers: dict = {}
        self._csv_files: dict = {}

    def open(self):
        os.makedirs(self.s.DATA_DIR, exist_ok=True)
        os.makedirs(self.s.CSV_DIR, exist_ok=True)
        self._db = sqlite3.connect(self.s.DATABASE_FILE)
        self._db.execute(CREATE_TABLE_SQL)
        self._db.commit()
        self.log.info("Storage opened. run_id=%s", self.run_id)

    def close(self):
        for f in self._csv_files.values():
            f.close()
        if self._db:
            self._db.close()
        self.log.info("Storage closed.")

    def record(self, channel: int, sample: dict):
        """Store one measurement sample (dict with voltage_v, current_a, etc.)."""
        now = datetime.now().isoformat()

        # SQLite
        if self._db:
            self._db.execute(
                "INSERT INTO measurements (run_id, channel, timestamp, elapsed_s, phase, voltage_v, current_a, temp_c) VALUES (?,?,?,?,?,?,?,?)",
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

        # CSV per channel
        writer = self._get_csv_writer(channel)
        writer.writerow({
            "run_id": self.run_id,
            "channel": channel,
            "timestamp": now,
            **sample,
        })

    def _get_csv_writer(self, channel: int):
        if channel not in self._csv_writers:
            path = os.path.join(self.s.CSV_DIR, f"{self.run_id}_ch{channel:02d}.csv")
            f = open(path, "w", newline="", encoding="utf-8")
            fieldnames = ["run_id", "channel", "timestamp", "elapsed_s", "phase", "voltage_v", "current_a", "temp_c"]
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            self._csv_files[channel] = f
            self._csv_writers[channel] = w
        return self._csv_writers[channel]

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()
