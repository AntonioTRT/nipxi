"""
Source-level regression test for test.py's own wiring of
battery_and_ntc_presence_precheck() into _run_monitor_battery() and
_run_charge_or_discharge() -- see docs/architecture.md "Battery Presence
+ NTC Presence Diagnostics".

Mirrors the established source-inspection technique already used in this
suite (tests/test_cancellation.py, tests/test_sense_router_testpy_wiring.py)
for the same reason: the full behavioral proof of
battery_and_ntc_presence_precheck() itself already lives in
tests/test_battery_presence_precheck.py against fakes directly; this file
only confirms test.py's own two call sites are wired in the right order,
without needing a full HardwareManager-level integration harness.
"""

import inspect
import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)


class _SourceInspectionMixin:
    def _first_index(self, needle):
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if needle in line and stripped and not stripped.startswith("#"):
                return i
        return None


class MonitorBatteryWiringTests(_SourceInspectionMixin, unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(test_module._run_monitor_battery)
        self.lines = self.src.splitlines()

    def test_precheck_is_called(self):
        self.assertIsNotNone(self._first_index("precheck = battery_and_ntc_presence_precheck("))

    def test_precheck_runs_before_the_sequence_is_constructed(self):
        precheck_idx = self._first_index("precheck = battery_and_ntc_presence_precheck(")
        construct_idx = self._first_index("sequence = MonitorBatterySequence(")
        self.assertIsNotNone(precheck_idx)
        self.assertIsNotNone(construct_idx)
        self.assertLess(precheck_idx, construct_idx)

    def test_a_failed_precheck_returns_without_constructing_the_sequence(self):
        gate_idx = self._first_index('if not precheck["ok"]:')
        construct_idx = self._first_index("sequence = MonitorBatterySequence(")
        self.assertIsNotNone(gate_idx)
        self.assertIsNotNone(construct_idx)
        # The nearest "return" after the gate must precede construction.
        return_idx = next(i for i in range(gate_idx, len(self.lines)) if self.lines[i].strip() == "return")
        self.assertLess(return_idx, construct_idx)

    def test_failure_path_prints_the_presence_precheck_report(self):
        gate_idx = self._first_index('if not precheck["ok"]:')
        report_idx = self._first_index("_print_presence_precheck_failure(precheck)")
        self.assertIsNotNone(report_idx)
        self.assertLess(gate_idx, report_idx)

    def test_no_longer_calls_the_old_ntc_only_precheck_directly(self):
        # The combined precheck now owns the NTC group snapshot call
        # internally -- this function must not also call it directly
        # (which would mean it runs twice, or the old gating logic is
        # still present alongside the new one).
        self.assertIsNone(self._first_index("ntc_snapshot = _ntc_group_snapshot("))


class ChargeOrDischargeWiringTests(_SourceInspectionMixin, unittest.TestCase):
    """
    The precheck/sequence-construction wiring this class inspects lives in
    _run_one_charge_or_discharge_position() -- the per-position workflow
    body extracted from _run_charge_or_discharge() so Group -> ALL
    orchestration (_run_charge_or_discharge_all_positions()) can call it
    once per position, without any multi-position logic inside
    ChargeSequence/DischargeSequence themselves. See docs/architecture.md
    "Group -> ALL Support".
    """

    def setUp(self):
        self.src = inspect.getsource(test_module._run_one_charge_or_discharge_position)
        self.lines = self.src.splitlines()

    def test_precheck_is_called(self):
        self.assertIsNotNone(self._first_index("precheck = battery_and_ntc_presence_precheck("))

    def test_precheck_runs_before_the_sequence_is_constructed(self):
        precheck_idx = self._first_index("precheck = battery_and_ntc_presence_precheck(")
        construct_idx = self._first_index("sequence = sequence_cls(")
        self.assertIsNotNone(precheck_idx)
        self.assertIsNotNone(construct_idx)
        self.assertLess(precheck_idx, construct_idx)

    def test_a_failed_precheck_returns_without_constructing_the_sequence(self):
        gate_idx = self._first_index('if not precheck["ok"]:')
        construct_idx = self._first_index("sequence = sequence_cls(")
        self.assertIsNotNone(gate_idx)
        self.assertIsNotNone(construct_idx)
        # The nearest "return" after the gate must precede construction --
        # now returns a classification string ("SKIPPED"), not a bare
        # return, since this function reports outcomes to its caller.
        return_idx = next(
            i for i in range(gate_idx, len(self.lines)) if self.lines[i].strip() == 'return "SKIPPED"'
        )
        self.assertLess(return_idx, construct_idx)

    def test_failure_path_prints_the_presence_precheck_report(self):
        gate_idx = self._first_index('if not precheck["ok"]:')
        report_idx = self._first_index("_print_presence_precheck_failure(precheck)")
        self.assertIsNotNone(report_idx)
        self.assertLess(gate_idx, report_idx)

    def test_no_longer_calls_the_old_ntc_only_precheck_directly(self):
        self.assertIsNone(self._first_index("ntc_snapshot = _ntc_group_snapshot("))

    def test_returns_every_classification_string_group_all_depends_on(self):
        # Group -> ALL orchestration depends on this exact vocabulary --
        # no try/except needed at the loop level. "PASS" is the initial
        # `result` value (returned via "return result" at the very end,
        # if nothing overwrites it); the rest appear as either a direct
        # "return ..." or a "result = ..." assignment later returned the
        # same way.
        self.assertIn('result = "PASS"', self.src)
        self.assertIn('return "FAIL"', self.src)
        self.assertIn('return "SKIPPED"', self.src)
        self.assertIn('result = "CANCELLED"', self.src)
        self.assertIn('result = "FAIL"', self.src)
        self.assertIn("return result", self.src)


if __name__ == "__main__":
    unittest.main()
