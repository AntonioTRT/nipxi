"""
Startup Safety Sweep -- see docs/architecture.md "Startup Safety Sweep".

Connects, force-safes, verifies, and disconnects EVERY configured SMU
(config/devices.py::SMU_ASSIGNMENTS) and EVERY configured Numato relay
matrix (config/devices.py::NUMATO_RELAY_MATRIX_CONFIGS) -- broader than
HardwareManager.connect_all(), which only ever connects the ONE SMU/relay
pair resolved for whichever group is about to run. Called once, from
test.py::main(), before the Main Menu is ever shown.

Uses the SAME safety-fault reporting/display/acknowledgement primitives
(utils/safety_fault.py) that HardwareManager.disconnect_all()'s
Post-Workflow Safety Sweep escalation uses -- one shared implementation of
"what happens when a device that responded could not be verified safe",
reused by both entry points rather than duplicated.

Respects config/system_mode.py exactly like HardwareManager.connect_all()
does: a device that is simply MISSING/unreachable is tolerated per
SYSTEM_MODE (logged, sweep continues) -- but a device that DID connect and
failed its safety verification is ALWAYS fatal, in every mode ("unknown
state = unsafe state" is never relaxed by SYSTEM_MODE). This is what lets
a hardware-free DEVELOPMENT laptop run this sweep and see it no-op (every
device logged as missing, tolerated) without ever blocking startup.
"""

from __future__ import annotations

import logging

from config import devices as dev_cfg
from config.settings import Settings
from config.system_mode import get_mode_policy
from hardware.relay_factory import RelayFactory
from hardware.smu import SMU
from utils.safety_fault import acknowledge_safety_fault, display_safety_fault_screen, report_safety_fault

_log = logging.getLogger("nipxi.safety_sweep")


class SafetyFaultBlocked(Exception):
    """
    Raised by run_startup_safety_sweep() when a device it reached could
    not be verified safe, AFTER the operator has acknowledged the SAFETY
    FAULT screen -- see module docstring. test.py::main() treats this as
    fatal and never shows the Main Menu.
    """


def run_startup_safety_sweep(settings=Settings, *, smu_assignments=None, relay_matrix_configs=None,
                              smu_factory=SMU, relay_factory_create=None) -> None:
    """
    Raises SafetyFaultBlocked if any configured SMU/relay matrix that
    connected could not be verified safe. Tolerates (logs, per
    SYSTEM_MODE) a device that simply fails to connect at all.

    `smu_assignments`/`relay_matrix_configs`/`smu_factory`/
    `relay_factory_create` default to the real config/devices.py globals
    and hardware/smu.py::SMU / hardware/relay_factory.py::RelayFactory.create
    but accept overrides -- matching this codebase's established
    testability convention (see hardware/sense_router.py::
    ConfigDrivenSenseRouter.__init__()'s identical pattern) -- so this
    sweep can be exercised against synthetic devices in tests without
    touching real config or real hardware.
    """
    if smu_assignments is None:
        smu_assignments = dev_cfg.SMU_ASSIGNMENTS
    if relay_matrix_configs is None:
        relay_matrix_configs = dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS
    if relay_factory_create is None:
        relay_factory_create = RelayFactory.create

    mode_policy = get_mode_policy(settings)
    level = mode_policy.hardware_failure_log_level

    for name, cfg in smu_assignments.items():
        smu = smu_factory(cfg)
        try:
            smu.connect()
        except Exception as e:
            _log.log(
                level, "Startup safety sweep: SMU %s not available (%s mode, sweep "
                "continues): %s", name, mode_policy.mode.value, e,
            )
            continue
        try:
            if not smu.emergency_output_off("startup safety sweep"):
                fault_reason = f"startup_sweep: SMU {name}: output could not be verified OFF."
                fault_id = report_safety_fault(
                    reason=fault_reason, source_method="emergency_output_off",
                    context="startup_sweep", device_name=name, device_type="SMU", settings=settings,
                )
                display_safety_fault_screen(smu_state="UNKNOWN", relay_state="UNKNOWN", reason=fault_reason)
                acknowledge_safety_fault(fault_id=fault_id, settings=settings)
                raise SafetyFaultBlocked(fault_reason)
        finally:
            try:
                smu.disconnect()
            except Exception:
                pass

    for name, cfg in relay_matrix_configs.items():
        relay = relay_factory_create(cfg)
        try:
            relay.connect()
        except Exception as e:
            _log.log(
                level, "Startup safety sweep: Relay %s not available (%s mode, sweep "
                "continues): %s", name, mode_policy.mode.value, e,
            )
            continue
        try:
            try:
                relay.open_all()
            except Exception as e:
                fault_reason = f"startup_sweep: Relay {name}: open_all()/verify failed -- {e}"
                fault_id = report_safety_fault(
                    reason=fault_reason, source_method="open_all",
                    context="startup_sweep", device_name=name, device_type="RELAY", settings=settings,
                )
                display_safety_fault_screen(smu_state="UNKNOWN", relay_state="UNVERIFIED", reason=fault_reason)
                acknowledge_safety_fault(fault_id=fault_id, settings=settings)
                raise SafetyFaultBlocked(fault_reason)
        finally:
            try:
                relay.disconnect()
            except Exception:
                pass

    _log.info("Startup safety sweep complete.")
