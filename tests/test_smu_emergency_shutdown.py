"""
Regression tests for hardware/smu.py::SMU.emergency_output_off()'s
bounded-retry, distinct-failure-mode shutdown safety fix.

Covers the approved scope from the shutdown-safety review:
  - retries emergency_output_off() a bounded number of times
  - distinguishes "output still enabled" from "verification communication
    failure"
  - never silently proceeds without recording the exact failure condition
  - preserves "unknown state = unsafe state" (both failure modes still
    return False -- retrying/distinguishing never means treating a failure
    as safe)

Uses a fake NI-DCPower session object -- no real hardware, no nidcpower
import required.
"""

import logging
import unittest

from config.settings import Settings
from hardware.smu import SMU, OutputVerificationResult
from tests._logging_helpers import reenable_logging_for_this_test


class _FakeSession:
    """
    Minimal stand-in for a nidcpower.Session, exposing only what
    SMU.output_disable()/emergency_output_off() touch: abort(), and an
    output_enabled property that can be configured to simulate each
    failure mode independently.
    """

    def __init__(self, fail_disable_command=False, fail_readback=False,
                 stays_enabled=False, succeed_after_n_calls=None):
        self._output_enabled = True
        self.fail_disable_command = fail_disable_command
        self.fail_readback = fail_readback
        self.stays_enabled = stays_enabled
        self.succeed_after_n_calls = succeed_after_n_calls
        self.disable_call_count = 0
        self.readback_call_count = 0

    def abort(self):
        pass

    @property
    def output_enabled(self):
        self.readback_call_count += 1
        if self.fail_readback:
            raise RuntimeError("simulated comms failure reading output_enabled")
        return self._output_enabled

    @output_enabled.setter
    def output_enabled(self, value):
        self.disable_call_count += 1
        if self.fail_disable_command:
            raise RuntimeError("simulated comms failure setting output_enabled")
        if self.succeed_after_n_calls is not None and self.disable_call_count >= self.succeed_after_n_calls:
            self._output_enabled = value
            return
        if self.stays_enabled:
            return  # command "accepted" but the instrument silently ignores it
        self._output_enabled = value


def _make_smu(session: _FakeSession) -> SMU:
    smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
    smu._session = session
    smu.log.disabled = True  # keep test output clean; behavior is asserted via return values
    return smu


class EmergencyOutputOffTests(unittest.TestCase):
    def test_first_attempt_success_returns_true_immediately(self):
        session = _FakeSession()
        smu = _make_smu(session)
        self.assertTrue(smu.emergency_output_off("test"))
        self.assertEqual(session.disable_call_count, 1)
        self.assertEqual(session.readback_call_count, 1)

    def test_transient_readback_failure_recovers_on_retry(self):
        # First readback fails (comm glitch), second succeeds -- proves the
        # retry loop gives a transient failure a real chance to resolve.
        session = _FakeSession()
        attempts = {"n": 0}
        original_getter = _FakeSession.output_enabled.fget

        class _FlakyThenOk(_FakeSession):
            @property
            def output_enabled(self):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("simulated one-shot comms glitch")
                return self._output_enabled

            @output_enabled.setter
            def output_enabled(self, value):
                self._output_enabled = value

        smu = _make_smu(_FlakyThenOk())
        self.assertTrue(smu.emergency_output_off("test"))

    def test_output_genuinely_stuck_enabled_exhausts_retries_and_fails(self):
        session = _FakeSession(stays_enabled=True)
        smu = _make_smu(session)
        result = smu.emergency_output_off("test")
        self.assertFalse(result, "must never report success when output never verified off")
        self.assertEqual(session.disable_call_count, Settings.EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS)

    def test_persistent_readback_comm_failure_exhausts_retries_and_fails(self):
        session = _FakeSession(fail_readback=True)
        smu = _make_smu(session)
        result = smu.emergency_output_off("test")
        self.assertFalse(result, "a comms failure must never be treated as safe")
        self.assertEqual(session.readback_call_count, Settings.EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS)

    def test_persistent_disable_command_failure_exhausts_retries_and_fails(self):
        session = _FakeSession(fail_disable_command=True)
        smu = _make_smu(session)
        result = smu.emergency_output_off("test")
        self.assertFalse(result)
        self.assertEqual(session.disable_call_count, Settings.EMERGENCY_OUTPUT_OFF_MAX_ATTEMPTS)

    def test_still_enabled_and_comm_failure_are_distinguished_in_final_log(self):
        """
        The two failure modes must produce distinguishable CRITICAL log
        records -- this is the "never silently proceeds without recording
        the exact failure condition" requirement. We don't parse exact
        wording (that's allowed to evolve); we assert the two scenarios
        produce DIFFERENT critical messages from each other.
        """
        reenable_logging_for_this_test(self)
        records = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _CapturingHandler()
        handler.setLevel(logging.CRITICAL)

        stuck_session = _FakeSession(stays_enabled=True)
        stuck_smu = _make_smu(stuck_session)
        stuck_smu.log.disabled = False
        stuck_smu.log.addHandler(handler)
        stuck_smu.log.setLevel(logging.CRITICAL)
        stuck_smu.emergency_output_off("stuck scenario")
        stuck_smu.log.removeHandler(handler)
        stuck_messages = list(records)
        records.clear()

        comm_fail_session = _FakeSession(fail_readback=True)
        comm_fail_smu = _make_smu(comm_fail_session)
        comm_fail_smu.log.disabled = False
        comm_fail_smu.log.addHandler(handler)
        comm_fail_smu.log.setLevel(logging.CRITICAL)
        comm_fail_smu.emergency_output_off("comm failure scenario")
        comm_fail_smu.log.removeHandler(handler)
        comm_fail_messages = list(records)

        self.assertTrue(stuck_messages, "expected at least one CRITICAL log for stuck-enabled scenario")
        self.assertTrue(comm_fail_messages, "expected at least one CRITICAL log for comm-failure scenario")
        self.assertNotEqual(
            sorted(stuck_messages), sorted(comm_fail_messages),
            "the two distinct failure modes must not produce identical log records",
        )


class CheckOutputDisabledDetailedTests(unittest.TestCase):
    def test_no_session_reports_disabled(self):
        smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
        smu.log.disabled = True
        self.assertEqual(smu._check_output_disabled_detailed(), OutputVerificationResult.DISABLED)

    def test_readback_false_reports_disabled(self):
        session = _FakeSession()
        session._output_enabled = False
        smu = _make_smu(session)
        self.assertEqual(smu._check_output_disabled_detailed(), OutputVerificationResult.DISABLED)

    def test_readback_true_reports_still_enabled(self):
        session = _FakeSession()
        session._output_enabled = True
        smu = _make_smu(session)
        self.assertEqual(smu._check_output_disabled_detailed(), OutputVerificationResult.STILL_ENABLED)

    def test_readback_exception_reports_comm_failure_not_disabled(self):
        session = _FakeSession(fail_readback=True)
        smu = _make_smu(session)
        result = smu._check_output_disabled_detailed()
        self.assertEqual(result, OutputVerificationResult.VERIFICATION_COMM_FAILURE)
        self.assertNotEqual(
            result, OutputVerificationResult.DISABLED,
            "a communication failure must never be reported as 'confirmed disabled'",
        )

    def test_verify_output_disabled_unchanged_bool_contract(self):
        """
        verify_output_disabled() (the pre-existing public method, used by
        force_output_off_and_verify() -> _configure_current_source() ->
        set_charge_mode()/set_discharge_mode() -- part of ChargeSequence's/
        DischargeSequence's operational path) must be completely untouched
        by this fix: same bool return, same semantics, for both failure
        modes collapsed to False exactly as before.
        """
        still_enabled_smu = _make_smu(_FakeSession())
        self.assertFalse(still_enabled_smu.verify_output_disabled())

        comm_fail_smu = _make_smu(_FakeSession(fail_readback=True))
        self.assertFalse(comm_fail_smu.verify_output_disabled())

        disabled_session = _FakeSession()
        disabled_session._output_enabled = False
        disabled_smu = _make_smu(disabled_session)
        self.assertTrue(disabled_smu.verify_output_disabled())


if __name__ == "__main__":
    unittest.main()
