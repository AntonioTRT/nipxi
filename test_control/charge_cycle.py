"""
Charge cycle module.
Implements CC-CV charge for a single battery channel.

Follows the 'charge step' branch of the VI Charge/Discharge flowchart:
    Set SMU to charge mode -> stabilize -> acquire -> check safety -> log -> check EOC
"""

import logging
import time
from config.settings import Settings
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import SafetyViolationError, TimeoutError


class ChargeCycle:
    def __init__(self, smu, daq, safety: SafetyMonitor, settings: Settings):
        self.smu = smu
        self.daq = daq
        self.safety = safety
        self.s = settings
        self.log = logging.getLogger("nipxi.charge")

    def run(self, channel: int, data_collector, token=None, battery_cfg: dict = None) -> bool:
        """
        Run one complete CC-CV charge on `channel`.
        Calls data_collector.record(channel, sample) for each sample.
        Returns True if charge completed normally, False on timeout.
        Raises SafetyViolationError on limit violation.
        Raises OperationCancelledError if `token` has a cancellation
        requested (see utils/cancellation.py) -- checked once before this
        cycle even starts (so a cancel requested before this channel begins
        skips energizing the PMU at all) and once per sampling loop
        iteration. Never checked mid-sequence inside a single hardware
        operation (see the module docstring's referenced safety sequence
        rules).

        `battery_cfg` (a config/devices.py BATTERY_CONFIGS[...] entry),
        if given, supplies the commanded charge current
        (max_charge_current_a) and CV voltage (voltage_max_v) instead of
        the global Settings.CHARGE_CURRENT_A/CHARGE_VOLTAGE_V, and is
        forwarded to self.safety so SafetyMonitor.check() enforces the
        same battery-specific limits. battery_cfg=None preserves prior
        (global-Settings-only) behavior exactly. The CV-taper cutoff
        current (CHARGE_CUTOFF_A) has no BATTERY_CONFIGS equivalent and
        remains a global Settings constant -- a deliberate scope boundary,
        not an oversight (see docs/architecture.md).
        """
        self.log.info("Starting charge cycle on channel %d", channel)

        current_a = self.s.CHARGE_CURRENT_A
        voltage_limit_v = self.s.CHARGE_VOLTAGE_V
        if battery_cfg is not None:
            current_a = battery_cfg.get("max_charge_current_a", current_a)
            voltage_limit_v = battery_cfg.get("voltage_max_v", voltage_limit_v)
        self.safety.set_battery_limits(battery_cfg)

        # Checkpoint: skip entirely if cancellation was already requested
        # before this cycle started (e.g. during the previous channel's
        # teardown) -- never energize the PMU only to immediately tear it
        # back down.
        check_cancellation(token)

        self.smu.set_charge_mode(
            current_a=current_a,
            voltage_limit_v=voltage_limit_v,
        )
        self.smu.output_enable()

        # PMU fail-safe: emergency_output_off() runs exactly once regardless
        # of how this block exits -- normal completion, timeout, a safety
        # violation, a cancellation, or any unhandled exception (e.g.
        # self.daq raising). "Unknown PMU state = unsafe state" applies to
        # every exit path, not just the ones we anticipated. See
        # hardware/smu.py module docstring and docs/architecture.md "PMU
        # Safety Philosophy".
        #
        # The try/finally now starts here, BEFORE the stabilization wait --
        # previously it started after that wait completed, so a
        # cancellation raised DURING stabilization would have skipped
        # emergency_output_off() entirely, leaving output enabled. This was
        # latent (a plain time.sleep() could never raise) until the
        # stabilization wait below became interruptible -- fixed as part of
        # that same change. See docs/architecture.md "Interruptible Wait
        # Mechanism" / docs/TIMING_ANALYSIS.md.
        try:
            # Interruptible: previously an uninterrupted time.sleep() with
            # NO cancellation checkpoint at all for the full
            # STABILIZATION_S duration, output already energized. Normal
            # (non-cancelled) timing is unchanged -- this still waits the
            # full STABILIZATION_S before the first sample.
            interruptible_sleep(self.s.STABILIZATION_S, token=token)

            t_start = time.monotonic()
            dt = 1.0 / self.s.SAMPLE_RATE_HZ

            while True:
                # Checkpoint: between atomic hardware operations only --
                # never inside the DAQ read or the safety check below.
                check_cancellation(token)

                elapsed = time.monotonic() - t_start
                if elapsed > self.s.CHARGE_TIMEOUT_S:
                    self.log.warning("Charge timeout on channel %d", channel)
                    return False

                sample = self.daq.read_all_batteries().get(channel, {})
                v = sample.get("voltage_v", 0.0)
                i = sample.get("current_a", 0.0)
                # TODO: get temperature from NTC channel
                t_c = None

                status = self.safety.check(v, i, t_c, mode="charge")
                if not status.safe:
                    raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                data_collector.record(channel, {"elapsed_s": elapsed, "voltage_v": v, "current_a": i, "temp_c": t_c, "phase": "charge"})

                # End of charge: CV taper current drops below cutoff
                if v >= voltage_limit_v and abs(i) <= self.s.CHARGE_CUTOFF_A:
                    self.log.info("Charge complete on channel %d (V=%.3f, I=%.4f)", channel, v, i)
                    return True

                # Interruptible: previously bounded cancellation latency to
                # ~one dt via the checkpoint at the top of the next
                # iteration; now checked at ~poll_interval_s granularity
                # during the sleep itself too. Normal timing unchanged.
                interruptible_sleep(dt, token=token)
        finally:
            if not self.smu.emergency_output_off(f"end of charge cycle on channel {channel}"):
                self.log.critical(
                    "Channel %d: PMU output could not be verified OFF after charge cycle.",
                    channel,
                )
