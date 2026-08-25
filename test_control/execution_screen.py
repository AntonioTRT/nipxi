"""
Shared runtime UI architecture (Milestone II).

ExecutionFrame is the canonical data model for every execution screen in
this project -- Proto Test Execution, future Battery Charge/Discharge/cycle
execution, Historical Replay (Historical Results Viewer), and UI Preview
Test all build an ExecutionFrame and hand it to the one shared
render_execution_frame(). No caller prints its own execution screen.

Why ExecutionFrame exists:
    Before this, ProtoTestSequence hand-rolled its own print() calls
    inline (test_control/proto_test_sequence.py). A second, independent
    implementation would have appeared the moment Battery Charge/Discharge
    needed a screen too, and the two would have drifted the first time
    either one's formatting changed alone. ExecutionFrame is the one place
    "what does an execution screen show" is defined; every caller only
    ever answers "what are the values right now", never "how do I print
    them."

Why BOTH constructors exist from day one (from_live() and
from_database()):
    ExecutionFrame.from_live() builds a frame from in-memory hardware
    readings during a real run. ExecutionFrame.from_database() builds the
    IDENTICAL frame shape from historical rows (run_summary/measurements/
    event_log, via data/storage.py::DataStorage) for UI Preview Test and
    the Historical Results Viewer. Building only one and bolting on the
    other later is exactly the mechanism by which a live screen and a
    replayed screen quietly drift apart -- every field the renderer can
    show must be reachable from both a live run and a historical read from
    the very first version of this module, not added to one constructor
    at a time.

Renderer:
    render_execution_frame(frame) is the ONLY function anywhere in this
    codebase that prints an execution screen. It does not know or care
    whether `frame` came from from_live() or from_database() -- that is
    the whole point. Any field whose value is None renders as "N/A".
    Terminal-friendly only: plain print(), no curses, no TUI framework.
"""

from dataclasses import dataclass, field

from utils.stop_reason import StopReason

# How many of the most recent measurement rows the "Recent Measurements"
# panel shows -- see data/storage.py::DataStorage.get_measurements()'s
# `recent_limit` param (queried directly via SQL, not fetched-then-sliced).
# A separate, single-row "Initial" reading (data/storage.py::
# get_first_measurement()) is always shown alongside this, so the very
# first sample of a run stays visible even once it has scrolled out of this
# bounded window during a multi-hour run.
RECENT_MEASUREMENTS_DISPLAY_LIMIT = 5

# How many of the most recent event_log rows the "Recent Events" panel
# shows -- see data/storage.py::DataStorage.get_recent_events()'s `limit`
# param (default 20; BatteryOperationSequence._render_frame() now passes
# this constant explicitly instead of taking that default). Matches
# RECENT_MEASUREMENTS_DISPLAY_LIMIT's value for a consistent "last 5"
# convention across both panels -- a separate constant since the two
# panels are conceptually independent bounds that could diverge later,
# not because the numbers need to differ today. Reduced from the
# previous unbounded-in-practice 20 specifically for long validation
# campaigns (docs/architecture.md "Current Execution Screen: Second
# Compactness Pass") -- 20 events routinely pushed the whole screen past
# a standard terminal height with no diagnostic benefit over the last 5.
RECENT_EVENTS_DISPLAY_LIMIT = 5


@dataclass
class ExecutionFrame:
    """
    Canonical execution-screen data model. Every field is optional (default
    None / empty list) -- a test type that doesn't support a given field
    (e.g. capacity/energy/cycle_count for Proto Test) simply never sets it,
    and render_execution_frame() displays "N/A" for it. Nothing in this
    dataclass or its constructors ever writes the string "N/A" -- that
    substitution happens in exactly one place, the renderer.
    """

    # Run information
    run_number: int = None
    run_id: str = None
    test_type: str = None

    # Execution context
    channel: int = None       # canonical DUT identifier
    relay: int = None         # physical routing path used (provenance)
    state: str = None         # coarse status -- ACTIVE/COMPLETED/FAILED/...
    phase_detail: str = None  # fine-grained phase -- ACTIVATING/DWELLING/CC_CHARGE/...

    # Elapsed wall-clock time since this run started, in seconds -- None
    # for a test type/constructor that doesn't track it (rendered "N/A").
    # Also drives the lightweight "RUNNING ..." activity indicator (see
    # _running_indicator() below) -- no separate tick counter needed.
    elapsed_s: float = None

    # Current measurements -- SMU/DMM (Proto Test / future Charge/Discharge
    # sourcing) and battery/DAQ (Monitor Battery -- a real DAQ.read_channel()
    # reading of the battery itself, no SMU sourcing involved). Kept as
    # distinct fields, not reused/overloaded, since they observe genuinely
    # different signals -- a test type populates whichever set applies and
    # leaves the other at None/"N/A".
    smu_voltage: float = None
    smu_current: float = None
    dmm_voltage: float = None
    battery_voltage: float = None
    battery_current: float = None
    battery_temp: float = None

    # Active charge/discharge timeout setpoints (config/devices.py::
    # BATTERY_GROUPS[group]["test_setpoints"], falling back to
    # Settings.CHARGE_TIMEOUT_S/DISCHARGE_TIMEOUT_S -- see
    # ChargeSequence/DischargeSequence's own resolution of these same
    # values). Display-only: this frame never enforces a timeout itself,
    # it only shows whichever value the sequence is actually using, so
    # an operator can see the effective validation-timeout override
    # (e.g. B1's temporary 300s/600s) without checking config/devices.py.
    # None (renders "N/A") for any test type without a timeout concept
    # (Monitor Battery, Proto Test) -- see render_execution_frame()'s
    # conditional gate on this pair.
    charge_timeout_s: float = None
    discharge_timeout_s: float = None

    # Active DMM measurement route -- "MATRIX_NAME CHn" when this run's
    # sense_channel is routed through a sense-routing relay matrix (see
    # config/devices.py::SENSE_ROUTING and hardware/sense_router.py; B1
    # is the only group configured this way today), or None for a direct
    # DMM read (every other group, and every test type that doesn't use
    # BatteryOperationSequence._render_frame() at all). Computed by
    # _render_frame() itself, not passed in by ChargeSequence/
    # DischargeSequence/MonitorBatterySequence -- this is resolved once,
    # in the one shared base-class method, from the sense_channel/
    # sense_router each sequence already stores, rather than duplicated
    # per subclass.
    dmm_route: str = None

    # NTC block (Charge/Discharge/future Cycle -- see docs/architecture.md
    # Section 58) -- which physical device/channel this run's temperature
    # reading actually came from, for the active battery position only.
    # ntc_device is the config/devices.py nickname (e.g. "MAIN_DAQ");
    # ntc_resource is that device's own resource string (e.g. "PXI1Slot2");
    # ntc_channel is the actual per-position analog input path used (e.g.
    # "Dev1/ai0"); ntc_status is a hardware/temperature.py::NTCPresence
    # value ("present"/"absent"/"fault"). battery_temp above already
    # carries the converted Celsius reading -- not duplicated here.
    ntc_device: str = None
    ntc_resource: str = None
    ntc_channel: str = None
    ntc_status: str = None

    # Battery metrics -- N/A for Proto Test, populated once Battery
    # Charge/Discharge/cycle execution computes them
    capacity: float = None
    energy: float = None
    cycle_count: int = None

    # Monitor Battery Scan (relay/DMM/DAQ path validation, no charging) --
    # see test_control/monitor_battery_scan_sequence.py. N/A for every other
    # test type.
    battery_type: str = None
    group: str = None
    position_in_group: int = None
    relay_state: str = None       # "OPEN"/"CLOSED", the commanded+verified state
    daq_channel_0_raw: float = None
    current_step: str = None
    scan_progress: str = None     # e.g. "3/8"
    dwell_progress: str = None    # e.g. "15/30 s", during the CLOSED monitoring dwell
    dwell_remaining_s: float = None

    # Initial measurement -- the run's very first recorded row (data/
    # storage.py::DataStorage.get_first_measurement()), shown once,
    # separately from recent_measurements below, so it stays visible for
    # the whole run even after later samples have pushed it out of the
    # bounded recent-measurements window. None if no measurement has been
    # recorded yet (or ever, e.g. a run that failed before its first
    # sample).
    initial_measurement: dict = None

    # Recent measurements -- list of dicts, same row shape whether it came
    # from an in-memory buffer (live) or data/storage.py::DataStorage.
    # get_measurements() (historical). Required from day one -- not an
    # add-on -- see the module docstring. Bounded to
    # RECENT_MEASUREMENTS_DISPLAY_LIMIT rows (see that constant and
    # get_measurements()'s `recent_limit` param) -- NOT the full run
    # history; a caller that genuinely needs the full history (CSV export,
    # offline analysis) reads data/storage.py directly, never through this
    # display-oriented frame.
    recent_measurements: list = field(default_factory=list)

    # Recent events -- list of dicts, same shape as event_log rows
    # (data/storage.py::DataStorage.get_recent_events()) whether the source
    # is a live in-memory event buffer or a historical database read.
    recent_events: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_live(cls, *, run_id, test_type, channel, run_number=None, relay=None,
                  state=None, phase_detail=None, elapsed_s=None, smu_voltage=None, smu_current=None,
                  dmm_voltage=None, battery_voltage=None, battery_current=None,
                  battery_temp=None, charge_timeout_s=None, discharge_timeout_s=None,
                  dmm_route=None, ntc_device=None, ntc_resource=None, ntc_channel=None,
                  ntc_status=None, capacity=None, energy=None, cycle_count=None,
                  battery_type=None, group=None, position_in_group=None,
                  relay_state=None, daq_channel_0_raw=None, current_step=None,
                  scan_progress=None, dwell_progress=None, dwell_remaining_s=None,
                  initial_measurement=None, recent_measurements=None, recent_events=None) -> "ExecutionFrame":
        """
        Build a frame from live, in-memory values during a real execution
        (Proto Test Execution / Monitor Battery / Monitor Battery Scan
        today; future Battery Charge/Discharge/cycle execution the same
        way). Callers pass exactly the values they have -- any field not
        applicable to the current test type is left at its None default,
        never invented.
        """
        return cls(
            run_number=run_number, run_id=run_id, test_type=test_type,
            channel=channel, relay=relay, state=state, phase_detail=phase_detail,
            elapsed_s=elapsed_s,
            smu_voltage=smu_voltage, smu_current=smu_current, dmm_voltage=dmm_voltage,
            battery_voltage=battery_voltage, battery_current=battery_current,
            battery_temp=battery_temp,
            charge_timeout_s=charge_timeout_s, discharge_timeout_s=discharge_timeout_s,
            dmm_route=dmm_route,
            ntc_device=ntc_device, ntc_resource=ntc_resource, ntc_channel=ntc_channel,
            ntc_status=ntc_status,
            capacity=capacity, energy=energy, cycle_count=cycle_count,
            battery_type=battery_type, group=group, position_in_group=position_in_group,
            relay_state=relay_state, daq_channel_0_raw=daq_channel_0_raw,
            current_step=current_step, scan_progress=scan_progress,
            dwell_progress=dwell_progress, dwell_remaining_s=dwell_remaining_s,
            initial_measurement=initial_measurement,
            recent_measurements=list(recent_measurements) if recent_measurements else [],
            recent_events=list(recent_events) if recent_events else [],
        )

    @classmethod
    def from_database(cls, storage, run_id: str = None, recent_limit: int = 10) -> "ExecutionFrame":
        """
        Build the IDENTICAL frame shape by reading history back from
        `storage` (a data/storage.py::DataStorage instance) -- used by
        UI Preview Test and the Historical Results Viewer. `run_id`
        defaults to the most recent run_summary row's run_id when omitted
        ("load the latest run").

        Reads only -- calls exclusively DataStorage.get_run_summary()/
        get_last_run_summary()/get_measurements()/get_recent_events().
        Never imports or references hardware/*.py, HardwareManager, or any
        live driver -- this constructor has no way to touch hardware even
        by accident, which is what makes UI Preview Test's "no hardware
        access" guarantee structural rather than a runtime check.

        Returns None if no run_summary row exists yet (nothing to replay).

        `state` is populated from run_summary.stop_reason -- the same
        StopReason vocabulary (COMPLETED/FAILED/SAFETY_VIOLATION/CANCELLED)
        used by station_state's `state` column for a live run; a
        completed historical run has no "ACTIVE" moment left to replay,
        only its final outcome. `phase_detail` comes from the last
        measurement row instead, since that IS captured per-row and
        survives into history (see docs/architecture.md's Execution UI
        Architecture section for why `state` and `phase_detail` are
        deliberately two different concepts with two different lifetimes).
        """
        run_summary = storage.get_run_summary(run_id) if run_id else storage.get_last_run_summary()
        if run_summary is None:
            return None

        resolved_run_id = run_summary["run_id"]
        # Bounded fetch (see RECENT_MEASUREMENTS_DISPLAY_LIMIT / data/
        # storage.py::get_measurements()'s `recent_limit`) -- a historical
        # replay of a multi-hour run must not re-pull its entire
        # measurement history just to find the latest row either.
        recent = storage.get_measurements(run_id=resolved_run_id, recent_limit=recent_limit)
        latest = recent[-1] if recent else {}
        initial_measurement = storage.get_first_measurement(run_id=resolved_run_id)
        events = storage.get_recent_events(run_id=resolved_run_id, limit=recent_limit)

        return cls(
            run_number=run_summary.get("id"),
            run_id=resolved_run_id,
            test_type=run_summary.get("test_type"),
            channel=latest.get("channel"),
            relay=latest.get("relay"),
            state=run_summary.get("stop_reason"),
            phase_detail=latest.get("phase_detail"),
            elapsed_s=run_summary.get("duration_s"),
            smu_voltage=latest.get("smu_measured_v"),
            smu_current=latest.get("smu_measured_i"),
            dmm_voltage=latest.get("dmm_measured_v"),
            # battery_voltage/current/temp reuse the ORIGINAL, pre-Milestone-II
            # measurements columns (voltage_v/current_a/temp_c) -- the same
            # columns charge_cycle.py/discharge_cycle.py's record() calls
            # already write, and what Monitor Battery's DAQ.read_channel()
            # readings populate via record_measurement(test_type="monitor", ...).
            # No new measurements columns needed for this.
            battery_voltage=latest.get("voltage_v"),
            battery_current=latest.get("current_a"),
            battery_temp=latest.get("temp_c"),
            capacity=run_summary.get("capacity_ah"),
            energy=run_summary.get("energy_wh"),
            cycle_count=run_summary.get("cycle_count"),
            battery_type=run_summary.get("battery_type"),
            daq_channel_0_raw=latest.get("daq_channel_0_raw"),
            current_step=latest.get("phase_detail"),
            initial_measurement=initial_measurement,
            recent_measurements=recent,
            recent_events=events,
        )


# =============================================================================
# Renderer -- the ONLY function that prints an execution screen anywhere in
# this codebase. Terminal-friendly only: plain print(), no curses/TUI.
# =============================================================================

def _fmt(value, spec: str = None) -> str:
    """None -> "N/A"; everything else formatted per `spec` if given, else str()."""
    if value is None:
        return "N/A"
    if spec is not None:
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            return str(value)
    return str(value)


def _fmt_volts(value) -> str:
    return "N/A" if value is None else f"{value:.6f} V"


def _fmt_amps(value) -> str:
    return "N/A" if value is None else f"{value:.6f} A"


def _fmt_temp(value) -> str:
    return "N/A" if value is None else f"{value:.1f} C"


def _fmt_timeout(value) -> str:
    return "N/A" if value is None else f"{int(value)} s"


def _fmt_dmm_route(value) -> str:
    """"DIRECT" (not "N/A") when no sense-routing matrix is in the DMM
    read path -- this is a real, valid, and today the MOST COMMON state
    (every group except B1), not a missing-data gap, so it must not read
    like one."""
    return "DIRECT" if value is None else value


def _active_timeout_s(frame: "ExecutionFrame"):
    """Which of charge_timeout_s/discharge_timeout_s applies to the
    CURRENT operation, per frame.test_type -- both are always populated
    together (see ExecutionFrame.charge_timeout_s's docstring: both come
    from the same test_setpoints dict regardless of which operation is
    active), only one is ever the countdown target."""
    if frame.test_type == "charge":
        return frame.charge_timeout_s
    if frame.test_type == "discharge":
        return frame.discharge_timeout_s
    return None


def _fmt_remaining(frame: "ExecutionFrame") -> str:
    """Countdown to the active timeout -- computed here, purely from
    fields ExecutionFrame already carries (elapsed_s, charge_timeout_s/
    discharge_timeout_s, test_type). No new plumbing into ChargeSequence/
    DischargeSequence was needed for this -- see docs/architecture.md
    "Current Execution Screen: Second Compactness Pass" for the review
    that confirmed this. Clamped at 0 rather than going negative once the
    timeout has actually elapsed (the sequence itself, not this display,
    is what raises NIPXITimeoutError at that point)."""
    timeout_s = _active_timeout_s(frame)
    if timeout_s is None or frame.elapsed_s is None:
        return "N/A"
    return _fmt_elapsed(max(0.0, timeout_s - frame.elapsed_s))


def _two_col(left: str, right: str, width: int = 33) -> str:
    """Pack two already-formatted "Label : value" strings onto one line,
    left column padded to a fixed width -- see docs/architecture.md
    "Current Execution Screen: Second Compactness Pass". A left value
    that overflows `width` just pushes the right column over on that one
    line rather than truncating anything -- no data is ever lost to
    fit a column."""
    return f"{left:<{width}}{right}"


def _fmt_elapsed(seconds) -> str:
    """"HH:MM:SS" (or "MM:SS" under an hour) -- REQUIREMENT 4's "Elapsed
    Time" line. "N/A" if the caller never threaded elapsed_s through (a
    test type/constructor that doesn't track it)."""
    if seconds is None:
        return "N/A"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# Operator-friendly status labels (REQUIREMENT 4's "Current Status" line) --
# "ACTIVE" is this codebase's own in-flight state string (not part of the
# StopReason vocabulary, which only covers how a run ENDED); every other
# key here is a real utils/stop_reason.py::StopReason value. A state not in
# this table (None, or some future value) falls back to the raw string (or
# "N/A") rather than crashing the renderer.
_STATUS_LABELS = {
    "ACTIVE": "RUNNING",
    StopReason.COMPLETED: "COMPLETED",
    StopReason.CANCELLED: "CANCELLED",
    StopReason.SAFETY_VIOLATION: "SAFETY STOP",
    StopReason.FAILED: "FAILED",
    StopReason.TIMEOUT: "TIMEOUT",
}


def _status_label(state) -> str:
    if state is None:
        return "N/A"
    return _STATUS_LABELS.get(state, state)


def _running_indicator(state, elapsed_s) -> str:
    """
    Lightweight activity indicator (REQUIREMENT 5) -- "RUNNING", "RUNNING .",
    "RUNNING ..", "RUNNING ...", cycling once per second of elapsed_s. No
    extra tick-counter state to track: every render call recomputes this
    purely from elapsed_s, which the run is already threading through for
    the "Elapsed Time" line above. Only animates while state == "ACTIVE" --
    a finished run just shows its final status label with no dots (nothing
    left to indicate as "alive").
    """
    label = _status_label(state)
    if state != "ACTIVE" or elapsed_s is None:
        return label
    dots = int(elapsed_s) % 4
    return f"{label} {'.' * dots}" if dots else label


def _clear_screen() -> None:
    """
    ANSI clear-screen + cursor-home, printed before every frame so a long
    run's execution screen refreshes in place instead of scrolling forever
    (REQUIREMENT 4's "endless scrolling list" complaint) -- still plain
    print(), no curses/TUI framework (see this module's own docstring
    constraint). Modern Windows terminals (Windows 10 1511+ conhost,
    Windows Terminal, PowerShell) interpret this natively; a legacy console
    without ANSI/VT processing enabled would show the raw escape sequence
    as visible characters instead of clearing -- a cosmetic degradation
    only, every other line still renders normally below it.
    """
    print("\033[H\033[J", end="")


def _fmt_measurement_row(m: dict) -> str:
    """One row of the Initial/Recent measurement tables -- timestamp, DMM
    voltage, SMU voltage, current, temperature. Always shows all five
    columns (never omits Temperature for a run type that doesn't have NTC
    wired yet) -- see the NTC hardware note in docs/architecture.md: once
    NTC readings are real, this column starts showing them with no
    layout change."""
    ts = m.get("timestamp") or ""
    ts_short = ts.split("T")[-1][:8] if "T" in ts else ts
    return (
        f"{ts_short:<10}"
        f"{_fmt_volts(m.get('dmm_measured_v')):<16}"
        f"{_fmt_volts(m.get('smu_measured_v')):<16}"
        f"{_fmt_amps(m.get('current_a')):<16}"
        f"{_fmt_temp(m.get('temp_c'))}"
    )


def render_execution_frame(frame: ExecutionFrame) -> None:
    """
    Render `frame` to the console. Used identically by live execution
    (Proto Test / Monitor Battery / Monitor Battery Scan / Charge/Discharge
    Battery) and historical replay (UI Preview Test / Historical Results
    Viewer) -- this function never inspects how `frame` was built, only
    what its fields contain.
    """
    _clear_screen()
    print("=" * 60)
    print("Current Execution")
    print("=" * 60)
    print(f"Run Number     : {_fmt(frame.run_number)}")
    print(f"Run ID         : {_fmt(frame.run_id)}")
    print(f"Test Type      : {_fmt(frame.test_type)}")
    print(f"Current Status : {_running_indicator(frame.state, frame.elapsed_s)}")
    print(_two_col(f"DUT / Channel  : {_fmt(frame.channel)}", f"Relay : {_fmt(frame.relay)}"))
    print(f"Phase Detail   : {_fmt(frame.phase_detail)}")
    print(_two_col(f"Elapsed : {_fmt_elapsed(frame.elapsed_s)}", f"Remaining : {_fmt_remaining(frame)}"))
    # Active charge/discharge timeout setpoints -- Charge/Discharge Battery
    # only (see ExecutionFrame.charge_timeout_s's own docstring); omitted
    # entirely, not shown as "N/A", for test types with no timeout concept
    # (Monitor Battery, Proto Test) so their screens stay exactly as
    # compact as before this feature existed.
    if frame.charge_timeout_s is not None or frame.discharge_timeout_s is not None:
        print(_two_col(f"Charge Timeout : {_fmt_timeout(frame.charge_timeout_s)}",
                        f"Discharge Timeout : {_fmt_timeout(frame.discharge_timeout_s)}"))
    if frame.battery_type is not None or frame.group is not None or frame.scan_progress is not None:
        print("-" * 60)
        print(f"Battery Type   : {_fmt(frame.battery_type)}")
        print(f"Group          : {_fmt(frame.group)}")
        print(f"Position       : {_fmt(frame.position_in_group)}")
        print(f"Relay State    : {_fmt(frame.relay_state)}")
        print(f"Current Step   : {_fmt(frame.current_step)}")
        print(f"Scan Progress  : {_fmt(frame.scan_progress)}")
        print(f"Dwell Progress : {_fmt(frame.dwell_progress)}")
        print(f"Remaining Time : {_fmt(frame.dwell_remaining_s)}" + ("" if frame.dwell_remaining_s is None else " s"))
    print("-" * 60)
    print(_two_col(f"SMU Voltage : {_fmt_volts(frame.smu_voltage)}", f"SMU Current : {_fmt_amps(frame.smu_current)}"))
    print(_two_col(f"DMM Voltage : {_fmt_volts(frame.dmm_voltage)}",
                    f"DMM Route : {_fmt_dmm_route(frame.dmm_route)}"))
    print(_two_col(f"Battery Voltage : {_fmt_volts(frame.battery_voltage)}",
                    f"Battery Temp : {_fmt_temp(frame.battery_temp)}"))
    # NTC channel/status, compacted onto one short line -- device/resource
    # dropped (operationally redundant once the channel is shown) and the
    # temperature VALUE itself is Battery Temp above, never repeated here.
    # Paired with DAQ Ch0 Raw (both are auxiliary/diagnostic fields) when
    # NTC applies; DAQ Ch0 Raw alone otherwise -- see docs/architecture.md
    # "Current Execution Screen: Second Compactness Pass".
    daq_line = (f"DAQ Ch0 Raw : {_fmt(frame.daq_channel_0_raw, '.6f')}"
                + ("" if frame.daq_channel_0_raw is None else " V (raw)"))
    if frame.ntc_device is not None or frame.ntc_channel is not None:
        status = frame.ntc_status.upper() if frame.ntc_status else "N/A"
        ntc_line = f"Temp Sensor : {_fmt(frame.ntc_channel)} ({status})"
        print(_two_col(ntc_line, daq_line))
    else:
        print(daq_line)
    print(f"Capacity: {_fmt(frame.capacity)} | Energy: {_fmt(frame.energy)} | Cycles: {_fmt(frame.cycle_count)}")

    print("=" * 60)
    print("Measurement History")
    print("=" * 60)
    header = f"{'Time':<10}{'DMM(V)':<16}{'SMU(V)':<16}{'Current(A)':<16}{'Temp':<10}"
    print("Initial")
    print(header)
    print("-" * len(header))
    if frame.initial_measurement:
        print(_fmt_measurement_row(frame.initial_measurement))
    else:
        print("(none)")
    print(f"Recent (last {RECENT_MEASUREMENTS_DISPLAY_LIMIT})")
    print(header)
    print("-" * len(header))
    if frame.recent_measurements:
        for m in frame.recent_measurements:
            print(_fmt_measurement_row(m))
    else:
        print("(none)")

    print("=" * 60)
    print(f"Recent Events (last {RECENT_EVENTS_DISPLAY_LIMIT})")
    print("=" * 60)
    if frame.recent_events:
        for e in frame.recent_events:
            ts = e.get("timestamp") or ""
            ts_short = ts.split("T")[-1][:8] if "T" in ts else ts
            print(f"{ts_short}  {e.get('message', '')}")
    else:
        print("(none)")
    print("=" * 60)
