"""
ConcurrentSupervisor -- Multi-Group Concurrent Execution entry point
(see docs/architecture.md for the full architecture review this
implements). Sibling to, NOT a replacement for,
orchestration/supervisor.py::SequentialSupervisor: main.py's existing
single-worker path is completely unchanged; this class is additive,
selected only when a caller explicitly asks to run more than one
worker at once.

RUNTIME OWNERSHIP (see orchestration/group_runtime.py,
orchestration/group_worker.py)
=============================================================================
One orchestration/workers.py::WorkerPlan == one
orchestration/group_runtime.py::GroupRuntime == one
orchestration/group_worker.py::GroupWorker == one Python thread. Each
owns its OWN HardwareManager/SafetyMonitor/CancellationToken -- no two
GroupRuntimes ever share a relay matrix, SMU, DMM, or DAQ (enforced by
orchestration/resource_ownership.py::ResourceOwnershipValidator BEFORE
any GroupRuntime is constructed, below). data/storage.py::DataStorage
is the one deliberate exception -- see that module's own docstring for
why it stays a single shared instance (global run sequence,
traceability) rather than one per group, made thread-safe instead of
split.

MATRIX OWNERSHIP -- see orchestration/resource_ownership.py's module
docstring for the full statement (matrix-level, exclusive, "one matrix
== one owner" for now; channel-range segmentation explicitly deferred,
not implemented anywhere in this file). This class never inspects a
relay-matrix resource_name's structure -- it only ever passes group
names through to ResourceOwnershipValidator/GroupRuntime, both of
which already document the same extensibility contract.

FAULT PROPAGATION / GLOBAL EMERGENCY STOP
=============================================================================
One orchestration/fault_bus.py::GlobalFaultBus, constructed here,
shared by reference with every GroupWorker. This supervisor's own
thread blocks on fault_bus.wait_for_fault() (with a short poll
interval so it can also notice every worker finishing normally) --
the moment it trips, orchestration/global_emergency_stop.py::
GlobalEmergencyStop.trigger() is called exactly once, stopping every
worker (including whichever one originated the fault) and waiting for
each to reach a verified-safe (or explicitly unverified-safe) state
before this method returns. No operator acknowledgement is shown by
this class itself -- run() returns a ConcurrentRunResult the caller
(main.py/test.py, not built yet) uses to render ONE combined
acknowledgement screen, reusing utils/safety_fault.py::
display_safety_fault_screen()'s existing display logic rather than
calling it once per group.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import config.devices as dev_cfg
from config.settings import Settings
from orchestration.fault_bus import GlobalFaultBus
from orchestration.global_emergency_stop import GlobalEmergencyStop
from orchestration.group_runtime import GroupRuntime
from orchestration.group_worker import GroupWorker
from orchestration.resource_ownership import ResourceOwnershipValidator
from orchestration.workers import discover_workers

_log = logging.getLogger("nipxi.orchestration.concurrent_supervisor")

#: How often the supervisor's own thread wakes up to check "has every
#: worker finished normally?" while also watching the fault bus. Not a
#: safety-relevant timing (the fault bus itself is event-driven via
#: threading.Event, not polled for the STOP decision) -- this interval
#: only bounds how quickly a normal (fault-free) completion is noticed.
_POLL_INTERVAL_S = 0.2


@dataclass
class ConcurrentRunResult:
    """
    Returned by ConcurrentSupervisor.run(). `faulted` is True iff a
    Global Emergency Stop was triggered (fault_bus.tripped). `shutdown`
    is GlobalEmergencyStop.trigger()'s own {group_id: "stopped" |
    "unverified_safe"} return value, empty if no fault occurred.
    """
    faulted: bool
    fault_reason: str | None
    shutdown: dict = field(default_factory=dict)
    fault_bus: GlobalFaultBus = None


class ConcurrentSupervisor:
    """
    One instance per multi-group run. `group_names` are the groups
    requested to run TOGETHER -- validated for resource exclusivity at
    construction time, before any hardware is touched.

    `run_group_factory(group_name, group_runtime) -> bool` is injected
    by the caller, exactly matching orchestration/supervisor.py::
    SequentialSupervisor's own `run_group` contract (see that module's
    docstring) -- this class has no coupling to ChargeSequence/
    DischargeSequence/CycleSequence/MonitorBatterySequence, same as
    SequentialSupervisor and GroupWorker.
    """

    def __init__(self, group_names, settings: Settings, run_group_factory,
                 battery_groups: dict = None, battery_cfg: dict = None):
        self._settings = settings
        self._run_group_factory = run_group_factory
        battery_groups = battery_groups if battery_groups is not None else dev_cfg.BATTERY_GROUPS

        # Fail fast, before any HardwareManager/GroupRuntime exists --
        # see orchestration/resource_ownership.py's module docstring.
        ResourceOwnershipValidator.validate(group_names, battery_groups)

        requested = {name: battery_groups[name] for name in group_names}
        worker_plans = discover_workers(requested)
        if not worker_plans:
            raise ValueError(
                f"none of the requested groups {list(group_names)!r} have an SMU assigned "
                "-- nothing to run (see config/devices.py::hardware_for_group())."
            )

        self._fault_bus = GlobalFaultBus()
        self._runtimes = [GroupRuntime(plan, settings, battery_cfg) for plan in worker_plans]
        self._workers = [
            GroupWorker(runtime, run_group_factory, self._fault_bus)
            for runtime in self._runtimes
        ]

    def run(self) -> ConcurrentRunResult:
        """
        Start every GroupWorker, then block until either every worker
        finishes normally OR the fault bus trips -- whichever happens
        first. Never returns early on a single worker's normal
        completion; only ALL-finished or a FAULT ends the wait.
        """
        for worker in self._workers:
            worker.start()

        while True:
            if self._fault_bus.wait_for_fault(timeout=_POLL_INTERVAL_S):
                break
            if all(not w.is_alive() for w in self._workers):
                return ConcurrentRunResult(faulted=False, fault_reason=None, fault_bus=self._fault_bus)

        origin = self._fault_bus.origin
        shutdown = GlobalEmergencyStop.trigger(self._workers, self._fault_bus)
        return ConcurrentRunResult(
            faulted=True,
            fault_reason=origin.reason if origin is not None else None,
            shutdown=shutdown,
            fault_bus=self._fault_bus,
        )
