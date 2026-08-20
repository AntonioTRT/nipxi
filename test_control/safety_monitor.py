"""
Safety monitor.
Runs continuously during a test cycle. Triggers emergency stop if any
safety limit is violated.

Rules (per BLOSS Hub spec and VI flowchart):
    - Voltage must stay in [BAT_VOLTAGE_MIN, BAT_VOLTAGE_MAX]
    - Current must not exceed BAT_CURRENT_MAX
    - Temperature must stay below BAT_TEMP_MAX_C
    - Relay must NOT switch while current > ZERO_CURRENT_THRESHOLD_A

Battery-aware limits (config/devices.py BATTERY_CONFIGS): when a
battery_cfg dict is supplied (constructor or set_battery_limits()),
voltage/current/temperature limits are resolved from it instead of the
global Settings.BAT_* constants -- see docs/architecture.md,
"BATTERY_CONFIGS -> SafetyMonitor Integration". battery_cfg=None (the
default) preserves the exact prior global-Settings-only behavior.
"""

import logging
from dataclasses import dataclass
from config.settings import Settings


@dataclass
class SafetyStatus:
    safe: bool
    reason: str = ""


class SafetyMonitor:
    def __init__(self, settings: Settings, battery_cfg: dict = None):
        self.s = settings
        self.battery_cfg = battery_cfg
        self.log = logging.getLogger("nipxi.safety")

    def set_battery_limits(self, battery_cfg: dict = None):
        """
        Set (or clear, with None) the active battery configuration
        (a config/devices.py BATTERY_CONFIGS[...] entry) used to resolve
        limits in check(). Passing None reverts to global Settings.BAT_*
        values -- the same behavior as never having called this at all.
        """
        self.battery_cfg = battery_cfg

    def _voltage_max(self) -> float:
        if self.battery_cfg is not None and "voltage_max_v" in self.battery_cfg:
            return self.battery_cfg["voltage_max_v"]
        return self.s.BAT_VOLTAGE_MAX

    def _voltage_min(self) -> float:
        if self.battery_cfg is not None and "voltage_min_v" in self.battery_cfg:
            return self.battery_cfg["voltage_min_v"]
        return self.s.BAT_VOLTAGE_MIN

    def _temp_max(self) -> float:
        if self.battery_cfg is not None and "max_temp_c" in self.battery_cfg:
            return self.battery_cfg["max_temp_c"]
        return self.s.BAT_TEMP_MAX_C

    def _current_max(self, mode: str = None) -> float:
        """
        Resolve the max allowable |current_a| for this battery_cfg/mode.
        mode="charge"/"discharge" selects the matching battery_cfg field.
        mode=None (battery_cfg present) falls back to the more restrictive
        (min) of charge/discharge limits, so an omitted mode is never
        accidentally more permissive than either. No battery_cfg -> the
        global Settings.BAT_CURRENT_MAX, unchanged from prior behavior.
        """
        if self.battery_cfg is None:
            return self.s.BAT_CURRENT_MAX
        if mode == "charge" and "max_charge_current_a" in self.battery_cfg:
            return self.battery_cfg["max_charge_current_a"]
        if mode == "discharge" and "max_discharge_current_a" in self.battery_cfg:
            return self.battery_cfg["max_discharge_current_a"]
        charge_i = self.battery_cfg.get("max_charge_current_a")
        discharge_i = self.battery_cfg.get("max_discharge_current_a")
        candidates = [i for i in (charge_i, discharge_i) if i is not None]
        if candidates:
            return min(candidates)
        return self.s.BAT_CURRENT_MAX

    def check(self, voltage_v: float, current_a: float, temp_c, mode: str = None) -> SafetyStatus:
        # temp_c may be None when NTC read is not yet wired in
        """
        Check a single measurement point against all limits.
        mode="charge"/"discharge" (optional) selects the battery_cfg
        current limit matching the active operation -- see _current_max().
        Has no effect when no battery_cfg is set.
        """
        v_max = self._voltage_max()
        v_min = self._voltage_min()
        i_max = self._current_max(mode)
        t_max = self._temp_max()

        if voltage_v > v_max:
            return SafetyStatus(False, f"Overvoltage: {voltage_v:.3f} V > {v_max} V")

        if voltage_v < v_min:
            return SafetyStatus(False, f"Undervoltage: {voltage_v:.3f} V < {v_min} V")

        if abs(current_a) > i_max:
            return SafetyStatus(False, f"Overcurrent: {abs(current_a):.3f} A > {i_max} A")

        if temp_c is not None and temp_c > t_max:
            return SafetyStatus(False, f"Overtemperature: {temp_c:.1f} C > {t_max} C")

        return SafetyStatus(True)

    def check_temperature(self, temp_c: float) -> SafetyStatus:
        """
        Temperature-only check, reusing the same _temp_max() resolution as
        check(). For workflows that do not source/sink current (Monitor
        Battery) and therefore have no real current_a to pass into check()
        -- calling check() with a placeholder current would also start
        enforcing voltage/current limits Monitor Battery has never enforced
        before, a behavior change beyond what a temperature-only integration
        should introduce. temp_c=None is always safe (nothing to check yet).
        """
        if temp_c is None:
            return SafetyStatus(True)
        t_max = self._temp_max()
        if temp_c > t_max:
            return SafetyStatus(False, f"Overtemperature: {temp_c:.1f} C > {t_max} C")
        return SafetyStatus(True)

    def is_safe_to_switch_relay(self, current_a: float) -> bool:
        """True if current is low enough to switch the relay without arcing."""
        return abs(current_a) <= self.s.ZERO_CURRENT_THRESHOLD_A

    def emergency_stop(self, smu, relay_matrix, reason: str):
        """
        Execute emergency stop sequence:
          1. PMU (SMU) output OFF, verified -- via emergency_output_off(),
             which never raises. See hardware/smu.py module docstring and
             docs/architecture.md "PMU Safety Philosophy": unknown PMU
             state = unsafe state, so a failed verification is logged as
             CRITICAL rather than assumed safe.
          2. Open all relays (force OFF + verify -- see
             docs/architecture.md "Emergency Shutdown Strategy". The relay
             driver has already made its own internal emergency-shutdown
             attempt by the time open_all() can still raise here -- see
             NumatoRelayMatrix.verify_all()/_emergency_all_off() -- so a
             failure at this point is a second failed attempt and is logged
             as CRITICAL.)
        Mirrors the 'Safe shutdown' node in the VI flowchart.
        """
        self.log.warning("[SHUTDOWN-TRACE] emergency_stop() entered (reason=%s)", reason)
        self.log.error("EMERGENCY STOP: %s", reason)
        if not smu.emergency_output_off(reason):
            self.log.critical(
                "PMU output could not be verified OFF during e-stop. PMU may still be "
                "actively sourcing/sinking current -- physically disconnect power if "
                "this cannot be resolved immediately."
            )
        self.log.warning("[SHUTDOWN-TRACE] Relay open command sending (open_all())")
        try:
            relay_matrix.open_all()
            self.log.warning("[SHUTDOWN-TRACE] Relay open command sent -- open_all() returned normally")
        except Exception as e:
            self.log.critical(
                "Relay open-all FAILED during e-stop: %s. Hardware may still be "
                "energized -- physically disconnect power if this cannot be "
                "resolved immediately.", e,
            )
        self.log.warning("Emergency stop complete.")

    def safe_cancel_shutdown(self, smu, relay_matrix, reason: str):
        """
        Safe shutdown sequence for an operator-requested cancellation
        (see utils/cancellation.py / OperationCancelledError). Hardware
        sequence is identical to emergency_stop() -- PMU output off and
        verified, then all relays forced open and verified -- but logged
        as a deliberate, expected operator action (INFO/WARNING) rather
        than "EMERGENCY STOP", so log output does not read as a fault when
        the operator asked for exactly this. Never raises.
        """
        self.log.warning("[SHUTDOWN-TRACE] safe_cancel_shutdown() entered (reason=%s)", reason)
        self.log.warning("SAFE CANCELLATION: %s -- entering safe shutdown", reason)
        if not smu.emergency_output_off(reason):
            self.log.critical(
                "PMU output could not be verified OFF during cancellation shutdown. "
                "PMU may still be actively sourcing/sinking current -- physically "
                "disconnect power if this cannot be resolved immediately."
            )
        self.log.warning("[SHUTDOWN-TRACE] Relay open command sending (open_all())")
        try:
            relay_matrix.open_all()
            self.log.warning("[SHUTDOWN-TRACE] Relay open command sent -- open_all() returned normally")
        except Exception as e:
            self.log.critical(
                "Relay open-all FAILED during cancellation shutdown: %s. Hardware may "
                "still be energized -- physically disconnect power if this cannot be "
                "resolved immediately.", e,
            )
        self.log.info("Safe cancellation shutdown complete.")
