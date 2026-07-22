"""
DMM (Digital Multimeter) driver. Covers NI 4065 used for independent
precision voltage verification.

connect()/disconnect()/identify() are real (NI-DMM session open/close +
instrument_model query + a hardware self-test). measure_dc_voltage() is
also real -- unlike the SMU/DAQ, a DMM measurement is inherently passive
(it only observes, it cannot source/energize anything), so there is no
safety reason to defer it the way SMU sourcing is deferred.

Verification philosophy (see docs/architecture.md "Instrument Verification
Philosophy" and hardware/relay_eth.py, which this mirrors): a bare identity
query is not a real verification. identify() does COMMAND (run the
instrument's built-in self-test) -> READBACK (result code/message) ->
VERIFY (code indicates success, else raise DMMError) -> return the model
string. measure_dc_voltage() does COMMAND (configure + trigger a DC volts
measurement) -> READBACK (the measured value) -> VERIFY (finite, within
the configured range) -> return it, raising DMMError on any failure --
never a bare "call the API and assume it worked."

Constructed from a config/devices.py DMM_CONFIG-shaped dict -- the same
config dict Hardware Discovery reads, so there is one source of truth for
the resource string (config/devices.py, not config/settings.py).
"""

import math

from hardware.base import HardwareBase
from utils.errors import DMMError


class DMM(HardwareBase):
    """Controls an NI DMM card for independent voltage verification."""

    def __init__(self, cfg: dict):
        resource = cfg.get("resource", "")
        super().__init__(f"DMM_{resource}")
        self.resource   = resource
        self._model     = cfg.get("model", "NI-4065")
        self._range_v   = float(cfg.get("range_v", 10.0))
        self._simulate  = bool(cfg.get("simulate", False))
        self._session   = None

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
        COMMAND (run the instrument's built-in self-test) -> READBACK (the
        self-test result code/message) -> VERIFY (code == 0, else raise) ->
        return the model string. Does not trigger or read a voltage
        measurement -- see measure_dc_voltage() for that.
        """
        if self._session is None:
            raise DMMError(f"DMM {self.resource} is not connected")
        try:
            self._session.self_test()
        except Exception as e:
            code = getattr(e, "code", None)
            message = getattr(e, "message", str(e))
            raise DMMError(
                f"DMM {self.resource} self-test FAILED: code={code} message={message!r}"
            ) from e
        self.log.info("DMM %s self-test PASSED", self.resource)
        return self._session.instrument_model

    def measure_dc_voltage(self) -> float:
        """
        Real DC voltage measurement -- COMMAND (configure the DMM for a DC
        volts measurement at the configured range and trigger a read) ->
        READBACK (the measured value) -> VERIFY (finite number, within the
        configured range -- not NaN/inf, not silently out of bounds) ->
        return it. Raises DMMError on any failure, including a value that
        is technically returned but fails verification -- never assumes a
        successful function call means the reading is trustworthy.

        Safe to call regardless of what else is connected: a DMM
        measurement is passive (it only observes), unlike SMU sourcing.
        """
        if self._session is None:
            raise DMMError(f"DMM {self.resource} is not connected")
        try:
            import nidmm
            self._session.configure_measurement_digits(
                nidmm.Function.DC_VOLTS, range=self._range_v, resolution_digits=5.5,
            )
            value = self._session.read()
        except Exception as e:
            raise DMMError(f"DMM {self.resource} measurement failed: {e}") from e

        if not math.isfinite(value):
            raise DMMError(
                f"DMM {self.resource} measurement FAILED verification: "
                f"reading is not a finite number ({value!r})"
            )
        margin = abs(self._range_v) * 0.05  # allow the DMM's own overrange headroom
        if abs(value) > abs(self._range_v) + margin:
            raise DMMError(
                f"DMM {self.resource} measurement FAILED verification: "
                f"{value:.6f} V is outside the configured range "
                f"(+/-{self._range_v} V, +5% overrange margin)"
            )
        self.log.info("DMM %s DC volts measurement: %.6f V (range +/-%.1f V)",
                      self.resource, value, self._range_v)
        return value
