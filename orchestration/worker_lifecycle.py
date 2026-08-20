"""
Worker Lifecycle -- a formal state model for what a future worker goes
through, and explicit rules for which transitions are legal (see docs/
architecture.md "Future Architecture: Worker Lifecycle"). No runtime
behavior and no execution logic live here: this module never touches
hardware, never calls into test_control/, and never runs anything on its
own. It exists so worker_runtime.py (not built yet -- explicitly out of
this task's scope) has an already-designed, already-tested vocabulary to
use instead of inventing ad hoc state tracking when it is eventually
written.

States
------
IDLE        -- no group assigned / worker is between groups.
DISCOVERED  -- Worker Discovery (workers.py) has found this worker in
               config/devices.py; nothing has been claimed yet.
READY       -- discovered and eligible to run (e.g. included in an
               ExecutionPlan); waiting for its resources to be claimed.
CLAIMING    -- attempting to claim its shared resources via an Arbiter
               (arbiter.py) before touching anything.
RUNNING     -- actively executing a BatteryOperationSequence for its
               current group.
COMPLETED   -- the current group finished successfully.
FAILED      -- the current group ended in an error.
ABORTED     -- cancelled (operator action or safety shutdown) before or
               during a group.

COMPLETED/FAILED/ABORTED are terminal for the CURRENT group but not for
the worker itself: a worker with more than one group (see WorkerPlan.groups
in workers.py -- groups on the same SMU run sequentially, never
concurrently) returns to IDLE afterwards to pick up its next group. This
mirrors the existing cooperative-cancellation checkpoint philosophy in
utils/cancellation.py: ABORTED is reachable from every non-terminal state,
not just RUNNING, so a cancellation requested between groups (worker
sitting IDLE) is representable too, not just one requested mid-operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class WorkerState(Enum):
    IDLE = "idle"
    DISCOVERED = "discovered"
    READY = "ready"
    CLAIMING = "claiming"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


#: States with no outgoing transition except back to IDLE (a worker
#: reset, ready to be discovered/claimed for its next group).
TERMINAL_STATES = frozenset({WorkerState.COMPLETED, WorkerState.FAILED, WorkerState.ABORTED})

#: The complete, explicit transition table. Anything not listed here is
#: illegal -- see validate_transition()/is_valid_transition() below.
ALLOWED_TRANSITIONS = {
    WorkerState.IDLE: frozenset({WorkerState.DISCOVERED, WorkerState.ABORTED}),
    WorkerState.DISCOVERED: frozenset({WorkerState.READY, WorkerState.ABORTED}),
    WorkerState.READY: frozenset({WorkerState.CLAIMING, WorkerState.ABORTED}),
    WorkerState.CLAIMING: frozenset({WorkerState.RUNNING, WorkerState.FAILED, WorkerState.ABORTED}),
    WorkerState.RUNNING: frozenset({WorkerState.COMPLETED, WorkerState.FAILED, WorkerState.ABORTED}),
    WorkerState.COMPLETED: frozenset({WorkerState.IDLE}),
    WorkerState.FAILED: frozenset({WorkerState.IDLE}),
    WorkerState.ABORTED: frozenset({WorkerState.IDLE}),
}


class InvalidWorkerTransitionError(Exception):
    """Raised when a transition is not present in ALLOWED_TRANSITIONS."""


def is_valid_transition(current: WorkerState, next_state: WorkerState) -> bool:
    """Pure predicate -- never raises, never mutates anything."""
    return next_state in ALLOWED_TRANSITIONS.get(current, frozenset())


def validate_transition(current: WorkerState, next_state: WorkerState) -> None:
    """Raise InvalidWorkerTransitionError if `current -> next_state` is not legal."""
    if not is_valid_transition(current, next_state):
        raise InvalidWorkerTransitionError(
            f"illegal worker lifecycle transition: {current.value!r} -> {next_state.value!r}"
        )


@dataclass
class WorkerLifecycle:
    """
    A minimal in-memory state holder enforcing ALLOWED_TRANSITIONS. Holds
    a single WorkerState and nothing else -- no resource handles, no
    hardware references, no group data (callers that need to track "which
    group is this worker currently on" keep that alongside, e.g. as done
    by orchestration/supervisor.py's reference implementation). Provided
    so a future worker_runtime.py does not have to re-derive this
    bookkeeping from scratch.
    """
    state: WorkerState = WorkerState.IDLE
    history: list = field(default_factory=list)

    def transition_to(self, next_state: WorkerState) -> WorkerState:
        validate_transition(self.state, next_state)
        self.history.append(self.state)
        self.state = next_state
        return self.state

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
