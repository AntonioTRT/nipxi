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
        overlap = worker_a.shared_dependencies & worker_b.shared_dependencies
        if overlap:
            conflicts[(worker_a.smu_name, worker_b.smu_name)] = overlap

    return ResourceGraph(workers=workers, conflicts=conflicts)
