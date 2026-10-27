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

Vendor-neutral contract (what every sequence/HardwareManager depends on, and
the ONLY relay API they may use -- no sequence calls write()/read_all()/
write_all()/verify_all(), those are NumatoRelayMatrix native primitives used
only by Numato-specific diagnostics in test.py):
  connect() / disconnect()
  close(channel)  -- "select position": after it returns, ONLY `channel` is
                     closed on the matrix and that was VERIFIED by readback
  open(channel)   -- after it returns, the matrix is all-open and VERIFIED
  open_all()      -- all-open, VERIFIED (startup, shutdown, atexit, sweeps)
  query(channel)  -- commanded/readback state of one channel
  settle()        -- the single global settle delay (RELAY_SETTLE_TIME_S)
  RelayStateVerificationError on any failed verification, after the driver's
  own best-effort all-open attempt (unknown relay state = unsafe state).

A new driver (e.g. a future NI-SWITCH PXIe-2569 driver registered as
"niswitch") must satisfy this contract in its own _close_impl/_open_impl/
open_all -- including the exclusive-select interlock (force all open ->
verify -> close target -> verify only target closed), which today lives in
NumatoRelayMatrix and is NOT enforced by this base class.
"""

import time
from abc import abstractmethod

from config.settings import Settings
from hardware.base import HardwareBase
from utils.errors import RelayError, RelayStateVerificationError, ValidationError

# Methods that are the single enforcement point for the mandatory settle
# delay -- a driver that overrides one would bypass it, so subclassing
# RelayBase with any of these fails at class-definition time.
_FINAL_METHODS = ("open", "close", "settle")


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

    FAIL-CLOSED DEFAULTS. A new driver never inherits behavior that could
    leave the matrix in an unsafe multi-relay state:
      - open_all() is abstract: every driver must provide its own bulk,
        VERIFIED all-open (the old per-channel default paid one full settle
        per channel and had no verification story).
      - close_all() raises RelayError unless a driver explicitly overrides
        it -- "close every channel" connects every battery at once and is
        never a valid target state on an interlocked matrix.
      - open/close/settle cannot be overridden (see _FINAL_METHODS).
      - closed_channels()/verify_all_open()/verify_only_closed() are
        read-only verification helpers built on query(); they never write.

    Behavioral contract every driver must satisfy: tests/_relay_conformance.py
    (run against each driver by tests/test_relay_conformance.py).
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        overridden = [m for m in _FINAL_METHODS if m in cls.__dict__]
        if overridden:
            raise TypeError(
                f"{cls.__name__} overrides RelayBase.{'/'.join(overridden)}() -- "
                f"these are the single enforcement point for the mandatory "
                f"relay settle delay and may not be overridden. Implement "
                f"_open_impl()/_close_impl() instead."
            )

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
    # Bulk operations -- no unsafe default
    # ------------------------------------------------------------------

    @abstractmethod
    def open_all(self):
        """
        Drive EVERY channel open and verify it (safe state -- all batteries
        disconnected). Called at startup, shutdown, atexit, safety sweeps
        and emergency paths, so it must be one bulk operation (not a
        per-channel loop that pays RELAY_SETTLE_TIME_S per channel) and it
        must raise RelayStateVerificationError if any channel cannot be
        confirmed open.
        """

    def close_all(self):
        """
        Not supported unless a driver explicitly overrides it: closing every
        channel connects every battery simultaneously, which is never a
        valid target state on an interlocked matrix.
        """
        raise RelayError(
            f"close_all() is not supported by {type(self).__name__} '{self.name}': "
            f"closing every channel at once is never a valid state. A driver "
            f"must explicitly override close_all() to allow it."
        )

    # ------------------------------------------------------------------
    # Read-only verification helpers (built on query(); never write)
    # ------------------------------------------------------------------

    def closed_channels(self) -> list:
        """1-based channels query() currently reports closed. Generic and
        slow (one query() per channel) -- a driver with a bulk readback may
        override it, but must keep the same return shape."""
        return [ch for ch in range(1, self.num_channels + 1) if self.query(ch)]

    def verify_all_open(self):
        """Raise RelayStateVerificationError unless every channel reads open."""
        closed = self.closed_channels()
        if closed:
            raise RelayStateVerificationError(
                f"Relay '{self.name}' verification FAILED: expected all channels "
                f"open, channels {closed} read closed."
            )

    def verify_only_closed(self, channel: int):
        """Raise RelayStateVerificationError unless `channel` is the one and
        only closed channel."""
        self._validate_channel(channel)
        closed = self.closed_channels()
        if closed != [channel]:
            raise RelayStateVerificationError(
                f"Relay '{self.name}' verification FAILED: expected only channel "
                f"{channel} closed, channels {closed} read closed."
            )

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    def _validate_channel(self, channel: int):
        if not (1 <= channel <= self.num_channels):
            raise ValidationError(
                f"Channel {channel} out of range (1..{self.num_channels}) "
                f"for relay '{self.name}'"
            )
