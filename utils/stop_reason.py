"""
Shared vocabulary for why a test run (or a single channel within one)
stopped -- kept orthogonal to whether it stopped successfully. A run can be
CANCELLED after 2 of 8 channels passed; "stop reason" and "how much
completed" are two different facts, not one enum (see docs/architecture.md
"Result State Model").

TIMEOUT is defined for future use (a charge/discharge cycle hitting its
configured deadline -- see config/settings.py CHARGE_TIMEOUT_S/
DISCHARGE_TIMEOUT_S) but is not yet wired end-to-end: charge_cycle.py/
discharge_cycle.py already return False on timeout, but
BatteryTestSequence.run() does not currently propagate that per-channel
outcome to TestExecutor (see test_executor.py's TODO on per-channel
results) -- that is a separate, pre-existing gap, not introduced or fixed
by the cancellation work this constant set was added for.
"""


class StopReason:
    COMPLETED        = "COMPLETED"
    FAILED           = "FAILED"
    SAFETY_VIOLATION = "SAFETY_VIOLATION"
    TIMEOUT          = "TIMEOUT"
    CANCELLED        = "CANCELLED"
