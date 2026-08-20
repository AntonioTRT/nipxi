"""
Topology Discovery -- pure, read-only resource-usage analysis over
config/devices.py (see docs/architecture.md "Future Architecture:
Topology Discovery"). NOT part of the current execution path: nothing in
test.py, main.py, or test_control/ imports this module.

Adds no new config fields and no new hardware assumptions -- it only
reads the existing per-group hardware role names (relay_matrix/smu/dmm/
daq/ntc_daq) already declared in config/devices.py::BATTERY_GROUPS, and
aggregates "which enabled groups use which physical resource" into a
lookup.

Deliberately does NOT call config/devices.py::hardware_for_group() --
that function always resolves against the real, global BATTERY_GROUPS/
SMU_ASSIGNMENTS/DMM_CONFIGS/DAQ_CONFIGS regardless of any dict passed to
IT, which would make this module untestable against a synthetic rack
config (Rack B/C/D-shaped dicts standing in for hardware that does not
exist yet). Instead, this module reads the plain role-name fields
directly off each group dict -- the same fields hardware_for_group()
itself reads -- and replicates its one non-trivial rule (ntc_daq falling
back to daq when unset) inline. If that rule ever changes in
hardware_for_group(), this must be updated to match.
"""

from __future__ import annotations

from typing import NamedTuple

import config.devices as dev_cfg

#: Resource roles considered by topology discovery -- exactly the roles
#: config/devices.py::hardware_for_group() already resolves. Deliberately
#: does not introduce any role that function does not already have.
RESOURCE_ROLES = ("relay_matrix_name", "smu_name", "dmm_name", "daq_name", "ntc_daq_name")


class ResourceKey(NamedTuple):
    """
    One physical resource, identified by (role, resource_name) -- e.g.
    ResourceKey("relay_matrix_name", "MATRIX_NUMATO_202"). The role is
    part of the key because config/devices.py's nickname namespaces are
    not guaranteed globally unique across categories -- keeping role and
    name together means two different physical devices can never be
    conflated just because a future rack happens to reuse a nickname
    across roles.
    """
    role: str
    resource_name: str


def discover_topology(battery_groups: dict = None) -> dict:
    """
    Return {ResourceKey(role, resource_name): {group_names using it}} for
    every group in `battery_groups` (defaults to config/devices.py::
    BATTERY_GROUPS, the single source of truth) with `enabled: True`. A
    group with no resource assigned for a given role (the field is None)
    simply contributes no entry for that role -- never a placeholder or
    guessed value.

    Pure function: no hardware I/O, no side effects. Running this against
    today's real config produces exactly today's single-group reality
    (only B1 is enabled); running it against a synthetic dict shaped like
    a bigger future rack produces the richer picture with zero code
    changes -- that is the point of deriving topology from configuration
    rather than hardcoding it.
    """
    groups = battery_groups if battery_groups is not None else dev_cfg.BATTERY_GROUPS
    usage: dict[ResourceKey, set] = {}
    for group_name, grp in groups.items():
        if not grp.get("enabled"):
            continue
        # Mirrors config/devices.py::hardware_for_group()'s own role
        # resolution exactly (relay_matrix/smu/dmm/daq read directly;
        # ntc_daq falling back to daq when unset) -- see this module's
        # docstring for why it is duplicated here rather than calling
        # that function directly.
        roles = {
            "relay_matrix_name": grp.get("relay_matrix"),
            "smu_name": grp.get("smu"),
            "dmm_name": grp.get("dmm"),
            "daq_name": grp.get("daq"),
            "ntc_daq_name": grp.get("ntc_daq") or grp.get("daq"),
        }
        for role, resource_name in roles.items():
            if resource_name is not None:
                usage.setdefault(ResourceKey(role, resource_name), set()).add(group_name)
    return usage
