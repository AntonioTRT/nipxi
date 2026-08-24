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
