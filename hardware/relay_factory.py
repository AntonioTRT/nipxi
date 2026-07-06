"""
Relay factory.

Usage:
    from config.devices import RELAY_CONFIG
    from hardware.relay_factory import RelayFactory

    relay = RelayFactory.create(RELAY_CONFIG)
    relay.connect()
    relay.close(1)   # energize channel 1
    relay.open(1)    # de-energize channel 1
    relay.open_all()
    relay.disconnect()

The factory reads the "type" key from the config dict and returns the
matching RelayBase subclass. Callers never import concrete relay classes.

Supported types:
    "serial"   -- SerialRelay   (hardware/relay_serial.py)
    "ethernet" -- EthernetRelay (hardware/relay_eth.py)

Adding a new relay type:
    1. Create hardware/relay_<type>.py inheriting RelayBase
    2. Add an entry to _DRIVERS below
    3. Document the required config keys in the new module's docstring
"""

from hardware.relay import RelayBase
from utils.errors import ValidationError


# Map the "type" string to its implementation class.
# Import lazily inside create() to avoid import errors when a driver's
# optional dependency (e.g. pyserial) is not installed.
_DRIVERS = {
    "serial":   ("hardware.relay_serial", "SerialRelay"),
    "ethernet": ("hardware.relay_eth",    "EthernetRelay"),
}


class RelayFactory:
    @staticmethod
    def create(cfg: dict) -> RelayBase:
        """
        Instantiate the correct relay driver from a config dict.

        The dict must contain at least:
            "type"  -- "serial" or "ethernet"

        Additional keys depend on the driver (see relay_serial.py / relay_eth.py).
        Raises ValidationError for unknown types or missing required keys.
        """
        relay_type = cfg.get("type", "serial").lower()
        if relay_type not in _DRIVERS:
            raise ValidationError(
                f"Unknown relay type: {relay_type!r}.  "
                f"Supported: {sorted(_DRIVERS)}"
            )

        module_name, class_name = _DRIVERS[relay_type]

        # Late import: only pull in the driver's dependencies when needed
        import importlib
        module = importlib.import_module(module_name)
        cls    = getattr(module, class_name)
        return cls(cfg)
