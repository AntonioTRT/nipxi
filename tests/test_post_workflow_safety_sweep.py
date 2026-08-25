"""
Tests for Change 3 ("Post-Workflow Safety Sweep") -- see
docs/architecture.md "Safety Fault Lifecycle". Exercising
HardwareManager.disconnect_all() behaviorally would require a large
fake-hardware harness that doesn't otherwise exist (see
tests/test_post_isolation_zeroing_ordering.py's identical rationale for
the same method) -- these are source-order/presence regression tests,
the established convention for this method, plus direct behavioral
coverage of the shared utils/safety_fault.py primitives it calls (already
exercised end to end in tests/test_safety_fault.py and
tests/test_shutdown_verification_escalation.py).
"""

import inspect
import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from test_control import hardware_manager


def _first_index(lines, needle):
    for i, line in enumerate(lines):
        stripped = line.strip()
        if needle in line and stripped and not stripped.startswith("#"):
            return i
    return None


class DisconnectAllSafetyFaultEscalationTests(unittest.TestCase):
    def test_smu_verification_failure_branch_reports_and_displays_a_safety_fault(self):
        src = inspect.getsource(hardware_manager.HardwareManager.disconnect_all)
        lines = src.splitlines()
        smu_fail_idx = _first_index(lines, "if not smu_output_verified_safe:")
        report_idx = _first_index(lines, "report_safety_fault(")
        display_idx = _first_index(lines, "display_safety_fault_screen(")
        ack_idx = _first_index(lines, "acknowledge_safety_fault(")
        self.assertIsNotNone(smu_fail_idx)
        self.assertIsNotNone(report_idx)
        self.assertIsNotNone(display_idx)
        self.assertIsNotNone(ack_idx)
        self.assertLess(smu_fail_idx, report_idx)
        self.assertLess(report_idx, display_idx)
        self.assertLess(display_idx, ack_idx)

    def test_relay_open_all_failure_branch_also_reports_a_safety_fault(self):
        src = inspect.getsource(hardware_manager.HardwareManager.disconnect_all)
        lines = src.splitlines()
        relay_fail_idx = _first_index(lines, "relay.open_all() failed during shutdown")
        report_calls = [i for i, line in enumerate(lines) if "= report_safety_fault(" in line]
        self.assertIsNotNone(relay_fail_idx)
        self.assertEqual(len(report_calls), 2, "expected one report_safety_fault() call for SMU, one for relay")
        self.assertTrue(any(i > relay_fail_idx for i in report_calls))

    def test_safety_fault_helpers_are_imported(self):
        src = inspect.getsource(hardware_manager)
        self.assertIn("from utils.safety_fault import", src)
        self.assertIn("report_safety_fault", src)
        self.assertIn("display_safety_fault_screen", src)
        self.assertIn("acknowledge_safety_fault", src)


if __name__ == "__main__":
    unittest.main()
