"""
Generic post-run summary renderer -- ONE function used by every workflow's
end-of-run display, the "Last Test Summary" menu entry, and Database Tools'
"View Latest Run" screen (see test.py). No caller prints its own summary.

Reuses run_summary as the single source of truth (data/storage.py::
DataStorage.get_run_summary()/get_last_run_summary()) -- the same row every
sequence already populates via BatteryOperationSequence.run_guarded()/
complete() (test_control/battery_operation_sequence.py). No new in-memory
buffer, no new table: the only additional query this module performs is
Monitor Battery Scan's per-relay breakdown, read from `measurements` (which
has no per-relay columns on run_summary itself).

Group is read from run_summary.group_name (Group Ownership Migration --
populated at start_run_summary() time by every implemented workflow), with
a fallback to parsing this run's own event_log "Group selected: <group>"
entry for any row written before that column existed (group_name is NULL
on those). relay_matrix_name alone cannot substitute for either: BATTERY_GROUPS
entries can share a relay matrix (see config/devices.py), so it cannot be
reversed back into a group name.
"""

from test_control.execution_screen import _fmt, _fmt_volts

TEST_TYPE_DISPLAY_NAMES = {
    "monitor": "Monitor Battery",
    "monitor_scan": "Monitor Battery Scan",
    "charge_battery": "Charge Battery",
    "discharge_battery": "Discharge Battery",
    "cycle_battery": "Cycle Battery",
}

# Event_log rows per run are few (relay/config traceability lines, plus
# Monitor Battery Scan's per-position/dwell narrative) -- large enough to
# never truncate the "Group selected" line, which is always one of the
# first events logged for a run, well before this limit could matter.
_GROUP_LOOKUP_EVENT_LIMIT = 2000
_GROUP_EVENT_PREFIX = "Group selected: "


def display_test_type(test_type: str) -> str:
    return TEST_TYPE_DISPLAY_NAMES.get(test_type, test_type or "N/A")


def _format_duration(seconds) -> str:
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_timestamp(value) -> str:
    """run_summary stores datetime.isoformat() strings -- render
    "YYYY-MM-DD HH:MM:SS", dropping the sub-second fraction and the 'T'."""
    if not value:
        return "N/A"
    return value.split(".")[0].replace("T", " ")


def _lookup_group(storage, run_id: str):
    """Recover this run's selected group letter from its own event_log
    narrative (every implemented workflow logs "Group selected: <group>"
    before touching hardware -- see test.py's _run_monitor_battery()/
    _run_monitor_battery_scan()/_run_charge_or_discharge()). Returns None
    if not found (e.g. a future workflow that doesn't log this yet)."""
    events = storage.get_recent_events(run_id=run_id, limit=_GROUP_LOOKUP_EVENT_LIMIT)
    for event in events:
        message = event.get("message") or ""
        if message.startswith(_GROUP_EVENT_PREFIX):
            return message[len(_GROUP_EVENT_PREFIX):].strip()
    return None


def _resolve_sample_count(run: dict, storage):
    """run_summary.sample_count is populated by Monitor Battery/Monitor
    Battery Scan today; Charge/Discharge Battery don't track a sample
    counter (see docs -- statistics enrichment is a later task). Fall back
    to counting this run's `measurements` rows so every test_type still
    shows a real number instead of an avoidable "N/A"."""
    if run.get("sample_count") is not None:
        return run["sample_count"]
    if storage is None:
        return None
    return len(storage.get_measurements(run_id=run["run_id"]))


def build_scan_relay_rows(storage, run_id: str) -> list:
    """
    Per-relay voltage statistics for Monitor Battery Scan -- queried from
    `measurements` (phase_detail == "MONITORING", the dwell samples), since
    run_summary has no per-relay columns. No new runtime data store: this
    reads the exact rows MonitorBatteryScanSequence already wrote via
    record_measurement() during the run.

    Returns a list of dicts sorted by relay number:
    {"relay": int, "samples": int, "first": float, "last": float,
     "min": float, "max": float, "avg": float}
    """
    by_relay = {}
    for row in storage.get_measurements(run_id=run_id):
        if row.get("phase_detail") != "MONITORING":
            continue
        voltage = row.get("voltage_v")
        if voltage is None:
            continue
        relay = row.get("relay")
        stats = by_relay.setdefault(relay, {
            "relay": relay, "samples": 0,
            "first": None, "last": None, "min": None, "max": None, "_sum": 0.0,
        })
        stats["samples"] += 1
        if stats["first"] is None:
            stats["first"] = voltage
        stats["last"] = voltage
        stats["min"] = voltage if stats["min"] is None else min(stats["min"], voltage)
        stats["max"] = voltage if stats["max"] is None else max(stats["max"], voltage)
        stats["_sum"] += voltage

    rows = []
    for relay in sorted(by_relay, key=lambda r: (r is None, r)):
        stats = by_relay[relay]
        stats["avg"] = (stats["_sum"] / stats["samples"]) if stats["samples"] else None
        del stats["_sum"]
        rows.append(stats)
    return rows


def _print_monitor_section(run: dict):
    print("First Voltage  : " + _fmt_volts(run.get("start_voltage")))
    print("Last Voltage   : " + _fmt_volts(run.get("end_voltage")))
    print("Minimum        : " + _fmt_volts(run.get("min_voltage")))
    print("Maximum        : " + _fmt_volts(run.get("max_voltage")))
    print("Average        : " + _fmt_volts(run.get("average_voltage")))


def _print_scan_section(run: dict, storage):
    rows = build_scan_relay_rows(storage, run["run_id"]) if storage is not None else []
    print(f"{'Relay':<8}{'Samples':<10}{'First(V)':<11}{'Last(V)':<11}{'Min(V)':<11}{'Max(V)':<11}{'Avg(V)':<11}")
    print("-" * 66)
    if not rows:
        print("(no per-relay data available)")
        return
    for row in rows:
        print(
            f"{_fmt(row['relay']):<8}{_fmt(row['samples']):<10}"
            f"{_fmt(row['first'], '.3f'):<11}{_fmt(row['last'], '.3f'):<11}"
            f"{_fmt(row['min'], '.3f'):<11}{_fmt(row['max'], '.3f'):<11}"
            f"{_fmt(row['avg'], '.3f'):<11}"
        )


def _print_charge_or_discharge_section(run: dict, *, is_charge: bool):
    # Start/End voltage: populated for every real run since
    # docs/architecture.md Section 55 (ChargeSequence/DischargeSequence's
    # own _ChargeDischargeStats.initial_voltage_v/final_voltage_v, folded
    # into finish_run_summary() via _charge_diagnostic_fields()/
    # _discharge_diagnostic_fields(), refreshed at the moment of an
    # abnormal exit by run_guarded()'s own fresh reading) -- N/A only for
    # a run that predates that change, or one that failed before any
    # voltage was ever measured (e.g. a RelayError closing the relay).
    # Min/Max voltage and the actual commanded charge/discharge current are
    # NOT accumulated for these workflows (no equivalent of Monitor
    # Battery's full _VoltageStats exists here, and commanded current has
    # no run_summary column) -- still N/A, a deliberately separate,
    # not-yet-scoped enhancement, not an oversight.
    print("Start Voltage  : " + _fmt_volts(run.get("start_voltage")))
    print("End Voltage    : " + _fmt_volts(run.get("end_voltage")))
    if is_charge:
        print("Maximum Voltage: " + _fmt_volts(run.get("max_voltage")))
        print("Charge Current : " + _fmt(None))
        print("End Of Charge  : " + _fmt(run.get("stop_reason")))
        print("Energy Delivered: " + _fmt(run.get("energy_wh")))
    else:
        print("Minimum Voltage: " + _fmt_volts(run.get("min_voltage")))
        print("Discharge Current: " + _fmt(None))
        print("End Of Discharge: " + _fmt(run.get("stop_reason")))
        print("Energy Removed : " + _fmt(run.get("energy_wh")))

    # Test Mode post-run diagnostic classification (informational only --
    # see test_control/battery_diagnostics.py). Never affects Result/
    # stop_reason above; NULL for a run predating this column or for
    # NORMAL_CHARGE_BEHAVIOR/NORMAL_DISCHARGE_BEHAVIOR's empty message.
    analysis_result = run.get("analysis_result")
    if analysis_result:
        from test_control.battery_diagnostics import message_for
        message = message_for(analysis_result, mode="charge" if is_charge else "discharge")
        print(f"Diagnostic     : {analysis_result}" + (f" -- {message}" if message else ""))


def _print_cycle_section():
    # Cycle Battery is not implemented yet (see test.py::run_main_test()) --
    # placeholder only, so this renderer already has a slot ready the
    # moment a CycleSequence exists.
    print("Cycle Battery workflow is not yet implemented -- no cycle data available.")


def render_run_summary(run: dict, storage=None) -> None:
    """
    Print the common Run Summary block, then a test_type-specific section,
    for `run` (a run_summary row dict, e.g. from DataStorage.
    get_run_summary()/get_last_run_summary()). `storage` is optional and
    is used ONLY for two lookups that aren't columns on `run` itself: the
    selected group (event_log) and, for Monitor Battery Scan, the
    per-relay table (measurements) -- pass it whenever available so those
    sections render fully instead of falling back to "N/A"/"no data".

    This is the ONLY summary-printing function in the codebase -- called
    identically after every workflow's safe shutdown, from the "Last Test
    Summary" menu entry, and from Database Tools' "View Latest Run" screen.
    """
    test_type = run.get("test_type")
    group = run.get("group_name") or (_lookup_group(storage, run["run_id"]) if storage is not None else None)

    print("=" * 60)
    print("Run Summary")
    print("=" * 60)
    print()
    print(f"Run Number     : {_fmt(run.get('id'))}")
    print(f"Test Type      : {display_test_type(test_type)}")
    print(f"Result         : {_fmt(run.get('result'))}")
    print()
    print(f"Group          : {_fmt(group)}")
    print(f"Battery Type   : {_fmt(run.get('battery_type'))}")
    print()
    print(f"Relay Matrix   : {_fmt(run.get('relay_matrix_name'))}")
    print(f"DMM            : {_fmt(run.get('dmm_name'))}")
    print(f"SMU            : {_fmt(run.get('smu_name'))}")
    print(f"DAQ            : {_fmt(run.get('daq_name'))}")
    print()
    print(f"Start Time     : {_format_timestamp(run.get('start_time'))}")
    print(f"End Time       : {_format_timestamp(run.get('end_time'))}")
    print(f"Duration       : {_format_duration(run.get('duration_s'))}")
    print()
    print(f"Samples        : {_fmt(_resolve_sample_count(run, storage))}")
    print()
    print("=" * 60)

    if test_type == "monitor":
        _print_monitor_section(run)
    elif test_type == "monitor_scan":
        _print_scan_section(run, storage)
    elif test_type == "charge_battery":
        _print_charge_or_discharge_section(run, is_charge=True)
    elif test_type == "discharge_battery":
        _print_charge_or_discharge_section(run, is_charge=False)
    elif test_type == "cycle_battery":
        _print_cycle_section()
    else:
        print(f"(no summary section defined for test_type={_fmt(test_type)})")

    print("=" * 60)
