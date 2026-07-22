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
from utils.cancellation import check_cancellation
from utils.errors import SafetyViolationError, RelayError, OperationCancelledError


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

    def run(self, channels: list[int] = None, token=None):
        """
        Run a full charge+discharge cycle on each channel.
        `channels` defaults to settings.ACTIVE_CHANNELS.

        `token` (see utils/cancellation.py) is checked once before each
        channel starts (never mid-channel here -- the finer-grained
        checkpoints live inside ChargeCycle.run()/DischargeCycle.run(),
        which is where a cancellation is actually likely to be noticed
        first during a multi-hour cycle) and is threaded through to both
        charge/discharge cycles so they can check it during their own
        sampling loops.

        On OperationCancelledError (a deliberate operator action, not a
        fault) or ANY other unexpected exception, the relay is forced open
        immediately at this fault location via safety.emergency_stop()/
        safety.safe_cancel_shutdown() -- rather than relying solely on the
        outer HardwareManager.disconnect_all() teardown to eventually open
        it. See docs/architecture.md's safety audit finding: previously,
        only SafetyViolationError/RelayError triggered an immediate relay
        open here; any other exception type left the relay closed until
        the process-level teardown ran. The PMU has never had this gap --
        ChargeCycle/DischargeCycle's own try/finally already forces it off
        on any exception -- this closes the equivalent gap for the relay.
        """
        channels = channels or self.s.ACTIVE_CHANNELS
        self.log.info("Test sequence start. Channels: %s", channels)

        for ch in channels:
            self.log.info("--- Channel %d ---", ch)

            # Verify current is zero before switching relay (safety rule)
            if not self.safety.is_safe_to_switch_relay(self._read_current(ch)):
                self.log.error("Channel %d: current not zero - skipping relay switch.", ch)
                continue

            try:
                # Checkpoint: before starting a new channel, never mid-channel.
                # Placed INSIDE the try block (not before it) so that if it
                # fires, the same except OperationCancelledError clause below
                # runs the safe shutdown sequence -- there must be exactly one
                # cancellation code path here, not two.
                check_cancellation(token)

                self.relay.close(ch)

                # Charge step first (standardizes SOC per protocol recommendation)
                self.charge.run(ch, self.data, token)

                # Verify zero current between charge and discharge
                # (SMU is already disabled inside charge_cycle.run)
                if not self.safety.is_safe_to_switch_relay(self._read_current(ch)):
                    self.log.error("Channel %d: current not zero after charge.", ch)
                    continue

                # Discharge step
                self.discharge.run(ch, self.data, token)

            except OperationCancelledError as e:
                # Deliberate operator action, not a fault -- distinct log
                # wording and a distinct shutdown helper (same hardware
                # sequence as emergency_stop(), different, non-alarming
                # log framing). PMU is already off (ChargeCycle/
                # DischargeCycle's own finally ran before this propagated
                # here) -- this call's PMU step is a harmless, idempotent
                # re-confirmation; its relay step is what actually matters
                # at this point.
                self.log.warning("Cancellation requested during channel %d: %s", ch, e)
                self.safety.safe_cancel_shutdown(self.smu, self.relay, str(e))
                raise

            except SafetyViolationError as e:
                self.log.error("Safety violation on channel %d: %s", ch, e)
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            except RelayError as e:
                # Includes RelayStateVerificationError -- a relay that did not
                # verifiably reach its commanded state is a safety fault, not a
                # retryable condition. Do not touch the relay again on this
                # channel: emergency_stop() re-attempts an all-off (and swallows
                # any further relay failure internally), then execution stops --
                # it must never fall through to open(ch) below or to the next
                # channel.
                self.log.error("Relay verification fault on channel %d: %s", ch, e)
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            except Exception as e:
                # Any other unanticipated failure (DAQError, SMUError,
                # NIPXITimeoutError, etc.) -- previously fell through
                # uncaught to the outer HardwareManager.disconnect_all()
                # teardown, leaving the relay closed in the meantime. Now
                # forced open immediately, at the fault location, matching
                # the PMU's existing guarantee.
                self.log.error("Unexpected error on channel %d: %s", ch, e, exc_info=True)
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            else:
                self.relay.open(ch)

        self.log.info("Test sequence complete.")

    def _read_current(self, channel: int) -> float:
        sample = self.daq.read_all_batteries().get(channel, {})
        return sample.get("current_a", 0.0)
