"""
Resource Dependency Graph -- combines Worker Discovery
(orchestration/workers.py) and Topology Discovery
(orchestration/topology.py) into an explicit statement of which workers
depend on which SHARED resources, and which pairs of workers would
conflict if ever run concurrently without arbitration (see docs/
architecture.md "Future Architecture: Resource Dependency Graph"). Pure,
read-only. NOT part of the current execution path.

This is deliberately a SEPARATE module from workers.py: "what is a
worker" (partition by exclusive SMU ownership) and "what does a worker
depend on besides its own SMU" (everything else it shares with other
workers) are different questions, and conflating them risks a naive
"one worker per SMU" model that looks fully independent when it is not
-- see docs/architecture.md for the concrete example (today's B1-B4
sharing one relay matrix).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from orchestration.topology import discover_topology
from orchestration.workers import discover_workers, WorkerPlan


def _split_resource_name(resource_name: str):
    """
    Matrix + Relay-Range Ownership -- PHASE 2. Split a resource_name into
    (matrix, range_or_None). "MATRIX_NUMATO_202:1-8" splits into
    ("MATRIX_NUMATO_202", (1, 8)); a bare "MATRIX_NUMATO_202" (no range --
    the ONLY form that existed before this phase, and still what every
    non-relay resource -- SMU/DMM/DAQ names -- and every whole-matrix
    relay owner produces) splits into ("MATRIX_NUMATO_202", None). Any
    string that isn't a valid "<matrix>:<lo>-<hi>" suffix (including one
    that simply has no ':') is treated as an unranged, whole-resource
    name -- unchanged pre-range behavior.
    """
    if ":" not in resource_name:
        return resource_name, None
    matrix, _, range_part = resource_name.rpartition(":")
    lo_s, _, hi_s = range_part.partition("-")
    try:
        lo, hi = int(lo_s), int(hi_s)
    except ValueError:
        return resource_name, None
    return matrix, (lo, hi)


def _resource_names_conflict(name_a: str, name_b: str) -> bool:
    """
    True if two resource_name strings identify overlapping hardware.

    Identical strings always conflict (this is the ENTIRE pre-range rule,
    preserved exactly). Two different strings only conflict if they name
    the same underlying matrix AND either side is unranged -- whole-matrix
    ownership still conflicts with any sub-range on that matrix, since
    hardware/relay_eth.py's write_all()/verify_all() safety sequence is
    UNCHANGED by this phase and still operates on the whole physical bank
    -- or their declared ranges actually overlap. Disjoint ranges on the
    same matrix (e.g. "MATRIX_NUMATO_202:1-8" vs
    "MATRIX_NUMATO_202:9-16") do NOT conflict -- this is the one new
    outcome this phase introduces relative to the old exact-equality rule.
    """
    if name_a == name_b:
        return True
    matrix_a, range_a = _split_resource_name(name_a)
    matrix_b, range_b = _split_resource_name(name_b)
    if matrix_a != matrix_b:
        return False
    if range_a is None or range_b is None:
        return True
    (lo_a, hi_a), (lo_b, hi_b) = range_a, range_b
    return lo_a <= hi_b and lo_b <= hi_a


def _overlapping_resources(names_a: set, names_b: set) -> set:
    """
    Every resource name in `names_a` or `names_b` that conflicts (see
    _resource_names_conflict()) with something in the other set. Replaces
    the plain `names_a & names_b` set-intersection used before Matrix +
    Relay-Range Ownership existed -- for every resource that never uses a
    range suffix (SMU/DMM/DAQ names, and any relay matrix nobody has ever
    assigned a relay_range to, which today is EVERY relay matrix in
    production), this produces the exact same result as `&`, since two
    unranged names only ever "conflict" here by being the identical
    string -- exactly what `&` already found.
    """
    overlap = set()
    for name_a in names_a:
        for name_b in names_b:
            if _resource_names_conflict(name_a, name_b):
                overlap.add(name_a)
                overlap.add(name_b)
    return overlap


@dataclass
class ResourceGraph:
    """
    `workers` -- the discovered WorkerPlans, each with its
    `shared_dependencies` populated by build_resource_graph() below (empty
    from discover_workers() alone).

    `conflicts` -- {(smu_name_a, smu_name_b): {shared resource names}} for
    every pair of workers that depend on at least one resource in common.
    Empty today (there is only one worker) -- populated automatically the
    moment a config declares a second SMU-anchored worker whose groups
    share a relay matrix, DMM, or DAQ with the first. This is exactly the
    thing a naive "group by SMU" derivation would miss.
    """
    workers: list = field(default_factory=list)
    conflicts: dict = field(default_factory=dict)


def build_resource_graph(battery_groups: dict = None) -> ResourceGraph:
    """
    Discover workers and topology from the SAME `battery_groups` input
    (defaults to config/devices.py::BATTERY_GROUPS) so a caller/test can
    never end up with the two disagreeing about which config they
    describe.

    A worker's `shared_dependencies` is every resource name used by its
    own groups, EXCLUDING the smu_name role -- the SMU is the one
    resource discover_workers() already treats as exclusively owned by
    construction, so it is never counted as "shared" here even though it
    technically appears once in the topology usage map too.
    """
    workers: list[WorkerPlan] = discover_workers(battery_groups)
    usage = discover_topology(battery_groups)

    groups_by_worker = {worker.smu_name: set(worker.groups) for worker in workers}
    for worker in workers:
        shared = set()
        for key, group_names in usage.items():
            if key.role == "smu_name":
                continue
            if group_names & groups_by_worker[worker.smu_name]:
                shared.add(key.resource_name)
        worker.shared_dependencies = shared

    conflicts = {}
    for worker_a, worker_b in combinations(workers, 2):
        overlap = _overlapping_resources(worker_a.shared_dependencies, worker_b.shared_dependencies)
        if overlap:
            conflicts[(worker_a.smu_name, worker_b.smu_name)] = overlap

    return ResourceGraph(workers=workers, conflicts=conflicts)
