"""
Tests for "Group -> ALL Support" (see docs/architecture.md) -- Charge/
Discharge Battery's B1 -> ALL selection, orchestration, and aggregate
summary. Mirrors the established pattern for deep test.py-level
orchestration in this suite: direct unit tests where a function is
standalone (input parsing, the pure summary printer), source-inspection
for the orchestration loop's structural properties (see
tests/test_presence_precheck_testpy_wiring.py / test_sense_router_testpy_wiring.py
for the identical, already-established technique and its rationale) --
the per-position workflow body itself
(_run_one_charge_or_discharge_position()) and ChargeSequence/
DischargeSequence are covered by their own, separate test files; this
file does not re-test that behavior.
"""

import builtins
import inspect
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)


class SelectBatteryPositionAllSupportTests(unittest.TestCase):
    """_select_battery_position(group, allow_all=...) -- both "B1 -> ALL"
    and "B1 -> 1" must remain supported, and every existing caller
    (allow_all defaults to False) must be completely unaffected."""

    def test_all_is_accepted_when_allowed(self):
        with patch.object(builtins, "input", return_value="ALL"):
            self.assertEqual(test_module._select_battery_position("B1", allow_all=True), "ALL")

    def test_all_is_case_insensitive(self):
        with patch.object(builtins, "input", return_value="all"):
            self.assertEqual(test_module._select_battery_position("B1", allow_all=True), "ALL")

    def test_a_specific_position_is_still_accepted_when_all_is_allowed(self):
        with patch.object(builtins, "input", return_value="1"):
            self.assertEqual(test_module._select_battery_position("B1", allow_all=True), 1)

    def test_all_is_rejected_as_invalid_when_not_allowed(self):
        # Every pre-existing caller (Monitor Battery, Monitor Battery
        # Scan) must be completely unaffected -- allow_all defaults to
        # False, and "ALL" must NOT silently be accepted there.
        with patch.object(builtins, "input", return_value="ALL"):
            self.assertIsNone(test_module._select_battery_position("B1"))

    def test_invalid_input_still_returns_none_when_all_is_allowed(self):
        with patch.object(builtins, "input", return_value="not a number"):
            self.assertIsNone(test_module._select_battery_position("B1", allow_all=True))

    def test_out_of_range_position_still_returns_none_when_all_is_allowed(self):
        with patch.object(builtins, "input", return_value="999"):
            self.assertIsNone(test_module._select_battery_position("B1", allow_all=True))


class PrintGroupRunSummaryTests(unittest.TestCase):
    """Pure console-output function -- the informational aggregate
    summary. The AUTHORITATIVE record is each position's own independent
    run_summary row (not tested here -- that's a storage-layer
    guarantee, covered by test_storage_measurement_scoping.py-style
    tests and the begin_new_run_id() docstring/contract itself)."""

    def _run(self, results, cancelled=False, station_fault=False):
        buf = io.StringIO()
        with redirect_stdout(buf):
            test_module._print_group_run_summary(
                "B1", "Charge Battery", [1, 2, 3, 4], results, cancelled, station_fault
            )
        return buf.getvalue()

    def test_counts_are_correct(self):
        output = self._run({1: "PASS", 2: "FAIL", 3: "SKIPPED", 4: "PASS"})
        self.assertIn("Processed: 4", output)
        self.assertIn("Passed:    2", output)
        self.assertIn("Failed:    1", output)
        self.assertIn("Skipped:   1", output)

    def test_every_position_is_listed_with_its_own_result(self):
        output = self._run({1: "PASS", 2: "FAIL", 3: "SKIPPED", 4: "PASS"})
        self.assertIn("Position 1: PASS", output)
        self.assertIn("Position 2: FAIL", output)
        self.assertIn("Position 3: SKIPPED", output)
        self.assertIn("Position 4: PASS", output)

    def test_not_attempted_position_shown_when_the_run_was_cancelled_early(self):
        # Positions 3/4 never ran because the operator cancelled during
        # position 2.
        output = self._run({1: "PASS", 2: "CANCELLED"}, cancelled=True)
        self.assertIn("Position 3: NOT ATTEMPTED", output)
        self.assertIn("Position 4: NOT ATTEMPTED", output)
        self.assertIn("Cancelled:", output)

    def test_no_cancelled_note_on_a_normal_completion(self):
        output = self._run({1: "PASS"}, cancelled=False)
        self.assertNotIn("Cancelled:", output)

    def test_station_fault_note_shown_when_the_run_was_aborted_by_a_station_fault(self):
        output = self._run({1: "PASS", 2: "STATION_FAULT"}, station_fault=True)
        self.assertIn("Position 3: NOT ATTEMPTED", output)
        self.assertIn("Station Fault:", output)
        self.assertNotIn("Cancelled:", output)

    def test_no_station_fault_note_on_a_normal_completion(self):
        output = self._run({1: "PASS"}, station_fault=False)
        self.assertNotIn("Station Fault:", output)


class RunChargeOrDischargeAllPositionsWiringTests(unittest.TestCase):
    """
    Source-inspection of _run_charge_or_discharge_all_positions() --
    mirrors the established technique in
    tests/test_presence_precheck_testpy_wiring.py for the same reason:
    this function's own logic (enabled-position filtering, per-position
    run_id scoping, cancellation stopping the loop, other results
    continuing it) is a real behavioral property this test pins, without
    needing a full HardwareManager-level integration harness (the
    per-position workflow itself is exercised directly by
    tests/test_hardware_event_logging.py and
    tests/test_battery_removal_during_charge.py against
    ChargeSequence/DischargeSequence).
    """

    def setUp(self):
        self.src = inspect.getsource(test_module._run_charge_or_discharge_all_positions)
        self.lines = self.src.splitlines()

    def _first_index(self, needle):
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if needle in line and stripped and not stripped.startswith("#"):
                return i
        return None

    def test_only_enabled_positions_are_selected(self):
        self.assertIsNotNone(self._first_index('if cfg.get("enabled")'))

    def test_a_fresh_run_id_is_started_for_every_position(self):
        # begin_new_run_id() no longer takes a suffix -- a freshly
        # allocated sequence number (see data/storage.py::
        # DataStorage._new_run_id()) is unique on every call by
        # construction, so there is nothing left to disambiguate.
        self.assertIsNotNone(self._first_index("storage.begin_new_run_id()"))

    def test_cancelled_result_stops_the_loop(self):
        cancelled_idx = self._first_index('if result == "CANCELLED":')
        break_idx = self._first_index("break")
        self.assertIsNotNone(cancelled_idx)
        self.assertIsNotNone(break_idx)
        self.assertLess(cancelled_idx, break_idx)

    def test_the_loop_has_no_unconditional_break_for_fail_or_skipped(self):
        # Exactly two "break"s in this function -- one inside the
        # CANCELLED branch, one inside the STATION_FAULT branch (see
        # docs/architecture.md "Group -> ALL Fault Classification
        # Policy"). A FAIL/SKIPPED result must never stop the loop.
        break_indices = [i for i, line in enumerate(self.lines) if line.strip() == "break"]
        self.assertEqual(len(break_indices), 2)
        cancelled_idx = self._first_index('if result == "CANCELLED":')
        station_fault_idx = self._first_index('if result == "STATION_FAULT":')
        self.assertIsNotNone(station_fault_idx)
        self.assertGreater(break_indices[0], cancelled_idx)
        self.assertGreater(break_indices[1], station_fault_idx)

    def test_group_run_started_and_completed_are_both_logged(self):
        self.assertIsNotNone(self._first_index("EventType.GROUP_RUN_STARTED"))
        self.assertIsNotNone(self._first_index("EventType.GROUP_RUN_COMPLETED"))

    def test_group_slot_started_is_logged_before_running_the_position(self):
        started_idx = self._first_index("EventType.GROUP_SLOT_STARTED")
        run_idx = self._first_index("result = _run_one_charge_or_discharge_position(")
        self.assertIsNotNone(started_idx)
        self.assertIsNotNone(run_idx)
        self.assertLess(started_idx, run_idx)

    def test_aggregate_summary_is_printed_after_the_loop(self):
        loop_idx = self._first_index("for position in positions:")
        # The real call site, not the docstring's prose mention of the
        # same function name (which appears earlier, before the loop).
        summary_idx = self._first_index(
            "_print_group_run_summary(group, operation, positions, results, cancelled, station_fault)"
        )
        self.assertIsNotNone(loop_idx)
        self.assertIsNotNone(summary_idx)
        self.assertLess(loop_idx, summary_idx)

    def test_hardware_connects_only_once_not_once_per_position(self):
        # connect_all() must appear exactly once, outside/before the loop
        # -- never re-connected per position.
        connect_indices = [i for i, line in enumerate(self.lines) if "hw_mgr.connect_all()" in line]
        loop_idx = self._first_index("for position in positions:")
        self.assertEqual(len(connect_indices), 1)
        self.assertLess(connect_indices[0], loop_idx)

    def test_sequence_classes_are_never_referenced_by_name_inside_this_function(self):
        # "Do NOT add multi-position logic inside the sequence classes" --
        # this function must only ever call sequence_cls generically
        # (passed in), never import/construct ChargeSequence/
        # DischargeSequence directly.
        self.assertNotIn("ChargeSequence(", self.src)
        self.assertNotIn("DischargeSequence(", self.src)


if __name__ == "__main__":
    unittest.main()
