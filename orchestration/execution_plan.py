"""
Execution Planning -- pure, read-only layer that turns Worker Discovery
(orchestration/workers.py) and the Resource Dependency Graph
(orchestration/resource_graph.py) into an explicit, ordered plan of what
would run and in what order (see docs/architecture.md "Future
Architecture: Execution Planning"). NOT part of the current execution
path -- nothing here touches hardware, spawns a thread/process, or calls
into test_control/.

This module does NOT implement concurrency. Today, and until a real
cross-process Arbiter exists, every ExecutionPlan is executed by running
its steps one at a time, in order -- exactly what main.py does today for
its one worker. What this module adds is the ANALYSIS that makes that
safe to generalize: which workers could, in principle, run at the same
time (no shared resource) versus which must be strictly ordered (share a
relay matrix, DMM, or DAQ) -- see `parallel_batches` below. A future
concurrent runtime can read that same information to decide what is safe
to actually parallelize; today's sequential main.py can ignore it and
just walk `steps` in order and get the correct (and only currently
possible) behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestration.resource_graph import build_resource_graph


@dataclass
class ExecutionStep:
    """
    One worker's turn in the plan. `groups` is that worker's own group
    list (run sequentially within the worker, per WorkerPlan semantics --
    this module does not change that). `depends_on` is every OTHER
    worker's smu_name that this step conflicts with and that was placed
    earlier in the plan -- i.e. resources this step must wait for, not a
    reference to a specific group or measurement.
    """
    smu_name: str
    groups: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """
    `steps` -- every included worker, in a valid dependency-respecting
    order (a worker never appears before a worker it depends on).

    `parallel_batches` -- the same workers grouped into batches such that
    no two workers in the same batch conflict with each other. Batch 0
    can start immediately; batch N can start once every worker in batches
    0..N-1 that it conflicts with has finished. With today's single
    worker this is always `[[smu_name]]`. Informational only until a real
    concurrent runtime exists -- it is not consumed by anything today.

    `excluded_workers` -- smu_names discovered by Worker Discovery but
    left out of this plan by the caller's `enabled_workers` selection
    (e.g. an operator disabling a worker for this run without touching
    config/devices.py). Never silently dropped -- always listed here so a
    caller/report can say explicitly what was left out and why.
    """
    steps: list = field(default_factory=list)
    parallel_batches: list = field(default_factory=list)
    excluded_workers: list = field(default_factory=list)


def build_execution_plan(battery_groups: dict = None, enabled_workers=None) -> ExecutionPlan:
    """
    Build an ExecutionPlan from Worker Discovery + the Resource Dependency
    Graph over `battery_groups` (defaults to config/devices.py::
    BATTERY_GROUPS).

    `enabled_workers`, if given, is an iterable of smu_name values to
    include; any discovered worker whose smu_name is not in it is left
    out of `steps`/`parallel_batches` and reported in `excluded_workers`
    instead. `enabled_workers=None` (the default) includes every
    discovered worker -- today that is exactly the single B1/AUX_SMU_1
    worker, matching current behavior with no filtering applied.

    Batch assignment is a simple greedy graph-coloring pass over the
    conflict graph: workers are considered in Worker Discovery's own
    deterministic order (see workers.py), and each is placed in the
    earliest existing batch it does not conflict with, or a new batch if
    none exists. This is deterministic and pure -- no hardware I/O, no
    randomness, no wall-clock dependence.
    """
    graph = build_resource_graph(battery_groups)

    if enabled_workers is not None:
        enabled_set = set(enabled_workers)
        included = [w for w in graph.workers if w.smu_name in enabled_set]
        excluded = [w.smu_name for w in graph.workers if w.smu_name not in enabled_set]
    else:
        included = list(graph.workers)
        excluded = []

    included_names = {w.smu_name for w in included}
    conflict_pairs = {
        frozenset(pair)
        for pair in graph.conflicts
        if pair[0] in included_names and pair[1] in included_names
    }

    def conflicts_with(name: str, others: list) -> bool:
        return any(frozenset((name, other)) in conflict_pairs for other in others)

    batches: list = []
    for worker in included:
        for batch in batches:
            if not conflicts_with(worker.smu_name, batch):
                batch.append(worker.smu_name)
                break
        else:
            batches.append([worker.smu_name])

    workers_by_name = {w.smu_name: w for w in included}
    steps: list = []
    scheduled_before: list = []
    for batch in batches:
        for name in batch:
            depends_on = [
                other for other in scheduled_before
                if frozenset((name, other)) in conflict_pairs
            ]
            steps.append(ExecutionStep(
                smu_name=name,
                groups=list(workers_by_name[name].groups),
                depends_on=depends_on,
            ))
        scheduled_before = scheduled_before + batch

    return ExecutionPlan(steps=steps, parallel_batches=batches, excluded_workers=excluded)
