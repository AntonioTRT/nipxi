"""
Workflow Integration -- Phase 3.5. Builds the `run_group_factory`
callable orchestration/concurrent_supervisor.py::ConcurrentSupervisor
requires, by REUSING the exact same non-interactive workflow building
blocks test.py already extracted for its own Group -> ALL orchestration
and Monitor Battery paths:

    ConcurrentSupervisor -> GroupWorker -> GroupRuntime -> run_group_factory
        -> test.py::_run_group_all_positions() / _run_one_monitor_position()
        -> test.py::_run_one_charge_or_discharge_position()
        -> ChargeSequence / DischargeSequence / CycleSequence / MonitorBatterySequence
        -> test_control/battery_operation_sequence.py::run_guarded()

Nothing here constructs a ChargeSequence/DischargeSequence/CycleSequence/
MonitorBatterySequence directly, duplicates the Battery Presence + NTC
Presence pre-check, or reimplements run_summary/event_log lifecycle --
every one of those already lives in test.py and stays there, called
through unchanged. This module's only job is resolving a GroupRuntime's
already-open hardware/storage/safety/cancellation into the exact keyword
arguments those existing functions already take.

WHY test.py, NOT A COPY: `_run_group_all_positions()`
(charge/discharge/cycle) and `_run_one_monitor_position()` (monitor)
are the SAME functions the interactive console menu calls today (see
test.py::_run_charge_or_discharge_all_positions() and
test.py::_run_monitor_battery()) -- reusing them here means any future
fix to traceability logging, the presence pre-check, or the Group -> ALL
Fault Classification Policy is inherited automatically, in both the
interactive and concurrent paths, from one place.
"""

from __future__ import annotations

import logging

import config.devices as dev_cfg
from orchestration.group_runtime import GroupRuntime
from utils.validators import validate_group_test_config

_log = logging.getLogger("nipxi.orchestration.workflow_integration")

#: operation key -> (display label, sequence_cls import path, event-log
#: `source` string) -- the exact three values test.py's own
#: _run_charge_battery()/_run_discharge_battery()/_run_cycle_battery()
#: already pass into _run_charge_or_discharge()/_run_group_all_positions().
_CHARGE_DISCHARGE_CYCLE_OPERATIONS = {
    "charge": ("Charge Battery", "test_control.charge_sequence", "ChargeSequence", "charge_battery"),
    "discharge": ("Discharge Battery", "test_control.discharge_sequence", "DischargeSequence", "discharge_battery"),
    "cycle": ("Cycle Battery", "test_control.cycle_sequence", "CycleSequence", "cycle_battery"),
}

#: Every group-run operation this factory can drive -- Task 5/6's
#: "ChargeSequence/DischargeSequence/CycleSequence/MonitorBatterySequence"
#: list, by key.
SUPPORTED_OPERATIONS = frozenset({*_CHARGE_DISCHARGE_CYCLE_OPERATIONS, "monitor"})


def _enabled_positions(group_name: str) -> list:
    return sorted(
        p for p, cfg in dev_cfg.BATTERY_GROUPS[group_name]["positions"].items() if cfg.get("enabled")
    )


def _resolve_group_test_config(group_name: str):
    """
    Same validate_group_test_config() every interactive entry point
    calls before constructing hardware -- reused unchanged. Raises
    GroupConfigurationError/ConfigurationError/HardwareConfigurationError
    on a bad config; this factory lets that propagate (SequentialSupervisor
    already treats a raised exception from run_group identically to a
    falsy return -- see orchestration/group_worker.py's module docstring).
    """
    cfg = validate_group_test_config(group_name)
    battery_type = cfg["battery_type"]
    test_setpoints = cfg["test_setpoints"]
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]
    return battery_type, battery_cfg, test_setpoints


def _run_charge_discharge_or_cycle(operation: str, group_name: str, runtime: GroupRuntime) -> bool:
    import test as test_module  # local import -- see module docstring

    label, module_path, class_name, source = _CHARGE_DISCHARGE_CYCLE_OPERATIONS[operation]
    sequence_cls = getattr(__import__(module_path, fromlist=[class_name]), class_name)

    positions = _enabled_positions(group_name)
    if not positions:
        _log.info("group %s: no enabled positions -- nothing to run.", group_name)
        return True

    hw = dev_cfg.hardware_for_group(group_name)
    battery_type, battery_cfg, test_setpoints = _resolve_group_test_config(group_name)

    outcome = test_module._run_group_all_positions(
        operation=label, sequence_cls=sequence_cls, source=source,
        group=group_name, hw=hw, battery_type=battery_type, battery_cfg=battery_cfg,
        test_setpoints=test_setpoints, positions=positions,
        hw_mgr=runtime.hardware, storage=runtime.storage, token=runtime.cancellation,
    )
    # Group -> ALL Fault Classification Policy (test.py's own docstring):
    # FAIL/SKIPPED positions do not stop the group's own loop and must not
    # stop the OTHER concurrently-running groups either -- only a station-
    # level fault (shared equipment in an unverified state) is worth
    # propagating to the GlobalFaultBus. A CANCELLED result is this
    # worker's own cooperative stop (e.g. GlobalEmergencyStop reacting to
    # ANOTHER group's fault) -- an expected clean stop, not a new failure.
    return not outcome["station_fault"]


def _run_monitor(group_name: str, runtime: GroupRuntime) -> bool:
    import test as test_module  # local import -- see module docstring

    positions = _enabled_positions(group_name)
    if not positions:
        _log.info("group %s: no enabled positions -- nothing to run.", group_name)
        return True

    hw = dev_cfg.hardware_for_group(group_name)
    battery_type, battery_cfg, _test_setpoints = _resolve_group_test_config(group_name)

    station_fault = False
    for position in positions:
        if runtime.cancellation.requested:
            break
        ch_cfg = dev_cfg.BATTERY_GROUPS[group_name]["positions"][position]
        relay_address = ch_cfg["relay_address"]
        channel = position

        # Independent run_summary row per position -- same "Storage
        # design" decision test_module._run_group_all_positions() makes
        # for charge/discharge/cycle (see that function's docstring).
        runtime.storage.begin_new_run_id()
        result = test_module._run_one_monitor_position(
            group=group_name, hw=hw, battery_type=battery_type, battery_cfg=battery_cfg,
            position=position, channel=channel, relay_address=relay_address, ch_cfg=ch_cfg,
            hw_mgr=runtime.hardware, storage=runtime.storage,
            safety=runtime.safety, token=runtime.cancellation,
        )
        if result == "STATION_FAULT":
            station_fault = True
            break
        if result == "CANCELLED":
            break

    return not station_fault


def make_run_group_factory(operation: str):
    """
    Returns a `run_group_factory(group_name, group_runtime) -> bool`
    callable for `ConcurrentSupervisor(..., run_group_factory=...)`.
    `operation` is one of SUPPORTED_OPERATIONS ("charge"/"discharge"/
    "cycle"/"monitor").

    The returned callable's contract matches orchestration/
    group_worker.py::GroupWorker's expectations exactly: return a truthy
    value for "this group's run did not hit a station-level fault"
    (including a clean, cooperative stop via the group's own
    cancellation token), a falsy value or a raised exception for
    anything that should be treated as this group's run failing.
    """
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported operation {operation!r} -- must be one of {sorted(SUPPORTED_OPERATIONS)}")

    if operation == "monitor":
        def run_group(group_name: str, runtime: GroupRuntime) -> bool:
            return _run_monitor(group_name, runtime)
    else:
        def run_group(group_name: str, runtime: GroupRuntime) -> bool:
            return _run_charge_discharge_or_cycle(operation, group_name, runtime)

    return run_group
