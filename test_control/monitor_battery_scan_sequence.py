"""
Monitor Battery Scan sequence -- intermediate hardware-path validation step
required before Milestone III Charge Battery workflows.

Built on test_control/battery_operation_sequence.py::BatteryOperationSequence,
which owns the relay/ExecutionFrame/DataStorage/SafetyMonitor/cancellation
skeleton shared with MonitorBatterySequence (and, going forward,
Charge/Discharge/Cycle Battery) -- this file supplies only Monitor Battery
Scan's own per-position scan logic on top of it, not a parallel
implementation of that skeleton.

Purpose: sequentially connect batteries through the relay matrix, one
position at a time, and verify that only one battery is visible to the
measurement system at any moment -- relay isolation, switching behavior, and
measurement-path integrity. NO charging, NO discharging, NO PSU/SMU output --
this sequence never calls anything on the SMU except (indirectly, via
SafetyMonitor) confirming its output is OFF on every exit path, exactly as
MonitorBatterySequence already does. The SMU is constructed/connected by
HardwareManager (which forces its output off and verifies at startup,
independent of this sequence) but this sequence itself never sources or
sinks a single milliamp through it.

This is a CHARACTERIZATION workflow, not a pass/fail validation gate: real
hardware behavior (relay settling time, DMM read jitter, actual open-circuit
readings on this rig) is not yet known, so no reading is classified as
"good"/"anomalous" against an assumed voltage threshold anywhere in this
file. Every reading is taken, recorded, and logged as-is; interpreting the
recorded data is a downstream analysis step.

Per battery position, three distinct relay states are measured, each after
its own settling delay and as the average (+min/max) of several DMM samples
(see RELAY_SETTLE_TIME_S/MONITOR_SCAN_SAMPLES below and config/settings.py):

    Voltage_Open_Before -- relay forced open (all relays off, verified),
        settle, then sampled. This is the state BEFORE this position's
        relay is closed.
    Voltage_Closed      -- this position's relay closed (and verified --
        the ONLY relay ever energized at a time; the driver itself refuses
        close_all()), settle, then sampled. DAQ Channel 0 (one fixed
        physical channel regardless of position -- channel-mapping
        architecture is not yet approved, see hardware/daq.py) is also
        read once here, completely raw: no unit conversion, no
        calibration, no engineering interpretation. The relay is then held
        closed for a monitoring dwell (Settings.MONITOR_SCAN_DWELL_TIME_S,
        default 30s) -- periodic DMM/DAQ samples during the dwell let this
        workflow observe relay/measurement stability over time, not just a
        single settled reading, before the position is disconnected.
    Voltage_Open_After  -- relay forced open again (all relays off,
        verified), settle, then sampled. This is what should show the
        battery path has been isolated again.

Relay safety (see hardware/relay_eth.py's module docstring, "Relay Safety
Verification Pattern"): EVERY relay transition in this file goes through
NumatoRelayMatrix.open()/close(), and both of those unconditionally run
Read Current State -> Verify Current State -> Force All OFF -> Verify All
OFF -> [target action] -> Verify Action before returning -- this sequence
never calls the native write()/write_all() primitives directly, and never
weakens or bypasses that pattern. This is what makes "only one relay ever
energized at a time" a structural guarantee rather than a scan-level
assumption -- see the class docstring below for the exact call sequence.

storage.record_measurement(test_type="monitor_scan", ...) persists one row
per relay state per position (phase_detail = OPEN_BEFORE/CLOSED/OPEN_AFTER)
into the existing `measurements` table -- no new table. voltage_v carries
the average of the sample set; voltage_min_v/voltage_max_v (additive
columns, see data/storage.py) carry the min/max of that same set;
daq_channel_0_raw (additive column) carries the raw DAQ Channel 0 sample,
populated only for the CLOSED row (DAQ is not read with the relay open).
"""

import time

from config import devices as dev_cfg
from config.settings import Settings
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import DAQError

# Fixed physical DAQ channel used for every position's CLOSED-state read,
# regardless of which battery position is under test -- see the module
# docstring. Reuses Group B1's position 1 voltage channel string as
# "Channel 0" (e.g. "Dev1/ai0") rather than inventing a second constant
# that could drift from config/devices.py.
DAQ_CHANNEL_0 = dev_cfg.BATTERY_GROUPS["B1"]["positions"][1]["daq_voltage_ch"]


class MonitorBatteryScanSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, daq, relay, safety: SafetyMonitor, storage, settings: Settings, group_name=None):
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="monitor_battery_scan", dmm=dmm, daq=daq, group_name=group_name)

    def run(self, battery_type: str, group: str, positions_in_group: list, token=None,
            samples: int = None,
            dwell_s: float = None, dwell_interval_s: float = None):
        """
        Scan every position in `positions_in_group` (in-group, 1-based --
        e.g. Group B1 positions 1-8) belonging to `group`, resolving each
        one's relay address via config/devices.py::
        BATTERY_GROUPS[group]["positions"] -- the same resolution
        test.py's battery-selection flow already uses for Monitor Battery.
        `channel` is just `position` itself (position_in_group) -- there is
        no separate global position number.

        The delay after every relay open/close, before the DMM is read, is
        NOT a parameter here -- it is Settings.RELAY_SETTLE_TIME_S, the one
        global relay settling/dead-time constant, enforced unconditionally
        by RelayBase.open()/close() (hardware/relay.py) itself. This
        sequence never adds its own relay-settle sleep on top of that.
        `samples` (default Settings.MONITOR_SCAN_SAMPLES) is how many DMM
        readings are averaged (min/max also recorded) per relay state.
        `dwell_s` (default Settings.MONITOR_SCAN_DWELL_TIME_S) is how long
        the relay is held closed AFTER the initial settled CLOSED reading,
        to observe stability over time; `dwell_interval_s` (default
        Settings.MONITOR_SCAN_DWELL_SAMPLE_INTERVAL_S) is the DMM/DAQ
        sample period during that dwell. All three are configurable per
        call -- never hardcoded into the per-reading measurement logic
        below.

        Run-level bookkeeping (start_run_summary()/battery-config snapshot,
        the pre-scan traceability event_log entries) is the caller's
        responsibility (test.py's selection/confirmation-screen flow
        already has that information), matching MonitorBatterySequence's
        division of labor -- this method owns "Scan started" through
        "Scan completed".

        Cancellation (utils/cancellation.py::CancellationToken) is checked
        once before each position starts, never mid-position; DURING a
        position's monitoring dwell, cancellation is checked every
        `dwell_interval_s` via interruptible_sleep() (never a blocking
        sleep()) and immediately runs the same safe-shutdown path as any
        other cancellation in this codebase.
        """
        samples = self.s.MONITOR_SCAN_SAMPLES if samples is None else samples
        dwell_s = self.s.MONITOR_SCAN_DWELL_TIME_S if dwell_s is None else dwell_s
        dwell_interval_s = self.s.MONITOR_SCAN_DWELL_SAMPLE_INTERVAL_S if dwell_interval_s is None else dwell_interval_s

        self.log.info(
            "Monitor Battery Scan starting. Battery: %s  Group: %s  Positions: %s  "
            "Settle (global): %.3fs  Samples: %d  Dwell: %.1fs (every %.1fs)",
            battery_type, group, positions_in_group, self.s.RELAY_SETTLE_TIME_S, samples, dwell_s, dwell_interval_s,
        )
        run_number = self._run_number()
        total = len(positions_in_group)
        # Set once, read by _render() below -- avoids threading elapsed_s
        # through every intermediate _scan_this_position()/_scan_one_position()
        # call signature just to reach the one place that needs it.
        self._scan_start_time = time.monotonic()

        self.storage.log_event(level="INFO", source="monitor_battery_scan", message="Scan started")

        last_channel = None
        last_relay = None
        total_samples = 0

        for idx, position in enumerate(positions_in_group, start=1):
            ch_cfg = dev_cfg.BATTERY_GROUPS[group]["positions"][position]
            channel = position
            relay_address = ch_cfg["relay_address"]
            last_channel, last_relay = channel, relay_address
            scan_progress = f"{idx}/{total}"

            self.storage.log_event(
                level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
                message=f"Position selected: Group {group} Position {position} (channel {channel})",
            )

            def _scan_this_position(position=position, channel=channel, relay_address=relay_address,
                                     scan_progress=scan_progress):
                check_cancellation(token)
                return self._scan_one_position(
                    battery_type=battery_type, group=group, position=position,
                    channel=channel, relay_address=relay_address,
                    run_number=run_number, scan_progress=scan_progress,
                    samples=samples,
                    dwell_s=dwell_s, dwell_interval_s=dwell_interval_s, token=token,
                )

            total_samples += self.run_guarded(
                _scan_this_position, channel=channel, relay_address=relay_address,
                label="Monitor Battery Scan", verb="scanning",
                cancel_message="Scan stopped by operator",
            )

        self.log.info("Monitor Battery Scan complete. Positions scanned: %d", total)
        self.complete(
            channel=last_channel, relay_address=last_relay,
            log_message=f"Scan completed -- {total} position(s) scanned",
            sample_count=total_samples,
        )

    def _scan_one_position(self, battery_type, group, position, channel, relay_address,
                            run_number, scan_progress, samples,
                            dwell_s, dwell_interval_s, token):
        """
        Measure Voltage_Open_Before / Voltage_Closed / Voltage_Open_After
        for one battery position, holding the relay closed for a
        monitoring dwell between the CLOSED reading and Voltage_Open_After.
        Every relay transition goes through RelayBase.open()/close()
        (hardware/relay.py), which for NumatoRelayMatrix runs Read State ->
        Verify -> Force All OFF -> Verify OFF -> [action] -> Verify Action
        (see hardware/relay_eth.py) and then unconditionally blocks for
        Settings.RELAY_SETTLE_TIME_S before returning -- no separate
        settling delay is added here; the relay call itself does not
        return until settled. No pass/fail interpretation of any reading --
        this sequence characterizes hardware, it does not gate on it. Only
        a genuine RelayError/SafetyViolationError (or an unexpected
        exception) stops the scan; that propagates to run()'s run_guarded()
        handling unchanged.

        Returns the total number of DMM samples taken at this position
        (for run()'s run_summary.sample_count).
        """
        sample_total = 0
        # ------------------------------------------------------------
        # Voltage_Open_Before: force ALL relays off (verified), settle,
        # sample. This is also the mandatory pre-connection safety check --
        # relay.open() never activates anything; it only ever forces every
        # relay off and verifies that.
        # ------------------------------------------------------------
        self.relay.open(relay_address)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message="All relays verified open",
        )

        open_before = self._sample_dmm(samples)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position}: voltage measured (open, before) -- "
                    f"avg {open_before['avg']:.6f} V (min {open_before['min']:.6f} / max {open_before['max']:.6f})",
        )
        self._record_measurement(
            position_in_group=position,
            test_type="monitor_scan", channel=channel, relay=relay_address,
            phase_detail="OPEN_BEFORE",
            voltage_v=open_before["avg"], voltage_min_v=open_before["min"], voltage_max_v=open_before["max"],
        )
        self._render(battery_type, group, position, channel, relay_address, run_number, scan_progress,
                     state="ACTIVE", current_step="OPEN_BEFORE", relay_state="OPEN",
                     battery_voltage=open_before["avg"], daq_raw=None)
        sample_total += samples

        # ------------------------------------------------------------
        # Voltage_Closed: close this position's relay (force-all-off ->
        # verify -> activate -> verify-single -> verify-all, unchanged --
        # the ONLY relay ever energized at a time), settle, sample. DAQ
        # Channel 0 is also read once here, raw.
        # ------------------------------------------------------------
        self.relay.close(relay_address)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position} relay closed",
        )

        closed = self._sample_dmm(samples)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position}: voltage measured (closed) -- "
                    f"avg {closed['avg']:.6f} V (min {closed['min']:.6f} / max {closed['max']:.6f})",
        )

        daq_raw = None
        try:
            daq_raw = self.daq.read_channel(DAQ_CHANNEL_0)
            self.storage.log_event(
                level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
                message=f"DAQ channel 0 sampled -- {daq_raw:.6f} V (raw, {DAQ_CHANNEL_0})",
            )
        except DAQError as e:
            self.log.warning("DAQ channel 0 read failed for channel %d: %s", channel, e)
            self.storage.log_event(
                level="WARNING", source="monitor_battery_scan", channel=channel, relay=relay_address,
                message=f"DAQ channel 0 read failed -- {e}",
            )

        self._record_measurement(
            position_in_group=position,
            test_type="monitor_scan", channel=channel, relay=relay_address,
            phase_detail="CLOSED",
            voltage_v=closed["avg"], voltage_min_v=closed["min"], voltage_max_v=closed["max"],
            daq_channel_0_raw=daq_raw,
        )
        self._render(battery_type, group, position, channel, relay_address, run_number, scan_progress,
                     state="ACTIVE", current_step="CLOSED", relay_state="CLOSED",
                     battery_voltage=closed["avg"], daq_raw=daq_raw)
        sample_total += samples

        # ------------------------------------------------------------
        # Monitoring dwell: relay remains closed. Periodic DMM/DAQ samples,
        # ExecutionFrame updates, and measurement storage -- observes
        # relay/measurement stability over time rather than a single
        # settled reading. interruptible_sleep() only -- cancellation is
        # checked every dwell_interval_s and immediately runs the same
        # safe-shutdown path as any other cancellation point.
        # ------------------------------------------------------------
        sample_total += self._monitor_dwell(
            battery_type, group, position, channel, relay_address, run_number, scan_progress,
            dwell_s=dwell_s, dwell_interval_s=dwell_interval_s, token=token,
        )

        # ------------------------------------------------------------
        # Voltage_Open_After: open the relay again (force-all-off ->
        # verify), settle, sample -- confirms isolation after disconnect.
        # ------------------------------------------------------------
        self.relay.open(relay_address)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position} relay reopened",
        )

        open_after = self._sample_dmm(samples)
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position}: voltage measured (open, after) -- "
                    f"avg {open_after['avg']:.6f} V (min {open_after['min']:.6f} / max {open_after['max']:.6f})",
        )
        self._record_measurement(
            position_in_group=position,
            test_type="monitor_scan", channel=channel, relay=relay_address,
            phase_detail="OPEN_AFTER",
            voltage_v=open_after["avg"], voltage_min_v=open_after["min"], voltage_max_v=open_after["max"],
        )
        self._render(battery_type, group, position, channel, relay_address, run_number, scan_progress,
                     state="ACTIVE", current_step="OPEN_AFTER", relay_state="OPEN",
                     battery_voltage=open_after["avg"], daq_raw=None)
        sample_total += samples

        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Position {position}: isolation measurement recorded "
                    f"(open_before={open_before['avg']:.6f} V, closed={closed['avg']:.6f} V, "
                    f"open_after={open_after['avg']:.6f} V)",
        )

        return sample_total

    def _monitor_dwell(self, battery_type, group, position, channel, relay_address,
                        run_number, scan_progress, dwell_s, dwell_interval_s, token) -> int:
        """
        Hold the relay closed for `dwell_s`, taking a DMM (+ best-effort
        DAQ Channel 0) reading every `dwell_interval_s` -- observes relay/
        measurement stability over the dwell rather than a single settled
        reading. Uses interruptible_sleep() exclusively (never a blocking
        sleep()): a cancellation request is detected at the next interval
        boundary (worst case ~dwell_interval_s latency) and raises
        OperationCancelledError, which propagates to _scan_one_position()/
        run()'s run_guarded() unchanged and triggers the existing
        safe-shutdown path.

        Returns the number of DMM samples taken during the dwell (0 if
        dwell_s <= 0 -- the dwell is skipped entirely, not run once).
        """
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message="Monitoring started",
        )
        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message=f"Monitoring dwell: {dwell_s:.1f} s",
        )

        sample_count = 0
        if dwell_s > 0:
            start = time.monotonic()
            elapsed = 0.0
            while elapsed < dwell_s:
                interruptible_sleep(min(dwell_interval_s, dwell_s - elapsed), token=token)
                elapsed = time.monotonic() - start

                voltage = self.dmm.measure_dc_voltage()
                daq_raw = None
                try:
                    daq_raw = self.daq.read_channel(DAQ_CHANNEL_0)
                except DAQError as e:
                    self.log.warning("DAQ channel 0 read failed during dwell for channel %d: %s", channel, e)
                    self.storage.log_event(
                        level="WARNING", source="monitor_battery_scan", channel=channel, relay=relay_address,
                        message=f"DAQ channel 0 read failed during monitoring dwell -- {e}",
                    )
                sample_count += 1

                remaining_s = max(0.0, dwell_s - elapsed)
                dwell_progress = f"{min(elapsed, dwell_s):.1f}/{dwell_s:.1f} s"
                self._record_measurement(
                    position_in_group=position,
                    test_type="monitor_scan", channel=channel, relay=relay_address,
                    phase_detail="MONITORING", voltage_v=voltage, daq_channel_0_raw=daq_raw,
                )
                self.storage.log_event(
                    level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
                    message=f"Position {position}: voltage measured (closed) -- {voltage:.6f} V "
                            f"({dwell_progress})",
                )
                self._render(battery_type, group, position, channel, relay_address, run_number, scan_progress,
                             state="ACTIVE", current_step="MONITORING", relay_state="CLOSED",
                             battery_voltage=voltage, daq_raw=daq_raw,
                             dwell_progress=dwell_progress, dwell_remaining_s=remaining_s)

        self.storage.log_event(
            level="INFO", source="monitor_battery_scan", channel=channel, relay=relay_address,
            message="Monitoring completed",
        )
        return sample_count

    def _sample_dmm(self, samples: int) -> dict:
        """
        Take `samples` consecutive DMM readings and return
        {"avg": ..., "min": ..., "max": ..., "readings": [...]}. Improves
        measurement stability over a single read; no filtering/outlier
        rejection is applied -- every sample is used as-is.
        """
        readings = [self.dmm.measure_dc_voltage() for _ in range(samples)]
        return {
            "avg": sum(readings) / len(readings),
            "min": min(readings),
            "max": max(readings),
            "readings": readings,
        }

    def _render(self, battery_type, group, position, channel, relay_address, run_number, scan_progress,
                *, state, current_step, relay_state, battery_voltage, daq_raw,
                dwell_progress=None, dwell_remaining_s=None):
        self._render_frame(
            test_type="monitor_scan", channel=channel, relay_address=relay_address,
            run_number=run_number, state=state, phase_detail=current_step,
            elapsed_s=time.monotonic() - self._scan_start_time,
            battery_voltage=battery_voltage, daq_channel_0_raw=daq_raw,
            battery_type=battery_type, group=group, position_in_group=position,
            relay_state=relay_state, current_step=current_step, scan_progress=scan_progress,
            dwell_progress=dwell_progress, dwell_remaining_s=dwell_remaining_s,
        )
