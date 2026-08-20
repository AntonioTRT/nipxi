"""
One-time NTC group snapshot -- pure hardware-reading logic extracted
from test.py so it has exactly one implementation shared by NTC Group
Scan, the pre-operation group NTC pre-check (Monitor/Charge/Discharge
Battery), and a future worker_runtime.py (see docs/architecture.md
"Preparation Phase: Six Resolved Decisions Before worker_runtime.py").

Already had no `print()`/`input()` before this extraction -- it only
ever used `storage.log_event()`/`storage.record_measurement()` -- so
this move changes its *location*, not its behavior. It stays out of
test.py specifically so a future caller does not have to import test.py
(and inherit test.py's own `logging.disable(logging.CRITICAL)` import-
time side effect -- see tests/_logging_helpers.py) just to reuse this.
"""

from __future__ import annotations

from config import devices as dev_cfg
from hardware.temperature import NTCPresence, classify_ntc_presence, ntc_voltage_to_celsius
from utils.errors import DAQError


def ntc_group_snapshot(storage, daq, group: str, size: int, source: str,
                        phase_detail: str = None, log_summary: bool = False) -> list:
    """
    One-time NTC read across every position 1..size in `group`, via `daq`
    (an already-connected DAQ -- the group's resolved "ntc_daq", or its
    "daq" fallback -- see config/devices.py::hardware_for_group()). NTC
    channels are independent per-position DAQ analog inputs
    (BATTERY_GROUPS[group]["positions"][...]["daq_ntc_ch"]), not routed
    through the relay matrix -- this never touches a relay, the SMU, or the
    PMU, so it's safe to call before any of those are ever engaged.

    Records one measurements row per position (test_type=`source`,
    phase_detail=`phase_detail` if given, else the presence value itself --
    NTC Group Scan's original, unchanged behavior) and, if `log_summary`
    is True, one event_log line per position summarizing presence/
    temperature (NTC Group Scan itself does not log a summary line for a
    normal PRESENT/ABSENT reading, only for the two failure cases below --
    unchanged; callers that need per-position traceability, e.g. a
    pre-operation group NTC pre-check, opt in explicitly).

    Returns a list of {"position", "channel", "presence", "temp_c",
    "readable"} dicts in position order. `readable` is True only when a
    real ADC value was obtained and classified -- False for both "no
    daq_ntc_ch configured" (a config gap) and a DAQError on the read
    itself (a DAQ connectivity problem). Both still record `presence` as
    FAULT (informative -- "no reliable reading available") but callers
    that gate an operation on presence must check `readable` too: a DAQ
    comms hiccup is an infrastructure problem, not a signal that the
    battery/sensor at that position is actually faulted, and must not be
    treated the same as a real, successfully-read ABSENT/FAULT signal --
    the active-monitoring loops (Monitor Battery/Charge/Discharge's own
    sampling loops) already degrade gracefully on the identical DAQError,
    never aborting the run over it; this pre-check must not be stricter
    than the loop it precedes.

    No-op (returns []) if `daq` is None -- a group with no NTC hardware
    assigned behaves exactly as if this were never called.

    Shared by NTC Group Scan and the pre-operation group NTC pre-check
    (Charge/Discharge/Monitor Battery) so there is exactly one place this
    scan loop is implemented -- not two independent copies of the same logic.
    """
    if daq is None:
        return []

    results = []
    for position in range(1, size + 1):
        ch_cfg = dev_cfg.BATTERY_GROUPS[group]["positions"].get(position)
        ntc_ch = ch_cfg["daq_ntc_ch"] if ch_cfg else None
        channel = position

        voltage_v = None
        temp_c = None
        readable = True
        if ntc_ch is None:
            presence = NTCPresence.FAULT
            readable = False
            storage.log_event(level="WARNING", source=source, channel=channel,
                               message=f"Position {position}: no daq_ntc_ch configured")
        else:
            try:
                voltage_v = daq.read_channel(ntc_ch)
                presence = classify_ntc_presence(voltage_v)
                if presence == NTCPresence.PRESENT:
                    temp_c = ntc_voltage_to_celsius(voltage_v)
            except DAQError as e:
                presence = NTCPresence.FAULT
                readable = False
                storage.log_event(level="WARNING", source=source, channel=channel,
                                   message=f"Position {position}: DAQ read failed -- {e}")

        storage.record_measurement(
            test_type=source, channel=channel,
            phase_detail=phase_detail if phase_detail is not None else presence,
            voltage_v=voltage_v, temp_c=temp_c,
            group_name=group, position_in_group=position,
        )
        if log_summary:
            temp_label = f" -- {temp_c:.1f} C" if temp_c is not None else ""
            storage.log_event(
                level="INFO" if presence == NTCPresence.PRESENT else "WARNING",
                source=source, channel=channel,
                message=f"NTC snapshot -- Position {position} (channel {channel}): {presence}{temp_label}",
            )
        results.append({
            "position": position, "channel": channel, "presence": presence,
            "temp_c": temp_c, "readable": readable,
        })
    return results
