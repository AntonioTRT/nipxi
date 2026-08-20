"""
Tests for orchestration/reporting.py.

Pure string-formatting checks over today's real config and a couple of
synthetic configs -- no hardware access, no I/O other than returning a
string (nothing here calls print()).
"""

import unittest

from orchestration.reporting import (
    conflict_report,
    dependency_report,
    execution_plan_report,
    full_report,
    topology_report,
    worker_report,
)


class TodaysRealConfigReportTests(unittest.TestCase):
    def test_topology_report_mentions_b1_and_its_smu(self):
        report = topology_report()
        self.assertIn("B1", report)
        self.assertIn("AUX_SMU_1", report)

    def test_worker_report_mentions_the_single_worker(self):
        report = worker_report()
        self.assertIn("Worker[AUX_SMU_1]", report)
        self.assertIn("B1", report)

    def test_dependency_report_lists_shared_resources(self):
        report = dependency_report()
        self.assertIn("AUX_SMU_1", report)
        self.assertIn("MATRIX_NUMATO_202", report)

    def test_conflict_report_shows_all_clear_for_one_worker(self):
        report = conflict_report()
        self.assertIn("No conflicts detected", report)

    def test_execution_plan_report_shows_one_step(self):
        report = execution_plan_report()
        self.assertIn("Step 1: AUX_SMU_1", report)
        self.assertIn("no dependencies", report)

    def test_full_report_contains_every_section_header(self):
        report = full_report()
        for header in ("Topology Summary", "Worker Summary", "Dependency Summary",
                       "Conflict Summary", "Execution Plan Summary"):
            self.assertIn(header, report)


class SyntheticConflictReportTests(unittest.TestCase):
    def _conflicting_config(self):
        return {
            "B1": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
            "B2": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_2",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
        }

    def test_conflict_report_names_both_workers_and_shared_resource(self):
        report = conflict_report(self._conflicting_config())
        self.assertIn("AUX_SMU_1", report)
        self.assertIn("AUX_SMU_2", report)
        self.assertIn("MATRIX_NUMATO_202", report)

    def test_execution_plan_report_shows_dependency_and_two_batches(self):
        report = execution_plan_report(self._conflicting_config())
        self.assertIn("after AUX_SMU_1", report)
        self.assertIn("Batch 1: AUX_SMU_1", report)
        self.assertIn("Batch 2: AUX_SMU_2", report)

    def test_execution_plan_report_lists_excluded_worker(self):
        report = execution_plan_report(self._conflicting_config(), enabled_workers=["AUX_SMU_1"])
        self.assertIn("Excluded from this plan: AUX_SMU_2", report)


class EmptyConfigReportTests(unittest.TestCase):
    def test_reports_handle_no_enabled_groups_gracefully(self):
        empty = {"B1": {
            "relay_matrix": None, "smu": None, "dmm": None, "daq": None,
            "ntc_daq": None, "enabled": False, "positions": {},
        }}
        self.assertIn("no enabled group", topology_report(empty))
        self.assertIn("no worker discovered", worker_report(empty))
        self.assertIn("no worker discovered", dependency_report(empty))
        self.assertIn("No conflicts detected", conflict_report(empty))
        self.assertIn("no worker included", execution_plan_report(empty))


if __name__ == "__main__":
    unittest.main()
