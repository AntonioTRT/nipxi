"""
Tests for orchestration/execution_plan.py.

Pure function, no hardware access anywhere in this file. Verifies:
  - today's real config produces exactly today's trivial one-step plan
  - a synthetic two-conflicting-worker config is correctly serialized
    into two separate batches (the case a naive "group by SMU" plan
    would get wrong by assuming independence)
  - a synthetic two-independent-worker config is correctly placed in one
    parallel-eligible batch (proving the graph doesn't over-serialize)
  - enabled_workers filtering excludes workers explicitly and reports them
"""

import unittest

from orchestration.execution_plan import build_execution_plan


class TodaysRealConfigTests(unittest.TestCase):
    def test_single_step_single_batch_no_exclusions(self):
        plan = build_execution_plan()
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].smu_name, "AUX_SMU_1")
        self.assertEqual(plan.steps[0].groups, ["B1"])
        self.assertEqual(plan.steps[0].depends_on, [])
        self.assertEqual(plan.parallel_batches, [["AUX_SMU_1"]])
        self.assertEqual(plan.excluded_workers, [])


class _ConfigMixin:
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

    def _independent_config(self):
        return {
            "B1": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
            "C1": {
                "relay_matrix": "MATRIX_NUMATO_203", "smu": "AUX_SMU_2",
                "dmm": None, "daq": None, "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
        }


class ConflictAwareSchedulingTests(_ConfigMixin, unittest.TestCase):
    def test_conflicting_workers_are_serialized_into_two_batches(self):
        plan = build_execution_plan(self._conflicting_config())
        self.assertEqual(len(plan.parallel_batches), 2)
        self.assertEqual(plan.parallel_batches[0], ["AUX_SMU_1"])
        self.assertEqual(plan.parallel_batches[1], ["AUX_SMU_2"])

    def test_second_step_depends_on_first(self):
        plan = build_execution_plan(self._conflicting_config())
        by_name = {s.smu_name: s for s in plan.steps}
        self.assertEqual(by_name["AUX_SMU_1"].depends_on, [])
        self.assertEqual(by_name["AUX_SMU_2"].depends_on, ["AUX_SMU_1"])

    def test_no_worker_appears_before_a_worker_it_depends_on(self):
        plan = build_execution_plan(self._conflicting_config())
        seen = []
        for step in plan.steps:
            for dep in step.depends_on:
                self.assertIn(dep, seen, "dependency must be scheduled earlier")
            seen.append(step.smu_name)


class IndependentWorkersScheduleTogetherTests(_ConfigMixin, unittest.TestCase):
    def test_independent_workers_share_one_batch(self):
        plan = build_execution_plan(self._independent_config())
        self.assertEqual(len(plan.parallel_batches), 1)
        self.assertEqual(set(plan.parallel_batches[0]), {"AUX_SMU_1", "AUX_SMU_2"})

    def test_independent_workers_have_no_dependencies(self):
        plan = build_execution_plan(self._independent_config())
        for step in plan.steps:
            self.assertEqual(step.depends_on, [])


class EnabledWorkersFilterTests(_ConfigMixin, unittest.TestCase):
    def test_excluded_worker_is_not_in_steps_but_is_reported(self):
        plan = build_execution_plan(self._conflicting_config(), enabled_workers=["AUX_SMU_1"])
        self.assertEqual([s.smu_name for s in plan.steps], ["AUX_SMU_1"])
        self.assertEqual(plan.excluded_workers, ["AUX_SMU_2"])

    def test_excluding_a_worker_removes_conflicts_it_would_have_caused(self):
        plan = build_execution_plan(self._conflicting_config(), enabled_workers=["AUX_SMU_1"])
        self.assertEqual(plan.steps[0].depends_on, [])
        self.assertEqual(len(plan.parallel_batches), 1)

    def test_enabled_workers_none_means_include_everything(self):
        plan = build_execution_plan(self._conflicting_config(), enabled_workers=None)
        self.assertEqual(plan.excluded_workers, [])
        self.assertEqual(len(plan.steps), 2)


if __name__ == "__main__":
    unittest.main()
