"""
Telemetry/Index database path resolution + monthly rotation decision logic
-- see docs/architecture.md "Telemetry / Index Database Split" and
"Monthly Telemetry Rotation".

Two files, one policy:
  - index_database_file()     -- permanent, never rotates. Holds
                                  run_summary/station_state/run_sequence.
  - telemetry_database_file() -- "nipxi_<YYYY>_<MM>.db", holds
                                  measurements/event_log/raw_hardware_log.

Both are used by data/storage.py::DataStorage AND data/raw_hardware_log.py
::RawHardwareLogWriter, so the two independent connections in this
codebase always agree on which physical file is "current" -- neither
module computes a path independently.

Rotation is NOT "close and reopen a live connection" anywhere in this
codebase: DataStorage/HardwareManager/RawHardwareLogWriter are already
constructed fresh per workflow (never a long-lived singleton -- see
docs/architecture.md), and telemetry_database_file() is resolved fresh,
once, at DataStorage.open() time and never re-read for that instance's
life. This is what makes "never split a group across databases" true by
construction: a Group -> ALL run that happens to straddle a month
boundary keeps writing to whichever file its own DataStorage instance
opened at the start, regardless of how long it runs. should_rotate()
below exists to gate a FUTURE scheduler's decision about which file the
NEXT workflow's DataStorage should open -- it is a pure function, with no
side effects, so it is trivially testable and safe to call speculatively.
"""

from __future__ import annotations

import os
from datetime import datetime


def telemetry_database_file(settings, dt=None) -> str:
    """
    Resolve the telemetry database path for `dt` (default: now).

    If `settings` defines a DATABASE_FILE attribute explicitly, that exact
    path is honored verbatim -- preserves single-fixed-file test fakes and
    legacy overrides (e.g. tests/test_testpy_extraction_parity.py, which
    points DATABASE_FILE at a bad path to force a failure) that need full
    control over where DataStorage/RawHardwareLogWriter connect,
    regardless of the current month. Real config.settings.Settings does
    NOT define DATABASE_FILE (removed -- see that module), so real
    callers always get the computed monthly path below.
    """
    override = getattr(settings, "DATABASE_FILE", None)
    if override is not None:
        return override
    dt = dt or datetime.now()
    return os.path.join(settings.DATA_DIR, f"nipxi_{dt:%Y_%m}.db")


def index_database_file(settings) -> str:
    """
    Resolve the permanent index database path.

    Same override convention as telemetry_database_file() (an explicit
    INDEX_DATABASE_FILE attribute wins), otherwise computed live from
    `settings.DATA_DIR` -- deliberately NOT a frozen class attribute
    (unlike the old DATABASE_FILE), so a test/caller that monkeypatches
    DATA_DIR at runtime (e.g. tests/test_testpy_extraction_parity.py)
    is respected without also needing to separately patch this path.
    """
    override = getattr(settings, "INDEX_DATABASE_FILE", None)
    if override is not None:
        return override
    return os.path.join(settings.DATA_DIR, "nipxi_index.db")


def should_rotate(*, current_telemetry_month: str, now_month: str,
                   group_finished: bool, sequence_running: bool, scheduler_idle: bool) -> bool:
    """
    Pure rotation-gate decision -- see docs/architecture.md "Monthly
    Telemetry Rotation". Rotation may occur ONLY when ALL of:

        1. the current group has completely finished  (group_finished)
        2. no BatteryOperationSequence is running      (not sequence_running)
        3. the scheduler is idle                        (scheduler_idle)
        4. the calendar month has actually changed since the currently-
           open telemetry database's own month
           (current_telemetry_month != now_month)

    Months are compared as "%Y_%m" strings pinned to the ALREADY-OPEN
    telemetry file's own month, never re-derived from wall-clock alone --
    this is what keeps a long-running group that spans a month boundary
    safe: nothing here (or in DataStorage) ever re-resolves the telemetry
    path mid-run; this function only ever influences which file the NEXT
    workflow's DataStorage.open() will resolve.

    No scheduler exists in this codebase yet (see docs/architecture.md) --
    at today's one real integration point (test.py's post-workflow idle
    checkpoint), `group_finished`/`sequence_running`/`scheduler_idle` are
    trivially true/false by construction (the workflow that just returned
    IS the group finishing, and nothing else runs between menu actions).
    This function is written to also serve a future real scheduler's idle
    tick without requiring any change here.
    """
    if current_telemetry_month == now_month:
        return False
    return group_finished and not sequence_running and scheduler_idle
