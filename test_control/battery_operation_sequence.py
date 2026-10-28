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
import sqlite3
import time

import config.devices as dev_cfg
from test_control.frozen_signal import FrozenSignalDetector
from test_control.safety_monitor import SafetyMonitor
from test_control.storage_guard import StorageSpaceGuard
from utils.watchdog import LoopWatchdog
from test_control.execution_screen import (
    RECENT_EVENTS_DISPLAY_LIMIT, RECENT_MEASUREMENTS_DISPLAY_LIMIT,
    ExecutionFrame, render_execution_frame,
)
from utils.errors import (
    SafetyViolationError, RelayError, OperationCancelledError, ReversePolarityError,
    NIPXITimeoutError, MeasurementFrozenError, RelayCommLostError, RelayStateVerificationError,
    StorageFailureError,
)
from utils.event_format import EventType, format_event
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
                 source: str, dmm=None, daq=None, group_name=None,
                 sense_router=None, sense_channel=None):
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
        # FUTURE PLANNED ARCHITECTURE -- see docs/architecture.md "Future
        # Architecture: Battery Sense Routing". Both None for every
        # operation EXCEPT Charge/Discharge Battery on B1 (see "SenseRouter
        # Deployment: MATRIX_NUMATO_201"), which construct and pass a real
        # ConfigDrivenSenseRouter/sense_channel=1. _safe_final_voltage_
        # reading() below is the only base-class DMM read, and its use of
        # read_battery_voltage_via_sense() is a pure passthrough when
        # sense_channel is None, identical to calling
        # dmm.measure_dc_voltage() directly.
        self.sense_router = sense_router
        self.sense_channel = sense_channel
        self._reset_supervision()

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
        try:
            return self.storage.record_measurement(
                group_name=self.group_name, position_in_group=position_in_group, **fields,
            )
        except (sqlite3.Error, OSError) as e:
            # A battery test must not continue without a record -- classified
            # as a safety violation so run_guarded() runs the standard shutdown.
            raise StorageFailureError(f"Measurement could not be persisted: {e}") from e

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
        docstring. recent_events is bounded to RECENT_EVENTS_DISPLAY_LIMIT
        rows for the identical "endless scrolling"/growing-query-cost
        reason recent_measurements already was -- see docs/architecture.md
        "Current Execution Screen: Second Compactness Pass" (this bound
        used to be get_recent_events()'s own default of 20).

        `dmm_route` is resolved here, once, for every subclass
        (Charge/Discharge/Monitor Battery all share this one method) from
        `self.sense_channel`/config/devices.py::SENSE_ROUTING -- None
        (rendered "DIRECT", not "N/A") when this operation's sense_channel
        is None, exactly like every other DMM read in this codebase today.
        """
        dmm_route = None
        if self.sense_channel is not None:
            route = dev_cfg.SENSE_ROUTING.get(self.sense_channel, {})
            matrix_name, relay_num = route.get("relay_matrix"), route.get("relay")
            if matrix_name is not None and relay_num is not None:
                dmm_route = f"{matrix_name} CH{relay_num}"

        frame = ExecutionFrame.from_live(
            run_number=run_number, run_id=self.storage.run_id, test_type=test_type,
            channel=channel, relay=relay_address, state=state, phase_detail=phase_detail,
            dmm_route=dmm_route,
            initial_measurement=self.storage.get_first_measurement(
                run_id=self.storage.run_id, channel=channel,
            ),
            recent_measurements=self.storage.get_measurements(
                run_id=self.storage.run_id, channel=channel,
                recent_limit=RECENT_MEASUREMENTS_DISPLAY_LIMIT,
            ),
            recent_events=self.storage.get_recent_events(
                run_id=self.storage.run_id, channel=channel, limit=RECENT_EVENTS_DISPLAY_LIMIT,
            ),
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

        Uses read_battery_voltage_via_sense() (see docs/architecture.md
        "Future Architecture: Battery Sense Routing"), not
        self.dmm.measure_dc_voltage() directly -- a pure passthrough when
        self.sense_channel is None (every operation today). When a sense
        channel IS configured, this deliberately reconnects/reads/
        disconnects fresh here even though the caller's own run() may have
        already disconnected it on the way out: this reading happens AFTER
        that disconnect (run_guarded() calls this from its exception
        branches, once the sequence's own try/finally has already
        unwound), so it must re-establish the sense path itself rather
        than assume it is still connected.
        """
        if self.dmm is None:
            return None
        try:
            from hardware.sense_router import read_battery_voltage_via_sense
            return read_battery_voltage_via_sense(self.dmm, self.sense_router, self.sense_channel)
        except Exception as e:
            self.log.warning("%s: final voltage reading failed -- %s", context, e)
            return None

    def _shutdown_trace_logger(self, *, channel, relay_address):
        """
        Returns an on_event(message: str) callback that records a
        "SHUTDOWN: <message>" event_log row via self.storage.log_event() --
        see docs/architecture.md "Shutdown Trace Logging". This is the DB-
        backed counterpart to the "[SHUTDOWN-TRACE]"-prefixed lines already
        emitted via self.log.* (Python's logging module, not the database)
        inside hardware/smu.py and test_control/safety_monitor.py -- those
        Python-logger lines go to whatever log handlers are configured
        (console/file), NOT to the SQLite event_log table
        get_recent_events() reads from. Without this, an operator inspecting
        the DB-backed event log after a Ctrl+C sees only the single, human-
        readable cancellation line (e.g. "Charging stopped by operator")
        and nothing about the underlying shutdown steps, even though those
        steps did execute -- a logging/observability gap, not evidence the
        shutdown sequence didn't run.

        This is the one place with legitimate access to BOTH `self.storage`
        and the specific channel/relay this call concerns -- hardware/smu.py
        and safety_monitor.py deliberately have no knowledge of `storage`/
        the event_log schema (a hardware/persistence layering boundary this
        project maintains elsewhere too), so they accept this callback as a
        plain string-in, nothing-out hook instead.

        Never raises: a failure to record a trace event is demoted to a
        Python-logging WARNING and swallowed here, same policy as every
        other post-isolation/best-effort step in the shutdown path -- this
        must never be able to interfere with the actual shutdown action it
        is describing.
        """
        def _on_event(message: str) -> None:
            try:
                self.storage.log_event(
                    level="INFO", source=self.source, channel=channel, relay=relay_address,
                    message=f"SHUTDOWN: {message}",
                )
            except Exception as log_err:
                self.log.warning("Failed to record shutdown-trace event %r: %s", message, log_err)
        return _on_event

    # ------------------------------------------------------------------
    # Energized-operation supervision -- called once per sampling iteration
    # by Charge/Discharge/Monitor Battery via _periodic_safety_checks().
    # ------------------------------------------------------------------

    def _setting(self, name, default):
        return getattr(self.s, name, default)

    def _reset_supervision(self):
        """Fresh watchdog/heartbeat/space-guard/frozen-detector state for one
        guarded run."""
        self._bookkeeping_failures = []
        self._frozen_detectors = {}
        self._last_heartbeat = None
        self._space_guard = StorageSpaceGuard(self.s)
        self._watchdog = LoopWatchdog(
            self._setting("LOOP_WATCHDOG_TIMEOUT_S", 0), self._watchdog_expired, name=self.source,
        )

    def _watchdog_expired(self, reason: str):
        """Runs on the watchdog thread when the loop stopped making progress:
        the same SMU-off-then-relay-open sequence every other fault uses."""
        self.log.critical("%s -- running emergency stop from the watchdog thread.", reason)
        self.safety.emergency_stop(self.smu, self.relay, reason)

    def _safe_log_event(self, **kwargs):
        """storage.log_event() that never raises -- for code that runs while
        hardware is being made safe, where a storage failure must not be able
        to interrupt the safing."""
        try:
            return self.storage.log_event(**kwargs)
        except Exception as e:
            self.log.error("event_log write failed (%s): %s", kwargs.get("message"), e)
            return None

    def _best_effort(self, what: str, fn, *args, **kwargs):
        """Run a bookkeeping call after the safety shutdown; failures are
        recorded and logged, never raised."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            self._bookkeeping_failures.append(what)
            self.log.error("Post-shutdown bookkeeping failed (%s): %s", what, e)
            return None

    def _periodic_safety_checks(self, *, channel, relay_address):
        """
        One call per sampling iteration, at the top of the loop:
          - pets the loop watchdog (raises if it already fired);
          - relay heartbeat, time-gated by Settings.RELAY_HEARTBEAT_INTERVAL_S;
          - storage free-space check, time-gated by
            Settings.STORAGE_CHECK_INTERVAL_S.
        Any failure raises and unwinds through the caller's own try/finally
        (SMU off) into run_guarded()'s safety shutdown.
        """
        self._watchdog.pet()
        self._relay_heartbeat(channel=channel, relay_address=relay_address)
        self._space_guard.check(on_warn=lambda message: self._safe_log_event(
            level="WARNING", source=self.source, channel=channel, relay=relay_address, message=message,
        ))

    def _relay_heartbeat(self, *, channel, relay_address):
        """
        Re-read the relay bank while a relay is closed under test, so a lost
        relay link is found within RELAY_HEARTBEAT_INTERVAL_S instead of at the
        end of the run. Verifies only communication plus "exactly the expected
        channel reads closed" -- a readback of the COMMANDED state, not an
        independent contact check.
        """
        interval = self._setting("RELAY_HEARTBEAT_INTERVAL_S", 0)
        closed_channels = getattr(self.relay, "closed_channels", None)
        if not interval or interval <= 0 or closed_channels is None:
            return
        now = time.monotonic()
        if self._last_heartbeat is None:
            self._last_heartbeat = now   # close() just verified the bank; start the clock
            return
        if now - self._last_heartbeat < interval:
            return
        self._last_heartbeat = now
        try:
            closed = list(closed_channels())
        except (RelayError, NIPXITimeoutError) as e:
            message = (f"Channel {channel}: relay {getattr(self.relay, 'name', '')} stopped "
                       f"answering during the run -- {e}")
            self._safe_log_event(level="ERROR", source=self.source, channel=channel,
                                 relay=relay_address, message=message)
            raise RelayCommLostError(message) from e
        if closed != [relay_address]:
            raise RelayStateVerificationError(
                f"Channel {channel}: relay heartbeat expected only channel {relay_address} "
                f"closed, read {closed}."
            )

    def _frozen_check(self, key, value, *, window_setting, epsilon_setting, label,
                      channel, relay_address, active=True):
        """
        Feed one sample to this run's FrozenSignalDetector for `key`. `active`
        False means the signal is not expected to move right now (e.g. no
        current flowing) -- the detector is reset instead of fed. Raises
        MeasurementFrozenError once the window is full.
        """
        if not self._setting("FROZEN_DETECTION_ENABLED", False) or value is None:
            return
        detector = self._frozen_detectors.get(key)
        if detector is None:
            detector = self._frozen_detectors[key] = FrozenSignalDetector(
                self._setting(window_setting, 60), self._setting(epsilon_setting, 0.0),
            )
        if not active:
            detector.reset()
            return
        if detector.update(value):
            message = (f"Channel {channel}: {label} frozen -- {detector.count} consecutive "
                       f"identical samples ({value!r}); measurement is stale.")
            self.log.error(message)
            self._safe_log_event(level="ERROR", source=self.source, channel=channel,
                                 relay=relay_address, message=message)
            raise MeasurementFrozenError(message)

    def _frozen_check_samples(self, *, channel, relay_address, v=None, i=None, ntc_v=None,
                               voltage_active=True):
        """
        Frozen-measurement checks for one sampling iteration (see
        test_control/frozen_signal.py). Current is always judged; voltage only
        while current flows (|I| >= FROZEN_CURRENT_MIN_ACTIVE_A) AND the caller
        says the voltage is expected to move (`voltage_active` -- False during
        the CV hold, where a regulated voltage legitimately sits still); NTC
        voltage always when supplied.
        """
        common = dict(channel=channel, relay_address=relay_address)
        if i is not None:
            self._frozen_check("current", i, window_setting="FROZEN_CURRENT_SAMPLES",
                               epsilon_setting="FROZEN_CURRENT_EPSILON_A", label="SMU current", **common)
            if v is not None:
                flowing = abs(i) >= self._setting("FROZEN_CURRENT_MIN_ACTIVE_A", 0.01)
                self._frozen_check("voltage", v, window_setting="FROZEN_VOLTAGE_SAMPLES",
                                   epsilon_setting="FROZEN_VOLTAGE_EPSILON_V", label="DMM voltage",
                                   active=flowing and voltage_active, **common)
        if ntc_v is not None:
            self._frozen_check("ntc", ntc_v, window_setting="FROZEN_NTC_SAMPLES",
                               epsilon_setting="FROZEN_NTC_EPSILON_V", label="NTC voltage", **common)

    def _abort_run(self, exc, *, channel, relay_address, trace, events, state, stop_reason,
                    result, final_ctx, extra_fn, cancelled=False):
        """
        Abnormal-exit handling shared by every run_guarded() branch.

        ORDER (fixed): safety shutdown FIRST (SMU off verified, then relay
        open), THEN best-effort bookkeeping. Persistence used to run first, so
        a storage failure -- the likeliest thing to be broken when a run
        aborts -- skipped the shutdown entirely. Every bookkeeping call below
        is individually guarded; a failure is logged, never raised.
        """
        self._watchdog.stop()
        on_event = self._shutdown_trace_logger(channel=channel, relay_address=relay_address)
        on_event(trace)

        try:
            if cancelled:
                self.safety.safe_cancel_shutdown(self.smu, self.relay, str(exc), on_event=on_event)
            else:
                self.safety.emergency_stop(self.smu, self.relay, str(exc), on_event=on_event)
        except Exception as shutdown_err:
            self.log.critical(
                "Safety shutdown raised unexpectedly: %s. Hardware may still be energized -- "
                "physically disconnect power if this cannot be resolved immediately.", shutdown_err,
            )

        for level, message in events:
            self._best_effort("event_log", self.storage.log_event, level=level, source=self.source,
                              channel=channel, relay=relay_address, message=message)
        self._best_effort("execution_state", self.storage.record_execution_state,
                          channel=channel, relay=relay_address, state=state)
        fields = self._best_effort("run_summary fields", extra_fn) or {}
        # Taken AFTER the shutdown: a defensive reading must never delay or
        # block the safing. The position is isolated by now, so this is a
        # resting (open-circuit) voltage rather than a loaded one.
        final_v = self._safe_final_voltage_reading(final_ctx)
        if final_v is not None:
            fields["end_voltage"] = final_v
        self._best_effort("run_summary", self.storage.finish_run_summary,
                          stop_reason=stop_reason, result=result, **fields)
        if self._bookkeeping_failures:
            self.log.critical(
                "Channel %s: hardware was made safe, but traceability is INCOMPLETE for this "
                "run (failed: %s).", channel, ", ".join(self._bookkeeping_failures),
            )

    def run_guarded(self, fn, *, channel, relay_address, label, verb, cancel_message,
                     extra_run_summary_fields_fn=lambda: {}):
        """
        Run `fn()` under the cancellation/safety/relay/unexpected-error
        handling every operation needs: run the matching SafetyMonitor
        shutdown FIRST, then (best effort) log the event, record execution
        state and finish run_summary, and re-raise -- see _abort_run().

        `label` (e.g. "Monitor Battery"/"Monitor Battery Scan") and `verb`
        (e.g. "monitoring"/"scanning") parameterize only the diagnostic log
        text; `cancel_message` is the exact event_log message recorded on
        operator cancellation (the one piece of wording that genuinely
        differs between operations). `extra_run_summary_fields_fn` supplies
        operation-specific finish_run_summary() fields (e.g. Monitor
        Battery's running voltage stats), evaluated at the moment of the
        exception, not at call time, so it reflects state as of the failure.

        Returns fn()'s return value on success. Re-raises on any of the
        handled exception types after the shutdown/bookkeeping above.
        """
        self._reset_supervision()

        def abort(exc, **kwargs):
            self._abort_run(exc, channel=channel, relay_address=relay_address,
                            extra_fn=extra_run_summary_fields_fn, **kwargs)

        try:
            return fn()

        except OperationCancelledError as e:
            self.log.warning("%s cancelled: %s", label, e)
            abort(e, trace="cancellation detected", events=[("INFO", cancel_message)],
                  state=StopReason.CANCELLED, stop_reason=StopReason.CANCELLED,
                  result="STOPPED_BY_OPERATOR", final_ctx="operator cancellation", cancelled=True)
            raise

        except SafetyViolationError as e:
            self.log.error("Safety violation while %s channel %d: %s", verb, channel, e)
            # Standardized Hardware Event Logging (see docs/architecture.md)
            # -- covers every SafetyViolationError source uniformly
            # (reverse polarity, a real safety.check() limit violation,
            # battery-removal-during-charge, frozen measurement, storage
            # failure, watchdog) via this one shared branch.
            abort(e, trace="safety violation detected",
                  events=[("ERROR", f"Safety violation -- {e}"),
                          ("ERROR", format_event(EventType.SAFETY_MONITOR_TRIGGERED, reason=str(e)))],
                  state=StopReason.SAFETY_VIOLATION, stop_reason=StopReason.SAFETY_VIOLATION,
                  result="FAIL", final_ctx="safety violation")
            raise

        except RelayError as e:
            self.log.error("Relay verification fault while %s channel %d: %s", verb, channel, e)
            kind = "Relay communication lost" if isinstance(e, RelayCommLostError) else "Relay verification fault"
            abort(e, trace="relay fault detected", events=[("ERROR", f"{kind} -- {e}")],
                  state=StopReason.FAILED, stop_reason=StopReason.FAILED,
                  result="FAIL", final_ctx="relay fault")
            raise

        except NIPXITimeoutError as e:
            # Classified as StopReason.TIMEOUT with its OWN dedicated
            # result="TIMEOUT" (not the generic "FAIL") -- see docs/
            # architecture.md "Timeout Traceability". Shutdown sequence is
            # identical to every other fault (emergency_stop()).
            self.log.error("Timeout while %s channel %d: %s", verb, channel, e)
            abort(e, trace="timeout detected", events=[("ERROR", f"Timeout -- {e}")],
                  state=StopReason.TIMEOUT, stop_reason=StopReason.TIMEOUT,
                  result="TIMEOUT", final_ctx="timeout")
            raise

        except Exception as e:
            self.log.error("Unexpected error while %s channel %d: %s", verb, channel, e, exc_info=True)
            abort(e, trace="unexpected error detected", events=[("ERROR", f"Unexpected error -- {e}")],
                  state=StopReason.FAILED, stop_reason=StopReason.FAILED,
                  result="FAIL", final_ctx="unexpected error")
            raise

        finally:
            self._watchdog.stop()

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
