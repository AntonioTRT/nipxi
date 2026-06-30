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
from utils.errors import SafetyViolationError


class DischargeCycle:
    def __init__(self, smu, daq, safety: SafetyMonitor, settings: Settings):
        self.smu = smu
        self.daq = daq
        self.safety = safety
        self.s = settings
        self.log = logging.getLogger("nipxi.discharge")

    def run(self, channel: int, data_collector) -> bool:
        """
        Run one complete CC discharge on `channel`.
        Calls data_collector.record(channel, sample) for each sample.
        Returns True if discharge completed normally, False on timeout.
        Raises SafetyViolationError on limit violation.
        """
        self.log.info("Starting discharge cycle on channel %d", channel)

        self.smu.set_discharge_mode(
            current_a=self.s.DISCHARGE_CURRENT_A,
            voltage_limit_v=self.s.DISCHARGE_CUTOFF_V,
        )
        self.smu.output_enable()

        time.sleep(self.s.STABILIZATION_S)

        t_start = time.monotonic()
        dt = 1.0 / self.s.SAMPLE_RATE_HZ

        while True:
            elapsed = time.monotonic() - t_start
            if elapsed > self.s.DISCHARGE_TIMEOUT_S:
                self.log.warning("Discharge timeout on channel %d", channel)
                self.smu.output_disable()
                return False

            sample = self.daq.read_all_batteries().get(channel, {})
            v = sample.get("voltage_v", 0.0)
            i = sample.get("current_a", 0.0)
            t_c = None  # TODO: read from NTC

            status = self.safety.check(v, i, t_c)
            if not status.safe:
                self.smu.output_disable()
                raise SafetyViolationError(f"Channel {channel}: {status.reason}")

            data_collector.record(channel, {"elapsed_s": elapsed, "voltage_v": v, "current_a": i, "temp_c": t_c, "phase": "discharge"})

            # End of discharge: voltage drops to cutoff
            if v <= self.s.DISCHARGE_CUTOFF_V:
                self.log.info("Discharge complete on channel %d (V=%.3f)", channel, v)
                self.smu.output_disable()
                return True

            time.sleep(dt)
