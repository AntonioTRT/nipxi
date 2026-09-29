"""
Resource Ownership Validator -- the startup gate for
orchestration/concurrent_supervisor.py (see that module's docstring).
Fails fast, before any GroupRuntime is constructed or any hardware is
touched, if two groups requested to run CONCURRENTLY would end up
sharing a physical resource.

OWNERSHIP MODEL -- MATRIX-LEVEL BY DEFAULT, RANGE-AWARE WHERE DECLARED
=============================================================================
Every relay matrix (position-control AND sense-routing) is owned, in
full, by at most one requested group UNLESS two or more groups
explicitly declare disjoint `relay_range` sub-ranges of the same matrix
(config/devices.py::compose_relay_resource_name() -- Matrix + Relay-Range
Ownership, PHASE 1-3). A group that declares no `relay_range` (every
group in production today) is still, exactly as before, an exclusive
whole-matrix owner: its bare "MATRIX_NUMATO_202"-style resource_name
conflicts with ANYTHING else on that matrix, ranged or not (see
orchestration/resource_graph.py::_resource_names_conflict()). Two groups
that both declare a `relay_range` on the same matrix conflict only if
their ranges actually overlap.

THIS PHASE IS OWNERSHIP-VALIDATION ONLY -- HARDWARE IS STILL UNCHANGED
=============================================================================
hardware/relay_eth.py::NumatoRelayMatrix.write_all()/verify_all()/
close()/_force_all_off_and_verify()/_emergency_all_off()/close_all() are
completely UNCHANGED and still operate on the WHOLE physical bank. This
means: even though this validator will now ALLOW two groups with disjoint
relay_range values to be requested together, actually running them
concurrently against the SAME physical Numato unit is still unsafe --
one owner's routine close()/open() still force-off/verify the entire
bank, which would trip the other owner's relay and very likely their own
false-positive emergency shutdown. Nothing in config, ownership, or
resource_graph.py claims otherwise; a channel-range-aware write/verify
path in hardware/relay_eth.py (and a shared-driver/serialization story in
orchestration/group_runtime.py, since each GroupRuntime today opens its
own exclusive socket to the matrix) is a SEPARATE, not-yet-started,
genuinely safety-critical redesign. Declaring relay_range in config today
is only useful for validating a FUTURE config shape ahead of that
redesign -- it must not be used to actually run two groups against one
physical matrix at the same time yet.

WHY THIS STAYED EXTENSIBLE WITHOUT A REDESIGN OF THE SURROUNDING LAYERS
=============================================================================
This validator, orchestration/concurrent_supervisor.py,
orchestration/group_runtime.py, and orchestration/group_worker.py all
identify a matrix purely by the resource_name STRING config/devices.py
returns (via hardware_for_group()/topology.py's role reads) -- none of
them parse or assume anything about that string's structure. That is why
adding relay_range support needed no change to those three classes, and
only a contained change to orchestration/resource_graph.py's conflict
rule (exact equality -> interval overlap, see
_resource_names_conflict()/_overlapping_resources() there) plus
topology.py/config/devices.py composing the range into the resource_name
string in the first place. This module's own check_ownership() needed NO
logic change: it already consumed resource_graph.py's conflict set
opaquely by exact string match, and that match still holds since the
overlap-aware set resource_graph.py now returns is built from the same
usage-map strings as before.
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
