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

Temperature: `ntc_channel` (this position's own BATTERY_GROUPS[group]
["positions"][...]["daq_ntc_ch"], resolved by the caller), read via
`self.daq` --
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

Post-Run Diagnostic Classification (Test Mode only, informational --
see test_control/battery_diagnostics.py): a pre-test Battery Presence +
NTC Presence check (test_control/battery_presence_precheck.py, called by
test.py before this sequence is ever constructed) now blocks a run from
starting at all against an empty/absent position -- but an empty position
that passes that check (e.g. a battery removed AFTER the pre-check, or a
position with no NTC hardware assigned to it) can still look deceptively
like "already charged" once running (SMU hits CV compliance, current near
zero, EOC trips almost instantly). A BatteryOperationSequence.
_ChargeDischargeStats accumulator (fed the exact voltage_v/current_a this
loop already computes -- no new read) is classified into `analysis_result`
(ALREADY_CHARGED/POSSIBLY_EMPTY_POSITION/NORMAL_CHARGE_BEHAVIOR) and
folded into run_summary via the existing `extra_run_summary_fields_fn`/
`complete()` mechanism -- purely additive, never touches stop_reason/
result. This classifier only catches an empty position from the very
START of the run (see its own SHORT_DURATION_S-gated logic) -- it does
NOT catch a battery that charged normally for some time and was then
physically removed partway through. That distinct case is what Battery
Removal During Charge Detection (below) exists to catch instead.

Battery Removal During Charge Detection (see test_control/
battery_diagnostics.py::charge_transition_suggests_battery_removed() and
docs/architecture.md "Battery Removal During Charge Detection"): unlike
the post-run classifier above, this runs INSIDE the sampling loop, on
every sample, and DOES gate -- it is what prevents a battery removed
mid-charge from ever being reported as a clean, passing EOC. A genuine
cell's CC->CV transition and current taper both take many sample
intervals; if the sample immediately before an EOC-satisfying sample was
still clearly in the CC phase (current near the full commanded value),
that one-sample jump is not physically achievable by a real, connected
cell. When detected, raises utils.errors.BatteryRemovedDuringChargeError
(a SafetyViolationError subclass) instead of accepting the sample as EOC
-- routed through the existing run_guarded()/SafetyMonitor.
emergency_stop() shutdown path unchanged, reported as
StopReason.SAFETY_VIOLATION/result="FAIL", never "PASS". Discharge does
not need an equivalent -- see discharge_sequence.py's module docstring for
why DischargeSequence was already safe against this scenario.
"""

import time

import config.devices as dev_cfg
from config.settings import Settings
from hardware.temperature import NTCPresence, classify_ntc_presence, ntc_voltage_to_celsius
from test_control.battery_diagnostics import charge_transition_suggests_battery_removed
from test_control.battery_operation_sequence import BatteryOperationSequence, _ChargeDischargeStats
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep
from utils.errors import (
    BatteryRemovedDuringChargeError, DAQError, DMMMeasurementLostError, NIPXITimeoutError,
    SafetyViolationError, ReversePolarityError,
)
from utils.event_format import EventType, format_event


class ChargeSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings, daq=None,
                 group_name=None, ntc_daq_name=None, sense_router=None, sense_channel=None):
        # `daq` -- this group's NTC/temperature DAQ (see docs/architecture.md
        # "Dual DAQ Ownership Model"); DMM + SMU.measure() remain the active
        # voltage/current telemetry source (see module docstring), `daq` is
        # read only for the NTC channel in run()'s sampling loop.
        # `ntc_daq_name` (optional -- config/devices.py::hardware_for_group()'s
        # resolved "ntc_daq_name", e.g. "MAIN_DAQ") is display-only: the
        # operator-facing NTC block (see docs/architecture.md Section 58)
        # needs the device's own nickname, which `daq` (the connected driver
        # instance) does not carry itself -- only its `resource` attribute
        # does.
        # `sense_router`/`sense_channel` -- FUTURE PLANNED ARCHITECTURE, see
        # docs/architecture.md "Future Architecture: Battery Sense Routing".
        # Both default to None, exactly reproducing today's direct-DMM-wiring
        # behavior for every group that does not configure a sense_channel
        # (every group today). `sense_channel` is
        # config/devices.py::hardware_for_group()'s resolved
        # "sense_channel" (an int or None); `sense_router` is a
        # hardware/sense_router.py::SenseRouter instance, required only when
        # `sense_channel` is not None.
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="charge_battery", dmm=dmm, daq=daq, group_name=group_name,
                          sense_router=sense_router, sense_channel=sense_channel)
        self.ntc_daq_name = ntc_daq_name

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
        BATTERY_GROUPS[group]["positions"][...]["daq_ntc_ch"]) enables real
        temperature acquisition into the existing safety check; omitted, temperature
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

        # Test Mode diagnostic classification ONLY (see
        # test_control/battery_diagnostics.py) -- accumulates the SAME
        # voltage_v/current_a samples the loop below already computes and
        # records, never a second read. Timed from just before the relay
        # closes so `duration_s` reflects however the run actually ended
        # (EOC, cancelled, safety violation, timeout), not just the EOC path.
        stats = _ChargeDischargeStats()
        run_start_time = time.monotonic()

        def _diagnostic_fields():
            return self._charge_diagnostic_fields(
                stats, commanded_current_a=current_a, battery_cfg=battery_cfg,
                duration_s=time.monotonic() - run_start_time,
            )

        def _run_charge():
            check_cancellation(token)
            self.relay.close(relay_address)
            self.storage.log_event(
                level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} activated -- charging started "
                        f"({current_a:.3f} A / {voltage_limit_v:.3f} V CV target)",
            )
            self.storage.log_event(
                level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                message=format_event(
                    EventType.RELAY_CLOSE, relay_matrix_name=self.relay.name, relay_address=relay_address,
                ),
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state="ACTIVE")

            # Sense-channel connect: FUTURE PLANNED ARCHITECTURE, see docs/
            # architecture.md "Future Architecture: Battery Sense Routing".
            # None for every group today -- a pure no-op, unchanged from
            # before this was added. When configured, connects ONCE for the
            # whole operation (mirroring self.relay.close()/open() above --
            # never toggled per-sample, to avoid needless relay-cycle wear)
            # and is guaranteed to disconnect via the try/finally below on
            # EVERY exit path, including a ReversePolarityError raised
            # before the SMU's own try/finally (further down) even starts.
            # NumatoSenseRouter.connect() already includes RelayBase's own
            # settle-time wait (hardware/relay.py) -- no additional delay
            # needed here.
            if self.sense_channel is not None:
                self.sense_router.connect(self.sense_channel)
                sense_route = dev_cfg.SENSE_ROUTING.get(self.sense_channel, {})
                self.storage.log_event(
                    level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                    message=format_event(
                        EventType.MATRIX_ROUTE_APPLIED,
                        matrix_name=sense_route.get("relay_matrix"), matrix_channel=sense_route.get("relay"),
                        source="DMM", destination=f"channel_{self.sense_channel}",
                    ),
                )
            try:
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

                # Captured BEFORE _check_battery_polarity() below, deliberately --
                # this value must always be persisted (see REQUIREMENT 1 in
                # docs/architecture.md's Start/End Voltage Persistence section),
                # including on a ReversePolarityError raised by that very check.
                # Capturing it only afterward (the previous ordering) meant a
                # rejected polarity reading was silently lost -- the one
                # measurement that would have explained the rejection never made
                # it into run_summary/analysis_result. Taken with the SMU output
                # still disabled, so it reflects whatever is actually connected
                # (a real cell's resting voltage, or an empty position's near-0V
                # open circuit). The sampling loop's own first sample is NOT
                # used for this: by then the SMU has already been sourcing
                # current for STABILIZATION_S, so an empty position would
                # already read near the CV compliance target too --
                # indistinguishable from a genuinely full battery at that point.
                # Reuses pre_enable_v itself, no new read.
                stats.initial_voltage_v = pre_enable_v

                self._check_battery_polarity(pre_enable_v, channel=channel, relay_address=relay_address)

                self.smu.set_charge_mode(current_a=current_a, voltage_limit_v=voltage_limit_v)
                self.smu.output_enable()
                self.storage.log_event(
                    level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                    message=format_event(
                        EventType.SMU_OUTPUT_ENABLED, device=self.smu.model,
                        resource=self.smu.resource, channel=channel,
                    ),
                )

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
                    # Per-group validation override (see docs/architecture.md
                    # "Configurable Validation Timeout") -- defaults to the
                    # unchanged global Settings.CHARGE_TIMEOUT_S when absent, so
                    # every group not explicitly opting in behaves exactly as
                    # before. Read once here, not re-read every iteration:
                    # test_setpoints is caller-owned and must not change mid-run.
                    # Already validated (positive, <= MAX_TIMEOUT_OVERRIDE_S, and
                    # refused outright in PRODUCTION mode) by
                    # utils/validators.py::validate_group_test_config() before
                    # this sequence was ever constructed -- trusted here exactly
                    # like every other test_setpoints value already is.
                    charge_timeout_s = test_setpoints.get("charge_timeout_s", self.s.CHARGE_TIMEOUT_S)
                    # Display-only -- shown on the execution screen alongside
                    # charge_timeout_s above (see test_control/
                    # execution_screen.py) so an operator can see BOTH active
                    # timeout setpoints regardless of which operation is
                    # running, without checking config/devices.py. Never
                    # used for any timeout/EOC/EOD decision in this sequence.
                    discharge_timeout_s = test_setpoints.get("discharge_timeout_s", self.s.DISCHARGE_TIMEOUT_S)

                    # Battery Removal During Charge Detection (see
                    # test_control/battery_diagnostics.py::
                    # charge_transition_suggests_battery_removed() and
                    # docs/architecture.md "Battery Removal During Charge
                    # Detection") -- tracks the immediately preceding
                    # sample so an abrupt CC->EOC transition (physically
                    # impossible for a real cell's gradual CV taper) can be
                    # distinguished from a genuine, passing end-of-charge.
                    # None on the first iteration -- see that function's
                    # docstring for why this is a deliberate, disclosed
                    # scope boundary, not an oversight.
                    prev_v = None
                    prev_i = None

                    # Standardized Hardware Event Logging -- DMM_MEASUREMENT_
                    # FAILED/_RECOVERED (see docs/architecture.md). DMM is the
                    # authoritative voltage source for EOC/safety.check(), so
                    # a read failure cannot be tolerated indefinitely (unlike
                    # an NTC read failure, just above/below, which only
                    # degrades temperature monitoring) -- but a single
                    # transient comms glitch gets a bounded number of
                    # consecutive attempts to resolve before the run aborts.
                    consecutive_dmm_failures = 0

                    while True:
                        check_cancellation(token)

                        elapsed = time.monotonic() - t_start
                        if elapsed > charge_timeout_s:
                            raise NIPXITimeoutError(
                                f"Channel {channel}: charge timeout after {elapsed:.0f}s (EOC not reached)"
                            )

                        # Telemetry: DMM for voltage (independent, already-
                        # validated -- mirrors Monitor Battery), SMU's own ADC
                        # readback for current (the only real current signal
                        # available without DAQ). See module docstring
                        # "Telemetry Source Strategy".
                        smu_reading = self.smu.measure()
                        try:
                            dmm_v = self.dmm.measure_dc_voltage()
                        except Exception as e:
                            consecutive_dmm_failures += 1
                            self.storage.log_event(
                                level="WARNING", source="charge_battery", channel=channel, relay=relay_address,
                                message=format_event(
                                    EventType.DMM_MEASUREMENT_FAILED, device=self.dmm.model,
                                    resource=self.dmm.resource, attempt=consecutive_dmm_failures,
                                    max_attempts=self.s.DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES,
                                    error=str(e),
                                ),
                            )
                            if consecutive_dmm_failures >= self.s.DMM_MEASUREMENT_MAX_CONSECUTIVE_FAILURES:
                                raise DMMMeasurementLostError(
                                    f"Channel {channel}: DMM measurement failed "
                                    f"{consecutive_dmm_failures} consecutive times -- {e}"
                                ) from e
                            interruptible_sleep(dt, token=token)
                            continue
                        if consecutive_dmm_failures > 0:
                            self.storage.log_event(
                                level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                                message=format_event(
                                    EventType.DMM_MEASUREMENT_RECOVERED, device=self.dmm.model,
                                    resource=self.dmm.resource, after_failures=consecutive_dmm_failures,
                                ),
                            )
                            consecutive_dmm_failures = 0
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

                        self._record_measurement(
                            position_in_group=channel,
                            test_type="charge", channel=channel, relay=relay_address,
                            phase_detail="CC_CV", voltage_v=v, current_a=i, temp_c=t_c,
                            smu_measured_v=smu_reading["voltage_v"], smu_measured_i=i,
                            dmm_measured_v=dmm_v,
                        )
                        self._render_frame(
                            test_type="charge", channel=channel, relay_address=relay_address,
                            run_number=run_number, state="ACTIVE", phase_detail="CC_CV",
                            elapsed_s=time.monotonic() - run_start_time,
                            smu_voltage=smu_reading["voltage_v"], smu_current=i, dmm_voltage=dmm_v,
                            battery_voltage=v, battery_current=i, battery_temp=t_c,
                            charge_timeout_s=charge_timeout_s, discharge_timeout_s=discharge_timeout_s,
                            ntc_device=self.ntc_daq_name, ntc_resource=getattr(self.daq, "resource", None),
                            ntc_channel=ntc_channel, ntc_status=presence,
                        )

                        # End of charge: CV taper -- voltage at/above the CV
                        # target and current tapered at/below the cutoff.
                        # Harvested unchanged from ChargeCycle.run() (see
                        # docs/architecture.md Section 33) -- EXCEPT this
                        # sample is no longer accepted at face value: see
                        # "Battery Removal During Charge Detection" above.
                        # A genuine CV taper reaching this point gradually,
                        # over many prior samples, is accepted normally; an
                        # abrupt one-sample jump from still-CC-phase current
                        # is treated as a safety violation instead of a pass.
                        if v >= voltage_limit_v and abs(i) <= self.s.CHARGE_CUTOFF_A:
                            if charge_transition_suggests_battery_removed(
                                prev_v=prev_v, prev_i=prev_i, v=v, i=i,
                                current_a=current_a, voltage_limit_v=voltage_limit_v,
                                cutoff_a=self.s.CHARGE_CUTOFF_A,
                            ):
                                message = (
                                    f"Channel {channel}: abrupt CC->EOC transition detected "
                                    f"(previous sample {prev_i:+.4f} A / {prev_v:.3f} V -> "
                                    f"current sample {i:+.4f} A / {v:.3f} V) -- consistent with "
                                    f"the battery being physically removed while charging, not a "
                                    f"genuine CV taper reaching completion. Treating as a safety "
                                    f"violation, not end-of-charge."
                                )
                                self.log.error(message)
                                self.storage.log_event(
                                    level="ERROR", source="charge_battery", channel=channel,
                                    relay=relay_address, message=message,
                                )
                                raise BatteryRemovedDuringChargeError(message)
                            self.log.info("Charge complete on channel %d (V=%.3f, I=%.4f)", channel, v, i)
                            break

                        prev_v, prev_i = v, i
                        interruptible_sleep(dt, token=token)
                finally:
                    on_event = self._shutdown_trace_logger(channel=channel, relay_address=relay_address)
                    smu_disabled_ok = self.smu.emergency_output_off(
                        f"end of charge sequence on channel {channel}", on_event=on_event,
                    )
                    if not smu_disabled_ok:
                        self.log.critical(
                            "Channel %d: PMU output could not be verified OFF after charge sequence.",
                            channel,
                        )
                    self.storage.log_event(
                        level="INFO" if smu_disabled_ok else "CRITICAL",
                        source="charge_battery", channel=channel, relay=relay_address,
                        message=format_event(
                            EventType.SMU_OUTPUT_DISABLED, device=self.smu.model,
                            resource=self.smu.resource, channel=channel,
                            verified=smu_disabled_ok,
                        ),
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
                self.storage.log_event(
                    level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                    message=format_event(
                        EventType.RELAY_OPEN, relay_matrix_name=self.relay.name, relay_address=relay_address,
                    ),
                )
                # Post-isolation defense-in-depth, not safety-critical -- the
                # battery is already isolated by the relay.open() above, so
                # this cannot affect it either way. Wrapped defensively even
                # though zero_output_setpoint_best_effort() itself never
                # raises -- this step must never prevent a completed charge
                # from returning normally. See docs/architecture.md
                # "Post-Isolation SMU Setpoint Zeroing".
                try:
                    self.smu.zero_output_setpoint_best_effort(
                        f"end of charge sequence on channel {channel}", on_event=on_event,
                    )
                except Exception as e:
                    self.log.warning("Channel %d: post-isolation setpoint-zeroing raised "
                                      "unexpectedly (non-critical): %s", channel, e)
                return True
            finally:
                if self.sense_channel is not None:
                    try:
                        self.sense_router.disconnect(self.sense_channel)
                        sense_route = dev_cfg.SENSE_ROUTING.get(self.sense_channel, {})
                        self.storage.log_event(
                            level="INFO", source="charge_battery", channel=channel, relay=relay_address,
                            message=format_event(
                                EventType.MATRIX_ROUTE_CLEARED,
                                matrix_name=sense_route.get("relay_matrix"), matrix_channel=sense_route.get("relay"),
                                source="DMM", destination=f"channel_{self.sense_channel}",
                            ),
                        )
                    except Exception as e:
                        self.log.warning(
                            "Channel %d: sense-channel disconnect raised unexpectedly "
                            "(non-critical): %s", channel, e,
                        )

        completed = self.run_guarded(
            _run_charge, channel=channel, relay_address=relay_address,
            label="Charge Battery", verb="charging",
            cancel_message="Charging stopped by operator",
            extra_run_summary_fields_fn=_diagnostic_fields,
        )
        self.complete(
            channel=channel, relay_address=relay_address,
            log_message=f"Charge complete on channel {channel} (EOC reached)",
            **_diagnostic_fields(),
        )
        return completed
