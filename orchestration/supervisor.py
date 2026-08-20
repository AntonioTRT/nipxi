"""
Supervisor contract -- the interface a future worker_runtime.py will
implement for real (see docs/architecture.md "Future Architecture:
Supervisor Interface"). This module defines the CONTRACT and one
reference implementation that proves the contract fits today's actual
(single-worker, fully sequential) behavior; it does not implement worker
execution, scheduling, multiprocessing, multithreading, or anything that
touches hardware or test_control/.

SequentialSupervisor below is intentionally NOT wired to
ChargeSequence/DischargeSequence/CycleSequence or any hardware manager --
the caller injects a plain `run_group(group_name) -> bool` callable. A
future worker_runtime.py would inject a function that actually builds and
runs the right BatteryOperationSequence subclass for that group; tests
here inject a fake. This keeps the contract exercisable and unit-tested
today without creating any coupling to the sequence layer, and without
requiring hardware, threads, or processes to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestration.worker_lifecycle import WorkerLifecycle, WorkerState
from orchestration.workers import WorkerPlan


@dataclass
class SupervisorStatus:
    """Point-in-time snapshot returned by Supervisor.status()."""
    smu_name: str
    state: WorkerState
    current_group: str = None
    completed_groups: list = field(default_factory=list)
    failed_group: str = None


class Supervisor:
    """
    Abstract contract. A real implementation (worker_runtime.py, not built
    yet) must preserve this exact shape so future orchestration code can
    be written against the interface, never against which implementation
    is active underneath -- the same pattern already used by
    orchestration/arbiter.py::Arbiter.
    """

    def start(self) -> None:
        """Begin running this supervisor's worker plan. Must not block
        forever with no way to observe progress -- callers use status()
        to poll. A real implementation may run the worker on a separate
        thread/process; that is explicitly out of scope for this module
        (see module docstring) and for SequentialSupervisor below, which
        runs synchronously in the caller's own thread."""
        raise NotImplementedError

    def stop(self) -> None:
        """Request cooperative cancellation, mirroring the same
        checkpoint-based philosophy as utils/cancellation.py -- a request
        to stop, not a guaranteed-immediate halt. Must be safe to call at
        any time, including before start() and after the worker has
        already finished (a no-op in both cases)."""
        raise NotImplementedError

    def status(self) -> SupervisorStatus:
        """Return a snapshot of current lifecycle state. Must never raise
        and must never block on hardware -- this is a pure read of
        already-known in-memory state."""
        raise NotImplementedError


class SequentialSupervisor(Supervisor):
    """
    Reference implementation. Runs `worker_plan.groups` one at a time, in
    order, in the caller's own thread -- exactly what main.py does today
    for its single worker. Exists to prove the Supervisor contract is
    sufficient for today's real behavior before any concurrent
    implementation is attempted, and to give worker_lifecycle.py's state
    machine a real (if hardware-free) exerciser.

    `run_group` is injected, not looked up -- this class has no import of
    test_control/ or hardware/*, and constructs no ChargeSequence/
    DischargeSequence/CycleSequence itself. It must return a truthy value
    on success, a falsy value on a handled failure, or raise on an
    unhandled one (treated the same as a falsy return: the group is
    marked FAILED and the plan stops there, no later groups run).
    """

    def __init__(self, worker_plan: WorkerPlan, run_group):
        self._plan = worker_plan
        self._run_group = run_group
        self._lifecycle = WorkerLifecycle()
        self._current_group = None
        self._completed_groups: list = []
        self._failed_group = None
        self._stop_requested = False

    def start(self) -> None:
        if self._stop_requested:
            self._lifecycle.transition_to(WorkerState.ABORTED)
            return

        for group_name in self._plan.groups:
            if self._stop_requested:
                break

            # Re-entered from IDLE for every group after the first (see
            # ALLOWED_TRANSITIONS in worker_lifecycle.py -- IDLE only
            # leads to DISCOVERED/ABORTED, never straight to CLAIMING).
            self._lifecycle.transition_to(WorkerState.DISCOVERED)
            self._lifecycle.transition_to(WorkerState.READY)

            self._current_group = group_name
            self._lifecycle.transition_to(WorkerState.CLAIMING)
            self._lifecycle.transition_to(WorkerState.RUNNING)

            try:
                success = self._run_group(group_name)
            except Exception:
                success = False

            if self._stop_requested:
                # stop() was called from inside run_group itself (the
                # only way "while running" is reachable with no threads)
                # -- it already moved lifecycle to ABORTED; do not also
                # try to record a COMPLETED/FAILED result for this group.
                break

            if success:
                self._completed_groups.append(group_name)
                self._lifecycle.transition_to(WorkerState.COMPLETED)
                self._lifecycle.transition_to(WorkerState.IDLE)
            else:
                self._failed_group = group_name
                self._lifecycle.transition_to(WorkerState.FAILED)
                self._current_group = None
                return

        self._current_group = None
        if self._stop_requested and self._lifecycle.state != WorkerState.ABORTED:
            self._lifecycle.transition_to(WorkerState.ABORTED)

    def stop(self) -> None:
        self._stop_requested = True
        if self._lifecycle.state == WorkerState.RUNNING:
            self._lifecycle.transition_to(WorkerState.ABORTED)

    def status(self) -> SupervisorStatus:
        return SupervisorStatus(
            smu_name=self._plan.smu_name,
            state=self._lifecycle.state,
            current_group=self._current_group,
            completed_groups=list(self._completed_groups),
            failed_group=self._failed_group,
        )
