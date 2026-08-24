"""
Combined Battery Presence + NTC Presence pre-check -- pure hardware-
reading + classification logic, extracted so it can be shared by every
workflow that gates test start on presence (Monitor Battery, Charge
Battery, Discharge Battery -- see test.py's call sites) without any of
them importing test.py itself (mirrors test_control/ntc_snapshot.py's own
extraction rationale exactly -- see that module's docstring).

Motivation (see docs/architecture.md "Battery Presence + NTC Presence
Diagnostics"): battery presence and NTC presence are two independent,
distinguishable failure modes -- an operator troubleshooting a
non-functional slot needs to know WHICH is missing, not just that
"something" failed the pre-check. Before this module existed, only NTC
presence was checked before a relay ever closed; battery presence was
never checked at all pre-test -- an absent battery on an NTC-present
position sailed through every existing pre-check, and was only ever
flagged retroactively (if at all) by test_control/battery_diagnostics.py's
POST-RUN POSSIBLY_EMPTY_POSITION classification, after a full charge/
discharge attempt had already run against nothing.

Order: Battery Presence Check -> NTC Presence Check -> combined
Start/Abort decision (per explicit design confirmation, and Q6 of the
review this was built from). This module performs both checks and
returns a structured result; the CALLER (test.py) still owns the actual
abort decision (record_execution_state()/finish_run_summary()/print()),
exactly as it already owns the existing NTC-only abort decision today --
this module only calls storage.log_event()/record_measurement(), the same
division of responsibility ntc_snapshot.py's own ntc_group_snapshot()
already uses.

Relay handling: measuring battery voltage requires the position's relay
to be closed (the DMM shares the same relay-selected bus as the SMU) --
unlike the NTC channel, which is an independent DAQ input never routed
through the relay matrix. This function closes the relay, takes the
reading, and ALWAYS reopens it before returning (whether the reading
succeeded, failed, or the battery reads absent) -- the caller's own
sequence (ChargeSequence/DischargeSequence/MonitorBatterySequence) closes
the relay again itself if/when it proceeds, exactly as it already does
today; this function never leaves the relay in a different state than it
found it. This is a real, accepted cost: one extra relay actuation + one
extra relay settle delay on EVERY run (even one that proceeds normally),
not only on an abort -- accepted explicitly when this check was added, in
exchange for a real pre-test battery-presence gate that did not exist
before.

The SMU's output is never enabled at this point in any caller (all
current call sites run this before hardware output is ever commanded) --
reusing the relay purely for a passive DMM read here carries no new
energization risk (hardware/dmm.py: "it only observes, it cannot
source/energize anything").

Reversed-polarity readings are deliberately NOT treated as an abort
reason by this check -- a reversed cell IS a physically present battery,
just backwards; that is a distinct diagnostic dimension (polarity, not
presence), already fully handled, unchanged, by
BatteryOperationSequence._check_battery_polarity() once the real sequence
starts (raises ReversePolarityError -> the existing, unchanged
SafetyViolationError -> emergency_stop() safety path). This function
never duplicates or races that check -- it only records the reversed
reading as an informational diagnostic note and lets the run proceed to
that existing check.

A DMM/NTC read failure (a comms problem, not a presence signal) degrades
gracefully -- never treated as "missing", matching the identical policy
ntc_group_snapshot() already applies to its own DAQError case: "this
pre-check must not be stricter than the loop it precedes."
"""

from __future__ import annotations

from hardware.temperature import NTCPresence
from test_control.battery_diagnostics import BatteryPresence, classify_battery_presence
from test_control.ntc_snapshot import ntc_group_snapshot

_BATTERY_LABELS = {
    BatteryPresence.PRESENT:  "PRESENT",
    BatteryPresence.ABSENT:   "ABSENT",
    BatteryPresence.REVERSED: "PRESENT (reversed polarity)",
    None:                     "UNKNOWN (read failed)",
}


def battery_and_ntc_presence_precheck(*, storage, dmm, relay, ntc_daq, group: str, size: int,
                                       position: int, channel: int, relay_address: int,
                                       source: str, measurement_test_type: str) -> dict:
    """
    Runs the combined pre-check for `position` (Group `group`, relay
    `relay_address`) and returns:

        {
            "ok": bool,                     # True iff nothing blocks test start
            "reasons": [str, ...],          # e.g. ["Battery Missing", "NTC Missing"] -- empty if ok
            "battery_presence": str | None,  # BatteryPresence.PRESENT/ABSENT/REVERSED, or None if unreadable
            "battery_readable": bool,        # False only on a DMM read failure
            "battery_voltage_v": float | None,
            "ntc_presence": str | None,      # NTCPresence.* for the selected position, or None (no NTC hardware)
            "ntc_readable": bool,
            "station_fault": bool,           # True iff the SELECTED position's NTC read
                                              # failed with a real DAQError (test_control/
                                              # ntc_snapshot.py's daq_comm_failure) -- a
                                              # test-station equipment problem, not a
                                              # per-position config gap. See docs/
                                              # architecture.md "Group -> ALL Fault
                                              # Classification Policy". Never True merely
                                              # because no daq_ntc_ch is configured for
                                              # this position -- that is ntc_readable=False,
                                              # station_fault=False (a benign config gap,
                                              # handled as a slot-level condition, not an
                                              # abort trigger).
        }

    Never raises. `source` is the workflow's own event_log source (e.g.
    "charge_battery") -- used for this function's own Presence-check/
    TEST ABORTED lines, matching the existing abort-message convention.
    `measurement_test_type` is the shorter per-measurement test_type (e.g.
    "charge") -- passed through unchanged to ntc_group_snapshot(), exactly
    as test.py's pre-existing NTC-only pre-check already did.
    """
    battery_voltage_v = None
    battery_presence = None
    battery_readable = True

    relay.close(relay_address)
    try:
        battery_voltage_v = dmm.measure_dc_voltage()
        battery_presence = classify_battery_presence(battery_voltage_v)
    except Exception as e:
        battery_readable = False
        storage.log_event(
            level="WARNING", source=source, channel=channel, relay=relay_address,
            message=f"Battery presence check: DMM read failed -- {e}",
        )
    finally:
        relay.open(relay_address)

    storage.record_measurement(
        test_type=measurement_test_type, channel=channel, relay=relay_address,
        phase_detail="BATTERY_PRECHECK", voltage_v=battery_voltage_v,
        group_name=group, position_in_group=position,
    )

    ntc_snapshot = ntc_group_snapshot(
        storage, ntc_daq, group, size, source=measurement_test_type,
        phase_detail="NTC_PRECHECK", log_summary=True,
    )
    target_ntc = next((r for r in ntc_snapshot if r["position"] == position), None)
    ntc_presence = target_ntc["presence"] if target_ntc is not None else None
    ntc_readable = target_ntc["readable"] if target_ntc is not None else True
    station_fault = target_ntc["daq_comm_failure"] if target_ntc is not None else False

    reasons = []
    if battery_readable and battery_presence == BatteryPresence.ABSENT:
        reasons.append("Battery Missing")
    if battery_readable and battery_presence == BatteryPresence.REVERSED:
        storage.log_event(
            level="WARNING", source=source, channel=channel, relay=relay_address,
            message=(
                f"Battery presence check: voltage ({battery_voltage_v:.3f} V) suggests "
                f"REVERSED polarity -- not treated as missing; the existing reverse-"
                f"polarity safety check will verify again before SMU output is enabled."
            ),
        )
    if target_ntc is not None and ntc_readable and ntc_presence != NTCPresence.PRESENT:
        reasons.append("NTC Missing" if ntc_presence == NTCPresence.ABSENT else "NTC Fault")

    battery_label = _BATTERY_LABELS[battery_presence if battery_readable else None]
    voltage_suffix = f" ({battery_voltage_v:.3f} V)" if battery_voltage_v is not None else ""
    storage.log_event(
        level="INFO" if not reasons else "ERROR", source=source, channel=channel, relay=relay_address,
        message=(
            f"Presence check -- Battery: {battery_label}{voltage_suffix}, "
            f"NTC: {ntc_presence if ntc_presence is not None else 'N/A'}"
        ),
    )
    if reasons:
        storage.log_event(
            level="ERROR", source=source, channel=channel, relay=relay_address,
            message="TEST ABORTED -- Reason: " + ", ".join(reasons),
        )

    return {
        "ok": not reasons,
        "reasons": reasons,
        "battery_presence": battery_presence,
        "battery_readable": battery_readable,
        "battery_voltage_v": battery_voltage_v,
        "ntc_presence": ntc_presence,
        "ntc_readable": ntc_readable,
        "station_fault": station_fault,
    }
