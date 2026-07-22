"""
Test Executor
=============

Drives the full battery test sequence and returns structured results.

Responsibilities:
  - Build the internal test-control objects (ChargeCycle, DischargeCycle, SafetyMonitor)
  - Run the BatteryTestSequence for the requested channels
  - Collect per-channel outcomes into a TestRunResult
  - Handle safety violations and unexpected errors gracefully

Usage:
    from test_control.test_executor import TestExecutor, TestRunResult
    from test_control.hardware_manager import HardwareManager
    from data.storage import DataStorage
    from config.settings import Settings

    hw      = HardwareManager(Settings, relay_cfg=NUMATO_RELAY_MATRIX_CONFIG)  # production
    storage = DataStorage(settings=Settings)

    executor = TestExecutor(hw=hw, storage=storage, settings=Settings)

    with hw, storage:
        result = executor.run(channels=[1, 2, 3])

    print(result.run_id)
    print("success:", result.success)
    for r in result.channel_results:
        print(r.channel, "charge:", r.charge_completed, "discharge:", r.discharge_completed)
"""

import logging
from dataclasses import dataclass, field

from config.settings import Settings
from test_control.battery_test import BatteryTestSequence
from test_control.charge_cycle import ChargeCycle
from test_control.discharge_cycle import DischargeCycle
from test_control.safety_monitor import SafetyMonitor
from utils.errors import (
    SafetyViolationError, HardwareInitError, RelayError, OperationCancelledError,
)
from utils.stop_reason import StopReason


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ChannelResult:
    """Outcome for a single battery channel."""
    channel: int
    charge_completed: bool = False
    discharge_completed: bool = False
    aborted: bool = False
    error: str = ""

    @property
    def success(self) -> bool:
        return self.charge_completed and self.discharge_completed and not self.aborted


@dataclass
class TestRunResult:
    """
    Aggregated outcome of a complete test run.

    run_id matches the DataStorage.run_id used to write measurements, so
    results can be correlated with the database records.
    """
    run_id: str
    channels_tested: list
    channel_results: list = field(default_factory=list)
    aborted: bool = False
    error: str = ""
    stop_reason: str = StopReason.COMPLETED

    @property
    def success(self) -> bool:
        """True only if every tested channel completed both charge and discharge."""
        if self.aborted or self.error:
            return False
        return all(r.success for r in self.channel_results)

    def summary(self) -> str:
        """
        One-line human-readable summary for logging. `stop_reason`
        (COMPLETED/FAILED/SAFETY_VIOLATION/TIMEOUT/CANCELLED -- see
        utils/stop_reason.py) takes precedence over the old OK/PARTIAL
        wording whenever it's not COMPLETED, so a cancelled run reads as
        "status=CANCELLED", never "status=ABORTED"/"status=PARTIAL" --
        stop_reason is why it stopped; passed/total is how much completed;
        these are deliberately independent, not folded into one value.
        """
        passed = sum(1 for r in self.channel_results if r.success)
        total = len(self.channel_results)
        if self.stop_reason != StopReason.COMPLETED:
            status = self.stop_reason
        else:
            status = "OK" if self.success else "PARTIAL"
        return (
            f"run_id={self.run_id}  "
            f"channels={self.channels_tested}  "
            f"passed={passed}/{total}  "
            f"status={status}"
        )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class TestExecutor:
    """
    Orchestrates a full charge+discharge test run across the requested channels.

    Composes the low-level test-control objects internally, so callers only
    need to supply the hardware handles and a storage backend.

    Args:
        hw:       HardwareManager with connected SMU, DAQ, relay.
        storage:  An open StorageBackend instance (DataStorage or MiniSQL).
        settings: Settings class (class-level attributes).
    """

    def __init__(self, hw, storage, settings: Settings):
        self.hw      = hw
        self.storage = storage
        self.s       = settings
        self.log     = logging.getLogger("nipxi.executor")

        # Build internal test-control objects here so main.py stays free of imports
        self._safety    = SafetyMonitor(settings)
        self._charge    = ChargeCycle(hw.smu, hw.daq, self._safety, settings)
        self._discharge = DischargeCycle(hw.smu, hw.daq, self._safety, settings)

        # The sequence object is stateless between channels -- safe to reuse
        self._sequence = BatteryTestSequence(
            smu=hw.smu,
            daq=hw.daq,
            relay=hw.relay,
            safety=self._safety,
            charge_cycle=self._charge,
            discharge_cycle=self._discharge,
            data_collector=storage,
            settings=settings,
        )

    def run(self, channels: list = None, token=None) -> TestRunResult:
        """
        Execute a full charge+discharge cycle on each requested channel.

        Args:
            channels: list of 1-based channel indices to test.
                      Defaults to settings.ACTIVE_CHANNELS.
            token:    optional CancellationToken (see utils/cancellation.py).
                      Passed through to BatteryTestSequence.run(), which
                      threads it into ChargeCycle/DischargeCycle. If None,
                      the run behaves exactly as before -- cancellation is
                      opt-in, not a required argument.

        Returns:
            TestRunResult with per-channel outcomes, run_id, and stop_reason
            (see utils/stop_reason.py -- distinguishes an operator
            cancellation from a genuine failure).

        Does not raise -- all exceptions are caught and recorded in the result.
        Callers should inspect result.success, result.stop_reason, or
        result.channel_results.
        """
        channels = channels or self.s.ACTIVE_CHANNELS
        run_id   = getattr(self.storage, "run_id", "unknown")
        result   = TestRunResult(run_id=run_id, channels_tested=list(channels))

        self.log.info("TestExecutor starting. channels=%s  run_id=%s", channels, run_id)

        try:
            per_channel = self._run_sequence(channels, token)
            result.channel_results = per_channel
            result.stop_reason = StopReason.COMPLETED

        except OperationCancelledError as e:
            # Deliberate operator action (Ctrl+C -> CancellationToken), not
            # a failure. Safe shutdown (PMU off/verified, relay open/
            # verified) already ran inside BatteryTestSequence via
            # safety.safe_cancel_shutdown() before this propagated here.
            result.aborted     = True
            result.error       = str(e)
            result.stop_reason = StopReason.CANCELLED
            self.log.warning("Test cancelled by operator: %s", e)

        except SafetyViolationError as e:
            # Emergency stop was already triggered inside BatteryTestSequence
            result.aborted     = True
            result.error       = str(e)
            result.stop_reason = StopReason.SAFETY_VIOLATION
            self.log.error("Test aborted: safety violation -- %s", e)

        except RelayError as e:
            # Includes RelayStateVerificationError. Emergency stop was already
            # triggered inside BatteryTestSequence -- this is a safety fault,
            # never a condition the executor retries or continues past.
            result.aborted     = True
            result.error       = str(e)
            result.stop_reason = StopReason.FAILED
            self.log.error("Test aborted: relay verification fault -- %s", e)

        except HardwareInitError as e:
            result.aborted     = True
            result.error       = str(e)
            result.stop_reason = StopReason.FAILED
            self.log.error("Test aborted: hardware error -- %s", e)

        except Exception as e:
            result.aborted     = True
            result.error       = str(e)
            result.stop_reason = StopReason.FAILED
            self.log.error("Test aborted: unexpected error -- %s", e, exc_info=True)

        self.log.info("TestExecutor finished. %s", result.summary())
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_sequence(self, channels: list, token=None) -> list:
        """
        Delegate to BatteryTestSequence and collect per-channel outcomes.

        BatteryTestSequence.run() drives the relay/charge/discharge loop.
        We wrap it here to capture which channels actually completed.
        """
        # BatteryTestSequence.run() handles its own logging and per-channel safety.
        # For now, completion means run() returned without raising -- all channels done.
        # TODO: extend BatteryTestSequence to return per-channel ChannelResult objects
        #       once real hardware feedback is available.
        self._sequence.run(channels, token)

        # Mark all as completed until the sequence returns per-channel data
        return [
            ChannelResult(
                channel=ch,
                charge_completed=True,
                discharge_completed=True,
            )
            for ch in channels
        ]
