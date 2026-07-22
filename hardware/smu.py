"""
SMU (Source Measure Unit) driver. Covers NI 4140, 4139, 4130 cards used as
the PMU/PSU for battery charge and discharge (there is no separate PMU/PSU
hardware or config in this project -- the SMU IS the PMU. "PMU" in
docs/architecture.md's "PMU Safety Philosophy" section refers to this class).

connect()/disconnect()/identify() are real (NI-DCPower session open/close +
instrument_model query + a hardware self-test). Sourcing functionality
(set_charge_mode, set_discharge_mode, output_enable, measure) is still a
TODO placeholder; implementing it is a separate, later step -- deliberately
NOT done here, since sourcing anything (even a tiny test current) is real
instrument functionality with real electrical consequences, not a
connectivity check.

output_disable()/verify_output_disabled()/emergency_output_off() ARE
implemented for real, unlike the sourcing methods above -- disabling output
is the inherently SAFE direction (mirrors why hardware/relay_eth.py
implemented open()/open_all() for real before close() was ever trusted
blind). See docs/architecture.md "PMU Safety Philosophy":

    Unknown PMU state = unsafe state. When in doubt, disable output and
    verify it. This takes precedence over continuing a test.

emergency_output_off(reason) is the single, public, non-recursive PMU
fail-safe reflex -- COMMAND (output_disable) -> READBACK
(verify_output_disabled, a real query of session.output_enabled, not
Python-side bookkeeping) -> log CRITICAL if verification fails (PMU may
still be sourcing/sinking current) -- called from every layer that can
detect a PMU-relevant failure: test_control/charge_cycle.py and
discharge_cycle.py (any exception during a charge/discharge loop),
test_control/safety_monitor.py::emergency_stop(), and
test_control/hardware_manager.py (startup safety + shutdown). None of
these callers need real sourcing to exist yet -- output_enable() being a
stub means there is currently no way for the PMU to actually be sourcing
anything, so this infrastructure is ready and waiting for when
output_enable()/set_charge_mode() are implemented for real, rather than
being retrofitted under time pressure later.

Verification philosophy (see docs/architecture.md "Instrument Verification
Philosophy" and hardware/relay_eth.py, which this mirrors): a bare identity
query is not a real verification -- an instrument can return its model
string even if its actual measurement/sourcing hardware is faulty. identify()
therefore does COMMAND (run the instrument's own built-in self-test) ->
READBACK (the self-test result code/message) -> VERIFY (code indicates
success, else raise SMUError) -> only then is the model string returned.
This is the strongest verification available before real sourcing exists,
and it never sources or measures anything -- self-test is a passive,
built-in instrument health check.

Constructed from a config/devices.py SMU_ASSIGNMENTS[...] dict -- the same
config dict HardwareManager and Hardware Discovery both read, so there is
one source of truth for the resource string (config/devices.py, not
config/settings.py).
"""

from hardware.base import HardwareBase
from utils.errors import SMUError


class SMU(HardwareBase):
    """
    Controls an NI SMU card for CC-CV charge and CC discharge (this
    project's PMU/PSU -- see the module docstring).

    Typical workflow:
        smu.connect()
        smu.identify()          # runs a real self-test, verifies it passed
        smu.set_charge_mode(current_a=0.5, voltage_limit_v=4.2)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.set_discharge_mode(current_a=0.5, voltage_limit_v=3.0)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.disconnect()

    Fail-safe reflex, available today even though sourcing isn't implemented
    yet:
        smu.emergency_output_off("reason")   # -> bool, never raises
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
        COMMAND (run the instrument's built-in self-test) -> READBACK (the
        self-test result code/message) -> VERIFY (code == 0, else raise) ->
        return the model string. Never enables output, configures charge/
        discharge mode, or sources/measures anything -- self-test is a
        passive, built-in instrument health check, not a real command.

        This is deliberately NOT a bare identity query: an instrument can
        answer "what model are you" even with faulty measurement/sourcing
        hardware. Self-test is the strongest verification available before
        real sourcing functionality exists (see the module docstring).
        """
        if self._session is None:
            raise SMUError(f"SMU {self.resource} is not connected")
        try:
            code, message = self._session.self_test()
        except Exception as e:
            raise SMUError(f"SMU {self.resource} self-test failed to run: {e}") from e
        if code != 0:
            raise SMUError(
                f"SMU {self.resource} self-test FAILED: code={code} message={message!r}"
            )
        self.log.info("SMU %s self-test PASSED (code=%d, %s)", self.resource, code, message)
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
        """
        Disable SMU output/sink (safe standby). Real, not a stub -- this is
        the safe direction and must be trustworthy on its own, since
        emergency_output_off() and every PMU safety caller depend on it.
        """
        if self._session is None:
            return
        try:
            self._session.output_enabled = False
        except Exception as e:
            raise SMUError(f"SMU {self.resource} failed to disable output: {e}") from e
        self.log.info("SMU %s output disabled.", self.resource)

    def verify_output_disabled(self) -> bool:
        """
        READBACK: query the instrument's actual output_enabled state --
        never trust Python-side bookkeeping. Returns True if disconnected
        (nothing can be sourcing when there is no session), False if the
        session reports output still enabled, False (never raises) if the
        query itself fails, since a PMU that can't confirm it is safe must
        be treated as unsafe.
        """
        if self._session is None:
            return True
        try:
            return not bool(self._session.output_enabled)
        except Exception as e:
            self.log.error(
                "SMU %s: failed to read back output_enabled state: %s", self.resource, e
            )
            return False

    def emergency_output_off(self, reason: str) -> bool:
        """
        Single, public, non-recursive PMU fail-safe reflex. Never raises.

        COMMAND (output_disable) -> READBACK+VERIFY (verify_output_disabled)
        -> log CRITICAL and return False if either step fails or leaves
        output verified-on, else return True.

        Callers (charge_cycle.py / discharge_cycle.py on any exception,
        safety_monitor.py::emergency_stop(), hardware_manager.py at startup
        and shutdown) must treat a False return as "PMU may still be
        sourcing/sinking current" -- unknown PMU state is unsafe state.
        """
        self.log.warning("SMU %s: emergency output off -- %s", self.resource, reason)
        try:
            self.output_disable()
        except Exception as e:
            self.log.critical(
                "SMU %s: emergency output off FAILED (%s) -- PMU may still be actively "
                "sourcing/sinking current. Physically disconnect power if this cannot "
                "be resolved immediately.", self.resource, e,
            )
            return False
        if not self.verify_output_disabled():
            self.log.critical(
                "SMU %s: output disable command sent but verification shows output "
                "still enabled -- PMU may still be actively sourcing/sinking current. "
                "Physically disconnect power if this cannot be resolved immediately.",
                self.resource,
            )
            return False
        self.log.info("SMU %s: emergency output off verified safe.", self.resource)
        return True

    def measure(self) -> dict:
        """Return instantaneous voltage and current reading."""
        # TODO: return self._session.measure(nidcpower.MeasurementTypes.VOLTAGE, CURRENT)
        return {"voltage_v": 0.0, "current_a": 0.0}
