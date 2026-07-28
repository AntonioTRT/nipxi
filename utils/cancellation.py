"""
Project-wide cooperative cancellation primitives.

Design (see docs/architecture.md "Safe Cancellation Architecture"):
  - CancellationToken is a plain, single-threaded flag -- no threads, no
    signal handling, no stdin listeners live here. Something else (a
    signal.signal(SIGINT, ...) handler installed by main.py/test.py) calls
    request_cancel() when the operator wants to stop; that's the only
    producer this module knows about today.
  - check_cancellation(token) is the one call every long-running loop makes
    at a checkpoint. It is a no-op if token is None, so existing callers
    that don't care about cancellation (direct/scripted use of ChargeCycle,
    DischargeCycle, etc.) need no changes.
  - Checkpoints must only ever be placed BETWEEN atomic hardware operations
    -- never inside a relay activate/verify sequence or a PMU verify
    sequence (see hardware/relay_eth.py's mandatory safety sequence and
    hardware/smu.py's emergency_output_off()). Interrupting mid-sequence
    would leave hardware state less certain, not safer.
  - request_cancel() is idempotent: once requested, later calls are a
    no-op -- the first reason recorded is the one that sticks. There is
    currently only one severity (cancel); Emergency Abort is a deliberately
    separate, not-yet-implemented feature (see docs/architecture.md) and is
    NOT modeled here.
"""

import logging
import time

from utils.errors import OperationCancelledError


class CancellationToken:
    """
    A single-threaded, single-severity cancellation flag.

    Not itself tied to Ctrl+C or any specific trigger -- callers create one,
    pass it down through the call chain (ChargeCycle.run(), DischargeCycle.
    run(), BatteryTestSequence.run(), TestExecutor.run(), the relay scan/
    ethernet test loops in test.py), and something outside this module
    (a signal handler today) calls request_cancel() on it.
    """

    def __init__(self, owner: str = ""):
        self.owner = owner
        self._requested = False
        self._reason = ""
        self._requested_at = None
        self.log = logging.getLogger("nipxi.cancellation")

    @property
    def requested(self) -> bool:
        return self._requested

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def requested_at(self):
        """time.monotonic() timestamp of the request, or None if not requested."""
        return self._requested_at

    def request_cancel(self, reason: str = "cancellation requested"):
        """
        Record a cancellation request. Idempotent -- if already requested,
        this is a no-op (the first reason recorded is kept). Safe to call
        from a signal handler: does no I/O, raises nothing, just sets
        plain attributes.
        """
        if self._requested:
            return
        self._requested = True
        self._reason = reason
        self._requested_at = time.monotonic()
        self.log.warning(
            "Cancellation requested%s: %s",
            f" ({self.owner})" if self.owner else "", reason,
        )

    def check(self):
        """
        Checkpoint call. Raises OperationCancelledError if a cancellation
        has been requested; otherwise returns None immediately. Callers
        must only invoke this between atomic hardware operations.
        """
        if self._requested:
            raise OperationCancelledError(self._reason or "cancellation requested")


def check_cancellation(token):
    """
    No-op if token is None; otherwise delegates to token.check(). Lets every
    checkpoint call site read the same way (`check_cancellation(token)`)
    regardless of whether the caller passed a real token or omitted it.
    """
    if token is not None:
        token.check()


def interruptible_sleep(duration_s: float, token=None, poll_interval_s: float = 0.2):
    """
    Reusable interruptible wait -- a drop-in replacement for `time.sleep(duration_s)`
    that checks `token` for a cancellation request every `poll_interval_s`
    seconds instead of blocking for the full duration uninterrupted.

    Why this exists (see docs/architecture.md "Interruptible Wait Mechanism"
    and docs/TIMING_ANALYSIS.md): several real dwells in this codebase --
    hardware/smu.py::SMU.source_dc_voltage_point()'s `hold_s`,
    test_control/charge_cycle.py/discharge_cycle.py's `STABILIZATION_S` --
    held hardware energized (PSU output enabled, relay closed) for the
    full configured duration with NO cancellation checkpoint at all, so a
    Ctrl+C during one of those windows was not noticed until the sleep
    completed. `Settings.PROTO_TEST_DWELL_S` is explicitly the
    highest-priority instance of this (a temporary 5s value standing in
    for an intended 120s production value) -- at 120s, an uninterrupted
    `time.sleep()` there would be a ~2-minute cancellation blind spot.

    Behavior:
      - `token=None` (the default, matching every other cancellation
        checkpoint in this codebase): sleeps the FULL `duration_s` via a
        single `time.sleep()` call, byte-for-byte the same as before this
        function existed -- existing callers that don't pass a token see
        zero behavior change.
      - `token` given: checks `check_cancellation(token)` BEFORE the first
        sleep slice (so a cancellation already requested before the wait
        even begins is caught immediately, never sleeping at all) and
        again before every subsequent slice, sleeping in increments of at
        most `poll_interval_s` until either `duration_s` has fully
        elapsed (normal return, identical total wait time to a plain
        `time.sleep(duration_s)`) or a cancellation is detected (raises
        `OperationCancelledError` immediately, bounding worst-case
        latency to ~`poll_interval_s` instead of the full duration).
      - `duration_s <= 0`: returns immediately, no-op -- matches
        `time.sleep()`'s own behavior for a non-positive duration, and
        keeps every existing caller that passes `hold_s=0.0` (the default)
        unaffected.

    Must only be used for a dwell BETWEEN atomic hardware operations, same
    as `check_cancellation()` itself -- never wrap a single atomic
    command/verify round trip in this (e.g. never use this inside
    hardware/relay_eth.py's Telnet response wait, which is deliberately
    NOT interruptible -- interrupting mid-command would leave relay state
    less certain, not safer). This function itself never touches any
    hardware -- it is a pure timing primitive, reusable by any future
    Charge/Discharge/Cycle Battery workflow exactly as it is by the
    callers wired in today.
    """
    if duration_s <= 0:
        return
    if token is None:
        time.sleep(duration_s)
        return

    deadline = time.monotonic() + duration_s
    while True:
        check_cancellation(token)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll_interval_s, remaining))
