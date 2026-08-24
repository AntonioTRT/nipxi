"""
Battery Sense Routing -- FUTURE PLANNED ARCHITECTURE, with a CURRENT
IMPLEMENTATION of the abstraction layer only (see docs/architecture.md
"Future Architecture: Battery Sense Routing"). Nothing here is called
from any real execution path today: no group in config/devices.py
declares a "sense_channel", so `read_battery_voltage_via_sense()` below
always takes its pure-passthrough branch in practice, identical to
calling `dmm.measure_dc_voltage()` directly (today's actual behavior).

Design principle: a battery group declares ONLY a logical sense_channel
number (config/devices.py::BATTERY_GROUPS[group]["sense_channel"]) -- it
never names a relay module, IP address, or physical relay number. The
mapping from that logical channel to a physical (relay matrix, relay
number) pair lives entirely in config/devices.py::SENSE_ROUTING, resolved
by ConfigDrivenSenseRouter below. Swapping or adding a physical routing
device -- a second/different Numato module, a future rack-integrated
matrix, or a mix of several -- is a SENSE_ROUTING config change; it never
touches a battery group's config or any sequence code, because this
module deliberately reuses hardware/relay_factory.py::RelayFactory
(already generic across relay transport types) instead of inventing a
second, parallel backend-name registry.
"""

from __future__ import annotations

from hardware.relay import RelayBase
from utils.errors import ConfigurationError


class SenseRouter:
    """
    Abstract interface -- mirrors orchestration/arbiter.py::Arbiter's
    interface-now/implementation-later pattern. A real caller (a future
    BatteryOperationSequence integration -- see
    read_battery_voltage_via_sense() below) must depend only on this
    interface, never on which physical routing device is active
    underneath.
    """

    def connect(self, channel: int) -> None:
        """Connect `channel`'s battery sense path to the DMM. Must be
        safe to call on an already-connected channel (idempotent)."""
        raise NotImplementedError

    def disconnect(self, channel: int) -> None:
        """Disconnect `channel`'s battery sense path from the DMM. Must
        be safe to call on an already-disconnected channel (idempotent)."""
        raise NotImplementedError


class NumatoSenseRouter(SenseRouter):
    """
    A SenseRouter backed by one already-constructed RelayBase-shaped
    relay driver -- a NumatoRelayMatrix today, but this class does not
    import or assume that concrete type; any RelayBase subclass works
    (including a future rack-integrated matrix driver), since
    hardware/relay_factory.py::RelayFactory already generalizes relay-
    driver construction across transport types. One instance serves
    exactly one physical relay matrix -- ConfigDrivenSenseRouter below
    combines several matrices, this class never does.

    connect()/disconnect() are thin wrappers over the relay driver's own
    close()/open(): "connect the sense path" means "energize (close) the
    relay routing it to the DMM," "disconnect" means open it. Reuses
    RelayBase's own settle-time/verification behavior unchanged -- no new
    relay-timing logic is introduced here.
    """

    def __init__(self, relay: RelayBase, channel_to_relay: dict):
        self.relay = relay
        self.channel_to_relay = dict(channel_to_relay)

    def _resolve(self, channel: int) -> int:
        if channel not in self.channel_to_relay:
            raise ConfigurationError(
                f"NumatoSenseRouter: sense channel {channel!r} has no relay mapping "
                f"on this matrix (known channels: {sorted(self.channel_to_relay)})."
            )
        return self.channel_to_relay[channel]

    def connect(self, channel: int) -> None:
        self.relay.close(self._resolve(channel))

    def disconnect(self, channel: int) -> None:
        self.relay.open(self._resolve(channel))


class ConfigDrivenSenseRouter(SenseRouter):
    """
    The composite a future caller would hold exactly one instance of.
    Reads config/devices.py::SENSE_ROUTING to decide which physical relay
    matrix and relay number serves a given logical sense_channel, lazily
    constructing (via RelayFactory) and caching one NumatoSenseRouter per
    distinct relay matrix actually referenced -- so N sense channels on
    the same physical module share ONE relay connection rather than
    opening N redundant ones.

    This is the ONE place that knows SENSE_ROUTING's shape. Adding a new
    physical routing device is a SENSE_ROUTING config change (plus, only
    if the device needs a transport RelayFactory does not already
    support, a new hardware/relay_<type>.py driver) -- never a change to
    this class's callers.

    `sense_routing`/`relay_matrix_configs` default to the real
    config/devices.py globals but accept overrides, matching this
    codebase's established testability convention (see
    orchestration/topology.py::discover_topology()'s identical pattern)
    -- so this class can be exercised against synthetic multi-matrix
    configs in tests without touching real config or real hardware.
    """

    def __init__(self, sense_routing: dict = None, relay_matrix_configs: dict = None,
                 relay_factory_create=None):
        if sense_routing is None:
            from config.devices import SENSE_ROUTING
            sense_routing = SENSE_ROUTING
        if relay_matrix_configs is None:
            from config.devices import ETHERNET_DEVICES
            relay_matrix_configs = ETHERNET_DEVICES
        if relay_factory_create is None:
            from hardware.relay_factory import RelayFactory
            relay_factory_create = RelayFactory.create

        self._sense_routing = sense_routing
        self._relay_matrix_configs = relay_matrix_configs
        self._create = relay_factory_create
        self._routers_by_matrix: dict = {}   # matrix nickname -> NumatoSenseRouter

    def _router_for_matrix(self, matrix_name: str) -> NumatoSenseRouter:
        if matrix_name not in self._routers_by_matrix:
            matrix_cfg = self._relay_matrix_configs.get(matrix_name)
            if matrix_cfg is None:
                raise ConfigurationError(
                    f"ConfigDrivenSenseRouter: SENSE_ROUTING names relay matrix "
                    f"{matrix_name!r}, which has no config entry."
                )
            channel_to_relay = {
                channel: entry["relay"]
                for channel, entry in self._sense_routing.items()
                if entry["relay_matrix"] == matrix_name
            }
            relay = self._create(matrix_cfg)
            relay.connect()
            self._routers_by_matrix[matrix_name] = NumatoSenseRouter(relay, channel_to_relay)
        return self._routers_by_matrix[matrix_name]

    def _router_for_channel(self, channel: int) -> NumatoSenseRouter:
        if channel not in self._sense_routing:
            raise ConfigurationError(
                f"ConfigDrivenSenseRouter: sense channel {channel!r} is not configured "
                f"in config/devices.py::SENSE_ROUTING (known channels: "
                f"{sorted(self._sense_routing)})."
            )
        return self._router_for_matrix(self._sense_routing[channel]["relay_matrix"])

    def connect(self, channel: int) -> None:
        self._router_for_channel(channel).connect(channel)

    def disconnect(self, channel: int) -> None:
        self._router_for_channel(channel).disconnect(channel)

    def shutdown(self) -> None:
        """
        Best-effort disconnect of every relay matrix this router opened --
        provided for a future caller's cleanup path (not wired into
        HardwareManager.disconnect_all() yet -- see docs/architecture.md
        for why that integration is deliberately deferred). Never raises.
        """
        for router in self._routers_by_matrix.values():
            try:
                router.relay.disconnect()
            except Exception:
                pass
        self._routers_by_matrix.clear()


def read_battery_voltage_via_sense(dmm, sense_router, sense_channel):
    """
    The future integration point (see docs/architecture.md "Future
    Architecture: Battery Sense Routing" for exactly which
    BatteryOperationSequence call sites would eventually call this
    instead of `dmm.measure_dc_voltage()` directly). NOT called from
    anywhere in the real execution path today.

    If `sense_channel` is None -- every group today -- this is a pure
    passthrough to `dmm.measure_dc_voltage()`, identical to current
    behavior with zero added code path. Only when a group declares a
    sense_channel does the connect -> read -> disconnect workflow engage:

        1. sense_router.connect(sense_channel)
        2. voltage_v = dmm.measure_dc_voltage()
        3. sense_router.disconnect(sense_channel)  -- in a finally, so a
           read failure never leaves a sense channel connected
        4. return voltage_v

    `sense_router` is only required (must not be None) when
    `sense_channel` is not None.
    """
    if sense_channel is None:
        return dmm.measure_dc_voltage()
    sense_router.connect(sense_channel)
    try:
        return dmm.measure_dc_voltage()
    finally:
        sense_router.disconnect(sense_channel)
