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

Temperature remains None -- NTC is not wired into this sequence, the same
pre-existing gap DischargeCycle/ChargeCycle/MonitorBatterySequence all
carry (SafetyMonitor.check() tolerates temp_c=None).

Timeout: a discharge timeout here raises NIPXITimeoutError, classified as
StopReason.TIMEOUT (not the generic FAILED) -- see charge_sequence.py's
module docstring for the identical rationale, and docs/architecture.md
"Timeout Traceability".

Reverse Polarity Protection: see charge_sequence.py's module docstring --
identical pre-output-enable DMM sanity check here, before
set_discharge_mode()/output_enable().
"""

import time

from config.settings import Settings
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import NIPXITimeoutError, SafetyViolationError, ReversePolarityError


class DischargeSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings, daq=None):
        # `daq` (optional, default None) -- see charge_sequence.py::
        # ChargeSequence.__init__()'s identical comment. Not read anywhere
        # in this class today.
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="discharge_battery", dmm=dmm, daq=daq)

    def run(self, channel: int, relay_address: int, battery_cfg: dict,
            test_setpoints: dict, token=None) -> bool:
        """
        Run one complete CC discharge on `channel`/`relay_address`.

        `battery_cfg` (a config/devices.py BATTERY_CONFIGS[...] entry --
        REQUIRED) supplies only the SafetyMonitor's absolute safety floor --
        never the commanded setpoint (see module docstring). `test_setpoints`
        (a config/devices.py BATTERY_GROUPS[group]["test_setpoints"] entry --
        REQUIRED, already validated by the caller via utils/validators.py::
        validate_group_test_config()) supplies the actual commanded
        discharge current and target cutoff.

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
            interruptible_sleep(self.s.STABILIZATION_S, token=token)
            pre_enable_v = self.dmm.measure_dc_voltage()
            self._check_battery_polarity(pre_enable_v, channel=channel, relay_address=relay_address)

            self.smu.set_discharge_mode(current_a=current_a, voltage_limit_v=compliance_voltage_v)
            self.smu.output_enable()

            try:
                interruptible_sleep(self.s.STABILIZATION_S, token=token)

                t_start = time.monotonic()
                dt = 1.0 / self.s.SAMPLE_RATE_HZ

                while True:
                    check_cancellation(token)

                    elapsed = time.monotonic() - t_start
                    if elapsed > self.s.DISCHARGE_TIMEOUT_S:
                        raise NIPXITimeoutError(
                            f"Channel {channel}: discharge timeout after {elapsed:.0f}s (EOD not reached)"
                        )

                    # Telemetry: DMM for voltage, SMU's own ADC readback for
                    # current -- see charge_sequence.py's identical rationale.
                    smu_reading = self.smu.measure()
                    dmm_v = self.dmm.measure_dc_voltage()
                    v = dmm_v
                    i = smu_reading["current_a"]
                    t_c = None  # NTC not wired in -- see module docstring

                    status = self.safety.check(v, i, t_c, mode="discharge")
                    if not status.safe:
                        raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                    self.storage.record_measurement(
                        test_type="discharge", channel=channel, relay=relay_address,
                        phase_detail="CC_DISCHARGE", voltage_v=v, current_a=i, temp_c=t_c,
                        smu_measured_v=smu_reading["voltage_v"], smu_measured_i=i,
                        dmm_measured_v=dmm_v,
                    )
                    self._render_frame(
                        test_type="discharge", channel=channel, relay_address=relay_address,
                        run_number=run_number, state="ACTIVE", phase_detail="CC_DISCHARGE",
                        smu_voltage=smu_reading["voltage_v"], smu_current=i, dmm_voltage=dmm_v,
                        battery_voltage=v, battery_current=i, battery_temp=t_c,
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
            return True

        completed = self.run_guarded(
            _run_discharge, channel=channel, relay_address=relay_address,
            label="Discharge Battery", verb="discharging",
            cancel_message="Discharging stopped by operator",
        )
        self.complete(
            channel=channel, relay_address=relay_address,
            log_message=f"Discharge complete on channel {channel} (EOD reached)",
        )
        return completed
