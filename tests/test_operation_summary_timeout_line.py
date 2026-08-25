"""
Source-inspection test for the "Operation Summary" confirmation screen's
new Charge/Discharge Timeout line (test.py::_confirm_operation(), fed via
each caller's own `extra_lines`) -- mirrors the established technique
already used for deep test.py-level orchestration in this suite (see
tests/test_group_all_support.py / tests/test_presence_precheck_testpy_
wiring.py) rather than driving the real input()-based confirmation flow.

Confirms the timeout line is built from `test_setpoints` (the same
resolution ChargeSequence/DischargeSequence themselves use, falling back
to Settings.CHARGE_TIMEOUT_S/DISCHARGE_TIMEOUT_S) in BOTH the
single-position (_run_charge_or_discharge) and Group -> ALL
(_run_charge_or_discharge_all_positions) paths, so the operator sees the
active timeout setpoints before committing to a run either way.
"""

import inspect
import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)


class OperationSummaryTimeoutLineTests(unittest.TestCase):
    def _assert_timeout_line_present(self, src: str):
        self.assertIn("Charge Timeout:", src)
        self.assertIn("Discharge Timeout:", src)
        self.assertIn("test_setpoints.get('charge_timeout_s', Settings.CHARGE_TIMEOUT_S)", src)
        self.assertIn("test_setpoints.get('discharge_timeout_s', Settings.DISCHARGE_TIMEOUT_S)", src)

    def test_single_position_path_shows_both_timeouts(self):
        self._assert_timeout_line_present(inspect.getsource(test_module._run_charge_or_discharge))

    def test_group_all_path_shows_both_timeouts(self):
        self._assert_timeout_line_present(
            inspect.getsource(test_module._run_charge_or_discharge_all_positions)
        )

    def test_timeout_line_is_built_before_confirm_operation_is_called(self):
        src = inspect.getsource(test_module._run_charge_or_discharge)
        timeout_idx = src.index("Charge Timeout:")
        confirm_idx = src.index("_confirm_operation(")
        self.assertLess(timeout_idx, confirm_idx)


if __name__ == "__main__":
    unittest.main()
