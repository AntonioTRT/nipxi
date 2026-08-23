"""
Discharge Sequence -- built on BatteryOperationSequence
(test_control/battery_operation_sequence.py), the target execution
architecture for battery charge/discharge/cycle work (see
docs/architecture.md Section 35 "Revised Roadmap"). This is the official
path forward for Discharge Battery -- not a second workflow architecture
alongside TestExecutor/BatteryTestSequence/DischargeCycle.

Harvested from test_control/discharge_cycle.py::DischargeCycle (see
docs/architecture.md Section 33 "ChargeCycle / DischargeCycle Harvest
Plan"), KEPT/MIGRATED unchanged in shape:
    - EOD logic: v <= cutoff_v (simple voltage-cutoff check -- CC-only
      discharge has no CV/taper phase).
    - PSU sequencing: set_discharge_mode() -> output_enable() -> sampling
      loop -> emergency_output_off() in a try/finally that starts
      immediately after output_enable() (covers the stabilization wait,
      per the fix in docs/architecture.md Section 27).
    - battery_cfg setpoint resolution + SafetyMonitor.set_battery_limits()
      + mode="discharge" passed into every safety.check() call.

Discharge Cutoff Policy (docs/architecture.md Section 30) -- applied from
the start, not retrofitted:
    Discharge target = cycle objective (where this discharge intends to
    stop). Battery minimum voltage = absolute safety floor. The floor
    ALWAYS has priority. The effective cutoff used for EOD detection is
    clamped to max(target, floor) -- the system must never discharge below
    the battery's own voltage_min_v, regardless of what any target says.
    Today's BATTERY_CONFIGS entries define only one voltage
    (voltage_min_v), so target and floor are numerically identical for
    HUB/SB -- the clamp exists so a future battery type whose target and
    floor differ (or a misconfiguration) can never discharge past the
    floor. SafetyMonitor.check(mode="discharge") remains the authoritative
    abort path on every sample regardless of where this cutoff sits; the
    clamp is a defensive measure, not the primary safety mechanism.

NOT carried forward from DischargeCycle (see the Harvest Plan section
above): `daq.read_all_batteries()` telemetry (docs/architecture.md Section
31 "Telemetry Source Strategy" -- DMM is the active telemetry source, DAQ
mapping remains unapproved and this work must not be blocked by it) and
TestExecutor/BatteryTestSequence orchestration coupling.

Battery type is a REQUIRED, explicit parameter (`battery_cfg`) -- never
inferred from channel/group/position/relay.

`battery_cfg` (BATTERY_CONFIGS[type]) vs. `test_setpoints`
(BATTERY_GROUPS[group]["test_setpoints"]) -- two different things, per the
Battery Group Test Configuration Architecture (see docs/architecture.md):
`battery_cfg` supplies only the safety floor/SafetyMonitor limits, never
the commanded setpoint. `test_setpoints` supplies the actual commanded
discharge current and target cutoff -- already validated by the caller
(utils/validators.py::validate_group_test_config()) before this class is
constructed. This class trusts that validation; it does not repeat it.

Temperature: `ntc_channel` (this position's own BATTERY_GROUPS[group]
["positions"][...]["daq_ntc_ch"], resolved by the caller), read via
`self.daq` --
HardwareManager's "ntc_daq" role (see docs/architecture.md "Dual DAQ
Ownership Model") and charge_sequence.py's identical rationale. Fed into
the same safety.check() call below that already enforces voltage/current
-- temp_c was always a real, checked parameter, just never supplied a
non-None value until now.

Timeout: a discharge timeout here raises NIPXITimeoutError, classified as
StopReason.TIMEOUT (not the generic FAILED) -- see charge_sequence.py's
module docstring for the identical rationale, and docs/architecture.md
"Timeout Traceability".

Reverse Polarity Protection: see charge_sequence.py's module docstring --
identical pre-output-enable DMM sanity check here, before
set_discharge_mode()/output_enable().

Post-Run Diagnostic Classification (Test Mode only, informational) -- see
charge_sequence.py's identical rationale and test_control/
battery_diagnostics.py::classify_discharge_behavior(). An empty position
sinking current from an open circuit drives voltage toward the SMU's
compliance floor almost immediately, tripping the undervoltage safety
check -- `analysis_result` (NORMAL_DISCHARGE_BEHAVIOR/
POSSIBLY_EMPTY_POSITION) distinguishes that from a genuine discharge,
purely additively, never touching stop_reason/result.
"""

import time

from config.settings import Settings
from hardware.temperature import NTCPresence, classify_ntc_presence, ntc_voltage_to_celsius
from test_control.battery_operation_sequence import BatteryOperationSequence, _ChargeDischargeStats
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import DAQError, NIPXITimeoutError, SafetyViolationError, ReversePolarityError


class DischargeSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings, daq=None,
                 group_name=None, ntc_daq_name=None):
        # `daq` -- see charge_sequence.py::ChargeSequence.__init__()'s
        # identical comment (this group's NTC DAQ; DMM/SMU remain the
        # voltage/current telemetry source). `ntc_daq_name` -- see the same
        # comment for its display-only purpose (operator-facing NTC block,
        # docs/architecture.md Section 58).
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="discharge_battery", dmm=dmm, daq=daq, group_name=group_name)
        self.ntc_daq_name = ntc_daq_name

    def run(self, channel: int, relay_address: int, battery_cfg: dict,
            test_setpoints: dict, ntc_channel: str = None, token=None) -> bool:
        """
        Run one complete CC discharge on `channel`/`relay_address`.

        `battery_cfg` (a config/devices.py BATTERY_CONFIGS[...] entry --
        REQUIRED) supplies only the SafetyMonitor's absolute safety floor --
        never the commanded setpoint (see module docstring). `test_setpoints`
        (a config/devices.py BATTERY_GROUPS[group]["test_setpoints"] entry --
        REQUIRED, already validated by the caller via utils/validators.py::
        validate_group_test_config()) supplies the actual commanded
        discharge current and target cutoff. `ntc_channel` (optional --
        this position's BATTERY_GROUPS[group]["positions"][...]
        ["daq_ntc_ch"]) enables real temperature acquisition into the
        existing safety check; omitted,
        temperature stays "N/A" exactly as before.

        Returns True once EOD is reached. Raises SafetyViolationError,
        RelayError, NIPXITimeoutError, or OperationCancelledError on any
        abnormal exit -- all handled by run_guarded().
        """
        self.log.info("Discharge Sequence starting. Channel: %d  Relay: %d", channel, relay_address)
        run_number = self._run_number()

        current_a = test_setpoints["discharge_current_a"]
        floor_v = battery_cfg["voltage_min_v"]
        target_v = test_setpoints["discharge_cutoff_v"]  # cycle objective, set by the group's
                                                          # test recipe -- see module docstring's
                                                          # Discharge Cutoff Policy.
        # SMU compliance ceiling -- DELIBERATELY NOT cutoff_v. NI-DCPower's
        # default compliance mode is SYMMETRIC: a DC_CURRENT session's
        # voltage_limit sets a +/-voltage_limit window, not a one-sided
        # floor. A real battery starts discharge near voltage_max_v (e.g.
        # 4.2V) -- if voltage_limit were set to the low EOD cutoff (~3.0V),
        # the SMU would sit in voltage compliance (unable to actually sink
        # the commanded current) for virtually the entire discharge, only
        # able to sink freely once voltage happened to fall inside +/-3.0V
        # -- by which point EOD would already have triggered. Using
        # voltage_max_v keeps the whole real discharge voltage range
        # (cutoff_v..voltage_max_v) inside the compliance window instead.
        # cutoff_v below remains the EOD *detection* threshold only -- a
        # separate concern from this SMU compliance parameter. Confirmed
        # against nidcpower's real (simulate=True) driver: default
        # compliance_limit_symmetry is SYMMETRIC. See docs/architecture.md
        # Section 37.
        compliance_voltage_v = battery_cfg["voltage_max_v"]
        self.safety.set_battery_limits(battery_cfg)
        last_ntc_state = None  # throttles repeated NTC-fault/absent event_log noise to one entry per transition

        # The safety floor always has priority -- never let the discharge
        # target sit below it. Defensive clamp; SafetyMonitor.check()
        # below remains the authoritative abort path regardless.
        cutoff_v = max(target_v, floor_v)
        if target_v < floor_v:
            self.log.warning(
                "Channel %d: discharge target %.3f V is below the safety floor "
                "%.3f V -- using the floor as the effective cutoff.",
                channel, target_v, floor_v,
            )

        # Test Mode diagnostic classification ONLY (see
        # test_control/battery_diagnostics.py) -- see charge_sequence.py's
        # identical rationale. Accumulates the SAME voltage_v/current_a
        # samples the loop below already computes, never a second read.
        stats = _ChargeDischargeStats()
        run_start_time = time.monotonic()

        def _diagnostic_fields():
            return self._discharge_diagnostic_fields(
                stats, commanded_current_a=current_a, battery_cfg=battery_cfg,
                duration_s=time.monotonic() - run_start_time,
            )

        def _run_discharge():
            check_cancellation(token)
            self.relay.close(relay_address)
            self.storage.log_event(
                level="INFO", source="discharge_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} activated -- discharging started "
                        f"({current_a:.3f} A sink, {compliance_voltage_v:.3f} V SMU compliance, "
                        f"{cutoff_v:.3f} V EOD cutoff)",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state="ACTIVE")

            # Pre-output-enable reverse-polarity sanity check -- Relay
            # Selection -> Battery Voltage Measurement (DMM) -> Sanity
            # Validation -> SMU Enable. See ChargeSequence.run()'s identical
            # rationale and BatteryOperationSequence._check_battery_polarity().
            # No separate relay-settle sleep here -- self.relay.close() above
            # already blocked for Settings.RELAY_SETTLE_TIME_S (the single
            # global relay settling/dead-time constant, enforced in
            # RelayBase.open()/close(), hardware/relay.py) before returning.
            pre_enable_v = self.dmm.measure_dc_voltage()

            # Captured BEFORE _check_battery_polarity() below -- see
            # charge_sequence.py's identical rationale (must always be
            # persisted, including on a ReversePolarityError raised by that
            # very check; taken with the SMU output still disabled, before
            # compliance can mask an empty position as looking like a real,
            # present cell). Reuses pre_enable_v itself, no new read.
            stats.initial_voltage_v = pre_enable_v

            self._check_battery_polarity(pre_enable_v, channel=channel, relay_address=relay_address)

            self.smu.set_discharge_mode(current_a=current_a, voltage_limit_v=compliance_voltage_v)
            self.smu.output_enable()

            nonlocal last_ntc_state
            try:
                interruptible_sleep(self.s.STABILIZATION_S, token=token)

                t_start = time.monotonic()
                dt = 1.0 / self.s.SAMPLE_RATE_HZ
                # Per-group validation override -- see charge_sequence.py's
                # identical rationale and docs/architecture.md "Configurable
                # Validation Timeout". Already validated by
                # validate_group_test_config() before this sequence was
                # constructed.
                discharge_timeout_s = test_setpoints.get("discharge_timeout_s", self.s.DISCHARGE_TIMEOUT_S)

                while True:
                    check_cancellation(token)

                    elapsed = time.monotonic() - t_start
                    if elapsed > discharge_timeout_s:
                        raise NIPXITimeoutError(
                            f"Channel {channel}: discharge timeout after {elapsed:.0f}s (EOD not reached)"
                        )

                    # Telemetry: DMM for voltage, SMU's own ADC readback for
                    # current -- see charge_sequence.py's identical rationale.
                    smu_reading = self.smu.measure()
                    dmm_v = self.dmm.measure_dc_voltage()
                    v = dmm_v
                    i = smu_reading["current_a"]
                    stats.add(v, i)

                    t_c = None
                    presence = None
                    if self.daq is not None and ntc_channel is not None:
                        try:
                            ntc_v = self.daq.read_channel(ntc_channel)
                            presence = classify_ntc_presence(ntc_v)
                            if presence == NTCPresence.PRESENT:
                                t_c = ntc_voltage_to_celsius(ntc_v)
                            elif presence != last_ntc_state:
                                self.storage.log_event(
                                    level="WARNING", source="discharge_battery",
                                    channel=channel, relay=relay_address,
                                    message=f"NTC reading {presence} -- temperature monitoring degraded",
                                )
                            last_ntc_state = presence
                        except DAQError as e:
                            if last_ntc_state != "fault":
                                self.storage.log_event(
                                    level="WARNING", source="discharge_battery",
                                    channel=channel, relay=relay_address,
                                    message=f"NTC read failed -- {e}",
                                )
                            last_ntc_state = "fault"

                    status = self.safety.check(v, i, t_c, mode="discharge")
                    if not status.safe:
                        raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                    self._record_measurement(
                        position_in_group=channel,
                        test_type="discharge", channel=channel, relay=relay_address,
                        phase_detail="CC_DISCHARGE", voltage_v=v, current_a=i, temp_c=t_c,
                        smu_measured_v=smu_reading["voltage_v"], smu_measured_i=i,
                        dmm_measured_v=dmm_v,
                    )
                    self._render_frame(
                        test_type="discharge", channel=channel, relay_address=relay_address,
                        run_number=run_number, state="ACTIVE", phase_detail="CC_DISCHARGE",
                        elapsed_s=time.monotonic() - run_start_time,
                        smu_voltage=smu_reading["voltage_v"], smu_current=i, dmm_voltage=dmm_v,
                        battery_voltage=v, battery_current=i, battery_temp=t_c,
                        ntc_device=self.ntc_daq_name, ntc_resource=getattr(self.daq, "resource", None),
                        ntc_channel=ntc_channel, ntc_status=presence,
                    )

                    # End of discharge: voltage at/below the (floor-clamped)
                    # cutoff. Harvested unchanged from DischargeCycle.run().
                    if v <= cutoff_v:
                        self.log.info("Discharge complete on channel %d (V=%.3f)", channel, v)
                        break

                    interruptible_sleep(dt, token=token)
            finally:
                if not self.smu.emergency_output_off(f"end of discharge sequence on channel {channel}"):
                    self.log.critical(
                        "Channel %d: PMU output could not be verified OFF after discharge sequence.",
                        channel,
                    )

            # Relay open only AFTER the PMU output is confirmed off (the
            # finally above) -- never switch a relay while current might
            # still be flowing. The only way execution reaches here without
            # having raised is EOD (the `break` above) -- every other exit
            # (timeout/safety violation/cancellation/unexpected error)
            # raises and is handled by run_guarded()'s safety.emergency_stop()/
            # safe_cancel_shutdown(), which already force-open every relay.
            # Without this, a successfully completed discharge would leave
            # the relay closed indefinitely -- found during review, see
            # docs/architecture.md Section 37.
            self.relay.open(relay_address)
            self.storage.log_event(
                level="INFO", source="discharge_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} deactivated -- discharge complete",
            )
            # Post-isolation defense-in-depth, not safety-critical -- the
            # battery is already isolated by the relay.open() above, so
            # this cannot affect it either way. Wrapped defensively even
            # though zero_output_setpoint_best_effort() itself never
            # raises -- this step must never prevent a completed discharge
            # from returning normally. See docs/architecture.md
            # "Post-Isolation SMU Setpoint Zeroing".
            try:
                self.smu.zero_output_setpoint_best_effort(f"end of discharge sequence on channel {channel}")
            except Exception as e:
                self.log.warning("Channel %d: post-isolation setpoint-zeroing raised "
                                  "unexpectedly (non-critical): %s", channel, e)
            return True

        completed = self.run_guarded(
            _run_discharge, channel=channel, relay_address=relay_address,
            label="Discharge Battery", verb="discharging",
            cancel_message="Discharging stopped by operator",
            extra_run_summary_fields_fn=_diagnostic_fields,
        )
        self.complete(
            channel=channel, relay_address=relay_address,
            log_message=f"Discharge complete on channel {channel} (EOD reached)",
            **_diagnostic_fields(),
        )
        return completed
