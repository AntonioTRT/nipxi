"""
System mode architecture.

Formalizes the three operating modes this project runs in, and the policy
each one implies, so that behavior differences (hardware strictness,
database location, recovery, simulated devices) live in ONE place instead
of being scattered `if` checks. See docs/architecture.md "System Modes"
for the full design writeup.

Modes:
    DEVELOPMENT -- daily software work, laptop development, no hardware
                   required. Missing hardware warns and startup continues.
    VALIDATION  -- hardware integration / driver validation. Missing
                   hardware is reported as a failure, but the framework
                   still launches (not a startup abort).
    PRODUCTION  -- real battery cycling. Any missing/unreachable hardware
                   aborts startup. No simulated devices.

Usage:
    from config.settings import Settings
    from config.system_mode import get_mode_policy

    policy = get_mode_policy(Settings)
    if policy.strict_hardware:
        ...

Set the active mode in config/settings.py:
    SYSTEM_MODE = "DEVELOPMENT"   # or "VALIDATION" / "PRODUCTION"
"""

import logging
from dataclasses import dataclass
from enum import Enum

from utils.errors import ValidationError


class SystemMode(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    PRODUCTION = "PRODUCTION"


@dataclass(frozen=True)
class ModePolicy:
    """
    The behavior implied by a SystemMode. One instance per mode, defined
    once in MODE_POLICIES below -- nothing else should hardcode
    mode-specific behavior; it should consult this instead.
    """
    mode: SystemMode
    description: str

    # Hardware startup behavior (test_control/hardware_manager.py).
    # True (PRODUCTION only): any device failing to connect aborts startup
    # (HardwareInitError, rollback of whatever already connected).
    # False (DEVELOPMENT/VALIDATION): each device connects independently;
    # a failure is logged (at hardware_failure_log_level) and recorded,
    # but startup continues. NOTE: this only ever applies to a MISSING/
    # unreachable device -- a relay that connects but cannot be confirmed
    # in a safe (all-off, verified) state always aborts startup, in every
    # mode. Unknown relay state = unsafe state is never relaxed by mode.
    strict_hardware: bool

    # Log level used when a device is missing/unreachable in a non-strict
    # mode (DEVELOPMENT logs a warning and moves on; VALIDATION logs an
    # error -- "test failure" -- but still launches, per the mode spec).
    hardware_failure_log_level: int

    # Recovery hook -- NOT implemented yet (no recovery engine exists in
    # this codebase). This is only a configuration flag future code can
    # read once cycle/state recovery is built. See docs/DATABASE_ROADMAP.md.
    recovery_enabled: bool

    # Whether HardwareManager is allowed to fall back to a Simulated*
    # device (hardware/simulated.py) in place of a missing real one.
    # NOT wired into HardwareManager yet -- see hardware/simulated.py's
    # module docstring for how this is expected to be consumed once it is.
    allow_simulated_devices: bool


MODE_POLICIES = {
    SystemMode.DEVELOPMENT: ModePolicy(
        mode=SystemMode.DEVELOPMENT,
        description=(
            "Daily software development, laptop development, UI/architecture/"
            "database work, simulation. Hardware optional -- missing devices "
            "warn and startup continues."
        ),
        strict_hardware=False,
        hardware_failure_log_level=logging.WARNING,
        recovery_enabled=False,
        allow_simulated_devices=True,
    ),
    SystemMode.VALIDATION: ModePolicy(
        mode=SystemMode.VALIDATION,
        description=(
            "Hardware integration, driver validation, system testing. Real "
            "hardware preferred -- missing devices are reported as failures, "
            "but the framework still launches."
        ),
        strict_hardware=False,
        hardware_failure_log_level=logging.ERROR,
        recovery_enabled=False,   # "recovery optionally enabled" -- default
                                  # off; override via Settings.RECOVERY_ENABLED_OVERRIDE
        allow_simulated_devices=False,
    ),
    SystemMode.PRODUCTION: ModePolicy(
        mode=SystemMode.PRODUCTION,
        description=(
            "Real battery cycling. No simulated hardware or batteries, "
            "strict startup validation -- any missing/unreachable device "
            "aborts startup."
        ),
        strict_hardware=True,
        hardware_failure_log_level=logging.CRITICAL,
        recovery_enabled=True,
        allow_simulated_devices=False,
    ),
}


def parse_system_mode(value) -> SystemMode:
    """
    Parse Settings.SYSTEM_MODE (a plain string, matching the style of every
    other Settings attribute) into a SystemMode. Raises ValidationError for
    an unrecognized value -- this is a configuration error, not a runtime
    one, and should surface the same way other bad Settings values do (see
    utils/validators.py::validate_settings()).
    """
    if isinstance(value, SystemMode):
        return value
    try:
        return SystemMode(str(value).upper())
    except ValueError:
        raise ValidationError(
            f"Settings.SYSTEM_MODE {value!r} is not a valid mode. "
            f"Valid values: {[m.value for m in SystemMode]}"
        ) from None


def get_mode_policy(settings) -> ModePolicy:
    """Resolve Settings.SYSTEM_MODE to its ModePolicy. Raises ValidationError if invalid."""
    return MODE_POLICIES[parse_system_mode(settings.SYSTEM_MODE)]


def is_recovery_enabled(settings) -> bool:
    """
    Whether cycle/state recovery should run -- NOT implemented yet, this is
    only the configuration hook (see docs/DATABASE_ROADMAP.md). Defaults to
    the active mode's policy; Settings.RECOVERY_ENABLED_OVERRIDE (if not
    None) takes precedence, so VALIDATION's "optionally enabled" can be
    toggled without changing SYSTEM_MODE.
    """
    override = getattr(settings, "RECOVERY_ENABLED_OVERRIDE", None)
    if override is not None:
        return bool(override)
    return get_mode_policy(settings).recovery_enabled
