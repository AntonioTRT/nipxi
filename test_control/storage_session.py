"""
Guarded DataStorage session lifecycle -- pure setup/error-handling logic
extracted from test.py so a future worker_runtime.py does not need its
own copy (see docs/architecture.md "Preparation Phase: Six Resolved
Decisions Before worker_runtime.py" and "Remaining Helper Extraction
Before worker_runtime.py"). No `input()` anywhere; the only operator-
facing surface is the optional `on_fail` callback, which test.py's own
wrappers pass as `print` to keep today's exact messages unchanged.

Covers the three steps every hardware-activating workflow performs, in
order, before touching a relay/SMU: open storage
(`open_storage_guarded()`), build the hardware-identity snapshot that
will be recorded on the run_summary row (`hardware_snapshot_fields()`),
and start that row (`start_run_summary_guarded()`).
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
        # Hardware Audit Trail (see docs/architecture.md "Hardware Audit
        # Trail" / "Session Tracking") -- as soon as a real run_id
        # exists, every raw_hardware_log row HardwareManager's already-
        # instrumented devices produce should carry it. `hasattr` guards
        # against any hw_mgr-like object that predates this feature (see
        # tests/test_storage_session.py's _FakeHardwareManager, which has
        # no such method) -- this call is best-effort wiring, never a
        # hard requirement of a successful storage open.
        if hw_mgr is not None and hasattr(hw_mgr, "attach_run_id_provider"):
            hw_mgr.attach_run_id_provider(lambda: storage.run_id)
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


def hardware_snapshot_fields(smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg) -> dict:
    """
    Build the run_summary hardware-identity snapshot dict (see
    data/storage.py's run_summary schema and docs/architecture.md
    "Hardware Identity Traceability") from the SAME resolved
    config/devices.py dicts HardwareManager was actually constructed
    with -- single source of truth, no independent re-derivation.

    `dmm_name`/`dmm_cfg` may be None (the DMM is optional for some
    workflows) -- every other role is always present, since
    HardwareManager always constructs an SMU/DAQ/relay driver. Pure --
    no I/O, no printing, nothing to inject via `on_fail`.
    """
    fields = {
        "smu_name": smu_name, "smu_resource": smu_cfg.get("resource"), "smu_model": smu_cfg.get("model"),
        "daq_name": daq_name, "daq_resource": daq_cfg.get("resource"), "daq_model": daq_cfg.get("model"),
        "relay_matrix_name": relay_cfg.get("name"),
        "relay_matrix_model": relay_cfg.get("driver"),
        "relay_matrix_resource": (
            f"{relay_cfg.get('ip', '')}:{relay_cfg.get('port', '')}"
            if relay_cfg.get("type", "").lower() == "ethernet"
            else str(relay_cfg.get("port", ""))
        ),
    }
    if dmm_cfg is not None:
        fields["dmm_name"] = dmm_name
        fields["dmm_resource"] = dmm_cfg.get("resource")
        fields["dmm_model"] = dmm_cfg.get("model")
    return fields


def start_run_summary_guarded(storage, test_type: str, on_fail=None, **fields) -> bool:
    """
    Call DataStorage.start_run_summary() with guarded error handling
    instead of a raw traceback if the database becomes unavailable
    between open() and here (e.g. the underlying file/volume
    disappears). Returns True on success, False on failure -- callers
    must abort (no relay activation/PSU output) on False, exactly as on
    any other Stage validation failure. Diagnostic detail is preserved
    via normal exception logging further up the call stack -- `on_fail`
    only replaces what an interactive caller additionally displays.

    `on_fail`, if given, is called once per operator-facing line, same
    convention as open_storage_guarded() above.
    """
    try:
        storage.start_run_summary(test_type=test_type, **fields)
        return True
    except sqlite3.Error as e:
        if on_fail is not None:
            on_fail(f"\n[FAIL] Database unavailable -- could not start run_summary: {e}")
            on_fail("       See logs for full diagnostic detail. Aborting, no hardware activated.")
        return False
