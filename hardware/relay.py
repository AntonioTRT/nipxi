"""
Relay driver architecture
=========================

All relay controllers implement RelayBase and are instantiated via RelayFactory.
Callers (BatteryTestSequence, SafetyMonitor, test.py) never import a concrete
relay class directly -- they call RelayFactory.create(cfg) and receive a RelayBase.

This design means adding a new relay type (e.g. USB-HID or Modbus) only requires:
  1. A new module hardware/relay_<type>.py that subclasses RelayBase
  2. One new branch in RelayFactory.create()

No caller code changes.

Supported relay types (as of this revision):
  "serial"   -- SerialRelay   (hardware/relay_serial.py)
  "ethernet" -- EthernetRelay (hardware/relay_eth.py, Numato RELAY32ETHRL00)

Relay convention (electrical):
  close(channel) -- energizes the coil, makes the contact, current can flow
  open(channel)  -- de-energizes the coil, breaks the contact, current cannot flow
"""

from abc import abstractmethod
from hardware.base import HardwareBase
from utils.errors import ValidationError


class RelayBase(HardwareBase):
    """
    Abstract base for all relay controllers.

    Subclasses must implement: connect, disconnect, open, close, query.
    open_all / close_all have default implementations that loop over channels;
    override them if the hardware supports a faster bulk command.
    """

    def __init__(self, name: str, num_channels: int = 8):
        super().__init__(name)
        self.num_channels = num_channels

    # ------------------------------------------------------------------
    # Abstract interface -- every driver must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def open(self, channel: int):
        """Open (de-energize) relay for the given channel."""

    @abstractmethod
    def close(self, channel: int):
        """Close (energize) relay for the given channel."""

    @abstractmethod
    def query(self, channel: int) -> bool:
        """Return True if the relay contact is closed (energized)."""

    # ------------------------------------------------------------------
    # Default bulk operations -- override for hardware-level speed-up
    # ------------------------------------------------------------------

    def open_all(self):
        """Open every relay channel (safe state -- all batteries disconnected)."""
        for ch in range(1, self.num_channels + 1):
            self.open(ch)

    def close_all(self):
        """Close every relay channel (all batteries connected simultaneously)."""
        for ch in range(1, self.num_channels + 1):
            self.close(ch)

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    def _validate_channel(self, channel: int):
        if not (1 <= channel <= self.num_channels):
            raise ValidationError(
                f"Channel {channel} out of range (1..{self.num_channels}) "
                f"for relay '{self.name}'"
            )
