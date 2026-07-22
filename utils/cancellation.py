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
