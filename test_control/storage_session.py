"""
Guarded DataStorage open -- pure setup/error-handling logic extracted
from test.py so a future worker_runtime.py does not need its own copy
(see docs/architecture.md "Preparation Phase: Six Resolved Decisions
Before worker_runtime.py"). No `input()` anywhere; the only operator-
facing surface is the optional `on_fail` callback, which test.py's own
wrapper passes as `print` to keep today's exact messages unchanged.
"""

from __future__ import annotations

import sqlite3

from data.storage import DataStorage


def open_storage_guarded(settings, hw_mgr=None, on_fail=None):
    """
    Open a DataStorage(settings=settings) with guarded error handling
    instead of a raw traceback if the database is unavailable at startup
    (file permissions, disk full, locked file, corrupt schema, etc.) --
    see docs/architecture.md "Database Startup Hardening". Full
    diagnostic detail is still preserved: DataStorage.open() already
    calls self.log.error(...) with the exception before re-raising (see
    data/storage.py) -- `on_fail` only replaces what an interactive
    caller additionally displays, never what is logged.

    Returns the opened DataStorage on success, or None on failure. On
    failure, if `hw_mgr` (an already-connected HardwareManager) is
    given, it is disconnected before returning -- no hardware is left
    connected just because the database could not be opened.

    `on_fail`, if given, is called once per operator-facing line (never
    affects control flow) -- test.py's wrapper passes `on_fail=print` to
    reproduce today's exact two-line `[FAIL]` messages unchanged; a
    future non-interactive caller can pass a logger method or omit it
    entirely.
    """
    storage = DataStorage(settings=settings)
    try:
        storage.open()
    except (OSError, sqlite3.Error) as e:
        if on_fail is not None:
            on_fail(f"\n[FAIL] Database unavailable -- could not open storage: {e}")
            on_fail("       See logs for full diagnostic detail. Aborting, no hardware activated.")
        if hw_mgr is not None:
            try:
                hw_mgr.disconnect_all()
            except Exception as shutdown_err:
                if on_fail is not None:
                    on_fail(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
                    on_fail("           Hardware may still be energized -- "
                            "physically disconnect power if this cannot be resolved immediately.")
        return None
    return storage
