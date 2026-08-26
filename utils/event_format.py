"""
Shared vocabulary + formatter for structured, machine-readable hardware
event_log entries -- see docs/architecture.md "Standardized Hardware
Event Logging". Mirrors utils/stop_reason.py's role: a small,
dependency-free "shared vocabulary" module usable both by hardware-layer
`on_event` callbacks (which have no `storage`/DB knowledge -- see
test_control/battery_operation_sequence.py::_shutdown_trace_logger()) and
by test_control/ orchestration (which owns the actual
`storage.log_event()` call).

Message format: "EVENT_TYPE=<type> KEY=<value> KEY=<value> ..." -- a
flat, greppable, machine-parseable key=value line stored in the existing
event_log.message TEXT column. RUN_ID is deliberately NOT embedded in the
formatted text: event_log.run_id is already a real, NOT NULL column,
populated by DataStorage.log_event() itself on every row -- repeating it
inside message text would be redundant, not additive.
"""

import re

# Matches the position immediately BEFORE an uppercase KEY= token that is
# preceded by whitespace -- used to split a formatted message back into
# its individual "KEY=value" fields without breaking on a free-text
# field's own embedded spaces (e.g. REASON=<a whole sentence>). Requires
# the key to start with an uppercase letter (format_event() always
# upper()s keys), so a lowercase "key=value" occurring INSIDE a free-text
# value (e.g. "...verification_result=still_enabled)." inside a REASON
# value) is never mistaken for a field boundary.
_FIELD_BOUNDARY = re.compile(r"(?=\s[A-Z][A-Z0-9_]*=)")


class EventType:
    """
    One string constant per required hardware/routing/group-run event
    category (see docs/architecture.md "Standardized Hardware Event
    Logging"). Plain string constants, same convention as
    utils/stop_reason.py::StopReason / hardware/temperature.py::
    NTCPresence.
    """
    SMU_OUTPUT_ENABLED = "SMU_OUTPUT_ENABLED"
    SMU_OUTPUT_DISABLED = "SMU_OUTPUT_DISABLED"
    RELAY_OPEN = "RELAY_OPEN"
    RELAY_CLOSE = "RELAY_CLOSE"
    RELAY_OPEN_ALL = "RELAY_OPEN_ALL"
    MATRIX_ROUTE_APPLIED = "MATRIX_ROUTE_APPLIED"
    MATRIX_ROUTE_CLEARED = "MATRIX_ROUTE_CLEARED"
    DMM_MEASUREMENT_FAILED = "DMM_MEASUREMENT_FAILED"
    DMM_MEASUREMENT_RECOVERED = "DMM_MEASUREMENT_RECOVERED"
    # SAFETY_MONITOR_TRIGGERED fires alongside every SafetyViolationError
    # (see BatteryOperationSequence.run_guarded()) -- reverse polarity,
    # a real safety.check() limit violation, or battery-removal-during-
    # charge all route through this one branch today.
    #
    # SAFETY_MONITOR_RECOVERED is defined here for vocabulary completeness
    # but is NOT reachable by any code path today, and this is deliberate,
    # not an oversight: a triggered safety violation is, by design,
    # immediately terminal (run_guarded() always re-raises after shutdown
    # -- see docs/architecture.md "Standardized Hardware Event Logging").
    # There is no scenario in which a run continues after one, so nothing
    # ever "recovers" within a single run. Changing that would be a real
    # safety-behavior change (tolerating a safety violation and
    # continuing), which is a different, much bigger decision than
    # "add event logging" and was not made here.
    SAFETY_MONITOR_TRIGGERED = "SAFETY_MONITOR_TRIGGERED"
    SAFETY_MONITOR_RECOVERED = "SAFETY_MONITOR_RECOVERED"
    EMERGENCY_STOP_STARTED = "EMERGENCY_STOP_STARTED"
    EMERGENCY_STOP_COMPLETED = "EMERGENCY_STOP_COMPLETED"
    GROUP_RUN_STARTED = "GROUP_RUN_STARTED"
    GROUP_SLOT_STARTED = "GROUP_SLOT_STARTED"
    GROUP_SLOT_SKIPPED = "GROUP_SLOT_SKIPPED"
    GROUP_SLOT_FAILED = "GROUP_SLOT_FAILED"
    GROUP_SLOT_COMPLETED = "GROUP_SLOT_COMPLETED"
    GROUP_RUN_COMPLETED = "GROUP_RUN_COMPLETED"
    # Group -> ALL Fault Classification Policy (see
    # docs/architecture.md and utils/errors.py::STATION_HARDWARE_EXCEPTIONS)
    # -- logged once, at the exact position that triggered the abort, when
    # a test-station hardware fault (not a battery-under-test fault) stops
    # the whole group run early rather than just failing that one slot.
    GROUP_RUN_ABORTED_STATION_FAULT = "GROUP_RUN_ABORTED_STATION_FAULT"
    # Safety Fault Lifecycle (see docs/architecture.md "Safety Fault
    # Lifecycle" and utils/safety_fault.py) -- SAFETY_FAULT_RAISED fires
    # whenever a shutdown-verification failure (emergency_output_off()/
    # verify_output_disabled()/force_output_off_and_verify() returning
    # False, or a relay open_all()/verify_all(0) failure) is escalated
    # into a SAFETY FAULT: an in-run STATION_FAULT escalation, a startup
    # safety sweep failure, or a post-workflow safety sweep failure.
    # SAFETY_FAULT_ACKNOWLEDGED fires once the operator presses ENTER on
    # the console SAFETY FAULT screen. Every RAISED/ACKNOWLEDGED pair
    # shares a `fault_id` field (see utils/safety_fault.py::new_fault_id())
    # correlating them without a database round trip.
    SAFETY_FAULT_RAISED = "SAFETY_FAULT_RAISED"
    SAFETY_FAULT_ACKNOWLEDGED = "SAFETY_FAULT_ACKNOWLEDGED"


def format_event(event_type: str, **fields) -> str:
    """
    Build one "EVENT_TYPE=<event_type> KEY=<value> ..." line. Field order
    matches the order passed (kwargs preserve insertion order in Python
    3.7+) -- callers should pass fields in the order they want them read.
    A field whose value is None is omitted entirely, never written as
    "KEY=None" -- this is what lets a caller pass every POSSIBLE
    provenance field (device/resource/channel/ip/port/...) and have only
    the ones it actually has appear, matching "include provenance
    whenever available" rather than forcing every event type to declare
    its own fixed field set.
    """
    parts = [f"EVENT_TYPE={event_type}"]
    for key, value in fields.items():
        if value is not None:
            parts.append(f"{key.upper()}={value}")
    return " ".join(parts)


def parse_event_fields(message: str) -> dict:
    """
    Inverse of format_event() -- parses a "EVENT_TYPE=<type> KEY=<value>
    ..." message back into a dict with lowercased keys (e.g.
    {"event_type": "SAFETY_FAULT_RAISED", "fault_id": "...", "reason": "..."}).
    Added for the Forensic Export feature (see docs/architecture.md
    "Forensic Export") -- lets the exporter recover structured fields from
    event_log.message without a second, parallel storage format.

    Returns {} for a message that doesn't start with "EVENT_TYPE=" (a
    plain free-text event_log message, e.g. "Run started") -- never
    raises, since this is applied to arbitrary historical rows.

    A field's value extends up to (but not including) the next KEY=
    boundary or the end of the string, so a free-text field passed last
    (e.g. REASON=<a whole sentence>, always the last kwarg at every real
    call site -- see utils/safety_fault.py::report_safety_fault()) comes
    back whole, spaces and all, rather than being cut at its first space.
    """
    if not message.startswith("EVENT_TYPE="):
        return {}
    fields = {}
    for part in _FIELD_BOUNDARY.split(message):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        fields[key.lower()] = value
    return fields
