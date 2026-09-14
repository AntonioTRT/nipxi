"""
Resource Ownership Validator -- the startup gate for
orchestration/concurrent_supervisor.py (see that module's docstring).
Fails fast, before any GroupRuntime is constructed or any hardware is
touched, if two groups requested to run CONCURRENTLY would end up
sharing a physical resource.

CURRENT OWNERSHIP MODEL -- MATRIX-LEVEL, EXCLUSIVE (deliberate, not
provisional)
=============================================================================
Every relay matrix (position-control AND sense-routing) is owned, in
full, by at most one requested group. "One matrix == one owner" is
enforced by treating a matrix's bare device name (e.g.
"MATRIX_NUMATO_202") as the unit of ownership -- never a sub-range of
its channels. This matches hardware/relay_eth.py::NumatoRelayMatrix.
close()'s existing behavior (write_all(0) across the WHOLE matrix
before enabling any requested channel): a matrix is not safely
partitionable across two owners today, so ownership below the
whole-matrix level would be unsafe to validate as "fine" even if this
validator tried to allow it. This module does not implement, and must
not be extended to implement, any channel-level or channel-range
conflict check -- see "Future: matrix segmentation" below.

FUTURE: MATRIX SEGMENTATION (explicitly deferred, not implemented here)
=============================================================================
The long-term hardware intent is that a single relay matrix may
eventually host multiple battery groups on disjoint channel ranges
(e.g. MATRIX_NUMATO_202 channels 1-8 -> B1, 9-16 -> B2, ...), with an
analogous per-channel model for sense-routing. That is NOT implemented
here, and this validator's current pass/fail rule (exact resource-name
overlap) is deliberately the wrong rule for it: "MATRIX_NUMATO_202:1-8"
and "MATRIX_NUMATO_202:5-12" are different strings but overlapping
channels, so a future segmented-ownership model would need interval-
overlap logic in this module (and in orchestration/resource_graph.py,
which this module builds on), plus a channel-range-aware close()/write
path in hardware/relay_eth.py -- a hardware-safety-philosophy change
this module does not make. Until that day, every requested group must
resolve to a distinct whole-matrix resource_name, full stop.

WHY THIS STAYS EXTENSIBLE WITHOUT A REDESIGN
=============================================================================
This validator, orchestration/concurrent_supervisor.py,
orchestration/group_runtime.py, and orchestration/group_worker.py all
identify a matrix purely by the resource_name STRING config/devices.py
already returns (via hardware_for_group()/topology.py's role reads) --
none of them parse or assume anything about that string's structure.
The day config/devices.py starts returning "MATRIX_NUMATO_202:1-8"
instead of "MATRIX_NUMATO_202" for a channel-range-scoped group, this
module's usage-map construction and those three classes' consumption of
it need NO change; only the equality-based conflict check just below
would need to become an interval-overlap check. That containment is by
design, not accidental -- see docs/architecture.md "Future Architecture:
Resource Dependency Graph" for the equivalent note on
resource_graph.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import config.devices as dev_cfg
from orchestration.resource_graph import build_resource_graph
from orchestration.topology import discover_topology, ResourceKey
from orchestration.workers import discover_workers

#: Sense-routing matrices are validated as a SEPARATE role from
#: "relay_matrix_name" (the position-control matrix each group's
#: hardware_for_group() resolves) -- a group's sense-routing matrix
#: (config/devices.py::SENSE_ROUTING[group["sense_channel"]]
#: ["relay_matrix"]) is a physically distinct device from its own
#: relay_matrix, and a group without a "sense_channel" simply has no
#: entry for this role, exactly mirroring topology.py's own "field is
#: None -> no entry" rule.
SENSE_ROUTING_ROLE = "sense_relay_matrix_name"


class ResourceOwnershipConflict(Exception):
    """
    Raised by validate() when two or more requested groups would share
    one physical resource. `conflicts` is
    {ResourceKey(role, resource_name): {group names}} for every
    resource with more than one requesting group -- the same shape
    discover_topology() itself returns, filtered to violations only.
    """

    def __init__(self, conflicts: dict):
        self.conflicts = conflicts
        detail = "; ".join(
            f"{key.role}={key.resource_name!r} shared by {sorted(group_names)}"
            for key, group_names in conflicts.items()
        )
        super().__init__(f"resource ownership conflict: {detail}")


@dataclass
class ResourceOwnershipReport:
    """Pure result of validate() -- no exception, for callers that want
    to inspect rather than fail immediately (e.g. a future pre-flight
    UI check)."""
    usage: dict = field(default_factory=dict)
    conflicts: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.conflicts


def _sense_routing_usage(requested: dict) -> dict:
    """
    {ResourceKey(SENSE_ROUTING_ROLE, matrix_name): {group_names}} for
    every requested group with a `sense_channel` that resolves in
    config/devices.py::SENSE_ROUTING. A group with no sense_channel (or
    one not present in SENSE_ROUTING) contributes no entry -- same
    "never guess, never placeholder" rule topology.py already follows.
    """
    usage: dict = {}
    for group_name, grp in requested.items():
        sense_channel = grp.get("sense_channel")
        if sense_channel is None:
            continue
        route = dev_cfg.SENSE_ROUTING.get(sense_channel)
        if route is None:
            continue
        key = ResourceKey(SENSE_ROUTING_ROLE, route["relay_matrix"])
        usage.setdefault(key, set()).add(group_name)
    return usage


def check_ownership(group_names, battery_groups: dict = None) -> ResourceOwnershipReport:
    """
    Pure check -- computes the combined (position-control + sense-
    routing) resource usage map for exactly `group_names` (NOT every
    enabled group -- unlike discover_topology()'s own default, this
    restricts to the groups actually requested to run concurrently),
    and returns every resource shared across two different WORKERS
    (SMU-anchored units -- orchestration/workers.py::discover_workers()).

    Conflicts are computed at WORKER grain, not raw-group grain, by
    delegating to orchestration/resource_graph.py::build_resource_graph()
    -- that function already excludes smu_name from "shared" (two
    groups on the SAME SMU-anchored worker run SEQUENTIALLY, one at a
    time, by construction; that is not a concurrency conflict) and
    aggregates each worker's OWN groups' resources into one union before
    comparing against another worker's union (so two groups on the SAME
    worker sharing a relay matrix/DMM/DAQ is never flagged either -- they
    never run at the same time). Reusing that function here, rather than
    re-deriving the same logic at raw-group grain, is deliberate: an
    earlier version of this module compared raw groups directly and
    would have wrongly rejected the legitimate "one worker, several
    groups, always sequential" pattern the moment a config used it.

    Sense-routing matrices are not covered by build_resource_graph()
    (it only knows the roles topology.py's RESOURCE_ROLES declares), so
    the same worker-grain comparison is repeated here for that one role.
    """
    groups = battery_groups if battery_groups is not None else dev_cfg.BATTERY_GROUPS
    requested = {name: groups[name] for name in group_names}

    usage = dict(discover_topology(requested))
    for key, names in _sense_routing_usage(requested).items():
        usage.setdefault(key, set()).update(names)

    conflicts: dict = {}

    graph = build_resource_graph(requested)
    for (_smu_a, _smu_b), resource_names in graph.conflicts.items():
        for resource_name in resource_names:
            for key in usage:
                if key.resource_name == resource_name and key.role != "smu_name":
                    conflicts.setdefault(key, set()).update(usage[key])

    workers = discover_workers(requested)
    sense_by_worker = {}
    for worker in workers:
        worker_groups = set(worker.groups)
        sense_by_worker[worker.smu_name] = {
            key.resource_name for key, names in usage.items()
            if key.role == SENSE_ROUTING_ROLE and names & worker_groups
        }
    for smu_a, smu_b in combinations(sense_by_worker, 2):
        for resource_name in sense_by_worker[smu_a] & sense_by_worker[smu_b]:
            key = ResourceKey(SENSE_ROUTING_ROLE, resource_name)
            conflicts.setdefault(key, set()).update(usage[key])

    return ResourceOwnershipReport(usage=usage, conflicts=conflicts)


class ResourceOwnershipValidator:
    """
    Startup gate -- see module docstring. Stateless; validate() is the
    only entry point orchestration/concurrent_supervisor.py calls,
    before constructing any GroupRuntime.
    """

    @staticmethod
    def validate(group_names, battery_groups: dict = None) -> ResourceOwnershipReport:
        """
        Return a ResourceOwnershipReport with ok=True, or raise
        ResourceOwnershipConflict. `group_names` must be exactly the
        groups a single ConcurrentSupervisor run intends to start
        together -- not "every enabled group in config".
        """
        report = check_ownership(group_names, battery_groups)
        if not report.ok:
            raise ResourceOwnershipConflict(report.conflicts)
        return report
