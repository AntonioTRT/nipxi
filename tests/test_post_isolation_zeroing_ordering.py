"""
Source-level ordering regression tests for the post-isolation SMU
setpoint-zeroing step (see docs/architecture.md "Post-Isolation SMU
Setpoint Zeroing"). Mirrors the exact technique already used by
tests/test_cancellation.py::SigintInstalledBeforeHardwareInitTests for
the SIGINT-before-connect_all() ordering fix -- appropriate here for the
same reason: exercising the full real-hardware call sequence end to end
would require a large fake-hardware harness for ChargeSequence/
DischargeSequence/HardwareManager that doesn't otherwise exist, while a
source-order check directly catches the one thing that matters (a future
edit reordering or dropping the new call) with no hardware access.

The safety-critical claim -- SMU output-off always happens before
relay-open -- is unchanged and already covered by existing code
structure/tests; these tests only check that the NEW, non-safety-critical
zeroing call is correctly positioned AFTER whichever relay-open call
already exists on each path, never before it and never replacing it.
"""

import inspect
import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from test_control import charge_sequence, discharge_sequence, hardware_manager


def _first_index(lines, needle):
    for i, line in enumerate(lines):
        stripped = line.strip()
        if needle in line and stripped and not stripped.startswith("#"):
            return i
    return None


class ChargeSequenceOrderingTests(unittest.TestCase):
    def test_zeroing_call_present_and_after_relay_open(self):
        # _run_charge() is a closure defined inside run() -- inspect the
        # whole method's source, same as the closure itself would show.
        src = inspect.getsource(charge_sequence.ChargeSequence.run)
        lines = src.splitlines()
        relay_idx = _first_index(lines, "self.relay.open(")
        zero_idx = _first_index(lines, "zero_output_setpoint_best_effort(")
        self.assertIsNotNone(relay_idx, "expected self.relay.open(...) in run()/_run_charge()")
        self.assertIsNotNone(zero_idx, "expected zero_output_setpoint_best_effort(...) in run()/_run_charge()")
        self.assertLess(relay_idx, zero_idx,
                         "post-isolation zeroing must be called AFTER relay.open(), never before")


class DischargeSequenceOrderingTests(unittest.TestCase):
    def test_zeroing_call_present_and_after_relay_open(self):
        src = inspect.getsource(discharge_sequence.DischargeSequence.run)
        lines = src.splitlines()
        relay_idx = _first_index(lines, "self.relay.open(")
        zero_idx = _first_index(lines, "zero_output_setpoint_best_effort(")
        self.assertIsNotNone(relay_idx, "expected self.relay.open(...) in run()/_run_discharge()")
        self.assertIsNotNone(zero_idx, "expected zero_output_setpoint_best_effort(...) in run()/_run_discharge()")
        self.assertLess(relay_idx, zero_idx,
                         "post-isolation zeroing must be called AFTER relay.open(), never before")


class HardwareManagerOrderingTests(unittest.TestCase):
    def test_zeroing_call_present_after_relay_open_all_and_before_disconnect_loop(self):
        src = inspect.getsource(hardware_manager.HardwareManager.disconnect_all)
        lines = src.splitlines()
        relay_idx = _first_index(lines, "self._relay.open_all()")
        zero_idx = _first_index(lines, "zero_output_setpoint_best_effort(")
        disconnect_loop_idx = _first_index(lines, "for dev in devices:")
        self.assertIsNotNone(relay_idx, "expected self._relay.open_all() in disconnect_all")
        self.assertIsNotNone(zero_idx, "expected zero_output_setpoint_best_effort(...) in disconnect_all")
        self.assertIsNotNone(disconnect_loop_idx, "expected the device disconnect loop in disconnect_all")
        self.assertLess(relay_idx, zero_idx,
                         "post-isolation zeroing must be called AFTER relay.open_all()")
        self.assertLess(zero_idx, disconnect_loop_idx,
                         "post-isolation zeroing must run BEFORE the SMU session might be closed")


class PsuTestOrderingTests(unittest.TestCase):
    def test_zeroing_call_present_in_functional_smu(self):
        src = inspect.getsource(test_module._functional_smu)
        self.assertIn("zero_output_setpoint_best_effort(", src)


if __name__ == "__main__":
    unittest.main()
