"""
Tests for hardware/smu.py::SMU.zero_output_setpoint_best_effort() -- the
post-isolation shutdown-hardening step (see docs/architecture.md
"Post-Isolation SMU Setpoint Zeroing").

This is explicitly NOT part of the safety-critical disable/verify chain
(that remains emergency_output_off()/verify_output_disabled(), both
unit-tested separately in test_smu_emergency_shutdown.py and unchanged
by this work). These tests only cover the new method itself: it zeros
the correct property for the session's active output_function, never
raises, and returns a bool without affecting anything else.

Uses a fake NI-DCPower session -- no real hardware. `nidcpower` itself
IS imported here (confirmed importable in this dev environment) so the
fake session's `output_function` values are the real
`nidcpower.OutputFunction` enum members production code compares
against -- not a string stand-in that could silently pass a test while
disagreeing with the real enum.
"""

import unittest

import nidcpower

from hardware.smu import SMU


class _FakeSessionForZeroing:
    def __init__(self, output_function=nidcpower.OutputFunction.DC_CURRENT,
                 fail_property_set=False, fail_commit=False, fail_output_function_read=False):
        self.output_function = output_function
        self.current_level = 0.5
        self.voltage_level = 3.7
        self.commit_calls = 0
        self._fail_property_set = fail_property_set
        self._fail_commit = fail_commit
        self._fail_output_function_read = fail_output_function_read

    def commit(self):
        self.commit_calls += 1
        if self._fail_commit:
            raise RuntimeError("simulated commit failure")


def _make_smu(session) -> SMU:
    smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
    smu._session = session
    smu.log.disabled = True
    return smu


class NoSessionTests(unittest.TestCase):
    def test_no_session_is_a_safe_no_op(self):
        smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
        smu.log.disabled = True
        self.assertIsNone(smu._session)
        self.assertTrue(smu.zero_output_setpoint_best_effort("test"))  # must not raise


class DcCurrentModeTests(unittest.TestCase):
    def test_zeros_current_level_not_voltage_level(self):
        session = _FakeSessionForZeroing(output_function=nidcpower.OutputFunction.DC_CURRENT)
        smu = _make_smu(session)
        result = smu.zero_output_setpoint_best_effort("end of charge sequence on channel 1")
        self.assertTrue(result)
        self.assertEqual(session.current_level, 0.0)
        self.assertEqual(session.voltage_level, 3.7, "voltage_level is not the active setpoint in DC_CURRENT mode")
        self.assertEqual(session.commit_calls, 1)


class DcVoltageModeTests(unittest.TestCase):
    def test_zeros_voltage_level_not_current_level(self):
        session = _FakeSessionForZeroing(output_function=nidcpower.OutputFunction.DC_VOLTAGE)
        smu = _make_smu(session)
        result = smu.zero_output_setpoint_best_effort("SMU Functional Validation complete")
        self.assertTrue(result)
        self.assertEqual(session.voltage_level, 0.0)
        self.assertEqual(session.current_level, 0.5, "current_level is not the active setpoint in DC_VOLTAGE mode")
        self.assertEqual(session.commit_calls, 1)


class FailureNeverRaisesTests(unittest.TestCase):
    def test_commit_failure_returns_false_without_raising(self):
        session = _FakeSessionForZeroing(fail_commit=True)
        smu = _make_smu(session)
        result = smu.zero_output_setpoint_best_effort("test")  # must not raise
        self.assertFalse(result)

    def test_property_write_failure_returns_false_without_raising(self):
        # current_level is a write-raising property on this instance --
        # constructed via object.__setattr__ to bypass the raising setter
        # for the base class's own __init__ assignment.
        class _RaisingSession:
            output_function = nidcpower.OutputFunction.DC_CURRENT
            commit_calls = 0

            @property
            def current_level(self):
                return 0.5

            @current_level.setter
            def current_level(self, value):
                raise RuntimeError("simulated write failure")

            def commit(self):
                self.commit_calls += 1

        smu = _make_smu(_RaisingSession())
        result = smu.zero_output_setpoint_best_effort("test")  # must not raise
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
