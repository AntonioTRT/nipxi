"""Input and configuration validation functions."""

from config.settings import Settings
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
    """Sanity-check the whole settings object at startup."""
    assert settings.NUM_CHANNELS > 0, "NUM_CHANNELS must be > 0"
    assert settings.BAT_VOLTAGE_MIN < settings.BAT_VOLTAGE_MAX
    assert settings.CHARGE_CURRENT_A > 0
    assert settings.DISCHARGE_CURRENT_A > 0
    assert settings.SAMPLE_RATE_HZ > 0
