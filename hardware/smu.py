"""
SMU (Source Measure Unit) driver. Covers NI 4140, 4139, 4130 cards used as
the PMU/PSU for battery charge and discharge (there is no separate PMU/PSU
hardware or config in this project -- the SMU IS the PMU. "PMU" in
docs/architecture.md's "PMU Safety Philosophy" section refers to this class).

connect()/disconnect()/identify() are real (NI-DCPower session open/close +
instrument_model query + a hardware self-test). Battery charge/discharge
sourcing (set_charge_mode, set_discharge_mode, output_enable, measure) is
still a TODO placeholder; implementing it is a separate, later step --
deliberately NOT done here, since sourcing anything for a real battery
channel has real electrical consequences well beyond a connectivity check.

source_dc_voltage_point() IS real, but is a separate, narrow capability:
a single static bench DC voltage point for SMU Functional Validation only
(see test.py's SMU Functional Validation workflow) -- no relay, no
battery channel, no charge/discharge mode. It always disables output
again before returning, and the caller always derives its voltage/current
arguments from existing config (config/devices.py + config/settings.py),
never from raw operator input.

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
test_control/hardware_manager.py (startup safety + shutdown), and now
also source_dc_voltage_point()'s own `finally` block (SMU Functional
Validation always ends every step, PASS or FAIL, by disabling output).
None of the charge/discharge callers above need battery sourcing to exist
yet -- output_enable()/set_charge_mode() being stubs means there is
currently no way for the PMU to be sourcing anything during a battery
cycle, so this infrastructure was ready and waiting for when they are
implemented for real, rather than being retrofitted under time pressure
later.

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

import time

from config.settings import Settings
from hardware.base import HardwareBase
from utils.errors import SMUError, SMUStateVerificationError


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
        # NI-DCPower channel name this instance operates on -- from
        # config/devices.py's PXI_SLOTS[...]["smu_channel"] (via SMU_ASSIGNMENTS
        # for HardwareManager, or the raw PXI_SLOTS entry for test.py). Single-
        # channel cards (4141, 4139) always use "0"; multi-channel cards
        # (4130) need this set explicitly per instance in config -- never
        # hardcoded here, since which physical channel is wired varies per
        # unit/installation.
        self._channel  = cfg.get("smu_channel", "0")
        self._session  = None

    def connect(self):
        self.log.info("Opening SMU session: %s (channel %s)", self.resource, self._channel)
        try:
            import nidcpower
        except ImportError as e:
            raise SMUError(
                "Library 'nidcpower' is not installed. Run: pip install nidcpower"
            ) from e
        try:
            options = {"simulate": True} if self._simulate else {}
            # Scoping the session to exactly one channel (from config, not
            # hardcoded) makes every repeated-capability property/method
            # below (voltage_level, output_enabled, measure(), etc.)
            # unambiguous -- this is what makes the same driver code work for
            # both single-channel (4141, 4139) and multi-channel (4130) cards.
            # An unscoped session on a multi-channel card raises NI-DCPower
            # error -1074118522 ("requested function only allows a single
            # channel to be specified") the moment any such property is set.
            self._session = nidcpower.Session(
                resource_name=self.resource, channels=self._channel, options=options
            )
        except Exception as e:
            raise SMUError(
                f"SMU {self.resource} channel {self._channel} failed to open session: {e}"
            ) from e
        self.connected = True
        self.log.info("SMU session open: %s (channel %s)", self.resource, self._channel)

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
            self._session.self_test()
        except Exception as e:
            code = getattr(e, "code", None)
            message = getattr(e, "message", str(e))
            raise SMUError(
                f"SMU {self.resource} self-test FAILED: code={code} message={message!r}"
            ) from e
        self.log.info("SMU %s self-test PASSED", self.resource)
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

    # ------------------------------------------------------------------
    # Configuration verification -- COMMAND -> READBACK -> VERIFY for
    # NI-DCPower session attributes (voltage_level, current_limit,
    # output_enabled), mirroring hardware/relay_eth.py's verify_single()/
    # verify_all(). Used by source_dc_voltage_point() below.
    # ------------------------------------------------------------------

    def _verify_config_readback(self, label: str, expected, actual, tolerance: float = None):
        """
        Compare an NI-DCPower attribute readback (`actual`, read from the
        session after commit()) against the value just commanded
        (`expected`). Raises SMUStateVerificationError on mismatch --
        always fatal, execution must stop rather than proceed with an
        unverified/ambiguous SMU configuration (same policy as
        hardware/relay_eth.py's RelayStateVerificationError).

        `tolerance` is None for exact-match properties (`output_enabled`,
        a bool). For numeric properties (`voltage_level`, `current_limit`),
        pass a small attribute-round-trip tolerance (see
        config/settings.py's SMU_VOLTAGE_READBACK_TOLERANCE_V /
        SMU_CURRENT_READBACK_TOLERANCE_A) -- these attributes are stored
        IVI properties echoed back by the driver, NOT a new ADC
        measurement (session.measure() is the real measurement, used
        separately in source_dc_voltage_point() below), so the tolerance
        only needs to bound floating-point round-trip and instrument
        coercion to its nearest programmable step -- never a measurement-
        accuracy figure.
        """
        mismatch = (actual != expected) if tolerance is None else (abs(actual - expected) > tolerance)
        if mismatch:
            self.log.error(
                "SMU %s channel %s: configuration verification FAILED for %s -- "
                "expected %r, readback %r",
                self.resource, self._channel, label, expected, actual,
            )
            raise SMUStateVerificationError(
                f"SMU {self.resource} channel {self._channel}: {label} verification "
                f"FAILED -- expected {expected!r}, readback {actual!r}. Execution stopped."
            )
        self.log.debug(
            "SMU %s channel %s: %s verified (expected %r, readback %r)",
            self.resource, self._channel, label, expected, actual,
        )

    # ------------------------------------------------------------------
    # Functional Validation -- bench-only DC voltage sourcing. Separate
    # from set_charge_mode()/set_discharge_mode()/output_enable() above
    # (which remain placeholders for real battery charge/discharge): this
    # method is real, but deliberately narrow -- a single static DC
    # voltage point, no relay, no channel, no battery involved. The
    # caller (test.py's SMU Functional Validation workflow) always derives
    # voltage_v/current_limit_a/voltage_range_v from config
    # (config/devices.py + config/settings.py) -- this method itself does
    # not choose or bound them beyond what the driver requires structurally.
    # ------------------------------------------------------------------

    def source_dc_voltage_point(self, voltage_v: float, current_limit_a: float,
                                 voltage_range_v: float, hold_s: float = 0.0,
                                 during_hold=None) -> dict:
        """
        Source a single static DC voltage point and confirm it electrically.

        COMMAND (configure DC_VOLTAGE output at voltage_v, current_limit_a
        compliance, enable output, commit()) -> READBACK + VERIFY the
        instrument actually accepted that configuration (voltage_level,
        current_limit, output_enabled all read back from the session and
        compared to what was just commanded via _verify_config_readback() --
        raises SMUStateVerificationError on any mismatch, execution stops) ->
        READBACK (query_in_compliance() + measure the SMU's own voltage AND
        current) -> VERIFY (not in current-limit compliance -- a compliance
        hit indicates a short or unexpected load, not a successful source
        point) -> return a dict with both the configuration readbacks and
        the runtime measurements (see the return statement below for the
        exact keys -- test.py's SMU Functional Validation workflow displays
        all of them, clearly labeled as one or the other).

        Output is always disabled again before this method returns or
        raises -- see the `finally` block below -- and that disable is
        itself verified (verify_output_disabled()), logged CRITICAL (not
        raised -- a teardown step must never mask whatever exception is
        already propagating) if it fails to confirm OFF.

        Never asserts the MEASURED voltage/current matches the commanded
        setpoint to some tolerance -- that is a distinct question from
        configuration verification above (the instrument accepted 4.200 V
        as its setpoint is not the same claim as "the output physically
        settled to exactly 4.200 V", and a real battery load makes the two
        diverge by design during CC-CV charging). There is no project-
        configured measurement tolerance to compare the physical reading
        against, and the operator's handheld DMM is the actual verification
        instrument for this step (see test.py's SMU Functional Validation
        workflow) -- the measured values here are reported as informational
        context only.

        `hold_s` (default 0.0 -- existing callers unaffected) keeps output
        enabled for `hold_s` seconds AFTER the SMU's own measurement is
        taken and BEFORE the `finally` block disables it -- used by
        test_control/proto_test_sequence.py::ProtoTestSequence (Proto Test
        Execution, Milestone 2) to dwell on a relay with output still
        active. `during_hold` (default None), if given, is called with no
        arguments once, immediately after the SMU's own measurement and
        BEFORE the `hold_s` sleep -- lets a caller take an external reading
        (e.g. DMM) while output is still genuinely active, without this
        driver knowing anything about what `during_hold` is or does. Its
        return value is included in the result dict as
        `"during_hold_result"` (None if `during_hold` was not given).
        """
        if self._session is None:
            raise SMUError(f"SMU {self.resource} channel {self._channel} is not connected")

        import nidcpower

        try:
            self._session.output_function = nidcpower.OutputFunction.DC_VOLTAGE
            self._session.source_mode = nidcpower.SourceMode.SINGLE_POINT
            self._session.voltage_level_range = voltage_range_v
            self._session.current_limit = current_limit_a
            self._session.voltage_level = voltage_v
            self._session.output_enabled = True
            self._session.commit()
        except Exception as e:
            raise SMUError(
                f"SMU {self.resource} channel {self._channel} failed to configure "
                f"{voltage_v:+.3f} V: {e}"
            ) from e

        in_compliance = None
        measured_v = None
        measured_i = None
        readback_v = None
        readback_current_limit_a = None
        readback_output_enabled = None
        during_hold_result = None
        try:
            # Configuration verification -- READBACK + VERIFY each commanded
            # attribute before trusting the output is in the state just
            # requested. See _verify_config_readback()'s docstring for why
            # the tolerance is an attribute round-trip bound, not a
            # measurement-accuracy figure. Values are captured once here and
            # reused for both verification and the returned dict below, so
            # the operator-facing display (test.py) sees exactly what was
            # verified -- never a second, separate read.
            readback_v = self._session.voltage_level
            readback_current_limit_a = self._session.current_limit
            readback_output_enabled = self._session.output_enabled

            self._verify_config_readback(
                "voltage_level", voltage_v, readback_v,
                tolerance=Settings.SMU_VOLTAGE_READBACK_TOLERANCE_V,
            )
            self._verify_config_readback(
                "current_limit", current_limit_a, readback_current_limit_a,
                tolerance=Settings.SMU_CURRENT_READBACK_TOLERANCE_A,
            )
            self._verify_config_readback("output_enabled", True, readback_output_enabled)

            # Runtime measurements -- real ADC readback of the physical output,
            # taken once output is initiated. Distinct from the configuration
            # readback above: these observe the actual analog signal (subject
            # to load, compliance, and settling behavior), not a stored
            # setpoint attribute -- see the module/method docstrings for why
            # they are never asserted equal to the commanded values.
            with self._session.initiate():
                in_compliance = self._session.query_in_compliance()
                measured_v = self._session.measure(nidcpower.MeasurementTypes.VOLTAGE)
                measured_i = self._session.measure(nidcpower.MeasurementTypes.CURRENT)
                # Optional hold -- output stays enabled for hold_s seconds
                # after the SMU's own measurement, above, so a caller-
                # supplied during_hold() (e.g. a DMM reading) observes a
                # genuinely active output, not one already disabled by the
                # finally block below. Both default to a no-op, so existing
                # callers (test.py's SMU Functional Validation) see zero
                # behavior change.
                if during_hold is not None:
                    during_hold_result = during_hold()
                if hold_s > 0:
                    time.sleep(hold_s)
        except SMUStateVerificationError:
            raise
        except Exception as e:
            raise SMUError(
                f"SMU {self.resource} channel {self._channel} failed while sourcing "
                f"{voltage_v:+.3f} V: {e}"
            ) from e
        finally:
            self.output_disable()
            if not self.verify_output_disabled():
                self.log.critical(
                    "SMU %s channel %s: output disable could not be verified OFF after "
                    "source_dc_voltage_point() -- PMU may still be actively sourcing/"
                    "sinking current. Physically disconnect power if this cannot be "
                    "resolved immediately.", self.resource, self._channel,
                )

        if in_compliance:
            raise SMUError(
                f"SMU {self.resource} entered current-limit compliance while sourcing "
                f"{voltage_v:+.3f} V (limit {current_limit_a:.3f} A) -- possible short "
                f"or unexpected load."
            )

        self.log.info(
            "SMU %s sourced %.3f V (measured %.6f V / %.6f A, current limit %.3f A, "
            "not in compliance)",
            self.resource, voltage_v, measured_v, measured_i, current_limit_a,
        )
        return {
            # Configuration readbacks (NI-DCPower attribute echo -- see
            # _verify_config_readback()'s docstring).
            "commanded_v":               voltage_v,
            "readback_v":                readback_v,
            "commanded_current_limit_a": current_limit_a,
            "readback_current_limit_a":  readback_current_limit_a,
            "output_enabled_readback":   readback_output_enabled,
            # Runtime measurements (real ADC readback of the physical output).
            "in_compliance": in_compliance,
            "measured_v":    measured_v,
            "measured_i":    measured_i,
            # Result of the optional during_hold() callback (None if unused).
            "during_hold_result": during_hold_result,
        }
