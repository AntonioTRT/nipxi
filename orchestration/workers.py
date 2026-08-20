"""
Worker Discovery -- pure, read-only partitioning of enabled battery groups
by their exclusively-owned SMU (see docs/architecture.md "Future
Architecture: Worker Discovery" and "Worker = SMU-anchored but
broker-dependent"). NOT part of the current execution path.

The number of workers is DERIVED from configuration, never hardcoded --
running discover_workers() against today's real config/devices.py::
BATTERY_GROUPS produces exactly one worker (Group B1, the only enabled
group with an assigned SMU); running it against a synthetic config
shaped like a bigger rack produces more, with zero code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config.devices as dev_cfg


@dataclass
class WorkerPlan:
    """
    One SMU-anchored worker candidate.

    `groups` is every enabled group declared for this SMU. Per the
    current config model an SMU is a strictly exclusive, one-battery-at-
    a-time resource -- a worker with more than one group runs them
    SEQUENTIALLY (one at a time), never simultaneously. "Worker 1: B1, B2"
    means "this worker serializes between B1 and B2," not "B1 and B2
    charge in parallel."

    `shared_dependencies` starts empty here -- worker discovery alone has
    no notion of shared resources. It is populated by
    orchestration/resource_graph.py::build_resource_graph(), which is the
    module that actually knows about relay-matrix/DMM/DAQ sharing.
    """
    smu_name: str
    groups: list = field(default_factory=list)
    shared_dependencies: set = field(default_factory=set)


def discover_workers(battery_groups: dict = None) -> list:
    """
    Partition every `enabled` group in `battery_groups` (defaults to
    config/devices.py::BATTERY_GROUPS) by its declared `smu` field. A
    group with no `smu` assigned (still None -- true for every group
    except B1 today) is not a worker candidate; it simply has no worker
    to run it, exactly matching today's reality.

    Pure function, no hardware I/O. Deterministic ordering: workers are
    returned in the order their SMU is first encountered while iterating
    `battery_groups`, and each worker's `groups` list is in that same
    iteration order -- callers/tests never need to depend on dict-ordering
    quirks beyond "whatever order battery_groups.items() naturally gives,"
    which is itself stable for a plain dict literal like BATTERY_GROUPS.
    """
    groups = battery_groups if battery_groups is not None else dev_cfg.BATTERY_GROUPS
    by_smu: dict = {}
    for group_name, grp in groups.items():
        if not grp.get("enabled"):
            continue
        smu_name = grp.get("smu")
        if smu_name is None:
            continue
        by_smu.setdefault(smu_name, []).append(group_name)
    return [WorkerPlan(smu_name=smu_name, groups=group_names) for smu_name, group_names in by_smu.items()]
