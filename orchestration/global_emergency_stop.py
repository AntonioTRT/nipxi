"""
Global Emergency Stop -- the trigger -> propagate -> verify sequence
run by orchestration/concurrent_supervisor.py the moment its
GlobalFaultBus trips (see that module's docstring for the full
architecture). A thin coordinator: it does not implement any new
hardware-safety logic of its own. Every actual hardware action it
causes is one of two ALREADY-EXISTING, already-tested paths:

  - the worker that reported the fault has typically already run its
    own emergency_stop()/safe_cancel_shutdown() by the time this class
    even runs (that happens inside test_control/
    battery_operation_sequence.py::run_guarded(), several layers below
    SequentialSupervisor/GroupWorker);
  - every OTHER worker is told to stop via
    orchestration/group_worker.py::GroupWorker.request_stop(), which
    sets that worker's OWN CancellationToken -- the same cooperative
    checkpoint mechanism Ctrl+C already uses -- which triggers THAT
    worker's own run_guarded()/safe_cancel_shutdown() the next time it
    checks. No group's hardware is ever touched from a thread other
    than that group's own.

KNOWN NUANCE: a worker stopped this way (because ANOTHER worker
faulted) goes through safe_cancel_shutdown() ("SAFE CANCELLATION" in
the log), not emergency_stop() ("EMERGENCY STOP") -- the two perform
an IDENTICAL hardware sequence (PMU output off + verified, relay
open_all + verified; see test_control/safety_monitor.py), so there is
no safety difference, only a logging/severity-label difference. Giving
CancellationToken a second severity so a global-fault-triggered stop
could route to emergency_stop() instead would mean redesigning
utils/cancellation.py's single-severity model -- explicitly out of
scope (see that module's docstring and the architecture review that
approved this design). Documented here as an accepted nuance, not
silently glossed over.
"""

from __future__ import annotations

import logging

from orchestration.fault_bus import GlobalFaultBus
from orchestration.group_worker import GroupWorker

_log = logging.getLogger("nipxi.orchestration.global_emergency_stop")


class GlobalEmergencyStop:
    """
    Stateless coordinator -- trigger() is the only entry point. Called
    exactly once per ConcurrentSupervisor run, by the supervisor's own
    thread, the moment fault_bus.tripped becomes true.
    """

    @staticmethod
    def trigger(workers: list, fault_bus: GlobalFaultBus, *, join_timeout: float = 30.0) -> dict:
        """
        Request every worker to stop, then wait (bounded by
        `join_timeout` PER WORKER) for each to actually finish. Returns
        {group_id: "stopped" | "unverified_safe"} -- "unverified_safe"
        means the worker did not finish within join_timeout, which MUST
        be escalated to the operator as an explicit unresolved state,
        never silently treated as safe (see docs/architecture.md
        "Global Emergency Stop: Shutdown Ordering" -- operator
        acknowledgement is gated on this return value).
        """
        origin = fault_bus.origin
        reason = origin.reason if origin is not None else "global emergency stop"
        _log.error("Global Emergency Stop triggered: %s", reason)

        for worker in workers:
            try:
                worker.request_stop(reason)
            except Exception as e:
                _log.critical(
                    "GlobalEmergencyStop: request_stop() itself raised for group %s "
                    "-- this worker's hardware state cannot be assumed safe: %s",
                    worker.group_id, e,
                )

        results: dict = {}
        for worker in workers:
            worker.join(timeout=join_timeout)
            if worker.is_alive():
                _log.critical(
                    "GlobalEmergencyStop: group %s did not stop within %.1fs -- "
                    "hardware state UNVERIFIED, physically inspect this station.",
                    worker.group_id, join_timeout,
                )
                results[worker.group_id] = "unverified_safe"
            else:
                results[worker.group_id] = "stopped"

        return results
