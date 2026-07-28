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

    def run(self, channel: int, data_collector, token=None) -> bool:
        """
        Run one complete CC discharge on `channel`.
        Calls data_collector.record(channel, sample) for each sample.
        Returns True if discharge completed normally, False on timeout.
        Raises SafetyViolationError on limit violation.
        Raises OperationCancelledError if `token` has a cancellation
        requested -- see charge_cycle.py::ChargeCycle.run() for the full
        rationale (same checkpoint placement, same fail-safe reasoning).
        """
        self.log.info("Starting discharge cycle on channel %d", channel)

        check_cancellation(token)

        self.smu.set_discharge_mode(
            current_a=self.s.DISCHARGE_CURRENT_A,
            voltage_limit_v=self.s.DISCHARGE_CUTOFF_V,
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

                status = self.safety.check(v, i, t_c)
                if not status.safe:
                    raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                data_collector.record(channel, {"elapsed_s": elapsed, "voltage_v": v, "current_a": i, "temp_c": t_c, "phase": "discharge"})

                # End of discharge: voltage drops to cutoff
                if v <= self.s.DISCHARGE_CUTOFF_V:
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
