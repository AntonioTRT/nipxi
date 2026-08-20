"""
Human-readable reporting over the orchestration read-only layer (see
docs/architecture.md "Future Architecture: Reporting Layer"). Every
function here is a pure formatter: given the same config/devices.py
input, discover_topology()/discover_workers()/build_resource_graph()/
build_execution_plan() already produce deterministic data -- this module
only renders it as text. No hardware access, no side effects other than
returning a string; callers decide whether/where to print it.

This is the one orchestration module a real (if minimal) integration
point is built on -- see main.py's `--show-topology` flag -- because
printing a report changes nothing about test execution or hardware
state.
"""

from __future__ import annotations

from orchestration.execution_plan import build_execution_plan
from orchestration.resource_graph import build_resource_graph
from orchestration.topology import discover_topology


def topology_report(battery_groups: dict = None) -> str:
    """One line per physical resource, with the enabled groups using it."""
    usage = discover_topology(battery_groups)
    lines = ["Topology Summary", "-" * 40]
    if not usage:
        lines.append("(no enabled group has any resource assigned)")
    else:
        for key in sorted(usage, key=lambda k: (k.role, k.resource_name)):
            groups = ", ".join(sorted(usage[key]))
            lines.append(f"  [{key.role}] {key.resource_name} <- {groups}")
    return "\n".join(lines)


def worker_report(battery_groups: dict = None) -> str:
    """One line per discovered worker, with its owning SMU and groups."""
    graph = build_resource_graph(battery_groups)
    lines = ["Worker Summary", "-" * 40]
    if not graph.workers:
        lines.append("(no worker discovered -- no enabled group has an smu assigned)")
    else:
        for worker in sorted(graph.workers, key=lambda w: w.smu_name):
            groups = ", ".join(worker.groups) if worker.groups else "(none)"
            lines.append(f"  Worker[{worker.smu_name}]: groups = {groups}")
    return "\n".join(lines)


def dependency_report(battery_groups: dict = None) -> str:
    """One line per worker, listing every shared (non-exclusive) resource it depends on."""
    graph = build_resource_graph(battery_groups)
    lines = ["Dependency Summary", "-" * 40]
    if not graph.workers:
        lines.append("(no worker discovered)")
    else:
        for worker in sorted(graph.workers, key=lambda w: w.smu_name):
            if worker.shared_dependencies:
                shared = ", ".join(sorted(worker.shared_dependencies))
            else:
                shared = "(none -- fully independent)"
            lines.append(f"  Worker[{worker.smu_name}] shares: {shared}")
    return "\n".join(lines)


def conflict_report(battery_groups: dict = None) -> str:
    """One line per conflicting worker pair, or an explicit all-clear."""
    graph = build_resource_graph(battery_groups)
    lines = ["Conflict Summary", "-" * 40]
    if not graph.conflicts:
        lines.append("  No conflicts detected -- every discovered worker is fully independent.")
    else:
        for pair in sorted(graph.conflicts, key=lambda p: tuple(sorted(p))):
            shared = ", ".join(sorted(graph.conflicts[pair]))
            a, b = sorted(pair)
            lines.append(f"  {a} <-> {b} share: {shared}")
    return "\n".join(lines)


def execution_plan_report(battery_groups: dict = None, enabled_workers=None) -> str:
    """
    Ordered steps and parallel-eligible batches. With today's real config
    (a single worker) this always renders one step, one batch, no
    excluded workers -- the interesting output only appears against a
    synthetic multi-worker config (see tests).
    """
    plan = build_execution_plan(battery_groups, enabled_workers)
    lines = ["Execution Plan Summary", "-" * 40]
    if not plan.steps:
        lines.append("  (no worker included in this plan)")
    else:
        for i, step in enumerate(plan.steps):
            groups = ", ".join(step.groups) if step.groups else "(none)"
            if step.depends_on:
                deps = " after " + ", ".join(step.depends_on)
            else:
                deps = " (no dependencies)"
            lines.append(f"  Step {i+1}: {step.smu_name} runs [{groups}]{deps}")
        lines.append("")
        lines.append("  Parallel-eligible batches (informational -- not executed "
                      "concurrently today):")
        for i, batch in enumerate(plan.parallel_batches):
            lines.append(f"    Batch {i+1}: {', '.join(batch)}")
    if plan.excluded_workers:
        lines.append("")
        lines.append(f"  Excluded from this plan: {', '.join(plan.excluded_workers)}")
    return "\n".join(lines)


def full_report(battery_groups: dict = None, enabled_workers=None) -> str:
    """All five sections, in a fixed order, separated by blank lines --
    this is what main.py's `--show-topology` flag prints."""
    sections = [
        topology_report(battery_groups),
        worker_report(battery_groups),
        dependency_report(battery_groups),
        conflict_report(battery_groups),
        execution_plan_report(battery_groups, enabled_workers),
    ]
    return "\n\n".join(sections)
