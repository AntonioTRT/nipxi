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
  "serial"   -- SerialRelay       (hardware/relay_serial.py) -- diagnostic only
  "ethernet" -- NumatoRelayMatrix (hardware/relay_eth.py, Numato RELAY32ETHRL00) --
                PRODUCTION. "EthernetRelay" is kept as a backward-compat alias.

Relay convention (electrical):
  close(channel) -- energizes the coil, makes the contact, current can flow
  open(channel)  -- de-energizes the coil, breaks the contact, current cannot flow
"""

import time
from abc import abstractmethod

from config.settings import Settings
from hardware.base import HardwareBase
from utils.errors import ValidationError


class RelayBase(HardwareBase):
    """
    Abstract base for all relay controllers.

    Subclasses must implement: connect, disconnect, query, and the
    driver-specific _open_impl/_close_impl. open()/close() themselves are
    concrete here (NOT overridable) and are the single enforcement point
    for Settings.RELAY_SETTLE_TIME_S -- the one global relay settling/
    dead-time constant used everywhere in the application. Every relay
    switch, in every workflow, always blocks for RELAY_SETTLE_TIME_S after
    the driver reports the action complete and verified, before control
    returns to the caller -- so no subsequent relay action (on this
    channel or any other) can ever follow with less than a full settle
    time in between, and no caller needs (or is permitted) to add its own
    relay-settle delay. RELAY_SETTLE_TIME_S must never be 0.

    open_all / close_all have default implementations that loop over channels;
    override them if the hardware supports a faster bulk command.
    """

    def __init__(self, name: str, num_channels: int = 8):
        super().__init__(name)
        self.num_channels = num_channels

    # ------------------------------------------------------------------
    # Public API -- concrete, not overridable. Both wrap the driver-
    # specific implementation with the mandatory settle delay.
    # ------------------------------------------------------------------

    def open(self, channel: int):
        """Open (de-energize) relay for the given channel, then settle."""
        self._open_impl(channel)
        self.settle()

    def close(self, channel: int):
        """Close (energize) relay for the given channel, then settle."""
        self._close_impl(channel)
        self.settle()

    def settle(self):
        """
        Block for Settings.RELAY_SETTLE_TIME_S -- the single global relay
        settling/dead-time delay. Called automatically by open()/close()
        above; ALSO the one method any code path that deliberately
        operates on native/raw relay primitives below the open()/close()
        wrapper (e.g. test.py::test_relay_ethernet_test(), which exercises
        NumatoRelayMatrix's native write()/write_all() directly, by design,
        to validate that layer independently -- see docs/architecture.md
        Section 24) MUST call after every state-changing native operation.
        This is the ONLY place the delay value/sleep is implemented -- no
        other relay code path may hardcode or duplicate this logic; every
        path either goes through open()/close() (automatic) or calls this
        method explicitly (native-primitive paths).
        """
        settle_s = Settings.RELAY_SETTLE_TIME_S
        if settle_s <= 0:
            raise ValidationError(
                "Settings.RELAY_SETTLE_TIME_S must be > 0 -- a 0 s relay "
                "settling/dead-time delay is never permitted"
            )
        time.sleep(settle_s)

    # ------------------------------------------------------------------
    # Abstract interface -- every driver must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def _open_impl(self, channel: int):
        """Drive the hardware to open (de-energize) the given channel."""

    @abstractmethod
    def _close_impl(self, channel: int):
        """Drive the hardware to close (energize) the given channel."""

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
