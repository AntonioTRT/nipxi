"""
Battery test sequence module.
Orchestrates the full test over all active channels.

Mirrors the Main Control Loop from the VI flowchart:
    Initialize -> For each channel: select relay -> verify I=0 -> charge/discharge -> log -> next
"""

import logging
from config.settings import Settings
from test_control.charge_cycle import ChargeCycle
from test_control.discharge_cycle import DischargeCycle
from test_control.safety_monitor import SafetyMonitor
from utils.errors import SafetyViolationError


class BatteryTestSequence:
    def __init__(
        self,
        smu,
        daq,
        relay,
        safety: SafetyMonitor,
        charge_cycle: ChargeCycle,
        discharge_cycle: DischargeCycle,
        data_collector,
        settings: Settings,
        # relay_matrix accepted as alias for backward compatibility
        relay_matrix=None,
    ):
        self.smu = smu
        self.daq = daq
        # Accept either 'relay' (new RelayBase) or legacy 'relay_matrix' kwarg
        self.relay = relay if relay is not None else relay_matrix
        self.safety = safety
        self.charge = charge_cycle
        self.discharge = discharge_cycle
        self.data = data_collector
        self.s = settings
        self.log = logging.getLogger("nipxi.test")

    def run(self, channels: list[int] = None):
        """
        Run a full charge+discharge cycle on each channel.
        `channels` defaults to settings.ACTIVE_CHANNELS.
        """
        channels = channels or self.s.ACTIVE_CHANNELS
        self.log.info("Test sequence start. Channels: %s", channels)

        for ch in channels:
            self.log.info("--- Channel %d ---", ch)

            # Verify current is zero before switching relay (safety rule)
            if not self.safety.is_safe_to_switch_relay(self._read_current(ch)):
                self.log.error("Channel %d: current not zero - skipping relay switch.", ch)
                continue

            self.relay.close(ch)

            try:
                # Charge step first (standardizes SOC per protocol recommendation)
                self.charge.run(ch, self.data)

                # Verify zero current between charge and discharge
                # (SMU is already disabled inside charge_cycle.run)
                if not self.safety.is_safe_to_switch_relay(self._read_current(ch)):
                    self.log.error("Channel %d: current not zero after charge.", ch)
                    continue

                # Discharge step
                self.discharge.run(ch, self.data)

            except SafetyViolationError as e:
                self.log.error("Safety violation on channel %d: %s", ch, e)
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                break
            finally:
                self.relay.open(ch)

        self.log.info("Test sequence complete.")

    def _read_current(self, channel: int) -> float:
        sample = self.daq.read_all_batteries().get(channel, {})
        return sample.get("current_a", 0.0)
