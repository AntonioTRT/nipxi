"""
Hardware Manager
================

Centralizes all hardware lifecycle management.

Responsibilities:
  - Create hardware driver instances from configuration
  - Connect and disconnect all devices in the correct order
  - Perform a health check to confirm devices are reachable
  - Provide a single access point for hardware objects

Usage:
    from test_control.hardware_manager import HardwareManager
    from config.settings import Settings
    from config import devices as dev_cfg

    hw = HardwareManager(Settings, relay_cfg=dev_cfg.RELAY_ETH_CONFIG)  # production: Ethernet

    with hw:                        # calls connect_all() / disconnect_all()
        smu   = hw.smu
        daq   = hw.daq
        relay = hw.relay
        ...

    # or explicit:
    hw.connect_all()
    try:
        ...
    finally:
        hw.disconnect_all()

Adding a new device:
  1. Instantiate it in __init__() and store as a private attribute.
  2. Add a property to expose it.
  3. Call connect() in connect_all() (after dependencies).
  4. Call disconnect() in disconnect_all() (before dependencies).
"""

import logging

from config.settings import Settings
from hardware.smu import SMU
from hardware.daq import DAQ
from hardware.relay_factory import RelayFactory
from utils.errors import HardwareInitError


class HardwareManager:
    """
    Owns and manages the lifecycle of all physical hardware drivers.

    Hardware is created from configuration in __init__(), but not connected
    until connect_all() is called. This allows the object to be created early
    (e.g. for inspection) without touching hardware.

    Disconnect order is the reverse of connect order, and always open-all-relays
    before closing the connection -- ensures batteries are disconnected on any exit.
    """

    def __init__(self, settings: Settings, relay_cfg: dict):
        """
        Build hardware driver objects from configuration.

        Args:
            settings:   Settings class (class-level attributes, not instance).
            relay_cfg:  RELAY_ETH_CONFIG (production -- Numato Ethernet relay) or
                        RELAY_CONFIG (serial, diagnostics only) from config/devices.py.
                        The factory reads cfg["type"] to select the correct driver.

        Raises:
            HardwareInitError: if required config keys are missing.
        """
        self.s = settings
        self.log = logging.getLogger("nipxi.hw_manager")

        # Build driver objects (no hardware communication yet)
        self._smu   = SMU(settings.PXI_RESOURCE_SMU1)
        self._daq   = DAQ(settings.PXI_RESOURCE_DAQ)
        self._relay = RelayFactory.create(relay_cfg)

        relay_class = self._relay.__class__.__name__
        if relay_cfg.get("type", "serial").lower() == "ethernet":
            detail = f"IP: {relay_cfg.get('ip', '')}"
        else:
            detail = f"Port: {relay_cfg.get('port', '')}"
        self.log.info("Selected Relay: %s  %s", relay_class, detail)

        # DMM is optional: used for independent voltage verification, not required for cycling
        self._dmm = None

    # ------------------------------------------------------------------
    # Public device accessors
    # ------------------------------------------------------------------

    @property
    def smu(self) -> SMU:
        """Source Measure Unit driver (charge / discharge)."""
        return self._smu

    @property
    def daq(self) -> DAQ:
        """Data Acquisition card driver (voltage / current / NTC)."""
        return self._daq

    @property
    def relay(self):
        """Relay matrix driver (RelayBase -- serial or Ethernet)."""
        return self._relay

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect_all(self):
        """
        Connect all hardware devices.

        Connect order: DAQ first (read-only, safest), then SMU, then relay.
        If any connection fails, disconnect whatever was already connected
        so the system does not end up in a partial state.

        Raises:
            HardwareInitError: wraps the underlying driver exception with context.
        """
        self.log.info("Connecting hardware...")

        connected = []
        try:
            self._daq.connect()
            connected.append(self._daq)

            self._smu.connect()
            connected.append(self._smu)

            self._relay.connect()
            connected.append(self._relay)

        except Exception as e:
            self.log.error("Hardware init failed: %s", e)
            # Roll back whatever connected before the failure
            for dev in reversed(connected):
                try:
                    dev.disconnect()
                except Exception:
                    pass
            raise HardwareInitError(f"Hardware initialization failed: {e}") from e

        self.log.info("All hardware connected.")

    def disconnect_all(self):
        """
        Disconnect all hardware in the safe shutdown order.

        Order: disable SMU output -> open all relays -> disconnect relay ->
               disconnect SMU -> disconnect DAQ.

        Errors during disconnect are logged but not re-raised, so that a
        failure on one device does not prevent the others from disconnecting.
        """
        self.log.info("Disconnecting hardware...")

        # 1. Disable SMU output -- stop any active current flow
        if self._smu.connected:
            try:
                self._smu.output_disable()
            except Exception as e:
                self.log.warning("SMU output_disable failed during shutdown: %s", e)

        # 2. Open all relays -- physically disconnect all batteries
        if self._relay.connected:
            try:
                self._relay.open_all()
            except Exception as e:
                self.log.warning("relay.open_all() failed during shutdown: %s", e)

        # 3. Disconnect relay, SMU, DAQ (reverse of connect order)
        for dev in (self._relay, self._smu, self._daq):
            try:
                if dev.connected:
                    dev.disconnect()
            except Exception as e:
                self.log.warning("disconnect() failed for %s: %s", dev.name, e)

        self.log.info("All hardware disconnected.")

    def health_check(self) -> dict:
        """
        Verify that all connected devices are reachable and report their status.

        Returns:
            dict: {device_name: {"ok": bool, "detail": str}, ...}

        Does not raise -- returns status for each device individually so that
        partial failures can be reported clearly.
        """
        results = {}

        for dev in (self._smu, self._daq, self._relay):
            key = dev.name
            if not dev.connected:
                results[key] = {"ok": False, "detail": "not connected"}
                continue
            try:
                # Each driver should implement a lightweight check (e.g. query state).
                # Until real drivers exist, connected=True is the check.
                results[key] = {"ok": True, "detail": "connected"}
            except Exception as e:
                results[key] = {"ok": False, "detail": str(e)}

        return results

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.connect_all()
        return self

    def __exit__(self, *_):
        self.disconnect_all()

    def __repr__(self):
        return (
            f"<HardwareManager "
            f"smu={self._smu.connected} "
            f"daq={self._daq.connected} "
            f"relay={self._relay.connected}>"
        )
