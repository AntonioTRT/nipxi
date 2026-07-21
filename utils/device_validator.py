"""
Startup device configuration validation.

Runs BEFORE any hardware communication is attempted -- main.py calls
validate_devices_or_raise() immediately after validate_settings() and
before HardwareManager is constructed; test.py's preflight_check() runs
the same check before the interactive menu is shown. A bad configuration
must be detected here, not surfaced as a confusing connect() failure.

Only config/devices.py content and driver construction (never connect())
are exercised -- this module never talks to hardware.
"""

from utils.errors import DeviceConfigError

REQUIRED_FIELDS = {
    "smu":            ("resource",),
    "dmm":            ("resource",),
    "daq":            ("resource",),
    "relay_ethernet": ("ip", "port", "channel_count"),
    "relay_serial":   ("port",),
}


def _build_registry(dev_cfg) -> list:
    """Flatten every configured device (any type) into (kind, name, cfg) tuples."""
    registry = []
    for name, cfg in dev_cfg.SMU_ASSIGNMENTS.items():
        registry.append(("smu", name, cfg))
    for name, cfg in dev_cfg.DMM_CONFIGS.items():
        registry.append(("dmm", name, cfg))
    for name, cfg in dev_cfg.DAQ_CONFIGS.items():
        registry.append(("daq", name, cfg))
    for name, cfg in dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS.items():
        registry.append(("relay_ethernet", name, cfg))
    for name, cfg in dev_cfg.RELAY_SERIAL_CONFIGS.items():
        registry.append(("relay_serial", name, cfg))
    return registry


def _check_required_fields(kind: str, name: str, cfg: dict, errors: list):
    for field in REQUIRED_FIELDS.get(kind, ()):
        if not cfg.get(field):
            errors.append(f"{kind} '{name}': missing required field '{field}'")


def _check_factory_type(kind: str, name: str, cfg: dict, errors: list):
    """Every relay 'type' must be registered in RelayFactory (SMU/DMM/DAQ have
    exactly one driver class each -- there is no type dispatch to check)."""
    if kind not in ("relay_ethernet", "relay_serial"):
        return
    from hardware.relay_factory import RelayFactory
    relay_type = cfg.get("type", "").lower()
    supported = RelayFactory.supported_types()
    if relay_type not in supported:
        errors.append(
            f"{kind} '{name}': type {relay_type!r} is not registered in "
            f"RelayFactory (known: {supported})"
        )


def _check_instantiable(kind: str, name: str, cfg: dict, errors: list):
    """Construction only -- never connect(). Verifies the driver class accepts
    this config without raising (missing 'ip', bad types, etc)."""
    try:
        if kind == "smu":
            from hardware.smu import SMU
            SMU(cfg)
        elif kind == "dmm":
            from hardware.dmm import DMM
            DMM(cfg)
        elif kind == "daq":
            from hardware.daq import DAQ
            DAQ(cfg)
        elif kind in ("relay_ethernet", "relay_serial"):
            from hardware.relay_factory import RelayFactory
            RelayFactory.create(cfg)
    except Exception as e:
        errors.append(f"{kind} '{name}': failed to instantiate -- {e}")


def _check_duplicate_names(registry: list, errors: list):
    seen = {}
    for kind, name, _cfg in registry:
        if name in seen:
            errors.append(
                f"Duplicate device name {name!r}: used by both "
                f"{seen[name]} and {kind} '{name}'"
            )
        else:
            seen[name] = f"{kind} '{name}'"


def _check_duplicate_resources(registry: list, errors: list):
    """SMU/DMM/DAQ share the same PXI bus -- no two may claim the same VISA resource."""
    seen = {}
    for kind, name, cfg in registry:
        if kind not in ("smu", "dmm", "daq"):
            continue
        resource = cfg.get("resource")
        if not resource:
            continue
        if resource in seen:
            errors.append(
                f"Duplicate VISA resource {resource!r}: used by both "
                f"{seen[resource]} and {kind} '{name}'"
            )
        else:
            seen[resource] = f"{kind} '{name}'"


def _check_duplicate_ips(registry: list, errors: list):
    seen = {}
    for kind, name, cfg in registry:
        if kind != "relay_ethernet":
            continue
        ip = cfg.get("ip")
        if not ip:
            continue
        if ip in seen:
            errors.append(f"Duplicate IP address {ip!r}: used by both "
                          f"relay '{seen[ip]}' and relay '{name}'")
        else:
            seen[ip] = name


def _check_duplicate_com_ports(registry: list, errors: list):
    seen = {}
    for kind, name, cfg in registry:
        if kind != "relay_serial":
            continue
        port = cfg.get("port")
        if not port:
            continue
        if port in seen:
            errors.append(f"Duplicate COM port {port!r}: used by both "
                          f"relay '{seen[port]}' and relay '{name}'")
        else:
            seen[port] = name


def _check_duplicate_relay_identifiers(dev_cfg, errors: list) -> dict:
    """
    BATTERY_CHANNELS 'relay_address' must be unique -- two logical battery
    channels must never be wired to the same physical relay. Returns the
    seen-address map so relay-count consistency can reuse it.
    """
    battery_channels = getattr(dev_cfg, "BATTERY_CHANNELS", {})
    seen = {}
    for ch_id, ch in battery_channels.items():
        addr = ch.get("relay_address")
        if addr is None:
            errors.append(f"BATTERY_CHANNELS[{ch_id}]: missing 'relay_address'")
            continue
        if addr in seen:
            errors.append(
                f"Duplicate relay_address {addr!r}: used by both "
                f"BATTERY_CHANNELS[{seen[addr]}] and BATTERY_CHANNELS[{ch_id}]"
            )
        else:
            seen[addr] = ch_id
    return battery_channels


def _check_relay_count_consistency(dev_cfg, battery_channels: dict, errors: list):
    """
    num_channels / channel_count / Settings.RELAY_COUNT must all agree, and
    every BATTERY_CHANNELS relay_address must fall within that count.
    """
    from config.settings import Settings

    for name, cfg in dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS.items():
        num_channels  = cfg.get("num_channels")
        channel_count = cfg.get("channel_count")
        if num_channels != channel_count:
            errors.append(
                f"relay '{name}': num_channels ({num_channels}) != "
                f"channel_count ({channel_count}) -- must match"
            )
        if channel_count != Settings.RELAY_COUNT:
            errors.append(
                f"relay '{name}': channel_count ({channel_count}) != "
                f"Settings.RELAY_COUNT ({Settings.RELAY_COUNT}) -- "
                f"config/devices.py has drifted from the single source of truth"
            )

        limit = channel_count or num_channels or Settings.RELAY_COUNT
        if not limit:
            continue
        for ch_id, ch in battery_channels.items():
            addr = ch.get("relay_address")
            if addr is not None and not (1 <= addr <= limit):
                errors.append(
                    f"BATTERY_CHANNELS[{ch_id}]: relay_address {addr} is "
                    f"out of range for relay '{name}' (1..{limit})"
                )


def validate_devices(dev_cfg) -> list:
    """
    Validate config/devices.py before any hardware communication.

    Returns a list of human-readable problem strings (empty = all checks
    passed). Never raises -- see validate_devices_or_raise() for the
    fail-fast entry point used at startup.

    Checks performed:
        - every configured device can be instantiated (construction only)
        - required configuration fields are present per device type
        - no duplicate device names across ALL configured devices
        - no duplicate VISA resources (SMU/DMM/DAQ)
        - no duplicate IP addresses (Numato Relay Matrix devices)
        - no duplicate COM ports (serial relays)
        - no duplicate relay identifiers (BATTERY_CHANNELS relay_address)
        - relay count consistency (num_channels == channel_count ==
          Settings.RELAY_COUNT, and every relay_address in range)
        - every relay 'type' is registered in RelayFactory
    """
    errors = []
    registry = _build_registry(dev_cfg)

    for kind, name, cfg in registry:
        _check_required_fields(kind, name, cfg, errors)
        _check_factory_type(kind, name, cfg, errors)
        _check_instantiable(kind, name, cfg, errors)

    _check_duplicate_names(registry, errors)
    _check_duplicate_resources(registry, errors)
    _check_duplicate_ips(registry, errors)
    _check_duplicate_com_ports(registry, errors)
    battery_channels = _check_duplicate_relay_identifiers(dev_cfg, errors)
    _check_relay_count_consistency(dev_cfg, battery_channels, errors)

    return errors


def validate_devices_or_raise(dev_cfg):
    """
    Run validate_devices() and raise DeviceConfigError listing every problem
    found, all at once, if any exist. Call at startup, before any hardware
    communication -- never continue past this on a bad configuration.
    """
    errors = validate_devices(dev_cfg)
    if errors:
        report = "\n".join(f"  - {e}" for e in errors)
        raise DeviceConfigError(
            f"config/devices.py failed startup validation "
            f"({len(errors)} problem{'s' if len(errors) != 1 else ''}):\n{report}"
        )
