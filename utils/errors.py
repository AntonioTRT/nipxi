"""Custom exception hierarchy for NIPXI."""


class NIPXIError(Exception):
    """Base exception for all NIPXI errors."""


class HardwareInitError(NIPXIError):
    """Raised when a hardware device fails to initialize."""


class RelayError(NIPXIError):
    """Raised on relay matrix communication failure."""


class DAQError(NIPXIError):
    """Raised on DAQ read/write failure."""


class SMUError(NIPXIError):
    """Raised on SMU communication or compliance failure."""


class DMMError(NIPXIError):
    """Raised on DMM communication failure."""


class SafetyViolationError(NIPXIError):
    """Raised when a safety limit is exceeded. Triggers emergency stop."""


class ReversePolarityError(SafetyViolationError):
    """
    Raised by the pre-output-enable voltage sanity check (ChargeSequence/
    DischargeSequence -- see docs/architecture.md "Reverse Polarity
    Protection") when the DMM reads a voltage at or below
    Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V with the SMU output still
    disabled. Deliberately a SafetyViolationError subclass -- it is caught
    by BatteryOperationSequence.run_guarded()'s existing SafetyViolationError
    branch and triggers the identical SafetyMonitor.emergency_stop() shutdown
    (PMU off + all relays forced open, run_summary/event_log recorded as
    SAFETY_VIOLATION) -- no separate shutdown path was introduced for this.
    Distinct from a generic Overvoltage/Undervoltage SafetyStatus message so
    operators get an immediately actionable diagnosis instead of "battery is
    just low"/"wiring is loose". Not a diagnosis of WHY the voltage is
    negative (reversed cell vs. a genuinely damaged cell vs. a wiring fault
    all read identically) -- only that connecting the SMU would be unsafe.
    """


class BatteryRemovedDuringChargeError(SafetyViolationError):
    """
    Raised by ChargeSequence's sampling loop (see test_control/
    battery_diagnostics.py::charge_transition_suggests_battery_removed()
    and docs/architecture.md "Battery Removal During Charge Detection")
    when the CC->EOC transition happens abruptly -- the immediately
    preceding sample was still clearly in the CC phase (current close to
    the full commanded value), then the very next sample already
    satisfies the EOC condition (voltage at the CV target, current at/
    below the termination threshold). A genuine cell's CV taper takes many
    sample intervals to decay from commanded current down to the
    termination threshold; an instantaneous one-sample jump is not
    physically achievable by a real, connected cell and is the signature
    of the load having vanished (the battery physically removed) while
    the SMU was still actively sourcing.

    Deliberately a SafetyViolationError subclass -- caught by
    BatteryOperationSequence.run_guarded()'s existing SafetyViolationError
    branch, triggering the identical SafetyMonitor.emergency_stop()
    shutdown and StopReason.SAFETY_VIOLATION/result="FAIL" reporting; no
    separate shutdown path was introduced for this. This is what prevents
    a battery removed mid-charge from ever being reported as a clean,
    passing EOC -- see ReversePolarityError's identical rationale for why
    this is deliberately NOT a generic Overvoltage/Undervoltage message.

    Discharge does not need an equivalent: sinking current into an open
    circuit (a removed battery) drives voltage sharply negative, which
    SafetyMonitor's own undervoltage check already catches BEFORE the EOD
    voltage-cutoff check ever runs in DischargeSequence's loop -- discharge
    was already safe against this scenario before this class existed.
    """


class DMMMeasurementLostError(SafetyViolationError):
    """
    Raised by ChargeSequence's/DischargeSequence's sampling loop when
    `dmm.measure_dc_voltage()` fails on
    Settings.DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES consecutive samples
    -- see docs/architecture.md "Standardized Hardware Event Logging".
    DMM is the authoritative voltage source for EOC/EOD detection and
    every safety.check() call in these loops -- unlike an NTC read
    failure (which only degrades temperature monitoring and is tolerated
    indefinitely), a DMM that cannot be read at all means voltage safety
    cannot be verified, which is unsafe per "unknown state = unsafe
    state". A single transient comms glitch is still given a bounded
    number of consecutive attempts to resolve (mirroring
    emergency_output_off()'s own bounded-retry philosophy) before this is
    raised. Deliberately a SafetyViolationError subclass -- caught by
    BatteryOperationSequence.run_guarded()'s existing SafetyViolationError
    branch, triggering the identical SafetyMonitor.emergency_stop()
    shutdown; no separate shutdown path was introduced for this.
    """


class OperationCancelledError(NIPXIError):
    """
    Raised when a cancellation checkpoint (see utils/cancellation.py) finds
    that the operator has requested a stop (currently: Ctrl+C ->
    CancellationToken.request_cancel()). This is NOT a fault -- it is a
    deliberate, expected operator action. Callers must report it distinctly
    from SafetyViolationError/generic failures (see the CANCELLED stop
    reason in utils/stop_reason.py), never as a failure.
    """


class NIPXITimeoutError(NIPXIError):
    """Raised when a test step exceeds its allowed duration."""


# Alias so existing imports of TimeoutError continue to work.
# Prefer NIPXITimeoutError in new code to avoid shadowing the Python builtin.
TimeoutError = NIPXITimeoutError


class ValidationError(NIPXIError):
    """Raised when configuration or input validation fails."""


class RelayStateVerificationError(RelayError):
    """
    Raised when a relay's actual (readback) state does not match the
    state the driver just commanded. Always fatal -- execution must stop
    rather than proceed with an unverified/ambiguous relay configuration.
    """


class SMUStateVerificationError(SMUError):
    """
    Raised when the SMU's actual (readback) configuration -- voltage
    setpoint, current limit, or output-enabled state -- does not match what
    the driver just commanded. Always fatal -- execution must stop rather
    than proceed with an unverified/ambiguous SMU configuration. Mirrors
    RelayStateVerificationError's role for the relay driver.
    """


class DeviceConfigError(ValidationError):
    """
    Raised by utils/device_validator.py when config/devices.py fails
    startup validation (missing fields, duplicate addresses, relay count
    mismatch, unknown factory type, etc). Always raised before any
    hardware communication is attempted.
    """


class GroupConfigurationError(ValidationError):
    """
    Raised by utils/validators.py::validate_group_test_config() when a
    config/devices.py BATTERY_GROUPS[...] entry is missing required
    information (a hardware role, battery_type, test_setpoints) or when
    the operator's explicitly-selected battery type does not match the
    group's own declared battery_type. Always raised before any hardware
    communication is attempted -- see docs/architecture.md "Battery Group
    Test Configuration Architecture".
    """


class ConfigurationError(ValidationError):
    """
    Raised by utils/validators.py::validate_group_test_config() when a
    group's configured test setpoint (charge/discharge current, charge
    voltage, discharge cutoff) would exceed the selected battery's own
    absolute safety limit (config/devices.py BATTERY_CONFIGS). Test
    setpoints are a chosen operating point, never automatically clamped to
    a safer value -- a violation here always aborts before any hardware
    communication is attempted.
    """


class HardwareConfigurationError(ValidationError):
    """
    Raised by utils/validators.py::validate_group_test_config() when a
    group's configured test setpoint would exceed the capability of the
    hardware assigned to run it (e.g. an SMU's rated max current -- see
    config/devices.py PXI_SLOTS[...]["max_current_a"]). Distinct from
    ConfigurationError (which compares against the BATTERY's own limit,
    not the hardware's) -- always raised before any hardware
    communication is attempted.
    """


# -----------------------------------------------------------------------
# Group -> ALL Fault Classification (see docs/architecture.md "Group ->
# ALL Fault Classification Policy") -- which exception types indicate a
# TEST-STATION HARDWARE problem (shared equipment -- relay matrix, SMU,
# DMM, DAQ -- comms/verification failures affecting every position in the
# group) vs a BATTERY-UNDER-TEST problem (the DUT's own electrical
# behavior/condition, specific to the one position under test). This is
# the single source of truth for that classification -- test.py's Group
# -> ALL orchestration (test.py::_classify_position_exception()) checks
# membership here rather than re-deriving the policy inline, so the
# policy can never drift out of sync with where these exceptions are
# actually defined.
#
# Order matters when USING these tuples (not when defining them):
# DMMMeasurementLostError is deliberately a SafetyViolationError subclass
# (so it reuses BatteryOperationSequence.run_guarded()'s existing
# SafetyViolationError shutdown branch, rather than needing a new one),
# but it must be classified STATION-level here. A caller MUST check
# STATION_HARDWARE_EXCEPTIONS membership BEFORE falling back to a
# broader SafetyViolationError/BATTERY_UNDER_TEST_EXCEPTIONS check, or
# DMMMeasurementLostError would be misclassified as battery-level
# (isinstance(exc, SafetyViolationError) is also True for it). See
# test_control/battery_operation_sequence.py's own class hierarchy note
# for the identical reasoning already applied to
# ReversePolarityError/BatteryRemovedDuringChargeError there.
STATION_HARDWARE_EXCEPTIONS = (
    RelayError,               # relay matrix -- shared switching hardware
                              # (covers both a verification mismatch and a
                              # raw TCP/comms failure -- hardware/relay_eth.py
                              # wraps every comms failure into RelayError,
                              # there is no separate "matrix communication
                              # failure" exception type to also list here)
    SMUError,                 # SMU -- shared source/measure hardware
    DMMError,                 # DMM -- shared, authoritative voltage source
    DAQError,                 # DAQ -- shared NTC/telemetry hardware
    DMMMeasurementLostError,  # DMM comms permanently lost (bounded retry
                              # exhausted) -- a SafetyViolationError
                              # subclass, listed here explicitly; see the
                              # ordering note above
)

BATTERY_UNDER_TEST_EXCEPTIONS = (
    BatteryRemovedDuringChargeError,
    ReversePolarityError,
    SafetyViolationError,   # catch-all for a real safety.check() limit
                              # violation (over/under-voltage, over-current,
                              # over-temperature) -- the DUT's own measured
                              # electrical behavior, not an equipment fault
    NIPXITimeoutError,
)
