"""
Shared execution skeleton for every battery-position operation (Monitor
Battery, Monitor Battery Scan, and -- once implemented -- Charge/Discharge/
Cycle Battery).

Extracted from test_control/monitor_battery_sequence.py and test_control/
monitor_battery_scan_sequence.py, which had begun to duplicate this exact
skeleton: relay/channel bookkeeping, ExecutionFrame rendering, DataStorage
measurement/event_log/run_summary/execution_state calls, SafetyMonitor-driven
shutdown, and cancellation-aware exception handling. BatteryOperationSequence
owns that shared machinery; each concrete sequence supplies only its own
sampling/control logic on top of it.

This is a base class, not a plugin framework -- a concrete sequence calls
self.run_guarded()/self._render_frame()/self.complete() directly from its
own run()/loop methods. Two (soon four) concrete subclasses do not justify a
driver-registry or strategy-object indirection on top of this.

Hardware handles (smu/dmm/daq/relay) are resolved by the caller (test.py, via
config/devices.py::hardware_for_group() and HardwareManager) and passed in
already connected -- this class is where they are held and used consistently
by every operation, not where they are first resolved.
"""

import logging

from test_control.safety_monitor import SafetyMonitor
from test_control.execution_screen import ExecutionFrame, render_execution_frame
from utils.errors import SafetyViolationError, RelayError, OperationCancelledError
from utils.stop_reason import StopReason


class BatteryOperationSequence:
    """
    Common state + shared skeleton for a battery-position operation.

    `dmm`/`daq` are optional (None) -- not every operation uses both (e.g.
    Monitor Battery today has no DAQ handle).
    """

    def __init__(self, smu, relay, safety: SafetyMonitor, storage, settings,
                 source: str, dmm=None, daq=None):
        self.smu = smu
        self.dmm = dmm
        self.daq = daq
        self.relay = relay
        self.safety = safety
        self.storage = storage
        self.s = settings
        self.source = source
        self.log = logging.getLogger(f"nipxi.{source}")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _run_number(self):
        """The current run_summary row's integer id, or None if not found --
        same lookup every operation performed inline before this extraction."""
        run_summary = self.storage.get_run_summary(self.storage.run_id)
        return run_summary["id"] if run_summary else None

    def _render_frame(self, *, test_type, channel, relay_address, run_number,
                       state, phase_detail, **extra_fields):
        """
        Build and render one ExecutionFrame. recent_measurements/
        recent_events are always pulled from storage here so no subclass
        re-fetches them independently -- every other frame field is
        operation-specific and passed through via `extra_fields`.
        """
        frame = ExecutionFrame.from_live(
            run_number=run_number, run_id=self.storage.run_id, test_type=test_type,
            channel=channel, relay=relay_address, state=state, phase_detail=phase_detail,
            recent_measurements=self.storage.get_measurements(run_id=self.storage.run_id),
            recent_events=self.storage.get_recent_events(run_id=self.storage.run_id),
            **extra_fields,
        )
        render_execution_frame(frame)

    def run_guarded(self, fn, *, channel, relay_address, label, verb, cancel_message,
                     extra_run_summary_fields_fn=lambda: {}):
        """
        Run `fn()` under the cancellation/safety/relay/unexpected-error
        handling every operation needs: log the event, record execution
        state, finish run_summary, run the matching SafetyMonitor shutdown,
        and re-raise -- identical to what MonitorBatterySequence/
        MonitorBatteryScanSequence each independently implemented before
        this extraction.

        `label` (e.g. "Monitor Battery"/"Monitor Battery Scan") and `verb`
        (e.g. "monitoring"/"scanning") parameterize only the diagnostic log
        text; `cancel_message` is the exact event_log message recorded on
        operator cancellation (the one piece of wording that genuinely
        differs between operations). `extra_run_summary_fields_fn` supplies
        operation-specific finish_run_summary() fields (e.g. Monitor
        Battery's running voltage stats), evaluated at the moment of the
        exception, not at call time, so it reflects state as of the failure.

        Returns fn()'s return value on success. Re-raises on any of the
        four handled exception types after the shutdown bookkeeping above.
        """
        try:
            return fn()

        except OperationCancelledError as e:
            self.log.warning("%s cancelled: %s", label, e)
            self.storage.log_event(
                level="INFO", source=self.source, channel=channel, relay=relay_address,
                message=cancel_message,
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.CANCELLED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.CANCELLED, result="STOPPED_BY_OPERATOR",
                **extra_run_summary_fields_fn(),
            )
            self.safety.safe_cancel_shutdown(self.smu, self.relay, str(e))
            raise

        except SafetyViolationError as e:
            self.log.error("Safety violation while %s channel %d: %s", verb, channel, e)
            self.storage.log_event(
                level="ERROR", source=self.source, channel=channel, relay=relay_address,
                message=f"Safety violation -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.SAFETY_VIOLATION)
            self.storage.finish_run_summary(
                stop_reason=StopReason.SAFETY_VIOLATION, result="FAIL",
                **extra_run_summary_fields_fn(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

        except RelayError as e:
            self.log.error("Relay verification fault while %s channel %d: %s", verb, channel, e)
            self.storage.log_event(
                level="ERROR", source=self.source, channel=channel, relay=relay_address,
                message=f"Relay verification fault -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.FAILED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **extra_run_summary_fields_fn(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

        except Exception as e:
            self.log.error("Unexpected error while %s channel %d: %s", verb, channel, e, exc_info=True)
            self.storage.log_event(
                level="ERROR", source=self.source, channel=channel, relay=relay_address,
                message=f"Unexpected error -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.FAILED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **extra_run_summary_fields_fn(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

    def complete(self, *, channel, relay_address, log_message, **extra_run_summary_fields):
        """
        Shared normal-completion bookkeeping (record execution state as
        COMPLETED, finish run_summary as PASS, log the completion event) --
        used by operations that have a natural end (e.g. Monitor Battery
        Scan finishing every position). Monitor Battery has no normal
        completion path (cancellation is the only expected exit) and never
        calls this.
        """
        self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.COMPLETED)
        self.storage.finish_run_summary(
            stop_reason=StopReason.COMPLETED, result="PASS", **extra_run_summary_fields,
        )
        self.storage.log_event(level="INFO", source=self.source, message=log_message)
