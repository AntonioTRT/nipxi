"""Input and configuration validation functions."""

from config.settings import Settings
from config.system_mode import parse_system_mode
from utils.errors import ValidationError


def validate_channel(channel: int, settings: Settings):
    if channel < 1 or channel > settings.NUM_CHANNELS:
        raise ValidationError(f"Channel {channel} out of range (1-{settings.NUM_CHANNELS})")


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

    if settings.NUM_CHANNELS <= 0:
        raise ValidationError("NUM_CHANNELS must be > 0")
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
