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
from test_control.execution_screen import (
    RECENT_MEASUREMENTS_DISPLAY_LIMIT, ExecutionFrame, render_execution_frame,
)
from utils.errors import (
    SafetyViolationError, RelayError, OperationCancelledError, ReversePolarityError,
    NIPXITimeoutError,
)
from utils.stop_reason import StopReason


class _ChargeDischargeStats:
    """
    Running voltage/current statistics over a Charge/Discharge sampling
    session -- Test Mode diagnostic classification ONLY (see
    test_control/battery_diagnostics.py). Reuses the exact voltage_v/
    current_a values ChargeSequence/DischargeSequence's sampling loop
    already computes each iteration (the same values already passed to
    _record_measurement()) -- add() performs no hardware read of its own.
    Never consulted for stop_reason/result/safety decisions.
    """

    def __init__(self):
        self.initial_voltage_v = None
        self.final_voltage_v = None
        self.max_current_a = 0.0
        self._current_sum = 0.0
        self.sample_count = 0

    def add(self, voltage_v, current_a):
        if self.initial_voltage_v is None:
            self.initial_voltage_v = voltage_v
        self.final_voltage_v = voltage_v
        self.max_current_a = max(self.max_current_a, abs(current_a))
        self._current_sum += abs(current_a)
        self.sample_count += 1

    @property
    def avg_current_a(self):
        return (self._current_sum / self.sample_count) if self.sample_count else 0.0


class BatteryOperationSequence:
    """
    Common state + shared skeleton for a battery-position operation.

    `dmm`/`daq` are optional (None) -- not every operation uses both (e.g.
    Monitor Battery today has no DAQ handle).
    """

    def __init__(self, smu, relay, safety: SafetyMonitor, storage, settings,
                 source: str, dmm=None, daq=None, group_name=None):
        self.smu = smu
        self.dmm = dmm
        self.daq = daq
        self.relay = relay
        self.safety = safety
        self.storage = storage
        self.s = settings
        self.source = source
        self.group_name = group_name
        self.log = logging.getLogger(f"nipxi.{source}")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _record_measurement(self, *, position_in_group=None, **fields):
        """
        storage.record_measurement() wrapper that fills in this sequence's
        own group_name (set at construction, constant for the sequence's
        lifetime) alongside the per-call position_in_group -- so every
        subclass's measurement-recording call site doesn't have to repeat
        `group_name=self.group_name` itself. `position_in_group` is passed
        per-call (not stored on self) since MonitorBatteryScanSequence
        scans many positions per instance; Monitor/Charge/Discharge pass
        their one fixed `channel` (== position_in_group under the Group
        Ownership Migration -- see config/devices.py::BATTERY_GROUPS).
        """
        return self.storage.record_measurement(
            group_name=self.group_name, position_in_group=position_in_group, **fields,
        )

    def _run_number(self):
        """The current run_summary row's integer id, or None if not found --
        same lookup every operation performed inline before this extraction."""
        run_summary = self.storage.get_run_summary(self.storage.run_id)
        return run_summary["id"] if run_summary else None

    def _render_frame(self, *, test_type, channel, relay_address, run_number,
                       state, phase_detail, **extra_fields):
        """
        Build and render one ExecutionFrame. recent_measurements/
        recent_events/initial_measurement are always pulled from storage
        here so no subclass re-fetches them independently -- every other
        frame field is operation-specific and passed through via
        `extra_fields` (e.g. `elapsed_s`, threaded by the caller so the
        compact execution screen can show a running indicator/elapsed time
        without this shared method needing its own notion of "when did
        this run start").

        recent_measurements is bounded to the last
        RECENT_MEASUREMENTS_DISPLAY_LIMIT rows (fetched directly via SQL
        `ORDER BY id DESC LIMIT n`, not "fetch everything and slice") --
        without this, a multi-hour run at 1 Hz re-queries and re-prints its
        ENTIRE history on every single sample, which is both the "endless
        scrolling" operator-UX complaint and a real, growing per-render
        query cost. initial_measurement is a separate, single-row fetch so
        the very first sample stays visible even once it has scrolled out
        of the bounded recent-measurements window.

        All three storage reads are scoped to `channel` (this call's own
        position under test) -- without this, the group-wide NTC pre-check
        (test.py::_ntc_group_snapshot(), which records one measurements row
        and one event_log line per position across the WHOLE group before
        the target relay ever closes) would leak other positions' NTC
        readings into this position's "Initial"/"Recent" measurement panels
        and "Recent Events" list -- e.g. Position 1's pre-check row showing
        up as the "Initial Measurement" for a run actually charging
        Position 5. Run-level setup messages logged with no channel at all
        (e.g. "Run started") are naturally excluded from the events panel
        once a channel filter is given -- see get_recent_events()'s own
        docstring.
        """
        frame = ExecutionFrame.from_live(
            run_number=run_number, run_id=self.storage.run_id, test_type=test_type,
            channel=channel, relay=relay_address, state=state, phase_detail=phase_detail,
            initial_measurement=self.storage.get_first_measurement(
                run_id=self.storage.run_id, channel=channel,
            ),
            recent_measurements=self.storage.get_measurements(
                run_id=self.storage.run_id, channel=channel,
                recent_limit=RECENT_MEASUREMENTS_DISPLAY_LIMIT,
            ),
            recent_events=self.storage.get_recent_events(run_id=self.storage.run_id, channel=channel),
            **extra_fields,
        )
        render_execution_frame(frame)

    def _check_battery_polarity(self, voltage_v: float, *, channel: int, relay_address: int):
        """
        Pre-output-enable reverse-polarity sanity check -- called with the
        SMU output still disabled, after the relay has closed and a DMM
        reading has settled, before set_charge_mode()/set_discharge_mode()/
        output_enable() ever run:

            Relay Selection -> Battery Voltage Measurement (DMM) ->
            Sanity Validation -> SMU Enable

        A correctly-connected, intact Li-ion cell never reads at or below
        Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V (see that setting's
        comment for why the threshold sits below 0.0 V rather than at it).
        Raises ReversePolarityError -- caught by run_guarded()'s existing
        SafetyViolationError branch -- with the SMU output never having been
        enabled at all. Does not attempt to distinguish a reversed cell from
        a disconnected lead or a genuinely damaged cell; only that none of
        those are safe to apply the SMU output to.
        """
        threshold = self.s.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V
        if voltage_v <= threshold:
            message = (
                f"Channel {channel}: pre-enable voltage sanity check failed -- "
                f"measured {voltage_v:.3f} V (at/below reverse-polarity threshold "
                f"{threshold:.3f} V) with SMU output disabled. SMU will NOT be enabled."
            )
            self.log.error(message)
            self.storage.log_event(
                level="ERROR", source=self.source, channel=channel, relay=relay_address,
                message=message,
            )
            raise ReversePolarityError(message)

    def _charge_diagnostic_fields(self, stats: _ChargeDischargeStats, *,
                                   commanded_current_a: float, battery_cfg: dict,
                                   duration_s: float) -> dict:
        """
        Test Mode post-run diagnostic classification for ChargeSequence --
        see test_control/battery_diagnostics.py::classify_charge_behavior().
        Purely additive/informational: returns {"analysis_result": ...} for
        the caller to fold into finish_run_summary()/complete()'s existing
        **fields -- never raises, never affects stop_reason/result. Called
        from BOTH run_guarded()'s extra_run_summary_fields_fn (every
        failure exit) and complete() (the normal-completion exit), so the
        classification reflects whichever path the run actually took.
        """
        from test_control.battery_diagnostics import classify_charge_behavior, message_for
        result = classify_charge_behavior(
            initial_voltage_v=stats.initial_voltage_v, avg_current_a=stats.avg_current_a,
            duration_s=duration_s, commanded_current_a=commanded_current_a, battery_cfg=battery_cfg,
        )
        message = message_for(result, mode="charge")
        if message:
            self.storage.log_event(level="INFO", source=self.source, message=f"Diagnostic: {message}")
        # start_voltage/end_voltage/sample_count are already-accepted
        # run_summary columns (see data/storage.py::finish_run_summary()'s
        # allowlist) -- MonitorBatterySequence's _VoltageStats has populated
        # these from day one; ChargeSequence/DischargeSequence never folded
        # their own already-tracked stats.initial_voltage_v/final_voltage_v
        # into this dict before, so every Charge/Discharge run_summary row
        # showed "Initial Voltage"/"End Voltage" as N/A regardless of what
        # was actually measured. end_voltage may be overwritten by
        # run_guarded()'s own fresher, defensive last-moment reading (taken
        # closer to the actual relay-open instant on an abnormal exit) --
        # see run_guarded()'s "final voltage" step below.
        return {
            "analysis_result": result,
            "start_voltage": stats.initial_voltage_v,
            "end_voltage": stats.final_voltage_v,
            "sample_count": stats.sample_count,
        }

    def _discharge_diagnostic_fields(self, stats: _ChargeDischargeStats, *,
                                      commanded_current_a: float, battery_cfg: dict,
                                      duration_s: float) -> dict:
        """DischargeSequence's counterpart to _charge_diagnostic_fields() --
        see test_control/battery_diagnostics.py::classify_discharge_behavior()."""
        from test_control.battery_diagnostics import classify_discharge_behavior, message_for
        result = classify_discharge_behavior(
            initial_voltage_v=stats.initial_voltage_v, final_voltage_v=stats.final_voltage_v,
            avg_current_a=stats.avg_current_a, duration_s=duration_s,
            commanded_current_a=commanded_current_a, battery_cfg=battery_cfg,
        )
        message = message_for(result, mode="discharge")
        if message:
            self.storage.log_event(level="INFO", source=self.source, message=f"Diagnostic: {message}")
        # See _charge_diagnostic_fields()'s identical comment -- same fix,
        # same rationale, DischargeSequence's own side.
        return {
            "analysis_result": result,
            "start_voltage": stats.initial_voltage_v,
            "end_voltage": stats.final_voltage_v,
            "sample_count": stats.sample_count,
        }

    def _safe_final_voltage_reading(self, context: str):
        """
        Best-effort FRESH voltage reading taken defensively at the moment a
        run is ending abnormally (safety violation, relay fault, timeout,
        or operator cancellation) -- while the position may still be
        electrically connected, deliberately BEFORE the safety shutdown
        that follows (safety.emergency_stop()/safe_cancel_shutdown()) opens
        every relay. A DMM measurement is passive (hardware/dmm.py's own
        docstring: "it only observes, it cannot source/energize anything"),
        so attempting one here is safe regardless of what actually went
        wrong with the SMU/relay.

        Never raises, and never delays or blocks the real safety shutdown
        that follows: returns None if no DMM is configured, or if the read
        itself fails (logged as a WARNING, not escalated -- a missing
        "last known voltage" is a traceability gap, not a safety-relevant
        one; safety.check()'s own limits are the authoritative abort path,
        not this best-effort reading).
        """
        if self.dmm is None:
            return None
        try:
            return self.dmm.measure_dc_voltage()
        except Exception as e:
            self.log.warning("%s: final voltage reading failed -- %s", context, e)
            return None

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
            fields = extra_run_summary_fields_fn()
            final_v = self._safe_final_voltage_reading("operator cancellation")
            if final_v is not None:
                fields["end_voltage"] = final_v
            self.storage.finish_run_summary(
                stop_reason=StopReason.CANCELLED, result="STOPPED_BY_OPERATOR",
                **fields,
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
            fields = extra_run_summary_fields_fn()
            final_v = self._safe_final_voltage_reading("safety violation")
            if final_v is not None:
                fields["end_voltage"] = final_v
            self.storage.finish_run_summary(
                stop_reason=StopReason.SAFETY_VIOLATION, result="FAIL",
                **fields,
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
            fields = extra_run_summary_fields_fn()
            final_v = self._safe_final_voltage_reading("relay fault")
            if final_v is not None:
                fields["end_voltage"] = final_v
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **fields,
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

        except NIPXITimeoutError as e:
            # Classified as StopReason.TIMEOUT, not the generic FAILED --
            # see docs/architecture.md "Timeout Traceability". Shutdown
            # sequence is identical to every other fault (emergency_stop()),
            # only the recorded stop_reason/execution_state differ.
            self.log.error("Timeout while %s channel %d: %s", verb, channel, e)
            self.storage.log_event(
                level="ERROR", source=self.source, channel=channel, relay=relay_address,
                message=f"Timeout -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.TIMEOUT)
            fields = extra_run_summary_fields_fn()
            final_v = self._safe_final_voltage_reading("timeout")
            if final_v is not None:
                fields["end_voltage"] = final_v
            self.storage.finish_run_summary(
                stop_reason=StopReason.TIMEOUT, result="FAIL",
                **fields,
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
            fields = extra_run_summary_fields_fn()
            final_v = self._safe_final_voltage_reading("unexpected error")
            if final_v is not None:
                fields["end_voltage"] = final_v
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **fields,
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
