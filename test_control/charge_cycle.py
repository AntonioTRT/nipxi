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
from utils.errors import SafetyViolationError, TimeoutError


class ChargeCycle:
    def __init__(self, smu, daq, safety: SafetyMonitor, settings: Settings):
        self.smu = smu
        self.daq = daq
        self.safety = safety
        self.s = settings
        self.log = logging.getLogger("nipxi.charge")

    def run(self, channel: int, data_collector) -> bool:
        """
        Run one complete CC-CV charge on `channel`.
        Calls data_collector.record(channel, sample) for each sample.
        Returns True if charge completed normally, False on timeout.
        Raises SafetyViolationError on limit violation.
        """
        self.log.info("Starting charge cycle on channel %d", channel)

        self.smu.set_charge_mode(
            current_a=self.s.CHARGE_CURRENT_A,
            voltage_limit_v=self.s.CHARGE_VOLTAGE_V,
        )
        self.smu.output_enable()

        time.sleep(self.s.STABILIZATION_S)

        t_start = time.monotonic()
        dt = 1.0 / self.s.SAMPLE_RATE_HZ

        while True:
            elapsed = time.monotonic() - t_start
            if elapsed > self.s.CHARGE_TIMEOUT_S:
                self.log.warning("Charge timeout on channel %d", channel)
                self.smu.output_disable()
                return False

            sample = self.daq.read_all_batteries().get(channel, {})
            v = sample.get("voltage_v", 0.0)
            i = sample.get("current_a", 0.0)
            # TODO: get temperature from NTC channel
            t_c = None

            status = self.safety.check(v, i, t_c)
            if not status.safe:
                self.smu.output_disable()
                raise SafetyViolationError(f"Channel {channel}: {status.reason}")

            data_collector.record(channel, {"elapsed_s": elapsed, "voltage_v": v, "current_a": i, "temp_c": t_c, "phase": "charge"})

            # End of charge: CV taper current drops below cutoff
            if v >= self.s.CHARGE_VOLTAGE_V and abs(i) <= self.s.CHARGE_CUTOFF_A:
                self.log.info("Charge complete on channel %d (V=%.3f, I=%.4f)", channel, v, i)
                self.smu.output_disable()
                return True

            time.sleep(dt)
