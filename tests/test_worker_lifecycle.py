"""
Tests for orchestration/worker_lifecycle.py.

No hardware, no execution -- purely the state model and its transition
rules. Verifies every legal transition, that illegal ones raise, that
terminal states can only return to IDLE, and that ABORTED is reachable
from every non-terminal state (the cooperative-cancellation-checkpoint
property this module's docstring calls out).
"""

import unittest

from orchestration.worker_lifecycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidWorkerTransitionError,
    WorkerLifecycle,
    WorkerState,
    is_valid_transition,
    validate_transition,
)

ALL_STATES = list(WorkerState)


class TransitionTableTests(unittest.TestCase):
    def test_every_state_has_an_entry(self):
        for state in ALL_STATES:
            self.assertIn(state, ALLOWED_TRANSITIONS)

    def test_terminal_states_only_return_to_idle(self):
        for state in TERMINAL_STATES:
            self.assertEqual(ALLOWED_TRANSITIONS[state], frozenset({WorkerState.IDLE}))

    def test_aborted_reachable_from_every_non_terminal_state(self):
        for state in ALL_STATES:
            if state in TERMINAL_STATES:
                continue
            self.assertIn(WorkerState.ABORTED, ALLOWED_TRANSITIONS[state],
                          f"{state} must be able to reach ABORTED")

    def test_happy_path_is_fully_legal(self):
        path = [
            WorkerState.IDLE, WorkerState.DISCOVERED, WorkerState.READY,
            WorkerState.CLAIMING, WorkerState.RUNNING, WorkerState.COMPLETED,
            WorkerState.IDLE,
        ]
        for current, nxt in zip(path, path[1:]):
            self.assertTrue(is_valid_transition(current, nxt), f"{current} -> {nxt}")

    def test_skipping_a_state_is_illegal(self):
        self.assertFalse(is_valid_transition(WorkerState.IDLE, WorkerState.RUNNING))
        self.assertFalse(is_valid_transition(WorkerState.DISCOVERED, WorkerState.CLAIMING))

    def test_validate_transition_raises_on_illegal_transition(self):
        with self.assertRaises(InvalidWorkerTransitionError):
            validate_transition(WorkerState.IDLE, WorkerState.RUNNING)

    def test_validate_transition_is_silent_on_legal_transition(self):
        validate_transition(WorkerState.IDLE, WorkerState.DISCOVERED)  # must not raise


class WorkerLifecycleObjectTests(unittest.TestCase):
    def test_starts_idle(self):
        lifecycle = WorkerLifecycle()
        self.assertEqual(lifecycle.state, WorkerState.IDLE)
        self.assertFalse(lifecycle.is_terminal())

    def test_transition_to_updates_state_and_history(self):
        lifecycle = WorkerLifecycle()
        lifecycle.transition_to(WorkerState.DISCOVERED)
        self.assertEqual(lifecycle.state, WorkerState.DISCOVERED)
        self.assertEqual(lifecycle.history, [WorkerState.IDLE])

    def test_illegal_transition_raises_and_does_not_mutate_state(self):
        lifecycle = WorkerLifecycle()
        with self.assertRaises(InvalidWorkerTransitionError):
            lifecycle.transition_to(WorkerState.RUNNING)
        self.assertEqual(lifecycle.state, WorkerState.IDLE)

    def test_is_terminal_true_for_terminal_states_only(self):
        lifecycle = WorkerLifecycle()
        for state in (WorkerState.COMPLETED, WorkerState.FAILED, WorkerState.ABORTED):
            lifecycle.state = state
            self.assertTrue(lifecycle.is_terminal())
        lifecycle.state = WorkerState.RUNNING
        self.assertFalse(lifecycle.is_terminal())

    def test_full_multi_group_cycle_returns_to_idle_between_groups(self):
        lifecycle = WorkerLifecycle()
        for _ in range(2):  # simulate two sequential groups on one worker
            lifecycle.transition_to(WorkerState.DISCOVERED)
            lifecycle.transition_to(WorkerState.READY)
            lifecycle.transition_to(WorkerState.CLAIMING)
            lifecycle.transition_to(WorkerState.RUNNING)
            lifecycle.transition_to(WorkerState.COMPLETED)
            lifecycle.transition_to(WorkerState.IDLE)
        self.assertEqual(lifecycle.state, WorkerState.IDLE)


if __name__ == "__main__":
    unittest.main()
