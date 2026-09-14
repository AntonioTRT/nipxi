"""
GroupWorker -- runs ONE GroupRuntime's worker plan on its own thread,
under orchestration/concurrent_supervisor.py (see that module's
docstring for the full Multi-Group Concurrent Execution architecture).

Deliberately a THIN wrapper: it does not reimplement worker execution.
It reuses orchestration/supervisor.py::SequentialSupervisor UNCHANGED
to actually iterate a WorkerPlan's groups (the exact same class/code
path today's single-worker main.py would use if wired up) -- the only
new thing this class adds is (a) putting that call on its own thread
and (b) catching everything that escapes it so one worker's failure
can never silently vanish or crash the whole process.

`run_group` is injected by the caller (orchestration/
concurrent_supervisor.py's own caller -- ultimately main.py/test.py),
exactly matching SequentialSupervisor's own existing contract: this
module has no import of and no coupling to ChargeSequence/
DischargeSequence/CycleSequence/MonitorBatterySequence. Which
operation runs is a decision made above this layer, unchanged from
today.

KNOWN LIMITATION (not fixed here -- see module docstring for why doing
so is out of today's scope): a GroupWorker only reports a GENERIC
failure to the GlobalFaultBus (`"group <smu>: run_group failed for
<group>"`), not which of SAFETY_FAULT/STATION_FAULT/
RelayVerificationFailure/etc. actually occurred -- that distinction is
made and logged inside battery_operation_sequence.py::run_guarded()/
utils/safety_fault.py today, several layers below the plain
True/False/raise contract SequentialSupervisor.start() exposes.
Surfacing the specific fault type through to the bus would require
threading it through that contract too -- a real Phase 4/5 follow-up,
not something this class silently pretends to already do.
"""

from __future__ import annotations

import logging
import threading

from orchestration.fault_bus import GlobalFaultBus
from orchestration.group_runtime import GroupRuntime
from orchestration.supervisor import SequentialSupervisor, SupervisorStatus
from utils.errors import HardwareInitError

_log = logging.getLogger("nipxi.orchestration.group_worker")


class GroupWorker:
    """
    One worker plan, one thread. Lifecycle:
      start()        -- spawns the thread, returns immediately (never
                         blocks the caller).
      request_stop()  -- cooperative, see GroupRuntime.request_stop().
      join(timeout)   -- block until the thread finishes or timeout.
      status()        -- SequentialSupervisor's own SupervisorStatus
                         snapshot; never blocks, never raises.
    """

    def __init__(self, runtime: GroupRuntime, run_group, fault_bus: GlobalFaultBus):
        self._runtime = runtime
        self._run_group = run_group
        self._fault_bus = fault_bus
        self._supervisor = SequentialSupervisor(runtime.worker_plan, self._run_group_guarded)
        self._thread: threading.Thread | None = None
        self._connect_failed = False

    @property
    def group_id(self) -> str:
        return self._runtime.worker_plan.smu_name

    def _run_group_guarded(self, group_name: str) -> bool:
        """
        Adapts the caller-supplied run_group(group_name, group_runtime)
        to the (group_name) -> bool contract SequentialSupervisor calls.
        Deliberately does NOT catch or report anything itself --
        SequentialSupervisor.start() already catches any exception from
        this call and records FAILED either way (raise or falsy
        return); _run() below, after start() returns, is the ONE place
        that inspects status.failed_group and reports to the fault bus,
        so both cases are reported exactly once, not from two places.
        """
        return bool(self._run_group(group_name, self._runtime))

    def _run(self) -> None:
        try:
            self._runtime.connect()
        except HardwareInitError as e:
            self._connect_failed = True
            self._fault_bus.report(self.group_id, f"group {self.group_id}: connect failed: {e}")
            return
        except Exception as e:
            self._connect_failed = True
            self._fault_bus.report(self.group_id, f"group {self.group_id}: unexpected connect error: {e}")
            return

        try:
            self._supervisor.start()
            status = self._supervisor.status()
            # A plain falsy run_group() return (no exception) is a FAILED
            # group too -- SequentialSupervisor.start() already recorded
            # it in status.failed_group, but does not itself know a
            # GlobalFaultBus exists, so this is the one place that
            # reports it onward. Skipped when this worker's OWN
            # cancellation caused the stop (an expected, cooperative
            # shutdown -- see GroupRuntime.request_stop() -- not a new
            # failure to propagate).
            if status.failed_group is not None and not self._runtime.cancellation.requested:
                self._fault_bus.report(
                    self.group_id,
                    f"group {self.group_id}: run_group failed for {status.failed_group}",
                )
        except Exception as e:
            # SequentialSupervisor.start() already catches run_group's own
            # exceptions internally (see its docstring) -- reaching this
            # branch means something escaped ITS OWN bookkeeping, which is
            # exactly the "uncaught worker exception" case
            # docs/architecture.md's Global Emergency Stop trigger list
            # names explicitly.
            _log.exception("GroupWorker %s: uncaught exception from SequentialSupervisor", self.group_id)
            self._fault_bus.report(self.group_id, f"group {self.group_id}: uncaught exception: {e}")
        finally:
            try:
                self._runtime.disconnect()
            except Exception as e:
                _log.error("GroupWorker %s: disconnect failed: %s", self.group_id, e)
                self._fault_bus.report(self.group_id, f"group {self.group_id}: disconnect failed: {e}")

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"GroupWorker-{self.group_id}", daemon=False,
        )
        self._thread.start()

    def request_stop(self, reason: str) -> None:
        self._runtime.request_stop(reason)
        self._supervisor.stop()

    def join(self, timeout: float = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> SupervisorStatus:
        return self._supervisor.status()
