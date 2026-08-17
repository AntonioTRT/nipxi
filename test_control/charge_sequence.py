"""
Charge Sequence -- built on BatteryOperationSequence
(test_control/battery_operation_sequence.py), the target execution
architecture for battery charge/discharge/cycle work (see
docs/architecture.md Section 35 "Revised Roadmap"). This is the official
path forward for Charge Battery -- not a second workflow architecture
alongside TestExecutor/BatteryTestSequence/ChargeCycle.

Harvested from test_control/charge_cycle.py::ChargeCycle (see
docs/architecture.md Section 33 "ChargeCycle / DischargeCycle Harvest
Plan"), KEPT/MIGRATED unchanged in shape:
    - EOC logic: v >= voltage_limit_v and abs(i) <= CHARGE_CUTOFF_A
      (CV-taper current threshold, a single combined per-sample check).
    - PSU sequencing: set_charge_mode() -> output_enable() -> sampling
      loop -> emergency_output_off() in a try/finally that starts
      immediately after output_enable() (covers the stabilization wait,
      per the fix in docs/architecture.md Section 27).
    - battery_cfg setpoint resolution + SafetyMonitor.set_battery_limits()
      + mode="charge" passed into every safety.check() call.

NOT carried forward from ChargeCycle (see the same Harvest Plan section):
    - `daq.read_all_batteries()` telemetry -- DAQ.read_all_batteries() is
      still a stub and channel mapping is unapproved (docs/architecture.md
      Section 31 "Telemetry Source Strategy"). This sequence uses the DMM
      for voltage (mirroring Monitor Battery) and the SMU's own measure()
      for current (the only real current signal available without DAQ).
      Charge Battery development must not be blocked on DAQ mapping work.
    - TestExecutor/BatteryTestSequence construction/orchestration -- this
      class is constructed directly by test.py (via hardware_for_group()
      + HardwareManager), exactly as MonitorBatterySequence is today, and
      is independently invokable (not hardwired to run discharge
      immediately afterward -- that composition belongs in a future
      CycleSequence, not here).
    - The raw `data_collector.record()` write path -- replaced by
      DataStorage.record_measurement()/BatteryOperationSequence.
      _render_frame(), the same Milestone II schema/UI Monitor Battery uses.

`battery_cfg` is a REQUIRED parameter -- there is no ChargeCycle-style
battery_cfg=None fallback here. Battery type is NEVER operator input --
it is derived entirely from the selected group's own engineering
configuration (config/devices.py::BATTERY_GROUPS[group]["battery_type"])
by the caller (test.py, via utils/validators.py::
validate_group_test_config()) before this class is ever constructed. See
docs/architecture.md Section 40 "Architectural Correction: Battery Type
Is Never Operator Input".

`battery_cfg` (BATTERY_CONFIGS[type]) vs. `test_setpoints`
(BATTERY_GROUPS[group]["test_setpoints"]) -- these are two different
things, per the Battery Group Test Configuration Architecture (see
docs/architecture.md): `battery_cfg` is the battery's own absolute SAFETY
LIMIT, used only to configure SafetyMonitor's runtime enforcement (never
as the commanded setpoint). `test_setpoints` is the CHOSEN operating point
for this run -- the actual commanded current/voltage -- already validated
(by utils/validators.py::validate_group_test_config(), called by the
caller before this class is ever constructed) to not exceed either the
battery's limit or the assigned SMU's capability. This class trusts that
validation already happened; it does not re-validate `test_setpoints`
itself.

Temperature: `ntc_channel` (this position's own BATTERY_CHANNELS[...]
["daq_ntc_ch"], resolved by the caller), read via `self.daq` --
HardwareManager's "ntc_daq" role (see docs/architecture.md "Dual DAQ
Ownership Model"), NOT the general DAQ telemetry this class still doesn't
use for voltage/current. A reading classified PRESENT
(hardware/temperature.py::classify_ntc_presence()) is converted to
Celsius and fed into the SAME safety.check() call below that already
enforces voltage/current -- temp_c was always a real, checked parameter
of check(), just never supplied a non-None value until now. `ntc_channel`
omitted (or no `daq` configured for this group) preserves prior behavior
exactly: temp_c stays None, check() never evaluates the overtemperature
branch.

Timeout: a charge timeout here raises NIPXITimeoutError, classified as
StopReason.TIMEOUT (not the generic FAILED) in run_summary/event_log/
final status reporting -- see docs/architecture.md "Timeout Traceability".

Reverse Polarity Protection (docs/architecture.md "Reverse Polarity
Protection"): after the relay closes and settles, but before
set_charge_mode()/output_enable() ever run, a DMM reading is taken with
the SMU output still disabled and checked against
Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V (see
BatteryOperationSequence._check_battery_polarity()). A reading at/below
that threshold raises ReversePolarityError -- a SafetyViolationError
subclass -- before the SMU is ever enabled.
"""

import time

from config.settings import Settings
from hardware.temperature import NTCPresence, classify_ntc_presence, ntc_voltage_to_celsius
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import DAQError, NIPXITimeoutError, SafetyViolationError, ReversePolarityError


class ChargeSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings, daq=None):
        # `daq` (optional, default None) -- accepted and stored now so a
        # future DAQ integration (Section 31 "Telemetry Source Strategy",
        # deliberately deferred) only needs to change the two telemetry
        # lines inside run()'s sampling loop, not this constructor or any
        # caller that doesn't pass one. Not read anywhere in this class
        # today -- DMM + SMU.measure() remain the active telemetry source.
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="charge_battery", dmm=dmm, daq=daq)

    def run(self, channel: int, relay_address: int, battery_cfg: dict,
            test_setpoints: dict, ntc_channel: str = None, token=None) -> bool:
        """
        Run one complete CC-CV charge on `channel`/`relay_address`.

        `battery_cfg` (a config/devices.py BATTERY_CONFIGS[...] entry --
        REQUIRED) supplies only the SafetyMonitor's absolute safety limits
        -- it is never read for the commanded setpoint (see module
        docstring). `test_setpoints` (a config/devices.py
        BATTERY_GROUPS[group]["test_setpoints"] entry -- REQUIRED, already
        validated by the caller via utils/validators.py::
        validate_group_test_config()) supplies the actual commanded
        current/CV voltage. `ntc_channel` (optional -- this position's
        BATTERY_CHANNELS[...]["daq_ntc_ch"]) enables real temperature
        acquisition into the existing safety check; omitted, temperature
        stays "N/A" exactly as before.

        Returns True once EOC is reached. Raises SafetyViolationError,
        RelayError, NIPXITimeoutError, or OperationCancelledError on any
        abnormal exit -- all handled by run_guarded() (relay close,
        traceability, and safety shutdown are its responsibility, not this
        method's own).
        """
        self.log.info("Charge Sequence starting. Channel: %d  Relay: %d", channel, relay_address)
        run_number = self._run_number()

        current_a = test_setpoints["charge_current_a"]
        voltage_limit_v = test_setpoints["charge_voltage_v"]
        self.safety.set_battery_limits(battery_cfg)
        last_ntc_state = None  # throttles repeated NTC-fault/absent event_log noise to one entry per transition

        def _run_charge():
            check_cancellation(token)
            self.relay.close(relay_address)
            self.storage.log_event(
                level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} activated -- charging started "
                        f"({current_a:.3f} A / {voltage_limit_v:.3f} V CV target)",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state="ACTIVE")

            # Pre-output-enable reverse-polarity sanity check -- Relay
            # Selection -> Battery Voltage Measurement (DMM) -> Sanity
            # Validation -> SMU Enable. The SMU output is still disabled at
            # this point (HardwareManager.connect_all() leaves it OFF, and
            # set_charge_mode()/output_enable() have not been called yet) --
            # see BatteryOperationSequence._check_battery_polarity() and
            # docs/architecture.md "Reverse Polarity Protection".
            # No separate relay-settle sleep here -- self.relay.close() above
            # already blocked for Settings.RELAY_SETTLE_TIME_S (the single
            # global relay settling/dead-time constant, enforced in
            # RelayBase.open()/close(), hardware/relay.py) before returning.
            pre_enable_v = self.dmm.measure_dc_voltage()
            self._check_battery_polarity(pre_enable_v, channel=channel, relay_address=relay_address)

            self.smu.set_charge_mode(current_a=current_a, voltage_limit_v=voltage_limit_v)
            self.smu.output_enable()

            # try/finally starts immediately after output_enable(), covering
            # the stabilization wait AND the sampling loop -- see
            # docs/architecture.md Section 27 "Interruptible Wait Mechanism"
            # (the exact latent-bug shape ChargeCycle's own fix avoided,
            # preserved here unchanged).
            nonlocal last_ntc_state
            try:
                interruptible_sleep(self.s.STABILIZATION_S, token=token)

                t_start = time.monotonic()
                dt = 1.0 / self.s.SAMPLE_RATE_HZ

                while True:
                    check_cancellation(token)

                    elapsed = time.monotonic() - t_start
                    if elapsed > self.s.CHARGE_TIMEOUT_S:
                        raise NIPXITimeoutError(
                            f"Channel {channel}: charge timeout after {elapsed:.0f}s (EOC not reached)"
                        )

                    # Telemetry: DMM for voltage (independent, already-
                    # validated -- mirrors Monitor Battery), SMU's own ADC
                    # readback for current (the only real current signal
                    # available without DAQ). See module docstring
                    # "Telemetry Source Strategy".
                    smu_reading = self.smu.measure()
                    dmm_v = self.dmm.measure_dc_voltage()
                    v = dmm_v
                    i = smu_reading["current_a"]

                    t_c = None
                    if self.daq is not None and ntc_channel is not None:
                        try:
                            ntc_v = self.daq.read_channel(ntc_channel)
                            presence = classify_ntc_presence(ntc_v)
                            if presence == NTCPresence.PRESENT:
                                t_c = ntc_voltage_to_celsius(ntc_v)
                            elif presence != last_ntc_state:
                                self.storage.log_event(
                                    level="WARNING", source="charge_battery",
                                    channel=channel, relay=relay_address,
                                    message=f"NTC reading {presence} -- temperature monitoring degraded",
                                )
                            last_ntc_state = presence
                        except DAQError as e:
                            if last_ntc_state != "fault":
                                self.storage.log_event(
                                    level="WARNING", source="charge_battery",
                                    channel=channel, relay=relay_address,
                                    message=f"NTC read failed -- {e}",
                                )
                            last_ntc_state = "fault"

                    status = self.safety.check(v, i, t_c, mode="charge")
                    if not status.safe:
                        raise SafetyViolationError(f"Channel {channel}: {status.reason}")

                    self.storage.record_measurement(
                        test_type="charge", channel=channel, relay=relay_address,
                        phase_detail="CC_CV", voltage_v=v, current_a=i, temp_c=t_c,
                        smu_measured_v=smu_reading["voltage_v"], smu_measured_i=i,
                        dmm_measured_v=dmm_v,
                    )
                    self._render_frame(
                        test_type="charge", channel=channel, relay_address=relay_address,
                        run_number=run_number, state="ACTIVE", phase_detail="CC_CV",
                        smu_voltage=smu_reading["voltage_v"], smu_current=i, dmm_voltage=dmm_v,
                        battery_voltage=v, battery_current=i, battery_temp=t_c,
                    )

                    # End of charge: CV taper -- voltage at/above the CV
                    # target and current tapered at/below the cutoff.
                    # Harvested unchanged from ChargeCycle.run() (see
                    # docs/architecture.md Section 33).
                    if v >= voltage_limit_v and abs(i) <= self.s.CHARGE_CUTOFF_A:
                        self.log.info("Charge complete on channel %d (V=%.3f, I=%.4f)", channel, v, i)
                        break

                    interruptible_sleep(dt, token=token)
            finally:
                if not self.smu.emergency_output_off(f"end of charge sequence on channel {channel}"):
                    self.log.critical(
                        "Channel %d: PMU output could not be verified OFF after charge sequence.",
                        channel,
                    )

            # Relay open only AFTER the PMU output is confirmed off (the
            # finally above) -- never switch a relay while current might
            # still be flowing. The only way execution reaches here without
            # having raised is EOC (the `break` above) -- every other exit
            # raises and is handled by run_guarded()'s safety shutdown,
            # which already force-opens every relay. Without this, a
            # successfully completed charge would leave the relay closed
            # indefinitely -- found during review, see docs/architecture.md
            # Section 37.
            self.relay.open(relay_address)
            self.storage.log_event(
                level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} deactivated -- charge complete",
            )
            return True

        completed = self.run_guarded(
            _run_charge, channel=channel, relay_address=relay_address,
            label="Charge Battery", verb="charging",
            cancel_message="Charging stopped by operator",
        )
        self.complete(
            channel=channel, relay_address=relay_address,
            log_message=f"Charge complete on channel {channel} (EOC reached)",
        )
        return completed
