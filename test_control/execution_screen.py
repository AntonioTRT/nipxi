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

    # Recent measurements -- list of dicts, same row shape whether it came
    # from an in-memory buffer (live) or data/storage.py::DataStorage.
    # get_measurements() (historical). Required from day one -- not an
    # add-on -- see the module docstring.
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
                  state=None, phase_detail=None, smu_voltage=None, smu_current=None,
                  dmm_voltage=None, battery_voltage=None, battery_current=None,
                  battery_temp=None, capacity=None, energy=None, cycle_count=None,
                  battery_type=None, group=None, position_in_group=None,
                  relay_state=None, daq_channel_0_raw=None, current_step=None,
                  scan_progress=None, dwell_progress=None, dwell_remaining_s=None,
                  recent_measurements=None, recent_events=None) -> "ExecutionFrame":
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
            smu_voltage=smu_voltage, smu_current=smu_current, dmm_voltage=dmm_voltage,
            battery_voltage=battery_voltage, battery_current=battery_current,
            battery_temp=battery_temp,
            capacity=capacity, energy=energy, cycle_count=cycle_count,
            battery_type=battery_type, group=group, position_in_group=position_in_group,
            relay_state=relay_state, daq_channel_0_raw=daq_channel_0_raw,
            current_step=current_step, scan_progress=scan_progress,
            dwell_progress=dwell_progress, dwell_remaining_s=dwell_remaining_s,
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
        measurements = storage.get_measurements(run_id=resolved_run_id)
        latest = measurements[-1] if measurements else {}
        recent = measurements[-recent_limit:] if measurements else []
        events = storage.get_recent_events(run_id=resolved_run_id, limit=recent_limit)

        return cls(
            run_number=run_summary.get("id"),
            run_id=resolved_run_id,
            test_type=run_summary.get("test_type"),
            channel=latest.get("channel"),
            relay=latest.get("relay"),
            state=run_summary.get("stop_reason"),
            phase_detail=latest.get("phase_detail"),
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


def render_execution_frame(frame: ExecutionFrame) -> None:
    """
    Render `frame` to the console. Used identically by live execution
    (Proto Test / future battery cycles) and historical replay (UI Preview
    Test / Historical Results Viewer) -- this function never inspects how
    `frame` was built, only what its fields contain.
    """
    print("=" * 60)
    print("Current Execution")
    print("=" * 60)
    print(f"Run Number     : {_fmt(frame.run_number)}")
    print(f"Run ID         : {_fmt(frame.run_id)}")
    print(f"Test Type      : {_fmt(frame.test_type)}")
    print()
    print(f"DUT / Channel  : {_fmt(frame.channel)}")
    print(f"Relay          : {_fmt(frame.relay)}")
    print()
    print(f"State          : {_fmt(frame.state)}")
    print(f"Phase Detail   : {_fmt(frame.phase_detail)}")
    print()
    if frame.battery_type is not None or frame.group is not None or frame.scan_progress is not None:
        print("Scan Context")
        print("-" * 60)
        print(f"Battery Type   : {_fmt(frame.battery_type)}")
        print(f"Group          : {_fmt(frame.group)}")
        print(f"Position       : {_fmt(frame.position_in_group)}")
        print(f"Relay State    : {_fmt(frame.relay_state)}")
        print(f"Current Step   : {_fmt(frame.current_step)}")
        print(f"Scan Progress  : {_fmt(frame.scan_progress)}")
        print(f"Dwell Progress : {_fmt(frame.dwell_progress)}")
        print(f"Remaining Time : {_fmt(frame.dwell_remaining_s)}" + ("" if frame.dwell_remaining_s is None else " s"))
        print()
    print("Current Measurements")
    print("-" * 60)
    print(f"SMU Voltage    : {_fmt_volts(frame.smu_voltage)}")
    print(f"SMU Current    : {_fmt_amps(frame.smu_current)}")
    print(f"DMM Voltage    : {_fmt_volts(frame.dmm_voltage)}")
    print(f"Battery Voltage: {_fmt_volts(frame.battery_voltage)}")
    print(f"Battery Current: {_fmt_amps(frame.battery_current)}")
    print(f"Battery Temp   : {_fmt(frame.battery_temp)}" + ("" if frame.battery_temp is None else " C"))
    print(f"DAQ Ch0 Raw    : {_fmt(frame.daq_channel_0_raw, '.6f')}" + ("" if frame.daq_channel_0_raw is None else " V (raw)"))
    print()
    print("Battery Metrics")
    print("-" * 60)
    print(f"Capacity       : {_fmt(frame.capacity)}")
    print(f"Energy         : {_fmt(frame.energy)}")
    print(f"Cycle Count    : {_fmt(frame.cycle_count)}")
    print()

    print("=" * 60)
    print("Recent Measurements")
    print("=" * 60)
    if frame.recent_measurements:
        print(f"{'DUT':<5}{'Relay':<7}{'SMU(V)':<12}{'DMM(V)':<12}")
        print("-" * 36)
        for m in frame.recent_measurements:
            smu_v = m.get("smu_measured_v")
            dmm_v = m.get("dmm_measured_v")
            print(
                f"{_fmt(m.get('channel')):<5}"
                f"{_fmt(m.get('relay')):<7}"
                f"{(f'{smu_v:.6f}' if smu_v is not None else 'N/A'):<12}"
                f"{(f'{dmm_v:.6f}' if dmm_v is not None else 'N/A'):<12}"
            )
    else:
        print("(none)")
    print()

    print("=" * 60)
    print("Recent Events")
    print("=" * 60)
    if frame.recent_events:
        for e in frame.recent_events:
            ts = e.get("timestamp") or ""
            ts_short = ts.split("T")[-1][:8] if "T" in ts else ts
            print(f"{ts_short}  {e.get('message', '')}")
    else:
        print("(none)")
    print("=" * 60)
