"""
Safety Fault persistence, correlation, and operator acknowledgement -- see
docs/architecture.md "Safety Fault Lifecycle". Reused by every safety-fault
trigger point in the codebase (in-run shutdown-verification escalation --
test_control/charge_sequence.py / discharge_sequence.py; the startup safety
sweep -- test_control/safety_sweep.py; the post-workflow safety check --
test_control/hardware_manager.py::HardwareManager.disconnect_all()) so there
is exactly one persistence/display implementation, not one per trigger.

Persists to BOTH existing tables, no schema change:
  - event_log (data/storage.py::DataStorage.log_event()) -- human-readable,
    run-scoped narrative, via the existing EventType.SAFETY_FAULT_RAISED/
    SAFETY_FAULT_ACKNOWLEDGED vocabulary (utils/event_format.py).
  - raw_hardware_log (data/raw_hardware_log.py::RawHardwareLogWriter) --
    structured/queryable, independent of whether a DataStorage session is
    even open (see that module's own docstring) -- this matters here
    because the startup safety sweep and HardwareManager.disconnect_all()
    both run with no open DataStorage in real call sites. The
    OutputVerificationResult distinction (DISABLED/STILL_ENABLED/
    VERIFICATION_COMM_FAILURE -- hardware/smu.py) is carried in the
    existing `error_type` column; `fault_id`/`context`/`source_method`/
    `linked_event_log_id` are carried in the existing free-JSON
    `additional_metadata` column.

`storage` is optional everywhere in this module: when the caller has no
open DataStorage (startup sweep, HardwareManager -- which deliberately has
no `storage` reference at all, matching hardware/smu.py's own storage-free
layering boundary), the event_log write is simply skipped and
raw_hardware_log alone records the fault -- never raises either way.
"""

from __future__ import annotations

import uuid

from config.settings import Settings
from data.raw_hardware_log import RawHardwareLogWriter
from utils.event_format import EventType, format_event


def new_fault_id() -> str:
    """
    One process-unique label correlating a SAFETY_FAULT_RAISED record with
    its eventual SAFETY_FAULT_ACKNOWLEDGED record (see Change 7 -- "Link
    Related Records"). Generated here, not read back from a database id,
    so acknowledge_safety_fault() never needs a round trip to find out
    what to correlate against.
    """
    return f"faultevt_{uuid.uuid4().hex[:12]}"


def extract_verification_result(message: str):
    """
    Best-effort extraction of hardware/smu.py::OutputVerificationResult's
    value from an emergency_output_off() `on_event` message ("output
    disabled verification result: <value> ..." -- see that method's
    docstring). Returns None for any other message, including messages a
    test's fake/scripted SMU may emit (or none at all) -- never raises.
    This is how the verification-result distinction reaches a caller
    without changing hardware/smu.py's public bool-returning, storage-free
    contract (see that module's docstring on why it must not gain
    persistence-layer knowledge).
    """
    marker = "output disabled verification result:"
    if marker not in message:
        return None
    tail = message.split(marker, 1)[1].strip()
    return tail.split(" ", 1)[0] if tail else None


def report_safety_fault(*, reason: str, source_method: str, context: str,
                         device_name: str, device_type: str, position=None,
                         run_id=None, verification_result=None,
                         storage=None, settings=Settings) -> str:
    """
    Persist one SAFETY_FAULT_RAISED record. Returns a fault_id for the
    matching acknowledge_safety_fault() call.

    `context` is one of "in_run_escalation" / "startup_sweep" /
    "post_workflow_sweep" (plain string, matching this codebase's existing
    small-closed-vocabulary convention -- see utils/stop_reason.py::
    StopReason). `verification_result` is one of hardware/smu.py::
    OutputVerificationResult's three values, or None when this fault did
    not originate from an SMU output-verification failure (e.g. a relay
    open_all()/verify_all() failure).
    """
    fault_id = new_fault_id()
    event_log_id = None
    if storage is not None:
        try:
            event_log_id = storage.log_event(
                level="CRITICAL", source="SAFETY", channel=position,
                message=format_event(
                    EventType.SAFETY_FAULT_RAISED, fault_id=fault_id,
                    device_name=device_name, device_type=device_type, position=position,
                    source_method=source_method, context=context,
                    verification_result=verification_result, reason=reason,
                ),
            )
        except Exception:
            pass  # event_log unavailable (e.g. not open yet) -- raw_hardware_log below still records this

    writer = RawHardwareLogWriter(settings)
    try:
        writer.log(
            run_id=run_id, position=position, device_type=device_type, device_name=device_name,
            resource=None, command="safety_fault_raised",
            command_parameters={"context": context, "source_method": source_method},
            response=reason, success=False, duration_ms=None,
            error_type=verification_result, error_message=reason,
            additional_metadata={
                "fault_id": fault_id, "context": context, "source_method": source_method,
                "linked_event_log_id": event_log_id,
            },
        )
    finally:
        writer.close()
    return fault_id


def acknowledge_safety_fault(*, fault_id: str, storage=None, settings=Settings, run_id=None) -> None:
    """
    Persist one SAFETY_FAULT_ACKNOWLEDGED record correlated to `fault_id`
    (see report_safety_fault()) -- called once the operator has pressed
    ENTER on the SAFETY FAULT screen (display_safety_fault_screen() below).
    """
    event_log_id = None
    if storage is not None:
        try:
            event_log_id = storage.log_event(
                level="INFO", source="SAFETY",
                message=format_event(EventType.SAFETY_FAULT_ACKNOWLEDGED, acknowledges_fault_id=fault_id),
            )
        except Exception:
            pass

    writer = RawHardwareLogWriter(settings)
    try:
        writer.log(
            run_id=run_id, position=None, device_type="SAFETY", device_name="OPERATOR",
            resource=None, command="operator_acknowledged_safety_fault",
            command_parameters=None, response="acknowledged", success=True, duration_ms=None,
            error_type=None, error_message=None,
            additional_metadata={"acknowledges_fault_id": fault_id, "linked_event_log_id": event_log_id},
        )
    finally:
        writer.close()


def display_safety_fault_screen(*, smu_state: str = "UNKNOWN", relay_state: str = "VERIFIED OPEN",
                                 reason: str) -> None:
    """
    Console-only SAFETY FAULT screen -- no GUI framework exists (pure CLI
    app, see test.py). Mirrors the existing "=" * 60 rule/banner idiom
    already used throughout test.py (e.g. the app startup banner,
    run_section(), the existing STATION FAULT summary in
    _print_group_run_summary()) -- no new banner style introduced. Blocks
    on the same input()-then-swallow-KeyboardInterrupt/EOFError idiom
    already used everywhere else in test.py (e.g. _pause_before_main_menu())
    -- no new input-gating pattern introduced.
    """
    print()
    print("=" * 60)
    print("                    SAFETY FAULT")
    print("=" * 60)
    print()
    print("SMU state:")
    print(f"    {smu_state}")
    print()
    print("Relay state:")
    print(f"    {relay_state}")
    print()
    print("Reason:")
    print(f"    {reason}")
    print()
    print("Physically inspect the station.")
    print()
    try:
        input("Press ENTER to acknowledge... ")
    except (KeyboardInterrupt, EOFError):
        print()
    print("=" * 60)
