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
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                        TEXT    NOT NULL,
    channel                       INTEGER NOT NULL,
    timestamp                     TEXT    NOT NULL,
    elapsed_s                     REAL,
    phase                         TEXT,
    voltage_v                     REAL,
    current_a                     REAL,
    temp_c                        REAL,
    test_type                     TEXT,
    relay                         INTEGER,
    phase_detail                  TEXT,
    commanded_v                   REAL,
    commanded_current_limit_a     REAL,
    smu_readback_v                REAL,
    smu_readback_current_limit_a  REAL,
    smu_measured_v                REAL,
    smu_measured_i                REAL,
    dmm_measured_v                REAL,
    output_enabled_readback       INTEGER,
    in_compliance                 INTEGER,
    daq_channel_0_raw             REAL,
    voltage_min_v                 REAL,
    voltage_max_v                 REAL,
    group_name                    TEXT,
    position_in_group             INTEGER
);
"""

# Original columns -- unchanged shape, still what record()/query()/the CSV
# writer use (backward compatible with charge_cycle.py/discharge_cycle.py's
# existing 2-arg record(channel, sample) callers).
_COLUMNS = ["run_id", "channel", "timestamp", "elapsed_s", "phase",
            "voltage_v", "current_a", "temp_c"]

# Milestone II additions -- Proto Test (and future Charge/Discharge/cycle
# execution) write through record_measurement() below, which populates
# these alongside _COLUMNS above. All nullable: a Proto Test row leaves
# elapsed_s/phase/temp_c NULL, a battery row leaves the SMU/DMM-specific
# columns NULL -- NULL is exactly what the UI layer renders as "N/A" (see
# test_control/execution_screen.py, Phase 2). test_type is deliberately
# nullable too (not NOT NULL) so this column can be added to an existing
# database via ALTER TABLE without a default-value migration -- every real
# caller populates it; nothing enforces that at the schema level.
_MEASUREMENT_EXTRA_COLUMNS = [
    "test_type", "relay", "phase_detail",
    "commanded_v", "commanded_current_limit_a",
    "smu_readback_v", "smu_readback_current_limit_a",
    "smu_measured_v", "smu_measured_i",
    "dmm_measured_v",
    "output_enabled_readback", "in_compliance",
    # Monitor Battery Scan (relay/DMM/DAQ path validation, no charging) --
    # a single, fixed-physical-channel raw AI reading (hardware/daq.py::
    # DAQ.read_channel()), stored unconverted since the DAQ channel-mapping
    # architecture is not yet approved (see test_control/
    # monitor_battery_scan_sequence.py). NULL for every other test_type.
    "daq_channel_0_raw",
    # Multi-sample DMM reading stats (Monitor Battery Scan) -- voltage_v
    # carries the average of MONITOR_SCAN_SAMPLES readings; these carry the
    # min/max of that same sample set, for relay-isolation/hardware-
    # characterization analysis. NULL for every other test_type (and NULL
    # here too if only a single sample was taken).
    "voltage_min_v", "voltage_max_v",
    # Group ownership traceability (Group Ownership Migration) -- which
    # BATTERY_GROUPS group/position this row belongs to. `channel` above
    # remains position_in_group's numeric value (no separate global
    # position number exists) -- group_name is what disambiguates rows
    # from two different groups that happen to share a channel number.
    "group_name", "position_in_group",
]

_MEASUREMENT_ALL_COLUMNS = _COLUMNS + _MEASUREMENT_EXTRA_COLUMNS

# Station/execution state -- Milestone II: RECOVERY ONLY. Previously also
# carried the full SMU/DMM measurement payload (commanded_v/smu_readback_v/
# .../dmm_measured_v below) -- that data now lives in `measurements` via
# record_measurement(). Those columns remain in the schema (existing rows
# from before this change stay readable) but new code no longer populates
# them -- see _STATION_STATE_COLUMNS' comment below.
CREATE_STATION_STATE_SQL = """
CREATE TABLE IF NOT EXISTS station_state (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                        TEXT    NOT NULL,
    timestamp                     TEXT    NOT NULL,
    channel                       INTEGER,
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

# Actively maintained going forward: channel, relay, state (+ run_id/
# timestamp, handled separately in record_execution_state()). The
# commanded_v/.../dmm_measured_v columns stay in this list so
# get_last_execution_state()/record_execution_state() remain able to read
# and write them for backward compatibility (a caller can still pass them),
# but no current caller does -- Proto Test (Phase 3) passes only
# channel/relay/state.
_STATION_STATE_COLUMNS = [
    "channel", "relay", "state", "timestamp", "commanded_v", "commanded_current_limit_a",
    "smu_readback_v", "smu_readback_current_limit_a", "smu_measured_v",
    "smu_measured_i", "dmm_measured_v",
]

# Run-level summary -- one row per run, Milestone II. Not part of the
# StorageBackend abstract interface (same reasoning as station_state above:
# a separate concern from per-sample measurement persistence). `id` is the
# operator-facing "Run Number" (matches the existing id/run_id convention
# already used by `measurements`/`station_state`); `run_id` remains the
# timestamp-based identifier used everywhere else in this codebase.
CREATE_RUN_SUMMARY_SQL = """
CREATE TABLE IF NOT EXISTS run_summary (
    id                                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                             TEXT UNIQUE NOT NULL,
    test_type                          TEXT,
    start_time                         TEXT,
    end_time                           TEXT,
    duration_s                         REAL,
    stop_reason                        TEXT,
    result                             TEXT,
    battery_type                       TEXT,
    battery_voltage_max_v              REAL,
    battery_voltage_min_v              REAL,
    battery_charge_current_limit_a     REAL,
    battery_discharge_current_limit_a  REAL,
    capacity_ah                        REAL,
    energy_wh                          REAL,
    cycle_count                        INTEGER,
    start_voltage                      REAL,
    end_voltage                        REAL,
    min_voltage                        REAL,
    max_voltage                        REAL,
    average_voltage                    REAL,
    sample_count                       INTEGER,
    smu_name                           TEXT,
    smu_resource                       TEXT,
    smu_model                          TEXT,
    dmm_name                           TEXT,
    dmm_resource                       TEXT,
    dmm_model                          TEXT,
    daq_name                           TEXT,
    daq_resource                       TEXT,
    daq_model                          TEXT,
    relay_matrix_name                  TEXT,
    relay_matrix_resource              TEXT,
    relay_matrix_model                 TEXT,
    group_name                         TEXT,
    position_in_group                  INTEGER,
    analysis_result                    TEXT
);
"""

_RUN_SUMMARY_COLUMNS = [
    "id", "run_id", "test_type", "start_time", "end_time", "duration_s",
    "stop_reason", "result", "battery_type", "battery_voltage_max_v",
    "battery_voltage_min_v", "battery_charge_current_limit_a",
    "battery_discharge_current_limit_a", "capacity_ah", "energy_wh", "cycle_count",
    # Monitor Battery voltage summary (Milestone II) -- populated by
    # MonitorBatterySequence.run() at end-of-run via finish_run_summary();
    # NULL for every other test_type (proto/future charge/discharge/cycle).
    "start_voltage", "end_voltage", "min_voltage", "max_voltage",
    "average_voltage", "sample_count",
    # Hardware identity/configuration snapshot (Milestone II traceability
    # extension) -- which physical instrument produced this run's data.
    # Populated once, at start_run_summary() time (same mechanism as the
    # battery-config snapshot above), before any relay closes -- see
    # docs/architecture.md "Hardware Identity Traceability". NULL for a
    # role that wasn't connected for this run (e.g. dmm_* when no DMM was
    # configured). "name" is the config/devices.py dict key (e.g.
    # "PRIMARY_SMU", "MATRIX_NUMATO_201"); "resource" is the VISA resource
    # string (PXI devices) or "ip:port" (Ethernet relay matrix); "model" is
    # the real instrument model string (PXI devices) or driver identifier
    # (relay matrix).
    "smu_name", "smu_resource", "smu_model",
    "dmm_name", "dmm_resource", "dmm_model",
    "daq_name", "daq_resource", "daq_model",
    "relay_matrix_name", "relay_matrix_resource", "relay_matrix_model",
    # Group ownership traceability (Group Ownership Migration) -- which
    # BATTERY_GROUPS group/position this run targeted. Populated once, at
    # start_run_summary() time, same as the hardware snapshot above.
    # position_in_group is NULL for a whole-group operation (Monitor
    # Battery Scan, NTC Group Scan) that has no single selected position.
    "group_name", "position_in_group",
    # Test Mode post-run diagnostic classification (informational only --
    # see test_control/battery_diagnostics.py) -- populated by
    # ChargeSequence/DischargeSequence (and, once built, CycleSequence, by
    # virtue of reusing them) at finish_run_summary() time. NULL for every
    # other test_type. Never influences stop_reason/result -- a separate,
    # additive column, not a replacement for either.
    "analysis_result",
]

# Runtime event history -- Milestone II. Fine-grained, timestamped narrative
# of meaningful runtime transitions (relay activated, output enabled,
# compliance check passed, measurement acquired, ...) -- NOT a replacement
# for logger output (self.log.* is untouched everywhere); only meaningful
# runtime events are written here. channel/relay are nullable so an event
# not tied to a specific DUT (e.g. a run-level event) is still valid.
CREATE_EVENT_LOG_SQL = """
CREATE TABLE IF NOT EXISTS event_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,
    channel   INTEGER,
    relay     INTEGER,
    level     TEXT,
    source    TEXT,
    message   TEXT
);
"""

_EVENT_LOG_COLUMNS = ["id", "run_id", "timestamp", "channel", "relay", "level", "source", "message"]

# Schema migration -- additive only (ALTER TABLE ... ADD COLUMN), for
# databases created before Milestone II's schema additions. CREATE TABLE
# IF NOT EXISTS above already gives a brand-new database every column from
# the start; this only matters for a pre-existing measurements.db/
# station_state that predates these columns. Never touches existing data --
# no DROP, no rebuild, matches every migration recommendation made earlier
# in this project's data-strategy reviews (docs/DATABASE_ROADMAP.md).
_MEASUREMENT_MIGRATION_COLUMNS = [
    ("test_type", "TEXT"),
    ("relay", "INTEGER"),
    ("phase_detail", "TEXT"),
    ("commanded_v", "REAL"),
    ("commanded_current_limit_a", "REAL"),
    ("smu_readback_v", "REAL"),
    ("smu_readback_current_limit_a", "REAL"),
    ("smu_measured_v", "REAL"),
    ("smu_measured_i", "REAL"),
    ("dmm_measured_v", "REAL"),
    ("output_enabled_readback", "INTEGER"),
    ("in_compliance", "INTEGER"),
    ("daq_channel_0_raw", "REAL"),
    ("voltage_min_v", "REAL"),
    ("voltage_max_v", "REAL"),
    ("group_name", "TEXT"),
    ("position_in_group", "INTEGER"),
]

_STATION_STATE_MIGRATION_COLUMNS = [
    ("channel", "INTEGER"),
]

# Monitor Battery voltage summary columns (Milestone II) -- additive, for a
# run_summary table created before these existed (e.g. an existing
# data_output/development/nipxi_dev.db from Proto Test Execution).
_RUN_SUMMARY_MIGRATION_COLUMNS = [
    ("start_voltage", "REAL"),
    ("end_voltage", "REAL"),
    ("min_voltage", "REAL"),
    ("max_voltage", "REAL"),
    ("average_voltage", "REAL"),
    ("sample_count", "INTEGER"),
    # Hardware identity/configuration snapshot -- additive, for a
    # run_summary table created before these existed.
    ("smu_name", "TEXT"),
    ("smu_resource", "TEXT"),
    ("smu_model", "TEXT"),
    ("dmm_name", "TEXT"),
    ("dmm_resource", "TEXT"),
    ("dmm_model", "TEXT"),
    ("daq_name", "TEXT"),
    ("daq_resource", "TEXT"),
    ("daq_model", "TEXT"),
    ("relay_matrix_name", "TEXT"),
    ("relay_matrix_resource", "TEXT"),
    ("relay_matrix_model", "TEXT"),
    ("group_name", "TEXT"),
    ("position_in_group", "INTEGER"),
    ("analysis_result", "TEXT"),
]


def _migrate_add_missing_columns(conn: sqlite3.Connection, table: str, columns: list):
    """
    Add any column in `columns` (list of (name, sql_type)) that isn't
    already present on `table`, via ALTER TABLE ... ADD COLUMN. Idempotent
    and safe to call every open() -- PRAGMA table_info() reflects reality,
    so a column added on a previous run is simply skipped on the next.
    Never touches existing rows/columns; new columns come back NULL for
    every row that predates them, which is exactly what should happen.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


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
            self._db.execute(CREATE_RUN_SUMMARY_SQL)
            self._db.execute(CREATE_EVENT_LOG_SQL)
            # Additive migration -- brings a pre-Milestone-II database (e.g.
            # an existing data_output/development/nipxi_dev.db) up to the
            # current schema without touching any existing row. No-op on a
            # brand-new database (CREATE TABLE above already has every
            # column) and no-op on an already-migrated one.
            _migrate_add_missing_columns(self._db, "measurements", _MEASUREMENT_MIGRATION_COLUMNS)
            _migrate_add_missing_columns(self._db, "station_state", _STATION_STATE_MIGRATION_COLUMNS)
            _migrate_add_missing_columns(self._db, "run_summary", _RUN_SUMMARY_MIGRATION_COLUMNS)
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
    # Historical measurements (Milestone II) -- the authoritative result
    # store for every test type (Proto Test, Charge, Discharge, future
    # cycle execution). NOT part of the StorageBackend abstract interface:
    # record()/query() above remain the original, narrower per-sample
    # contract (still used by charge_cycle.py/discharge_cycle.py exactly as
    # before -- untouched by this change). record_measurement() is the new,
    # general write path Proto Test (Phase 3) and future battery cycles use.
    # ------------------------------------------------------------------

    def record_measurement(self, test_type: str, channel: int, relay: int = None,
                            timestamp: str = None, **fields) -> int:
        """
        Persist one historical measurement row -- the Milestone II
        replacement for writing result data into station_state.

        `test_type` ("proto"/"charge"/"discharge"/future) and `channel`
        (canonical DUT identifier) are required; `relay` (physical routing
        path -- provenance, may diverge from `channel` in the future, see
        docs/architecture.md) is optional.

        `fields` accepts any of: elapsed_s, phase, phase_detail, voltage_v,
        current_a, temp_c, commanded_v, commanded_current_limit_a,
        smu_readback_v, smu_readback_current_limit_a, smu_measured_v,
        smu_measured_i, dmm_measured_v, output_enabled_readback,
        in_compliance, daq_channel_0_raw, voltage_min_v, voltage_max_v --
        any field omitted is stored NULL (rendered as
        "N/A" by the UI layer, never by this method). Unknown keys are
        ignored rather than raising, same policy as
        record_execution_state().

        Raises if the storage backend is not open -- this is historical
        result data, a caller must know immediately if it silently failed
        to persist.
        """
        if self._db is None:
            raise RuntimeError("DataStorage.record_measurement() called before open()")
        row = {
            "run_id": self.run_id,
            "channel": channel,
            "timestamp": timestamp or datetime.now().isoformat(),
            "test_type": test_type,
            "relay": relay,
        }
        writable = set(_MEASUREMENT_ALL_COLUMNS) - set(row)
        for key in writable:
            if key in fields:
                row[key] = fields[key]
        for key in _MEASUREMENT_ALL_COLUMNS:
            row.setdefault(key, None)
        cols = ["run_id", "channel", "timestamp"] + [
            c for c in _MEASUREMENT_ALL_COLUMNS if c not in ("run_id", "channel", "timestamp")
        ]
        placeholders = ", ".join("?" for _ in cols)
        cursor = self._db.execute(
            f"INSERT INTO measurements ({', '.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )
        self._db.commit()
        return cursor.lastrowid

    def get_measurements(self, run_id: str = None, channel: int = None,
                          recent_limit: int = None) -> list:
        """
        Return measurement rows (every Milestone II column, not just the
        original _COLUMNS subset query() returns) as a list of dicts,
        optionally filtered by run_id/channel. Defaults to all rows when
        both filters are None -- callers needing "this run only" should
        pass run_id explicitly (e.g. data/report.py::ReportGenerator).

        `recent_limit` (default None -- unchanged behavior, every existing
        caller unaffected): when given, returns only the most recent
        `recent_limit` rows (still in chronological order), fetched via
        `ORDER BY id DESC LIMIT ?` rather than pulling the full table and
        slicing in Python -- see test_control/execution_screen.py's compact
        "Recent Measurements" panel, which would otherwise re-query and
        re-render the ENTIRE run's history on every single sample during a
        long (multi-hour) run.
        """
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
            if recent_limit is not None:
                cur = self._db.execute(
                    f"SELECT {', '.join(_MEASUREMENT_ALL_COLUMNS)} FROM measurements "
                    f"{where} ORDER BY id DESC LIMIT ?",
                    [*params, recent_limit],
                )
                rows = [dict(zip(_MEASUREMENT_ALL_COLUMNS, row)) for row in cur.fetchall()]
                rows.reverse()
                return rows
            cur = self._db.execute(
                f"SELECT {', '.join(_MEASUREMENT_ALL_COLUMNS)} FROM measurements "
                f"{where} ORDER BY id",
                params,
            )
            return [dict(zip(_MEASUREMENT_ALL_COLUMNS, row)) for row in cur.fetchall()]
        except sqlite3.Error as e:
            self.log.error("DB query failed (get_measurements): %s", e)
            return []

    def get_first_measurement(self, run_id: str = None, channel: int = None) -> dict:
        """
        Return the earliest measurement row (by id) that represents a real
        battery-telemetry sample, as a dict, or None -- a cheap, single-row
        companion to get_measurements(recent_limit=...) for a "captured at
        the very start of this run" display (see test_control/
        execution_screen.py's "Initial" panel), without fetching the whole
        table to find it.

        Excludes rows tagged `phase_detail = 'NTC_PRECHECK'` explicitly --
        NOT just "voltage_v is NULL". A first attempt at this filter (see
        git history) checked only for at least one non-NULL electrical
        column, on the wrong assumption that test.py::_ntc_group_snapshot()
        ever's pre-check row leaves `voltage_v` NULL. It does not:
        _ntc_group_snapshot() stores the RAW NTC DIVIDER VOLTAGE in that
        same `voltage_v` column (see its own docstring/_run_ntc_group_scan()'s
        "voltage_v=<raw divider volts>") -- a real, non-NULL float that
        satisfied the old filter's OR-condition despite `dmm_measured_v`/
        `smu_measured_v`/`current_a` (the columns the "Initial" panel
        actually displays) staying NULL on that row. The pre-check row is
        written for the selected position BEFORE the relay ever closes and
        would otherwise still be "row one" for that channel every time --
        showing the operator an Initial Measurement of DMM/SMU/Current
        all N/A even though the battery plainly had a real, valid voltage
        reading (the pre-enable polarity check) before charging was ever
        allowed to start.

        The non-NULL-electrical-column check is kept as a second, general
        guard (any future phase_detail that also writes an all-NULL row
        stays excluded too) -- safe to combine with the phase_detail
        exclusion since MonitorBatteryScanSequence's own real per-position
        rows (OPEN_BEFORE/CLOSED/OPEN_AFTER) are never tagged
        NTC_PRECHECK and do populate `voltage_v` for real (the averaged
        DMM reading), so they are unaffected by either condition.

        Once a row passing both conditions exists, `ORDER BY id ASC
        LIMIT 1` always returns that SAME row on every call (never a later
        one) -- "store the first valid measurement permanently for the
        rest of the run" falls out of the query itself, no separate state
        needed. Returns None (rendered "N/A"/"(none)") if no such row
        exists yet -- e.g. a run that failed before its first real sample.
        """
        if self._db is None:
            return None
        conditions, params = [
            "(phase_detail IS NULL OR phase_detail != 'NTC_PRECHECK')",
            "(voltage_v IS NOT NULL OR current_a IS NOT NULL "
            "OR smu_measured_v IS NOT NULL OR dmm_measured_v IS NOT NULL)",
        ], []
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if channel is not None:
            conditions.append("channel = ?")
            params.append(channel)
        where = "WHERE " + " AND ".join(conditions)
        try:
            cur = self._db.execute(
                f"SELECT {', '.join(_MEASUREMENT_ALL_COLUMNS)} FROM measurements "
                f"{where} ORDER BY id ASC LIMIT 1",
                params,
            )
            row = cur.fetchone()
            return dict(zip(_MEASUREMENT_ALL_COLUMNS, row)) if row else None
        except sqlite3.Error as e:
            self.log.error("DB query failed (get_first_measurement): %s", e)
            return None

    # ------------------------------------------------------------------
    # Run summary (Milestone II) -- one row per run, the entry point for
    # "list all historical runs without scanning telemetry". `id` is the
    # operator-facing Run Number; `run_id` is the timestamp-based identifier
    # used everywhere else. One DataStorage instance == one run (self.run_id
    # is generated once in __init__), so start/finish operate on that run_id
    # implicitly rather than taking it as a parameter.
    # ------------------------------------------------------------------

    def start_run_summary(self, test_type: str, **fields) -> None:
        """
        Insert the initial run_summary row for this DataStorage instance's
        run_id. Call once, at the start of a run. `fields` accepts any of
        the optional run_summary columns (battery_type, battery_voltage_max_v,
        etc.) -- all NULL/"N/A" if omitted, exactly as with
        record_measurement().

        Raises if the storage backend is not open, same policy as
        record_measurement()/record_execution_state().
        """
        if self._db is None:
            raise RuntimeError("DataStorage.start_run_summary() called before open()")
        row = {
            "run_id": self.run_id,
            "test_type": test_type,
            "start_time": datetime.now().isoformat(),
        }
        optional_cols = [c for c in _RUN_SUMMARY_COLUMNS if c not in ("id", "run_id", "test_type", "start_time")]
        for key in optional_cols:
            if key in fields:
                row[key] = fields[key]
        for key in optional_cols:
            row.setdefault(key, None)
        cols = ["run_id", "test_type", "start_time"] + optional_cols
        placeholders = ", ".join("?" for _ in cols)
        self._db.execute(
            f"INSERT INTO run_summary ({', '.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )
        self._db.commit()

    def finish_run_summary(self, stop_reason: str = None, result: str = None, **fields) -> None:
        """
        Update this run's run_summary row with end-of-run values (end_time
        is always set to now; duration_s is computed from start_time if not
        explicitly provided in `fields`). Call once, at the end of a run.
        No-op (logs a warning) if start_run_summary() was never called for
        this run_id, rather than raising -- a missing summary row is a
        historical-visibility gap, not a safety-relevant failure.
        """
        if self._db is None:
            raise RuntimeError("DataStorage.finish_run_summary() called before open()")
        end_time = datetime.now().isoformat()
        updates = {"end_time": end_time, "stop_reason": stop_reason, "result": result}
        for key in ("capacity_ah", "energy_wh", "cycle_count",
                    "start_voltage", "end_voltage", "min_voltage",
                    "max_voltage", "average_voltage", "sample_count",
                    "analysis_result"):
            if key in fields:
                updates[key] = fields[key]
        if "duration_s" in fields:
            updates["duration_s"] = fields["duration_s"]
        else:
            cur = self._db.execute(
                "SELECT start_time FROM run_summary WHERE run_id = ?", (self.run_id,)
            )
            row = cur.fetchone()
            if row and row[0]:
                try:
                    start_dt = datetime.fromisoformat(row[0])
                    updates["duration_s"] = (datetime.fromisoformat(end_time) - start_dt).total_seconds()
                except ValueError:
                    pass
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = self._db.execute(
            f"UPDATE run_summary SET {set_clause} WHERE run_id = ?",
            [*updates.values(), self.run_id],
        )
        if cur.rowcount == 0:
            self.log.warning(
                "finish_run_summary(): no run_summary row found for run_id=%s "
                "(start_run_summary() was never called for this run)", self.run_id,
            )
        self._db.commit()

    def get_last_run_summary(self):
        """Return the most recent run_summary row (by id) as a dict, or None."""
        if self._db is None:
            return None
        cur = self._db.execute(
            f"SELECT {', '.join(_RUN_SUMMARY_COLUMNS)} FROM run_summary ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return dict(zip(_RUN_SUMMARY_COLUMNS, row)) if row else None

    def get_run_summary(self, run_id: str):
        """Return the run_summary row for a specific run_id as a dict, or None."""
        if self._db is None:
            return None
        cur = self._db.execute(
            f"SELECT {', '.join(_RUN_SUMMARY_COLUMNS)} FROM run_summary WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        return dict(zip(_RUN_SUMMARY_COLUMNS, row)) if row else None

    def list_run_summaries(self) -> list:
        """Return every run_summary row, most recent first."""
        if self._db is None:
            return []
        cur = self._db.execute(
            f"SELECT {', '.join(_RUN_SUMMARY_COLUMNS)} FROM run_summary ORDER BY id DESC"
        )
        return [dict(zip(_RUN_SUMMARY_COLUMNS, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Event log (Milestone II) -- fine-grained runtime narrative. NOT a
    # replacement for logger output (self.log.* is unrelated and
    # untouched) -- only meaningful runtime transitions are written here
    # (relay activated, output enabled, compliance check passed,
    # measurement acquired, ...), never per-loop-iteration noise.
    # ------------------------------------------------------------------

    def log_event(self, level: str, source: str, message: str,
                   channel: int = None, relay: int = None) -> int:
        """
        Persist one event_log row for this DataStorage instance's run_id.
        `channel`/`relay` are optional -- a run-level event (not tied to a
        specific DUT) is still valid with both NULL.
        """
        if self._db is None:
            raise RuntimeError("DataStorage.log_event() called before open()")
        cursor = self._db.execute(
            "INSERT INTO event_log (run_id, timestamp, channel, relay, level, source, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, datetime.now().isoformat(), channel, relay, level, source, message),
        )
        self._db.commit()
        return cursor.lastrowid

    def get_recent_events(self, run_id: str = None, limit: int = 20, channel: int = None) -> list:
        """
        Return the most recent `limit` event_log rows for `run_id`
        (defaults to this DataStorage instance's own run_id), oldest first
        (natural reading order for a "recent events" panel).

        `channel` (default None -- unchanged behavior, every existing
        caller unaffected): when given, restricts to rows tagged with that
        exact channel -- e.g. Charge/Discharge/Monitor Battery Scan's live
        execution screen showing only the position currently under test,
        not the group-wide NTC pre-check's per-position summary lines for
        every OTHER position (see test.py::_ntc_group_snapshot(), which
        already tags each position's own log_event() call with
        channel=<that position>). Run-level setup messages logged with no
        channel at all (e.g. "Run started", "Battery selected: ...") are
        excluded when a channel filter is given -- those were already
        printed to the console directly at the time they happened; the
        live panel's job is "what's happening now for this position", not
        replaying one-time setup traceability.
        """
        if self._db is None:
            return []
        run_id = run_id or self.run_id
        conditions, params = ["run_id = ?"], [run_id]
        if channel is not None:
            conditions.append("channel = ?")
            params.append(channel)
        cur = self._db.execute(
            f"SELECT {', '.join(_EVENT_LOG_COLUMNS)} FROM event_log "
            f"WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )
        rows = [dict(zip(_EVENT_LOG_COLUMNS, row)) for row in cur.fetchall()]
        return list(reversed(rows))

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
