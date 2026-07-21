"""
SMU (Source Measure Unit) driver. Covers NI 4140, 4139, 4130 cards used as
the PSU for battery charge and discharge (there is no separate PSU hardware
or config in this project -- the SMU is the PSU).

connect()/disconnect()/identify() are real (NI-DCPower session open/close +
instrument_model query) -- this is the connectivity/identification surface
exercised by Hardware Discovery. Charge/discharge/measure functionality
(set_charge_mode, set_discharge_mode, output_enable/disable, measure) is
still a TODO placeholder; implementing it is a separate, later step.

Constructed from a config/devices.py SMU_ASSIGNMENTS[...] dict -- the same
config dict HardwareManager and Hardware Discovery both read, so there is
one source of truth for the resource string (config/devices.py, not
config/settings.py).
"""

from hardware.base import HardwareBase
from utils.errors import SMUError


class SMU(HardwareBase):
    """
    Controls an NI SMU card for CC-CV charge and CC discharge.

    Typical workflow:
        smu.connect()
        smu.identify()          # connectivity/discovery only
        smu.set_charge_mode(current_a=0.5, voltage_limit_v=4.2)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.set_discharge_mode(current_a=0.5, voltage_limit_v=3.0)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.disconnect()
    """

    def __init__(self, cfg: dict):
        resource = cfg.get("resource", "")
        super().__init__(f"SMU_{resource}")
        self.resource = resource
        self._model    = cfg.get("model", "NI-SMU")
        self._simulate = bool(cfg.get("simulate", False))
        self._session  = None

    def connect(self):
        self.log.info("Opening SMU session: %s", self.resource)
        try:
            import nidcpower
        except ImportError as e:
            raise SMUError(
                "Library 'nidcpower' is not installed. Run: pip install nidcpower"
            ) from e
        try:
            options = {"simulate": True} if self._simulate else {}
            self._session = nidcpower.Session(resource_name=self.resource, options=options)
        except Exception as e:
            raise SMUError(f"SMU {self.resource} failed to open session: {e}") from e
        self.connected = True
        self.log.info("SMU session open: %s", self.resource)

    def disconnect(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception as e:
                self.log.warning("SMU session close failed for %s: %s", self.resource, e)
            self._session = None
        self.connected = False
        self.log.info("SMU session closed: %s", self.resource)

    def identify(self) -> str:
        """
        Identification only -- the connectivity/discovery surface. Does not
        enable output, configure charge/discharge mode, or source anything.
        """
        if self._session is None:
            raise SMUError(f"SMU {self.resource} is not connected")
        return self._session.instrument_model

    # ------------------------------------------------------------------
    # Charge/discharge functionality -- TODO, not implemented yet.
    # Out of scope for connectivity/discovery work; see docs/TODO.md.
    # ------------------------------------------------------------------

    def set_charge_mode(self, current_a: float, voltage_limit_v: float):
        """Configure CC-CV charge. Call before output_enable()."""
        self.log.debug("SMU charge mode: %.3f A / %.3f V", current_a, voltage_limit_v)
        # TODO: configure nidcpower for CC-CV source

    def set_discharge_mode(self, current_a: float, voltage_limit_v: float):
        """Configure CC discharge (sink). Call before output_enable()."""
        self.log.debug("SMU discharge mode: %.3f A / %.3f V", current_a, voltage_limit_v)
        # TODO: configure nidcpower for current sink

    def output_enable(self):
        """Enable SMU output/sink."""
        # TODO: self._session.initiate()
        self.log.info("SMU output enabled.")

    def output_disable(self):
        """Disable SMU output/sink (safe standby)."""
        # TODO: self._session.abort()
        self.log.info("SMU output disabled.")

    def measure(self) -> dict:
        """Return instantaneous voltage and current reading."""
        # TODO: return self._session.measure(nidcpower.MeasurementTypes.VOLTAGE, CURRENT)
        return {"voltage_v": 0.0, "current_a": 0.0}
