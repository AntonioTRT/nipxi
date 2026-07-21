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

    hw = HardwareManager(Settings, relay_cfg=dev_cfg.NUMATO_RELAY_MATRIX_CONFIG)  # production

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
from config import devices as dev_cfg
from hardware.smu import SMU
from hardware.daq import DAQ
from hardware.dmm import DMM
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

    def __init__(self, settings: Settings, relay_cfg: dict,
                 smu_cfg: dict = None, daq_cfg: dict = None, dmm_cfg: dict = None):
        """
        Build hardware driver objects from configuration.

        config/devices.py is the single source of truth for every device's
        resource string / address -- config/settings.py no longer duplicates
        it (see the removed PXI_RESOURCE_* constants in docs/CONFIGURATION.md's
        changelog note). smu_cfg/daq_cfg/dmm_cfg default to the first entry of
        config/devices.py's SMU_ASSIGNMENTS/DAQ_CONFIGS/DMM_CONFIGS so existing
        callers (main.py, test.py) do not need to change, but callers that
        manage multiple SMUs/DAQs may pass a specific device's cfg dict
        explicitly.

        Args:
            settings:  Settings class (class-level attributes, not instance).
            relay_cfg: NUMATO_RELAY_MATRIX_CONFIG (production -- Numato Relay
                       Matrix, Ethernet) or RELAY_CONFIG (serial, diagnostics
                       only) from config/devices.py. The factory reads
                       cfg["type"] to select the correct driver.
            smu_cfg:   A config/devices.py SMU_ASSIGNMENTS[...] dict. Defaults to
                       the first configured SMU.
            daq_cfg:   config/devices.py DAQ_CONFIG (or a DAQ_CONFIGS[...] entry).
                       Defaults to DAQ_CONFIG.
            dmm_cfg:   config/devices.py DMM_CONFIG (or a DMM_CONFIGS[...] entry).
                       Optional -- the DMM is not required for charge/discharge
                       cycling (independent voltage verification only). Defaults
                       to None (no DMM constructed) unless explicitly passed.

        Raises:
            HardwareInitError: if required config keys are missing.
        """
        self.s = settings
        self.log = logging.getLogger("nipxi.hw_manager")

        smu_cfg = smu_cfg or next(iter(dev_cfg.SMU_ASSIGNMENTS.values()))
        daq_cfg = daq_cfg or dev_cfg.DAQ_CONFIG

        # Build driver objects (no hardware communication yet)
        self._smu   = SMU(smu_cfg)
        self._daq   = DAQ(daq_cfg)
        self._relay = RelayFactory.create(relay_cfg)
        self._dmm   = DMM(dmm_cfg) if dmm_cfg else None

        relay_class = self._relay.__class__.__name__
        if relay_cfg.get("type", "serial").lower() == "ethernet":
            detail = f"IP: {relay_cfg.get('ip', '')}"
        else:
            detail = f"Port: {relay_cfg.get('port', '')}"
        self.log.info("Selected Relay: %s  %s", relay_class, detail)

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

    @property
    def dmm(self):
        """DMM driver (independent voltage verification), or None if not configured."""
        return self._dmm

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

            if self._dmm is not None:
                self._dmm.connect()
                connected.append(self._dmm)

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

        # 3. Disconnect relay, DMM (if present), SMU, DAQ (reverse of connect order)
        devices = [self._relay]
        if self._dmm is not None:
            devices.append(self._dmm)
        devices += [self._smu, self._daq]
        for dev in devices:
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

        devices = (self._smu, self._daq, self._relay)
        if self._dmm is not None:
            devices += (self._dmm,)

        for dev in devices:
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
        dmm_state = f" dmm={self._dmm.connected}" if self._dmm is not None else ""
        return (
            f"<HardwareManager "
            f"smu={self._smu.connected} "
            f"daq={self._daq.connected}{dmm_state} "
            f"relay={self._relay.connected}>"
        )
