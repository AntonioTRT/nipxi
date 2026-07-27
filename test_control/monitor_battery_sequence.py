"""
Monitor Battery sequence -- Run Main Test's first battery-centric mode
(Milestone II). Read-only battery monitoring: NO charging, NO discharging.
Mirrors test_control/proto_test_sequence.py::ProtoTestSequence's structure
deliberately (same constructor shape, same safety-exception handling, same
storage/event-logging/ExecutionFrame usage) -- this is the second real
consumer of the Milestone II infrastructure, not a parallel design.

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
    4. ExecutionFrame.from_live()/render_execution_frame() -- the same
       shared renderer, using its battery_voltage/battery_current/
       battery_temp fields (added alongside the existing smu_*/dmm_*
       fields specifically for this DAQ-only-in-the-final-architecture,
       no-sourcing case).
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

import logging
import time

from config.settings import Settings
from test_control.execution_screen import ExecutionFrame, render_execution_frame
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation
from utils.errors import SafetyViolationError, RelayError, OperationCancelledError
from utils.stop_reason import StopReason


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


class MonitorBatterySequence:
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings):
        self.smu = smu
        self.dmm = dmm
        self.relay = relay
        self.safety = safety
        self.storage = storage
        self.s = settings
        self.log = logging.getLogger("nipxi.monitor_battery")

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
        run_summary = self.storage.get_run_summary(self.storage.run_id)
        run_number = run_summary["id"] if run_summary else None
        stats = _VoltageStats()

        try:
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
                )

                frame = ExecutionFrame.from_live(
                    run_number=run_number, run_id=self.storage.run_id, test_type="monitor",
                    channel=channel, relay=relay_address, state="ACTIVE", phase_detail="MONITORING",
                    battery_voltage=voltage_v, battery_current=current_a, battery_temp=temp_c,
                    recent_measurements=self.storage.get_measurements(run_id=self.storage.run_id),
                    recent_events=self.storage.get_recent_events(run_id=self.storage.run_id),
                )
                render_execution_frame(frame)

                time.sleep(sample_interval_s)

        except OperationCancelledError as e:
            # Deliberate operator action, not a fault -- monitoring is
            # EXPECTED to end this way.
            self.log.warning("Monitor Battery cancelled: %s", e)
            self.storage.log_event(
                level="INFO", source="monitor_battery", channel=channel, relay=relay_address,
                message="Monitoring stopped by operator",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.CANCELLED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.CANCELLED, result="STOPPED_BY_OPERATOR",
                **stats.as_run_summary_fields(),
            )
            self.safety.safe_cancel_shutdown(self.smu, self.relay, str(e))
            raise

        except SafetyViolationError as e:
            self.log.error("Safety violation while monitoring channel %d: %s", channel, e)
            self.storage.log_event(
                level="ERROR", source="monitor_battery", channel=channel, relay=relay_address,
                message=f"Safety violation -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.SAFETY_VIOLATION)
            self.storage.finish_run_summary(
                stop_reason=StopReason.SAFETY_VIOLATION, result="FAIL",
                **stats.as_run_summary_fields(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

        except RelayError as e:
            self.log.error("Relay verification fault while monitoring channel %d: %s", channel, e)
            self.storage.log_event(
                level="ERROR", source="monitor_battery", channel=channel, relay=relay_address,
                message=f"Relay verification fault -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.FAILED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **stats.as_run_summary_fields(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise

        except Exception as e:
            self.log.error("Unexpected error while monitoring channel %d: %s", channel, e, exc_info=True)
            self.storage.log_event(
                level="ERROR", source="monitor_battery", channel=channel, relay=relay_address,
                message=f"Unexpected error -- {e}",
            )
            self.storage.record_execution_state(channel=channel, relay=relay_address, state=StopReason.FAILED)
            self.storage.finish_run_summary(
                stop_reason=StopReason.FAILED, result="FAIL",
                **stats.as_run_summary_fields(),
            )
            self.safety.emergency_stop(self.smu, self.relay, str(e))
            raise
