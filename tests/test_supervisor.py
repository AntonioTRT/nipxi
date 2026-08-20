"""
Tests for orchestration/supervisor.py.

No hardware, no threads/processes, no test_control/ import anywhere --
SequentialSupervisor is exercised entirely with injected fake
`run_group` callables. Verifies the abstract contract raises
NotImplementedError, and that the reference implementation correctly
drives WorkerLifecycle through success, failure, exception, and
stop()-while-running / stop()-before-start scenarios.
"""

import unittest

from orchestration.supervisor import SequentialSupervisor, Supervisor, SupervisorStatus
from orchestration.worker_lifecycle import WorkerState
from orchestration.workers import WorkerPlan


class AbstractContractTests(unittest.TestCase):
    def test_start_stop_status_are_not_implemented(self):
        supervisor = Supervisor()
        with self.assertRaises(NotImplementedError):
            supervisor.start()
        with self.assertRaises(NotImplementedError):
            supervisor.stop()
        with self.assertRaises(NotImplementedError):
            supervisor.status()


class SequentialSupervisorHappyPathTests(unittest.TestCase):
    def test_all_groups_succeed(self):
        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1", "B2"])
        supervisor = SequentialSupervisor(plan, run_group=lambda name: True)
        supervisor.start()
        status = supervisor.status()
        self.assertEqual(status.state, WorkerState.IDLE)
        self.assertEqual(status.completed_groups, ["B1", "B2"])
        self.assertIsNone(status.current_group)
        self.assertIsNone(status.failed_group)

    def test_run_group_called_once_per_group_in_order(self):
        calls = []
        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1", "B2"])
        supervisor = SequentialSupervisor(plan, run_group=lambda name: calls.append(name) or True)
        supervisor.start()
        self.assertEqual(calls, ["B1", "B2"])


class SequentialSupervisorFailureTests(unittest.TestCase):
    def test_failed_group_stops_the_plan(self):
        calls = []

        def run_group(name):
            calls.append(name)
            return name != "B1"  # B1 fails

        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1", "B2"])
        supervisor = SequentialSupervisor(plan, run_group=run_group)
        supervisor.start()
        status = supervisor.status()
        self.assertEqual(status.state, WorkerState.FAILED)
        self.assertEqual(status.failed_group, "B1")
        self.assertEqual(status.completed_groups, [])
        self.assertEqual(calls, ["B1"], "B2 must never run after B1 fails")

    def test_exception_in_run_group_is_treated_as_failure(self):
        def run_group(name):
            raise RuntimeError("simulated failure, not a real hardware error")

        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1"])
        supervisor = SequentialSupervisor(plan, run_group=run_group)
        supervisor.start()  # must not propagate the exception
        status = supervisor.status()
        self.assertEqual(status.state, WorkerState.FAILED)
        self.assertEqual(status.failed_group, "B1")


class SequentialSupervisorStopTests(unittest.TestCase):
    def test_stop_before_start_aborts_immediately(self):
        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1"])
        supervisor = SequentialSupervisor(plan, run_group=lambda name: True)
        supervisor.stop()
        supervisor.start()
        status = supervisor.status()
        self.assertEqual(status.state, WorkerState.ABORTED)
        self.assertEqual(status.completed_groups, [])

    def test_stop_called_from_within_run_group_aborts_that_group(self):
        def run_group(name):
            supervisor.stop()
            return True

        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1", "B2"])
        supervisor = SequentialSupervisor(plan, run_group=run_group)
        supervisor.start()
        status = supervisor.status()
        self.assertEqual(status.state, WorkerState.ABORTED)
        self.assertEqual(status.completed_groups, [], "B1 must not be recorded as completed")

    def test_stop_between_groups_prevents_the_next_group_from_starting(self):
        calls = []

        def run_group(name):
            calls.append(name)
            if name == "B1":
                supervisor.stop()
            return True

        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1", "B2"])
        supervisor = SequentialSupervisor(plan, run_group=run_group)
        supervisor.start()
        self.assertEqual(calls, ["B1"], "B2 must never start once stop() was requested")

    def test_stop_after_completion_is_a_safe_no_op(self):
        plan = WorkerPlan(smu_name="AUX_SMU_1", groups=["B1"])
        supervisor = SequentialSupervisor(plan, run_group=lambda name: True)
        supervisor.start()
        supervisor.stop()  # must not raise
        self.assertEqual(supervisor.status().state, WorkerState.IDLE)


class SupervisorStatusTests(unittest.TestCase):
    def test_status_is_a_plain_snapshot(self):
        status = SupervisorStatus(smu_name="AUX_SMU_1", state=WorkerState.RUNNING,
                                   current_group="B1")
        self.assertEqual(status.completed_groups, [])
        self.assertIsNone(status.failed_group)


if __name__ == "__main__":
    unittest.main()
