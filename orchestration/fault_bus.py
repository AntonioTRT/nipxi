"""
Global Fault Bus -- the single, thread-safe choke point every
GroupWorker reports a terminal fault into, and the single thing
orchestration/concurrent_supervisor.py watches to know when to trigger
a Global Emergency Stop (see that module's docstring). No GroupWorker
ever calls another GroupWorker directly -- every cross-group
interaction goes through one instance of this class, held by the
supervisor.

Ownership: one GlobalFaultBus per ConcurrentSupervisor run, constructed
by the supervisor, passed by reference into every GroupWorker it
starts. Threads write via report(); the supervisor's own thread reads
via wait_for_fault()/tripped.

Deliberately separate from utils/cancellation.py::CancellationToken --
a token is a single group's own, single-thread-owned "please stop"
flag; this bus is the cross-thread "something ELSE just failed, tell
every group to stop" signal. See docs/architecture.md
"Multi-Group Concurrency: Fault Propagation" for the full rationale.
This module does not touch, and never should touch, hardware directly
-- resource identity (matrix names, etc.) is out of scope here; that is
orchestration/resource_ownership.py's job, checked once at startup
before this bus exists.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FaultReport:
    """One terminal fault, as reported to the bus. `group_id=None` means
    the fault has no single owning group (e.g. an operator SIGINT, or an
    uncaught exception on the main/supervisor thread itself)."""
    group_id: str | None
    reason: str
    reported_at: float


class GlobalFaultBus:
    """
    Thread-safe. report() is safe to call from any GroupWorker thread,
    the process's SIGINT handler, or a threading.excepthook installed by
    ConcurrentSupervisor -- see that module. Idempotent in effect: only
    the FIRST report becomes `origin`; later reports are still recorded
    in `all_reports` (for forensic/timeline purposes) but do not change
    which fault is treated as the trigger.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tripped = threading.Event()
        self._origin: FaultReport | None = None
        self._all_reports: list[FaultReport] = []

    def report(self, group_id: str | None, reason: str) -> None:
        with self._lock:
            record = FaultReport(group_id=group_id, reason=reason, reported_at=time.monotonic())
            self._all_reports.append(record)
            if self._origin is None:
                self._origin = record
        self._tripped.set()

    @property
    def tripped(self) -> bool:
        return self._tripped.is_set()

    @property
    def origin(self) -> FaultReport | None:
        with self._lock:
            return self._origin

    @property
    def all_reports(self) -> list:
        with self._lock:
            return list(self._all_reports)

    def wait_for_fault(self, timeout: float = None) -> bool:
        """Block the caller's thread (the supervisor's own thread) until
        report() is called or `timeout` elapses. Returns True if tripped,
        False on timeout -- never raises."""
        return self._tripped.wait(timeout)
