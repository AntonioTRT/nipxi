"""
Discharge cycle module.
Implements CC discharge for a single battery channel.

Follows the 'discharge step' branch of the VI Charge/Discharge flowchart:
    Set SMU to discharge mode -> stabilize -> acquire -> check safety -> log -> check EOD
"""

import logging
import time
from config.settings import Settings
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import SafetyViolationError


class DischargeCycle:
    def __init__(self, smu, daq, safety: SafetyMonitor, settings: Settings):
        self.smu = smu
        self.daq = daq
        self.safety = safety
        self.s = settings
        self.log = logging.getLogger("nipxi.discharge")

    def run(self, channel: int, data_collector, token=None, battery_cfg: dict = None) -> bool:
        """
        Run one complete CC discharge on `channel`.
        Calls data_collector.record(channel, sample) for each sample.
        Returns True if discharge completed normally, False on timeout.
        Raises SafetyViolationError on limit violation.
        Raises OperationCancelledError if `token` has a cancellation
        requested -- see charge_cycle.py::ChargeCycle.run() for the full
        rationale (same checkpoint placement, same fail-safe reasoning).

        `battery_cfg` (a config/devices.py BATTERY_CONFIGS[...] entry),
        if given, supplies the commanded discharge current
        (max_discharge_current_a) and voltage floor (voltage_min_v)
        instead of the global Settings.DISCHARGE_CURRENT_A/BAT_VOLTAGE_MIN,
        and is forwarded to self.safety so SafetyMonitor.check() enforces
        the same battery-specific limits. battery_cfg=None preserves prior
        (global-Settings-only) behavior exactly.

        Discharge Cutoff Policy (see docs/architecture.md Section 30):
        DISCHARGE_CUTOFF_V (or a future battery_cfg "discharge_target_v")
        is a cycle OBJECTIVE -- where this discharge intends to stop -- not
        a safety limit. BAT_VOLTAGE_MIN / battery_cfg["voltage_min_v"] is
        the battery's absolute safety FLOOR. These are two different
        questions and are never treated as conflicting: the effective
        cutoff used for end-of-discharge detection is clamped to never sit
        below the active floor, so the safety limit always has priority
        even if a target is ever misconfigured below it. Today's
        BATTERY_CONFIGS entries only specify one voltage (voltage_min_v),
        so target and floor resolve to the same value for HUB/SB -- the
        clamp exists for the global-Settings-only fallback path (where
        DISCHARGE_CUTOFF_V=3.0 V historically sat below BAT_VOLTAGE_MIN=
        3.5 V) and for any future battery type whose target and floor
        differ.
        """
        self.log.info("Starting discharge cycle on channel %d", channel)

        current_a = self.s.DISCHARGE_CURRENT_A
        target_v = self.s.DISCHARGE_CUTOFF_V   # cycle objective, not the safety floor
        floor_v = self.s.BAT_VOLTAGE_MIN        # absolute safety floor
        if battery_cfg is not None:
            current_a = battery_cfg.get("max_discharge_current_a", current_a)
            floor_v = battery_cfg.get("voltage_min_v", floor_v)
            target_v = battery_cfg.get("voltage_min_v", target_v)
        self.safety.set_battery_limits(battery_cfg)

        # The safety floor always has priority: never let the discharge
        # target sit below it. This is a defensive clamp, not the primary
        # safety mechanism -- SafetyMonitor.check() (below, every sample)
        # remains the authoritative abort path if voltage ever drops below
        # the floor before this cutoff is reached.
        cutoff_v = max(target_v, floor_v)
        if target_v < floor_v:
            self.log.warning(
                "Channel %d: discharge target %.3f V is below the safety floor "
                "%.3f V -- using the floor as the effective cutoff.",
                channel, target_v, floor_v,
            )

        check_cancellation(token)

        self.smu.set_discharge_mode(
            current_a=current_a,
            voltage_limit_v=cutoff_v,
        )
        self.smu.output_enable()

        # PMU fail-safe: emergency_output_off() runs exactly once regardless
        # of how this block exits -- normal completion, timeout, a safety
        # violation, a cancellation, or any unhandled exception. See
        # hardware/smu.py module docstring and docs/architecture.md "PMU
        # Safety Philosophy".
        #
        # The try/finally now starts here, BEFORE the stabilization wait --
        # see charge_cycle.py::ChargeCycle.run() for the full rationale
        # (same latent gap, same fix, same reasoning).
        try:
            # Interruptible -- see charge_cycle.py::ChargeCycle.run().
            # Normal (non-cancelled) timing unchanged.
            interruptible_sleep(self.s.STABILIZATION_S, token=token)

            t_start = time.monotonic()
            dt = 1.0 / self.s.SAMPLE_RATE_HZ

            while True:
                check_cancellation(token)

                elapsed = time.monotonic() - t_start
                if elapsed > self.s.DISCHARGE_TIMEOUT_S:
                    self.log.warning("Discharge timeout on channel %d", channel)
                    return False

                sample = self.daq.read_all_batteries().get(channel, {})
                v = sample.get("voltage_v", 0.0)
                i = sample.get("current_a", 0.0)
                t_c = None  # TODO: read from NTC

                status = self.safety.check(v, i, t_c, mode="discharge")
                if not status.safe:
                    raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                data_collector.record(channel, {"elapsed_s": elapsed, "voltage_v": v, "current_a": i, "temp_c": t_c, "phase": "discharge"})

                # End of discharge: voltage drops to cutoff
                if v <= cutoff_v:
                    self.log.info("Discharge complete on channel %d (V=%.3f)", channel, v)
                    return True

                # Interruptible -- see charge_cycle.py::ChargeCycle.run().
                interruptible_sleep(dt, token=token)
        finally:
            if not self.smu.emergency_output_off(f"end of discharge cycle on channel {channel}"):
                self.log.critical(
                    "Channel %d: PMU output could not be verified OFF after discharge cycle.",
                    channel,
                )
