"""
DMM (Digital Multimeter) driver. Covers NI 4065 used for independent
precision voltage verification.

connect()/disconnect()/identify() are real (NI-DMM session open/close +
instrument_model query) -- this is the connectivity/identification surface
exercised by Hardware Discovery. No measurement is triggered here; actual
readings are a separate, later step (this driver has no measure() yet --
none of the battery workflow code calls one).

Constructed from a config/devices.py DMM_CONFIG-shaped dict -- the same
config dict Hardware Discovery reads, so there is one source of truth for
the resource string (config/devices.py, not config/settings.py).
"""

from hardware.base import HardwareBase
from utils.errors import DMMError


class DMM(HardwareBase):
    """Controls an NI DMM card for independent voltage verification."""

    def __init__(self, cfg: dict):
        resource = cfg.get("resource", "")
        super().__init__(f"DMM_{resource}")
        self.resource  = resource
        self._model    = cfg.get("model", "NI-4065")
        self._simulate = bool(cfg.get("simulate", False))
        self._session  = None

    def connect(self):
        self.log.info("Opening DMM session: %s", self.resource)
        try:
            import nidmm
        except ImportError as e:
            raise DMMError(
                "Library 'nidmm' is not installed. Run: pip install nidmm"
            ) from e
        try:
            options = {"simulate": True} if self._simulate else {}
            self._session = nidmm.Session(resource_name=self.resource, options=options)
        except Exception as e:
            raise DMMError(f"DMM {self.resource} failed to open session: {e}") from e
        self.connected = True
        self.log.info("DMM session open: %s", self.resource)

    def disconnect(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception as e:
                self.log.warning("DMM session close failed for %s: %s", self.resource, e)
            self._session = None
        self.connected = False
        self.log.info("DMM session closed: %s", self.resource)

    def identify(self) -> str:
        """
        Identification only -- the connectivity/discovery surface. Does not
        trigger or read any measurement.
        """
        if self._session is None:
            raise DMMError(f"DMM {self.resource} is not connected")
        return self._session.instrument_model
