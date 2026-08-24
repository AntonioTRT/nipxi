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
