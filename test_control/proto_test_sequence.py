"""
Proto Test Execution sequence (Milestone 2) -- infrastructure validation,
NO battery connected. Mirrors test_control/battery_test.py::
BatteryTestSequence's structure deliberately (same constructor shape, same
try/except/else exception handling around each iteration, same
check_cancellation()/safety-shutdown calls) rather than inventing a
different pattern for a second sequence class -- this is the second member
of the same "sequence" family as BatteryTestSequence, not a parallel
framework.

Per relay N (see docs/architecture.md "Proto Test Execution" for the full
design and rationale):
    1. relay.close(N) -- reuses hardware/relay_eth.py's mandatory
       force-all-off -> verify -> activate -> verify-single -> verify-all
       sequence unchanged; this class never talks to the relay driver's
       native primitives directly.
    2. smu.source_dc_voltage_point(...) -- reuses hardware/smu.py's fully
       verified configure -> verify -> enable -> verify sequence unchanged
       (Phase 1 SMU hardening); passes hold_s/during_hold (both new,
       backward-compatible optional parameters -- see hardware/smu.py) so
       the DMM reading below happens while output is still genuinely
       active, and so the relay dwells for the configured interval before
       the SMU's own finally block disables and verifies output OFF.
    3. storage.record_measurement(test_type="proto", ...) -- Milestone II
       Phase 3: the historical result now lives in `measurements` (the
       authoritative store for every test type), not `station_state`.
       storage.record_execution_state(...) is now called with only
       channel/relay/state -- station_state is recovery-position-only,
       per the approved Milestone II architecture.
    4. storage.log_event(...) at each phase transition, and
       render_execution_frame(ExecutionFrame.from_live(...)) once per
       relay (at the point real measurement data exists) -- the shared
       runtime UI from test_control/execution_screen.py replaces this
       class's previous ad hoc print() calls. No secondary renderer.

No battery-limit logic lives here -- SafetyMonitor remains the sole owner
of limit/abort decisions, exactly as established during SMU Verification
Hardening; this class only calls safety.emergency_stop()/
safety.safe_cancel_shutdown() on failure/cancellation, identical to
BatteryTestSequence.

Milestone II Phase 3 note: this is an architecture migration only. Relay
sequencing, SMU sourcing configuration/timing, DMM measurement logic, dwell
timing, and the safety-exception handling below are byte-for-byte the same
calls, in the same order, as before this phase -- only the storage layer,
event logging, and runtime UI integration changed. The physical validation
path is electrically identical to the version already run on the rack.
"""

import logging

from config.settings import Settings
from test_control.execution_screen import ExecutionFrame, render_execution_frame
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation
from utils.errors import SafetyViolationError, RelayError, OperationCancelledError
from utils.stop_reason import StopReason


class ProtoTestSequence:
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings: Settings):
        self.smu = smu
        self.dmm = dmm
        self.relay = relay
        self.safety = safety
        self.storage = storage
        self.s = settings
        self.log = logging.getLogger("nipxi.proto_test")

    def run(self, relays: list, dwell_s: float = None, token=None):
        """
        Cycle through `relays` (defaults to settings.ACTIVE_CHANNELS),
        sourcing settings.CHARGE_VOLTAGE_V at settings.CHARGE_CURRENT_A
        current limit (the same constants SMU Functional Validation already
        uses -- no new duplicate voltage/current configuration) on each,
        holding for `dwell_s` seconds (defaults to settings.PROTO_TEST_DWELL_S)
        with output active, reading the DMM during that hold, then
        disabling (verified) and moving to the next relay.

        `token` (see utils/cancellation.py) is checked once before each
        relay starts -- same checkpoint granularity as
        BatteryTestSequence.run(), never mid-relay-sequence.

        On any failure or cancellation, records the abnormal stop reason to
        storage (so the next startup's "previous execution found" display
        reflects it), runs the same safety shutdown BatteryTestSequence
        uses (safety.emergency_stop()/safety.safe_cancel_shutdown() --
        PMU output off+verified, all relays open+verified), then re-raises.
        On full completion, records a final COMPLETED row.
        """
        relays  = relays or self.s.ACTIVE_CHANNELS
        dwell_s = self.s.PROTO_TEST_DWELL_S if dwell_s is None else dwell_s
        self.log.info("Proto Test Execution starting. Relays: %s  Dwell: %.1fs", relays, dwell_s)

        self.storage.start_run_summary(test_type="proto")
        run_summary = self.storage.get_run_summary(self.storage.run_id)
        run_number = run_summary["id"] if run_summary else None

        last_relay = None
        for relay_n in relays:
            last_relay = relay_n
            self.log.info("--- Relay %d ---", relay_n)
            self.storage.log_event(
                level="INFO", source="proto_test", channel=relay_n, relay=relay_n,
                message=f"Relay {relay_n} activating (force-all-off -> verify -> activate -> verify)",
            )

            try:
                # Checkpoint: before starting a new relay, never mid-relay --
                # placed INSIDE the try (not before it), same reasoning as
                # BatteryTestSequence.run(), so that a cancellation detected
                # right here still persists state via the except clause
                # below rather than propagating before anything is recorded.
                check_cancellation(token)
                self.relay.close(relay_n)
                self.storage.log_event(
                    level="INFO", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n} activated -- output enabled, "
                            f"sourcing {self.s.CHARGE_VOLTAGE_V:.3f} V / "
                            f"{self.s.CHARGE_CURRENT_A:.3f} A limit, dwelling {dwell_s:.0f}s",
                )

                dmm_reading = {"voltage_v": None}

                def _read_dmm():
                    try:
                        dmm_reading["voltage_v"] = self.dmm.measure_dc_voltage() if self.dmm else None
                    except Exception as e:
                        self.log.warning("Relay %d: DMM read failed: %s", relay_n, e)
                        self.storage.log_event(
                            level="WARNING", source="proto_test", channel=relay_n, relay=relay_n,
                            message=f"DMM read failed: {e}",
                        )
                        return dmm_reading["voltage_v"]
                    self.storage.log_event(
                        level="INFO", source="proto_test", channel=relay_n, relay=relay_n,
                        message=(
                            "DMM read: no DMM configured" if dmm_reading["voltage_v"] is None
                            else f"Measurement acquired -- DMM {dmm_reading['voltage_v']:.6f} V"
                        ),
                    )
                    return dmm_reading["voltage_v"]

                reading = self.smu.source_dc_voltage_point(
                    voltage_v=self.s.CHARGE_VOLTAGE_V,
                    current_limit_a=self.s.CHARGE_CURRENT_A,
                    voltage_range_v=self.s.BAT_VOLTAGE_MAX,
                    hold_s=dwell_s,
                    during_hold=_read_dmm,
                )

                dmm_v = reading["during_hold_result"]

                self.storage.record_measurement(
                    test_type="proto", channel=relay_n, relay=relay_n, phase_detail="MEASURED",
                    commanded_v=reading["commanded_v"],
                    commanded_current_limit_a=reading["commanded_current_limit_a"],
                    smu_readback_v=reading["readback_v"],
                    smu_readback_current_limit_a=reading["readback_current_limit_a"],
                    smu_measured_v=reading["measured_v"],
                    smu_measured_i=reading["measured_i"],
                    dmm_measured_v=dmm_v,
                    output_enabled_readback=int(bool(reading["output_enabled_readback"])),
                    in_compliance=int(bool(reading["in_compliance"])),
                )
                self.storage.record_execution_state(channel=relay_n, relay=relay_n, state="ACTIVE")

                frame = ExecutionFrame.from_live(
                    run_number=run_number, run_id=self.storage.run_id, test_type="proto",
                    channel=relay_n, relay=relay_n, state="ACTIVE", phase_detail="MEASURED",
                    smu_voltage=reading["measured_v"], smu_current=reading["measured_i"],
                    dmm_voltage=dmm_v,
                    recent_measurements=self.storage.get_measurements(run_id=self.storage.run_id),
                    recent_events=self.storage.get_recent_events(run_id=self.storage.run_id),
                )
                render_execution_frame(frame)

            except OperationCancelledError as e:
                self.log.warning("Cancellation requested during relay %d: %s", relay_n, e)
                self.storage.log_event(
                    level="WARNING", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n}: cancellation requested -- {e}",
                )
                self.storage.record_execution_state(channel=relay_n, relay=relay_n, state=StopReason.CANCELLED)
                self.storage.finish_run_summary(stop_reason=StopReason.CANCELLED, result="CANCELLED")
                self.safety.safe_cancel_shutdown(self.smu, self.relay, str(e))
                raise

            except SafetyViolationError as e:
                self.log.error("Safety violation on relay %d: %s", relay_n, e)
                self.storage.log_event(
                    level="ERROR", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n}: safety violation -- {e}",
                )
                self.storage.record_execution_state(channel=relay_n, relay=relay_n, state=StopReason.SAFETY_VIOLATION)
                self.storage.finish_run_summary(stop_reason=StopReason.SAFETY_VIOLATION, result="FAIL")
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            except RelayError as e:
                # Includes RelayStateVerificationError -- a relay that did
                # not verifiably reach its commanded state is a safety
                # fault, never a retryable condition.
                self.log.error("Relay verification fault on relay %d: %s", relay_n, e)
                self.storage.log_event(
                    level="ERROR", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n}: relay verification fault -- {e}",
                )
                self.storage.record_execution_state(channel=relay_n, relay=relay_n, state=StopReason.FAILED)
                self.storage.finish_run_summary(stop_reason=StopReason.FAILED, result="FAIL")
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            except Exception as e:
                self.log.error("Unexpected error on relay %d: %s", relay_n, e, exc_info=True)
                self.storage.log_event(
                    level="ERROR", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n}: unexpected error -- {e}",
                )
                self.storage.record_execution_state(channel=relay_n, relay=relay_n, state=StopReason.FAILED)
                self.storage.finish_run_summary(stop_reason=StopReason.FAILED, result="FAIL")
                self.safety.emergency_stop(self.smu, self.relay, str(e))
                raise

            else:
                self.relay.open(relay_n)
                self.storage.log_event(
                    level="INFO", source="proto_test", channel=relay_n, relay=relay_n,
                    message=f"Relay {relay_n} deactivated -- output disabled, verified OFF",
                )

        self.storage.record_execution_state(channel=last_relay, relay=last_relay, state=StopReason.COMPLETED)
        self.storage.finish_run_summary(stop_reason=StopReason.COMPLETED, result="PASS")
        self.log.info("Proto Test Execution complete.")
        self.storage.log_event(
            level="INFO", source="proto_test",
            message=f"Proto Test Execution complete -- all {len(relays)} relay(s) cycled",
        )
