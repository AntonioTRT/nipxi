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

import atexit
import logging

from config.settings import Settings
from config import devices as dev_cfg
from config.system_mode import get_mode_policy
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
                 smu_cfg: dict = None, daq_cfg: dict = None, dmm_cfg: dict = None,
                 ntc_daq_cfg: dict = None):
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
            ntc_daq_cfg: config/devices.py hardware_for_group()'s "ntc_daq_cfg"
                       (a DAQ_CONFIGS[...] or USB_DAQ_DEVICES[...] entry) --
                       the group's NTC/temperature-reading DAQ, which may be a
                       physically different instrument from daq_cfg (e.g. a
                       temporary USB DAQ) or may resolve to the SAME entry as
                       daq_cfg (once one rack DAQ serves every role). Optional
                       -- None means this group has no NTC acquisition
                       capability yet. When it resolves to the identical cfg
                       dict as daq_cfg, no second driver instance/connection is
                       created -- see the identity check below -- so this
                       never double-connects to one physical device.

        Raises:
            HardwareInitError: if required config keys are missing.
        """
        self.s = settings
        self.log = logging.getLogger("nipxi.hw_manager")

        self._mode_policy = get_mode_policy(settings)
        self.hardware_status = {}   # populated by connect_all() -- see that method
        self.log.info(
            "System mode: %s -- %s",
            self._mode_policy.mode.value, self._mode_policy.description,
        )

        smu_cfg = smu_cfg or next(iter(dev_cfg.SMU_ASSIGNMENTS.values()))
        daq_cfg = daq_cfg or dev_cfg.DAQ_CONFIG

        # Build driver objects (no hardware communication yet)
        self._smu   = SMU(smu_cfg)
        self._daq   = DAQ(daq_cfg)
        self._relay = RelayFactory.create(relay_cfg)
        self._dmm   = DMM(dmm_cfg) if dmm_cfg else None

        # NTC/temperature DAQ -- a fifth, optional device role (see
        # docs/architecture.md "Dual DAQ Ownership Model"). `ntc_daq_cfg is
        # daq_cfg` (identity, not equality) is true exactly when
        # hardware_for_group()'s "ntc_daq" fell back to the group's own
        # "daq" -- the eventual production shape, one rack DAQ serving every
        # role. In that case self._ntc_daq IS self._daq: the same physical
        # instrument is never connected twice.
        if ntc_daq_cfg is None:
            self._ntc_daq = None
        elif ntc_daq_cfg is daq_cfg:
            self._ntc_daq = self._daq
        else:
            self._ntc_daq = DAQ(ntc_daq_cfg)

        relay_class = self._relay.__class__.__name__
        if relay_cfg.get("type", "serial").lower() == "ethernet":
            detail = f"IP: {relay_cfg.get('ip', '')}"
        else:
            detail = f"Port: {relay_cfg.get('port', '')}"
        self.log.info("Selected Relay: %s  %s", relay_class, detail)

        # Application-exit safety net (see docs/architecture.md "Emergency
        # Shutdown Strategy" and "PMU Shutdown Safe State"): registered once,
        # here, so it exists even if connect_all() only partially succeeds.
        # This is a SECOND, independent attempt at "all relays OFF / PMU
        # output OFF on exit" -- it does not replace disconnect_all()'s own
        # try/finally-driven call, which is the primary path; this catches
        # process-exit paths that bypass it (an exception during interpreter
        # shutdown, os._exit() elsewhere, etc). No-ops if a device was never
        # connected or already safely disconnected. Cannot catch SIGKILL /
        # hard process kill -- nothing in userspace can.
        atexit.register(self._atexit_relay_shutdown)
        atexit.register(self._atexit_smu_shutdown)

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

    @property
    def ntc_daq(self):
        """NTC/temperature DAQ driver for this group, or None if not configured.
        May be the same instance as `daq` (see __init__) -- callers must not
        assume a distinct connection just because this property is non-None."""
        return self._ntc_daq

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect_all(self):
        """
        Connect all hardware devices. Behavior depends on the active
        SYSTEM_MODE (config/system_mode.py) -- see docs/architecture.md
        "System Modes":

          PRODUCTION (strict_hardware=True): all-or-nothing, exactly as
          before -- any device failing to connect rolls back whatever
          already connected and raises HardwareInitError.

          DEVELOPMENT / VALIDATION (strict_hardware=False): each device
          connects independently; a missing/unreachable device is logged
          (WARNING in DEVELOPMENT, ERROR in VALIDATION -- "test failure"
          per the mode spec) and recorded in self.hardware_status, but
          does NOT stop startup or roll back devices that already
          connected. "Framework may still launch" even with hardware missing.

        In EVERY mode, startup safety (see docs/architecture.md "Emergency
        Shutdown Strategy") is unconditional: if the relay connects, ALL
        relays are forced OFF and verified before this method returns, and
        a FAILURE to confirm that (relay present but its state cannot be
        verified safe) always aborts startup -- unknown relay state = unsafe
        state is never relaxed by mode. Only a genuinely MISSING relay is
        tolerated in DEVELOPMENT/VALIDATION.

        Raises:
            HardwareInitError: on any strict-mode failure, or if a
            connected relay's startup force-off/verify fails (any mode).
        """
        self.log.info("Connecting hardware (mode=%s)...", self._mode_policy.mode.value)

        if self._mode_policy.strict_hardware:
            self._connect_all_strict()
        else:
            self._connect_all_lenient()

        self.log.info("Hardware connection phase complete (mode=%s).", self._mode_policy.mode.value)

    def _connect_all_strict(self):
        """PRODUCTION: all-or-nothing. See connect_all()'s docstring."""
        connected = []
        try:
            self._daq.connect()
            connected.append(self._daq)

            if self._ntc_daq is not None and self._ntc_daq is not self._daq:
                self._ntc_daq.connect()
                connected.append(self._ntc_daq)

            self._smu.connect()
            connected.append(self._smu)

            # PMU startup safety (see docs/architecture.md "PMU Startup Safe
            # State"): never assume the PMU starts in a safe state. Force
            # output OFF and verify before any battery operation is allowed,
            # in every mode -- a failure here aborts startup exactly like an
            # unverifiable relay does.
            if not self._smu.emergency_output_off("startup safety check"):
                raise HardwareInitError(
                    "PMU startup safety check failed: output could not be verified OFF."
                )
            self.log.info("Startup safety: PMU output forced OFF and verified.")

            if self._dmm is not None:
                self._dmm.connect()
                connected.append(self._dmm)

            self._relay.connect()
            connected.append(self._relay)

            # Startup safety: guarantee a known, verified all-off baseline
            # before any relay operation is ever requested.
            self._relay.open_all()
            self.log.info("Startup safety: all relays forced OFF and verified.")

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

    def _connect_all_lenient(self):
        """
        DEVELOPMENT/VALIDATION: each device connects independently -- a
        missing device does not stop startup or roll back devices that
        already connected. See connect_all()'s docstring for the one
        exception (relay present but unverifiable is still fatal, in any mode).
        """
        level = self._mode_policy.hardware_failure_log_level
        self.hardware_status = {}

        def _try_connect(dev, label):
            if dev is None:
                return
            try:
                dev.connect()
                self.hardware_status[label] = {"connected": True, "error": None}
            except Exception as e:
                self.log.log(
                    level, "%s not available (%s mode, startup continues): %s",
                    label, self._mode_policy.mode.value, e,
                )
                self.hardware_status[label] = {"connected": False, "error": str(e)}

        _try_connect(self._daq, "DAQ")
        _try_connect(self._ntc_daq if self._ntc_daq is not self._daq else None, "NTC_DAQ")
        _try_connect(self._smu, "SMU")
        _try_connect(self._dmm, "DMM")
        _try_connect(self._relay, "Relay")

        # PMU startup safety is unconditional in every mode, mirroring the
        # relay rule below -- but only meaningful if the SMU actually
        # connected. An SMU that IS connected but whose output cannot be
        # confirmed OFF is always fatal, regardless of mode: unknown PMU
        # state = unsafe state is never relaxed just because we're in
        # DEVELOPMENT/VALIDATION. See docs/architecture.md "PMU Startup Safe
        # State".
        if self._smu.connected:
            if not self._smu.emergency_output_off("startup safety check"):
                self.log.critical(
                    "SMU connected but startup output-off/verify FAILED. "
                    "Aborting startup regardless of mode -- an unverifiable "
                    "PMU state is never acceptable."
                )
                for dev in (self._daq, self._ntc_daq, self._smu, self._dmm, self._relay):
                    if dev is not None:
                        try:
                            if dev.connected:
                                dev.disconnect()
                        except Exception:
                            pass
                self.hardware_status["SMU"] = {
                    "connected": False,
                    "error": "connected but startup output-off/verify failed",
                }
                raise HardwareInitError("PMU startup safety check failed.")
            self.log.info("Startup safety: PMU output forced OFF and verified.")

        # Startup safety is unconditional -- but only meaningful if the
        # relay actually connected. A relay that IS connected but cannot be
        # confirmed in a safe (all-off, verified) state is always fatal,
        # regardless of mode: unknown relay state = unsafe state is never
        # relaxed just because we're in DEVELOPMENT/VALIDATION.
        if self._relay.connected:
            try:
                self._relay.open_all()
                self.log.info("Startup safety: all relays forced OFF and verified.")
            except Exception as e:
                self.log.critical(
                    "Relay connected but startup force-off/verify FAILED: %s. "
                    "Aborting startup regardless of mode -- an unverifiable "
                    "relay state is never acceptable.", e,
                )
                for dev in (self._daq, self._ntc_daq, self._smu, self._dmm, self._relay):
                    if dev is not None:
                        try:
                            if dev.connected:
                                dev.disconnect()
                        except Exception:
                            pass
                self.hardware_status["Relay"] = {
                    "connected": False,
                    "error": f"connected but startup force-off/verify failed: {e}",
                }
                raise HardwareInitError(f"Startup safety check failed: {e}") from e
        else:
            self.log.log(
                level, "Relay not connected -- startup safe-state could not be "
                "verified (%s mode).", self._mode_policy.mode.value,
            )

        missing = [name for name, status in self.hardware_status.items() if not status["connected"]]
        if missing:
            self.log.log(
                level, "Hardware connection phase finished with missing device(s): %s "
                "(%s mode -- startup continues).", missing, self._mode_policy.mode.value,
            )
        else:
            self.log.info("All hardware connected.")

    def disconnect_all(self):
        """
        Disconnect all hardware in the safe shutdown order.

        Order: disable SMU output (retried, verified -- see
               hardware/smu.py::SMU.emergency_output_off()) -> open all
               relays -> disconnect relay -> disconnect SMU (SKIPPED if its
               output state could not be verified -- see step 1) ->
               disconnect DAQ.

        Errors during disconnect are logged but not re-raised, so that a
        failure on one device does not prevent the others from disconnecting.
        """
        self.log.warning("[SHUTDOWN-TRACE] disconnect_all() entered")
        self.log.info("Disconnecting hardware...")

        # 1. PMU output OFF, verified -- emergency_output_off() itself now
        #    retries internally (Settings.EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS)
        #    and distinguishes "genuinely still enabled" from "verification
        #    communication failure" (see hardware/smu.py::
        #    OutputVerificationResult). If every attempt still fails, this
        #    is logged as CRITICAL, not a warning, and -- unlike before --
        #    the SMU is deliberately EXCLUDED from step 3's disconnect()
        #    loop below: closing an NI-DCPower session does not guarantee
        #    the instrument returns output to a safe state if
        #    output_enabled was never successfully verified False, so
        #    closing an unverified session would trade a known-unsafe,
        #    still-monitorable state for an unknown, abandoned one. Per
        #    "unknown state = unsafe state," the session is left open
        #    under this process's control instead. See docs/architecture.md
        #    "Shutdown Safety -- Bounded Retry + Distinct Failure Modes"
        #    for the full rationale and the operational tradeoff this
        #    implies (the SMU resource may stay unavailable to a later
        #    connect_all() call until this process exits).
        smu_output_verified_safe = True
        if self._smu.connected:
            smu_output_verified_safe = self._smu.emergency_output_off("normal shutdown")
            if not smu_output_verified_safe:
                self.log.critical(
                    "SMU output could not be verified OFF during shutdown after retries. "
                    "PMU may still be actively sourcing/sinking current -- physically "
                    "disconnect power if this cannot be resolved immediately."
                )
                self.log.critical(
                    "[SHUTDOWN-TRACE] disconnect_all(): SMU session will NOT be closed -- "
                    "output state could not be verified even after retries. Leaving the "
                    "session open under software control rather than closing an unverified "
                    "session. This SMU resource may remain unavailable to a subsequent "
                    "connect_all() until this process exits."
                )

        # 2. Open all relays -- physically disconnect all batteries. By the
        #    time this raises, the driver has already made its own internal
        #    emergency-shutdown attempt (see NumatoRelayMatrix.verify_all()/
        #    _emergency_all_off()) -- a failure here is therefore already a
        #    second failed attempt and is logged as CRITICAL, not a warning.
        #    Always attempted regardless of the SMU outcome above -- opening
        #    the relay physically isolates the battery from the SMU circuit,
        #    which matters MORE, not less, when the SMU's own state is
        #    uncertain.
        if self._relay.connected:
            try:
                self._relay.open_all()
            except Exception as e:
                self.log.critical(
                    "relay.open_all() failed during shutdown: %s. Hardware may "
                    "still be energized -- physically disconnect power if this "
                    "cannot be resolved immediately.", e,
                )

        # 2b. Post-isolation defense-in-depth, not safety-critical --
        #     attempted unconditionally (regardless of the SMU/relay
        #     outcomes above, and before the SMU session is possibly
        #     closed in step 3 below), since the SMU output was already
        #     commanded off in step 1 either way. See docs/architecture.md
        #     "Post-Isolation SMU Setpoint Zeroing".
        if self._smu.connected:
            try:
                self._smu.zero_output_setpoint_best_effort("normal shutdown")
            except Exception as e:
                self.log.warning("Post-isolation SMU setpoint-zeroing raised unexpectedly "
                                  "(non-critical, shutdown already complete): %s", e)

        # 3. Disconnect relay, DMM (if present), SMU (only if its output was
        #    verified OFF in step 1), DAQ, NTC_DAQ (if a distinct instance)
        #    -- reverse of connect order.
        devices = [self._relay]
        if self._dmm is not None:
            devices.append(self._dmm)
        if smu_output_verified_safe:
            devices.append(self._smu)
        devices.append(self._daq)
        if self._ntc_daq is not None and self._ntc_daq is not self._daq:
            devices.append(self._ntc_daq)
        for dev in devices:
            try:
                if dev.connected:
                    dev.disconnect()
            except Exception as e:
                self.log.warning("disconnect() failed for %s: %s", dev.name, e)

        self.log.info("All hardware disconnected.")
        self.log.warning("[SHUTDOWN-TRACE] disconnect_all() completed")

    def _atexit_relay_shutdown(self):
        """
        Registered via atexit() in __init__ -- a second, independent safety
        net for "all relays OFF on exit" alongside disconnect_all(). Covers
        process-exit paths that bypass a try/finally around disconnect_all()
        (an exception during interpreter shutdown, os._exit() called
        elsewhere, etc). No-ops if the relay was never connected or has
        already been safely disconnected (the normal case -- this is a
        backstop, not the primary path).

        Never raises -- atexit callbacks must not raise; an exception here
        would be printed by Python and could prevent other registered atexit
        handlers from running. Logs CRITICAL on failure instead.
        """
        try:
            if self._relay.connected:
                self.log.warning("atexit: forcing all relays OFF as a final safety net.")
                self._relay.open_all()
        except Exception as e:
            self.log.critical(
                "atexit emergency relay shutdown FAILED: %s. Hardware may still "
                "be energized -- physically disconnect power if this cannot be "
                "resolved immediately.", e,
            )

    def _atexit_smu_shutdown(self):
        """
        Registered via atexit() in __init__ -- a second, independent safety
        net for "PMU output OFF on exit" alongside disconnect_all(). Same
        rationale as _atexit_relay_shutdown(): covers process-exit paths
        that bypass a try/finally around disconnect_all(). No-ops if the
        SMU was never connected or has already been safely disabled.

        Never raises -- atexit callbacks must not raise. Logs CRITICAL on
        failure instead.
        """
        try:
            if self._smu.connected:
                self.log.warning("atexit: forcing PMU output OFF as a final safety net.")
                if not self._smu.emergency_output_off("atexit safety net"):
                    self.log.critical(
                        "atexit emergency PMU output-off FAILED to verify. PMU may "
                        "still be actively sourcing/sinking current -- physically "
                        "disconnect power if this cannot be resolved immediately."
                    )
        except Exception as e:
            self.log.critical(
                "atexit emergency PMU shutdown raised unexpectedly: %s. PMU may "
                "still be actively sourcing/sinking current -- physically "
                "disconnect power if this cannot be resolved immediately.", e,
            )

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
        if self._ntc_daq is not None and self._ntc_daq is not self._daq:
            devices += (self._ntc_daq,)

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
        if self._ntc_daq is None:
            ntc_daq_state = ""
        elif self._ntc_daq is self._daq:
            ntc_daq_state = " ntc_daq=(shared with daq)"
        else:
            ntc_daq_state = f" ntc_daq={self._ntc_daq.connected}"
        return (
            f"<HardwareManager "
            f"smu={self._smu.connected} "
            f"daq={self._daq.connected}{dmm_state}{ntc_daq_state} "
            f"relay={self._relay.connected}>"
        )
