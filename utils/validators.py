"""Input and configuration validation functions."""

from config import devices as dev_cfg
from config.settings import Settings
from config.system_mode import parse_system_mode
from utils.errors import (
    ConfigurationError,
    GroupConfigurationError,
    HardwareConfigurationError,
    ValidationError,
)


def validate_channel(channel: int, settings: Settings):
    if channel < 1 or channel > settings.BATTERY_POSITIONS:
        raise ValidationError(f"Channel {channel} out of range (1-{settings.BATTERY_POSITIONS})")


def validate_channels(channels: list, settings: Settings):
    for ch in channels:
        validate_channel(ch, settings)


def validate_voltage(voltage_v: float, settings: Settings):
    if not (settings.BAT_VOLTAGE_MIN <= voltage_v <= settings.BAT_VOLTAGE_MAX):
        raise ValidationError(
            f"Voltage {voltage_v:.3f} V out of range "
            f"[{settings.BAT_VOLTAGE_MIN}, {settings.BAT_VOLTAGE_MAX}]"
        )


def validate_current(current_a: float, settings: Settings):
    if abs(current_a) > settings.BAT_CURRENT_MAX:
        raise ValidationError(
            f"Current {current_a:.3f} A exceeds max {settings.BAT_CURRENT_MAX} A"
        )


def validate_settings(settings: Settings):
    """Sanity-check the whole settings object at startup. Raises ValidationError on failure."""
    parse_system_mode(settings.SYSTEM_MODE)  # raises ValidationError if not DEVELOPMENT/VALIDATION/PRODUCTION

    if settings.BATTERY_POSITIONS <= 0:
        raise ValidationError("BATTERY_POSITIONS must be > 0")
    if settings.BAT_VOLTAGE_MIN >= settings.BAT_VOLTAGE_MAX:
        raise ValidationError(
            f"BAT_VOLTAGE_MIN ({settings.BAT_VOLTAGE_MIN}) must be < "
            f"BAT_VOLTAGE_MAX ({settings.BAT_VOLTAGE_MAX})"
        )
    if settings.CHARGE_CURRENT_A <= 0:
        raise ValidationError(f"CHARGE_CURRENT_A must be > 0, got {settings.CHARGE_CURRENT_A}")
    if settings.DISCHARGE_CURRENT_A <= 0:
        raise ValidationError(f"DISCHARGE_CURRENT_A must be > 0, got {settings.DISCHARGE_CURRENT_A}")
    if settings.SAMPLE_RATE_HZ <= 0:
        raise ValidationError(f"SAMPLE_RATE_HZ must be > 0, got {settings.SAMPLE_RATE_HZ}")


_REQUIRED_TEST_SETPOINTS = (
    "charge_current_a", "charge_voltage_v", "discharge_current_a", "discharge_cutoff_v",
)


def validate_group_test_config(group: str) -> dict:
    """
    Three-stage validation pipeline for a Charge/Discharge Battery request,
    run BEFORE any hardware is touched (no HardwareManager constructed, no
    relay closed, no PSU output enabled on any failure):

        Group Configuration -> Battery Limits Validation ->
        Hardware Capability Validation -> (caller proceeds to) Execution

    Battery type is NEVER an operator choice and never a second source of
    truth -- it is read here directly from config/devices.py
    BATTERY_GROUPS[group]["battery_type"], the group's own engineering
    declaration of what it is wired/qualified to test. There is no
    operator-supplied battery_type parameter to cross-check against (see
    docs/architecture.md "Battery Group Test Configuration Architecture" --
    an earlier revision of this function took battery_type as a parameter
    and cross-checked it against the group's declaration; that entire
    concept was removed, not just the check, once battery type stopped
    being operator input at all).

    Returns `{"battery_type": ..., "test_setpoints": ...}` once every
    stage passes -- the caller (test.py) uses `battery_type` to resolve
    `BATTERY_CONFIGS` and threads `test_setpoints` into ChargeSequence/
    DischargeSequence.run() unchanged. Raises, and never silently
    substitutes a safer value:

        GroupConfigurationError    -- group unknown, or a required hardware
                                       role/battery_type/test_setpoints is
                                       missing.
        ConfigurationError         -- a configured setpoint exceeds the
                                       group's own battery's safety limit
                                       (config/devices.py BATTERY_CONFIGS).
        HardwareConfigurationError -- a configured setpoint exceeds the
                                       capability of the SMU assigned to
                                       this group (PXI_SLOTS[...]
                                       ["max_current_a"]).

    See docs/architecture.md "Battery Group Test Configuration
    Architecture" for the full design rationale.
    """
    # -- Stage 1: Group Configuration --------------------------------------
    if group not in dev_cfg.BATTERY_GROUPS:
        raise GroupConfigurationError(f"Unknown battery group {group!r}.")
    grp = dev_cfg.BATTERY_GROUPS[group]

    hw = dev_cfg.hardware_for_group(group)
    missing_roles = [role for role in ("relay_matrix", "smu", "dmm")
                      if hw[f"{role}_cfg"] is None]
    if missing_roles:
        raise GroupConfigurationError(
            f"Group {group!r} has no {', '.join(missing_roles)} assigned -- "
            f"see config/devices.py::BATTERY_GROUPS[{group!r}]."
        )

    battery_type = grp.get("battery_type")
    if battery_type is None:
        raise GroupConfigurationError(
            f"Group {group!r} has no battery_type configured -- "
            f"see config/devices.py::BATTERY_GROUPS[{group!r}]."
        )

    test_setpoints = grp.get("test_setpoints")
    if not test_setpoints or any(k not in test_setpoints for k in _REQUIRED_TEST_SETPOINTS):
        raise GroupConfigurationError(
            f"Group {group!r} is missing one or more required test_setpoints "
            f"{_REQUIRED_TEST_SETPOINTS} -- see config/devices.py::"
            f"BATTERY_GROUPS[{group!r}]."
        )

    # -- Stage 2: Battery Limits Validation ---------------------------------
    # An unknown battery_type (e.g. a typo in BATTERY_GROUPS[group]
    # ["battery_type"]) must never reach a bare BATTERY_CONFIGS[battery_type]
    # KeyError -- that would be an uncaught, operator-unfriendly crash
    # instead of the same ConfigurationError/"no hardware activated" path
    # every other Stage 2 failure takes. See docs/architecture.md "Battery
    # Type Validation".
    if battery_type not in dev_cfg.BATTERY_CONFIGS:
        raise ConfigurationError(
            f"Group {group!r}: battery_type {battery_type!r} is not a known "
            f"BATTERY_CONFIGS entry -- see config/devices.py::BATTERY_GROUPS"
            f"[{group!r}] and config/devices.py::BATTERY_CONFIGS."
        )

    # BATTERY_CONFIGS values are ceilings/floors, never required operating
    # points -- a setpoint below the limit is always fine; a setpoint above
    # it is always a ConfigurationError, never silently clamped.
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]

    if test_setpoints["charge_current_a"] > battery_cfg["max_charge_current_a"]:
        raise ConfigurationError(
            f"Group {group!r}: configured charge_current_a "
            f"({test_setpoints['charge_current_a']:.3f} A) exceeds {battery_type}'s "
            f"max_charge_current_a ({battery_cfg['max_charge_current_a']:.3f} A)."
        )
    if test_setpoints["charge_voltage_v"] > battery_cfg["voltage_max_v"]:
        raise ConfigurationError(
            f"Group {group!r}: configured charge_voltage_v "
            f"({test_setpoints['charge_voltage_v']:.3f} V) exceeds {battery_type}'s "
            f"voltage_max_v ({battery_cfg['voltage_max_v']:.3f} V)."
        )
    if test_setpoints["discharge_current_a"] > battery_cfg["max_discharge_current_a"]:
        raise ConfigurationError(
            f"Group {group!r}: configured discharge_current_a "
            f"({test_setpoints['discharge_current_a']:.3f} A) exceeds {battery_type}'s "
            f"max_discharge_current_a ({battery_cfg['max_discharge_current_a']:.3f} A)."
        )
    if test_setpoints["discharge_cutoff_v"] < battery_cfg["voltage_min_v"]:
        # The battery safety floor always has priority -- see docs/architecture.md
        # "Discharge Cutoff Policy". DischargeSequence also clamps this
        # defensively at runtime, but this stage catches it up front, before
        # any hardware is touched, rather than relying on that clamp alone.
        raise ConfigurationError(
            f"Group {group!r}: configured discharge_cutoff_v "
            f"({test_setpoints['discharge_cutoff_v']:.3f} V) is below {battery_type}'s "
            f"voltage_min_v safety floor ({battery_cfg['voltage_min_v']:.3f} V)."
        )

    # -- Stage 3: Hardware Capability Validation ----------------------------
    smu_cfg = hw["smu_cfg"]
    smu_max_a = smu_cfg.get("max_current_a")
    if smu_max_a is not None:
        if test_setpoints["charge_current_a"] > smu_max_a:
            raise HardwareConfigurationError(
                f"Group {group!r}: configured charge_current_a "
                f"({test_setpoints['charge_current_a']:.3f} A) exceeds "
                f"{hw['smu_name']}'s rated capability ({smu_max_a:.3f} A)."
            )
        if test_setpoints["discharge_current_a"] > smu_max_a:
            raise HardwareConfigurationError(
                f"Group {group!r}: configured discharge_current_a "
                f"({test_setpoints['discharge_current_a']:.3f} A) exceeds "
                f"{hw['smu_name']}'s rated capability ({smu_max_a:.3f} A)."
            )

    return {"battery_type": battery_type, "test_setpoints": test_setpoints}
