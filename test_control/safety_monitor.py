"""
Safety monitor.
Runs continuously during a test cycle. Triggers emergency stop if any
safety limit is violated.

Rules (per BLOSS Hub spec and VI flowchart):
    - Voltage must stay in [BAT_VOLTAGE_MIN, BAT_VOLTAGE_MAX]
    - Current must not exceed BAT_CURRENT_MAX
    - Temperature must stay below BAT_TEMP_MAX_C
    - Relay must NOT switch while current > ZERO_CURRENT_THRESHOLD_A
"""

import logging
from dataclasses import dataclass
from config.settings import Settings


@dataclass
class SafetyStatus:
    safe: bool
    reason: str = ""


class SafetyMonitor:
    def __init__(self, settings: Settings):
        self.s = settings
        self.log = logging.getLogger("nipxi.safety")

    def check(self, voltage_v: float, current_a: float, temp_c) -> SafetyStatus:
        # temp_c may be None when NTC read is not yet wired in
        """Check a single measurement point against all limits."""

        if voltage_v > self.s.BAT_VOLTAGE_MAX:
            return SafetyStatus(False, f"Overvoltage: {voltage_v:.3f} V > {self.s.BAT_VOLTAGE_MAX} V")

        if voltage_v < self.s.BAT_VOLTAGE_MIN:
            return SafetyStatus(False, f"Undervoltage: {voltage_v:.3f} V < {self.s.BAT_VOLTAGE_MIN} V")

        if abs(current_a) > self.s.BAT_CURRENT_MAX:
            return SafetyStatus(False, f"Overcurrent: {abs(current_a):.3f} A > {self.s.BAT_CURRENT_MAX} A")

        if temp_c is not None and temp_c > self.s.BAT_TEMP_MAX_C:
            return SafetyStatus(False, f"Overtemperature: {temp_c:.1f} C > {self.s.BAT_TEMP_MAX_C} C")

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
        self.log.error("EMERGENCY STOP: %s", reason)
        if not smu.emergency_output_off(reason):
            self.log.critical(
                "PMU output could not be verified OFF during e-stop. PMU may still be "
                "actively sourcing/sinking current -- physically disconnect power if "
                "this cannot be resolved immediately."
            )
        try:
            relay_matrix.open_all()
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
        self.log.warning("SAFE CANCELLATION: %s -- entering safe shutdown", reason)
        if not smu.emergency_output_off(reason):
            self.log.critical(
                "PMU output could not be verified OFF during cancellation shutdown. "
                "PMU may still be actively sourcing/sinking current -- physically "
                "disconnect power if this cannot be resolved immediately."
            )
        try:
            relay_matrix.open_all()
        except Exception as e:
            self.log.critical(
                "Relay open-all FAILED during cancellation shutdown: %s. Hardware may "
                "still be energized -- physically disconnect power if this cannot be "
                "resolved immediately.", e,
            )
        self.log.info("Safe cancellation shutdown complete.")
