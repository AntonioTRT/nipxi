"""
State machine placeholder for test flow control.
Extend this if you need explicit state tracking (e.g., for GUI or recovery).

States (to be refined):
    IDLE -> INITIALIZING -> SELECTING_CHANNEL -> CHARGING -> DISCHARGING
         -> COMPLETE -> ERROR
"""

from enum import Enum, auto


class TestState(Enum):
    IDLE             = auto()
    INITIALIZING     = auto()
    SELECTING_CHANNEL = auto()
    VERIFYING_CURRENT = auto()
    CHARGING         = auto()
    DISCHARGING      = auto()
    COMPLETE         = auto()
    ERROR            = auto()


class StateMachine:
    def __init__(self):
        self.state = TestState.IDLE

    def transition(self, new_state: TestState):
        # TODO: add allowed-transition guard if needed
        self.state = new_state

    def is_running(self) -> bool:
        return self.state not in (TestState.IDLE, TestState.COMPLETE, TestState.ERROR)
