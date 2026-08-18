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
from test_control.battery_operation_sequence import _ChargeDischargeStats
from test_control.battery_diagnostics import classify_charge_behavior, message_for
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

        # Test Mode diagnostic classification ONLY (see
        # test_control/battery_diagnostics.py -- the SAME module/functions
        # ChargeSequence uses, reused here rather than a second/parallel
        # diagnostic engine). Reuses the exact voltage_v/current_a samples
        # the loop below already reads -- no new hardware access. LIMITATION
        # vs. ChargeSequence: this legacy path has no pre-enable (SMU
        # disabled) voltage reading to use as initial_voltage_v -- there is
        # no reverse-polarity check here to reuse -- so the FIRST in-loop
        # sample is used instead. By that point the SMU has already been
        # sourcing current for STABILIZATION_S, so an empty channel's
        # voltage may already be compliance-limited toward voltage_limit_v,
        # making ALREADY_CHARGED vs. POSSIBLY_EMPTY_POSITION less reliable
        # here than in ChargeSequence -- a known limitation, not silently
        # worked around by adding a new hardware read to this
        # already-validated legacy path. See docs/architecture.md.
        stats = _ChargeDischargeStats()
        run_start_time = time.monotonic()

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
                stats.add(v, i)

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
            self._log_diagnostic(channel, stats, current_a, voltage_limit_v,
                                  time.monotonic() - run_start_time, data_collector)

    def _log_diagnostic(self, channel, stats, commanded_current_a, voltage_limit_v,
                         duration_s, data_collector):
        """
        Test Mode diagnostic classification -- reuses test_control/
        battery_diagnostics.py::classify_charge_behavior(), the exact same
        function ChargeSequence uses (single source of truth, no parallel
        engine). Informational only: logged via the existing logger, and
        via data_collector.log_event() if the storage backend supports it
        (duck-typed -- log_event() is NOT part of the abstract
        StorageBackend interface, so a plugged-in MiniSQLStorage without it
        still works, just without the DB-side event). Never raises, never
        affects this cycle's return value/control flow.

        `voltage_limit_v` (the resolved CV target -- either from
        battery_cfg or the global CHARGE_VOLTAGE_V fallback) doubles as the
        "voltage_max_v" classify_charge_behavior() needs: it IS this
        charge's own commanded target, the correct reference point for
        "already at/near the target" regardless of which source it came
        from -- no run_summary row exists in this legacy path to read a
        real battery_cfg["voltage_max_v"] from either way.
        """
        try:
            if stats.initial_voltage_v is None:
                return
            result = classify_charge_behavior(
                initial_voltage_v=stats.initial_voltage_v, avg_current_a=stats.avg_current_a,
                duration_s=duration_s, commanded_current_a=commanded_current_a,
                battery_cfg={"voltage_max_v": voltage_limit_v},
            )
            message = message_for(result, mode="charge")
            log_line = f"Diagnostic (channel {channel}): {result}" + (f" -- {message}" if message else "")
            self.log.info(log_line)
            if hasattr(data_collector, "log_event"):
                data_collector.log_event(level="INFO", source="charge_cycle", channel=channel, message=log_line)
        except Exception as e:
            # Best-effort, informational only -- must never mask whatever
            # exception (if any) is already propagating out of run()'s
            # finally block (e.g. a genuine SafetyViolationError).
            self.log.warning("Channel %d: diagnostic classification failed -- %s", channel, e)
