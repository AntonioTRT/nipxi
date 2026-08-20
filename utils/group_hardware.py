"""
Group -> hardware resolution and position validation -- pure logic
extracted from test.py so it has exactly one implementation shared by
test.py's interactive workflows today and a future worker_runtime.py
(non-interactive) tomorrow (see docs/architecture.md "Preparation Phase:
Six Resolved Decisions Before worker_runtime.py").

Every function here is pure with respect to config/devices.py -- no
`print()`, no `input()`, no hardware I/O. Anything operator-facing
(printing a `[FAIL]` message, prompting for input) stays in test.py's own
thin wrappers, which pass an `on_fail` callback so today's exact
operator-visible text is unchanged. A future non-interactive caller
passes a different `on_fail` (e.g. `log.error`) or none at all and
inspects the return value itself -- neither caller duplicates the
resolution logic.
"""

from __future__ import annotations

from config import devices as dev_cfg


def missing_hardware_roles(hw: dict, required_roles=("relay_matrix", "smu", "dmm", "daq")) -> list:
    """
    Return the subset of `required_roles` whose config/devices.py::
    hardware_for_group() cfg resolved to None -- i.e. no device assigned
    to that role for this group. Never silently substitute another
    device for a missing role; the caller must abort before any hardware
    activation.
    """
    return [role for role in required_roles if hw[f"{role}_cfg"] is None]


def resolve_group_hardware(group: str, required_roles=("relay_matrix", "smu", "dmm", "daq"),
                            on_fail=None):
    """
    Resolve hardware assignment and battery type for `group`, entirely
    from config/devices.py::BATTERY_GROUPS via hardware_for_group()/
    group_test_config() -- never operator input, never a second hardware
    map. Shared by every hardware-activating workflow (Monitor Battery,
    Monitor Battery Scan, Charge/Discharge Battery, future Cycle Battery,
    future worker_runtime.py).

    Returns `(hw, battery_type, battery_cfg)` on success, or `None` on
    failure (missing hardware role for `required_roles`, or unset/unknown
    battery_type). The caller must abort on `None` -- no hardware
    activated.

    `on_fail`, if given, is called once with a ready-to-display failure
    message (the exact text test.py has always printed on this path) --
    it never affects control flow, only whether/how the failure is
    reported. Pass `on_fail=None` (the default) for a fully silent
    resolution -- e.g. a non-interactive caller that wants to construct
    its own error instead.
    """
    hw = dev_cfg.hardware_for_group(group)
    missing = missing_hardware_roles(hw, required_roles=required_roles)
    if missing:
        if on_fail is not None:
            on_fail(f"\n[FAIL] Group {group} has no {', '.join(missing)} assigned -- "
                     f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return None

    battery_type = dev_cfg.group_test_config(group)["battery_type"]
    if battery_type is None:
        if on_fail is not None:
            on_fail(f"\n[FAIL] Group {group} has no battery_type configured -- "
                     f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return None
    if battery_type not in dev_cfg.BATTERY_CONFIGS:
        if on_fail is not None:
            on_fail(f"\n[FAIL] Group {group} references unknown battery_type {battery_type!r} -- "
                     f"see config/devices.py::BATTERY_GROUPS[{group!r}] and BATTERY_CONFIGS. "
                     f"Aborting, no hardware activated.")
        return None
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]
    return hw, battery_type, battery_cfg


def validate_position_in_group(group: str, position: int) -> bool:
    """
    True if `position` is a valid, in-range position number for `group`
    (1 <= position <= config/devices.py::group_size(group)). Pure
    predicate -- no I/O, no printing, no exception on an invalid
    position; callers decide how to report/handle it (test.py prints
    "out of range"; a future non-interactive caller can raise a
    validation error before any hardware is touched, matching
    validate_group_test_config()'s existing fail-before-hardware
    posture).
    """
    size = dev_cfg.group_size(group)
    return 1 <= position <= size
