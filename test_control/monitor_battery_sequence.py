"""
Monitor Battery sequence -- Run Main Test's first battery-centric mode
(Milestone II). Read-only battery monitoring: NO charging, NO discharging.

Built on test_control/battery_operation_sequence.py::BatteryOperationSequence,
which owns the relay/ExecutionFrame/DataStorage/SafetyMonitor/cancellation
skeleton shared with MonitorBatteryScanSequence (and, going forward,
Charge/Discharge/Cycle Battery) -- this file supplies only Monitor Battery's
own sampling loop on top of it, not a parallel implementation of that
skeleton.

TEMPORARY IMPLEMENTATION -- voltage source is the DMM, not the DAQ:
    The original DAQ-per-channel voltage read (hardware/daq.py::
    DAQ.read_channel()) failed during real-hardware validation due to
    channel/device configuration issues that require further NI-MAX/wiring
    work to resolve. To validate the Milestone II architecture end-to-end
    on real hardware now, this sequence instead takes one DMM voltage
    reading per monitoring iteration (hardware/dmm.py::
    DMM.measure_dc_voltage(), the same fully-verified call
    ProtoTestSequence already uses) -- the DMM is already validated and
    available, and basic voltage-only monitoring is sufficient for this
    development phase.

    TODO (future): Charge/Discharge/Cycle Battery workflows -- and Monitor
    Battery itself, once channel mapping and hardware integration are
    completed -- must migrate battery telemetry acquisition to the final
    DAQ-based architecture (per-position voltage/current/NTC channels via
    BATTERY_CHANNELS' daq_voltage_ch/daq_current_ch/daq_ntc_ch), not stay on
    a single shared DMM. See docs/architecture.md Section 20 ("Temporary
    DMM-based monitoring" / "Future DAQ-based battery telemetry").

Per relay/channel:
    1. relay.close(channel) -- reuses hardware/relay_eth.py's mandatory
       force-all-off -> verify -> activate -> verify-single -> verify-all
       sequence unchanged, exactly as ProtoTestSequence uses it.
    2. dmm.measure_dc_voltage() -- reuses hardware/dmm.py's already-real,
       fully-verified single measurement (current/NTC temperature remain
       None -- the DMM measures voltage only; this is a temporary,
       documented limitation, not a silent gap). No SMU involvement at
       all -- this mode never sources or sinks current.
    3. storage.record_measurement(test_type="monitor", ...) -- persists
       into `measurements`, reusing the ORIGINAL voltage_v/current_a/
       temp_c columns (the same ones charge/discharge cycles already
       write), not the SMU/DMM-specific columns Proto Test populates.
    4. ExecutionFrame.from_live()/render_execution_frame() (via
       BatteryOperationSequence._render_frame()) -- the same shared
       renderer, using its battery_voltage/battery_current/battery_temp
       fields (added alongside the existing smu_*/dmm_* fields specifically
       for this DAQ-only-in-the-final-architecture, no-sourcing case).
    5. Running voltage statistics (start/end/min/max/average/sample count)
       are tracked in-memory across the loop and written to `run_summary`
       via finish_run_summary() on every exit path -- the same "one row per
       run" record Proto Test already populates, not a new mechanism.

Safety: no battery-limit logic lives here -- SafetyMonitor remains the
sole owner of limit/abort decisions. smu.emergency_output_off() is still
called on every exit path (via safety.emergency_stop()/safe_cancel_shutdown(),
identical to ProtoTestSequence) even though this mode never sources
anything through the SMU -- a cheap, idempotent no-op that keeps the same
single safety-shutdown entry point for every mode, rather than a
Monitor-specific relay-only shutdown path.
"""

from config.settings import Settings
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep


class _VoltageStats:
    """Running start/end/min/max/average/count over a monitoring session --
    plain in-memory accumulation, no new storage mechanism. Used to populate
    run_summary's start_voltage/end_voltage/min_voltage/max_voltage/
    average_voltage/sample_count columns on every exit path."""

    def __init__(self):
        self.start_voltage = None
        self.end_voltage = None
        self.min_voltage = None
        self.max_voltage = None
        self._sum = 0.0
        self.sample_count = 0

    def add(self, voltage_v):
        if voltage_v is None:
            return
        if self.start_voltage is None:
            self.start_voltage = voltage_v
        self.end_voltage = voltage_v
        self.min_voltage = voltage_v if self.min_voltage is None else min(self.min_voltage, voltage_v)
        self.max_voltage = voltage_v if self.max_voltage is None else max(self.max_voltage, voltage_v)
        self._sum += voltage_v
        self.sample_count += 1

    @property
    def average_voltage(self):
        return (self._sum / self.sample_count) if self.sample_count else None

    def as_run_summary_fields(self) -> dict:
        return {
            "start_voltage": self.start_voltage,
            "end_voltage": self.end_voltage,
            "min_voltage": self.min_voltage,
            "max_voltage": self.max_voltage,
            "average_voltage": self.average_voltage,
            "sample_count": self.sample_count,
        }


class MonitorBatterySequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings):
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="monitor_battery", dmm=dmm)

    def run(self, channel: int, relay_address: int, sample_interval_s: float = 2.0, token=None):
        """
        Continuously monitor one battery position -- no charging, no
        discharging. Closes the relay for `relay_address` once, then
        repeatedly takes a DMM voltage reading and renders/persists each
        sample, until the operator cancels (Ctrl+C -> CancellationToken) or
        a real fault occurs. Cancellation is the EXPECTED way a monitoring
        session ends -- there is no natural "success" exit the way a
        bounded Proto Test relay cycle has one.

        Run-level bookkeeping (start_run_summary()/battery-config snapshot,
        the pre-relay traceability event_log entries) is the caller's
        responsibility (test.py's battery-selection/confirmation-screen
        flow already has that information) -- this method owns the
        relay-close-through-monitoring-loop portion, plus the voltage
        summary written to run_summary on exit.
        """
        self.log.info("Monitor Battery starting. Channel: %d  Relay: %d", channel, relay_address)
        run_number = self._run_number()
        stats = _VoltageStats()

        def _loop():
            check_cancellation(token)
            self.relay.close(relay_address)
            self.storage.log_event(
                level="INFO", source="monitor_battery", channel=channel, relay=relay_address,
                message=f"Relay {relay_address} activated -- monitoring started",
            )
            self.storage.log_event(
                level="INFO", source="monitor_battery",
                message="Monitoring source: DMM",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state="ACTIVE")

            while True:
                check_cancellation(token)

                voltage_v = self.dmm.measure_dc_voltage()
                current_a = None  # DMM is voltage-only -- see module TODO
                temp_c = None     # NTC not wired in yet -- same pre-existing TODO as charge/discharge cycles
                stats.add(voltage_v)

                self.storage.record_measurement(
                    test_type="monitor", channel=channel, relay=relay_address,
                    phase_detail="MONITORING",
                    voltage_v=voltage_v, current_a=current_a, temp_c=temp_c,
                    # Also populate dmm_measured_v (in addition to the original
                    # voltage_v column) -- test_control/execution_screen.py::
                    # render_execution_frame()'s "Recent Measurements" panel
                    # only reads smu_measured_v/dmm_measured_v (the columns
                    # ChargeSequence/DischargeSequence/ProtoTestSequence
                    # populate), not voltage_v -- without this, every Monitor
                    # Battery row in that panel renders "N/A" even though a
                    # real DMM reading was taken. Found during pre-hardware-
                    # validation review; smu_measured_v stays None since this
                    # mode never sources/sinks through the SMU.
                    dmm_measured_v=voltage_v,
                )

                self._render_frame(
                    test_type="monitor", channel=channel, relay_address=relay_address,
                    run_number=run_number, state="ACTIVE", phase_detail="MONITORING",
                    battery_voltage=voltage_v, battery_current=current_a, battery_temp=temp_c,
                )

                # Interruptible -- see utils/cancellation.py::interruptible_sleep()
                # / docs/architecture.md "Interruptible Wait Mechanism". Previously
                # a plain time.sleep(); cancellation was already checked at the top
                # of the next loop iteration either way, so this tightens worst-case
                # Ctrl+C latency from ~sample_interval_s down to ~poll_interval_s
                # without changing normal (non-cancelled) timing.
                interruptible_sleep(sample_interval_s, token=token)

        self.run_guarded(
            _loop, channel=channel, relay_address=relay_address,
            label="Monitor Battery", verb="monitoring",
            cancel_message="Monitoring stopped by operator",
            extra_run_summary_fields_fn=stats.as_run_summary_fields,
        )
