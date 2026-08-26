"""
Cycle Sequence -- built on BatteryOperationSequence
(test_control/battery_operation_sequence.py), CURRENT IMPLEMENTATION of
the design persisted in docs/architecture.md Section 67 "CycleSequence --
Final Design". Composes a fresh ChargeSequence + DischargeSequence per
repetition -- never reimplements a sampling loop, an EOC/EOD condition, a
safety check, or a shutdown call. Everything hardware-touching stays
exactly where it is already validated (ChargeSequence/DischargeSequence
themselves, unmodified).

Deviations from Section 67's original pseudocode (both driven by the
same fact: `RunSpec`/`orchestration/worker_runtime.py`, which that design
assumed as its caller, do not exist anywhere in this codebase -- grepped
and confirmed absent):

  1. `cycle_count` is read from `test_setpoints["cycle_count"]` (default
     1), NOT a separate `run()` parameter. This makes CycleSequence.run()'s
     signature IDENTICAL to ChargeSequence.run()/DischargeSequence.run(),
     which is what lets it slot into test.py's EXISTING Charge/Discharge
     orchestration (_run_charge_or_discharge() /
     _run_one_charge_or_discharge_position() /
     _run_charge_or_discharge_all_positions()) as a drop-in `sequence_cls`
     with ZERO changes to that shared, already-validated code -- see
     test.py::_run_cycle_battery().
  2. `storage_factory` is not a caller-supplied constructor parameter --
     CycleSequence builds its own fresh per-phase DataStorage internally
     (_make_phase_storage(), using the same Settings it was itself
     constructed with). This keeps CycleSequence's constructor signature
     IDENTICAL to ChargeSequence/DischargeSequence's, for the same
     drop-in reason as (1).

Everything else -- the per-repetition composition loop, the single rest
phase between charge and discharge, cooperative cancellation via the same
token/checkpoints, the "no remaining repetitions run after a failure"
stop condition, and the "one cycle-level run_summary row + one row per
phase" reporting model -- follows Section 67 exactly.
"""

from data.storage import DataStorage
from test_control.battery_operation_sequence import BatteryOperationSequence
from test_control.charge_sequence import ChargeSequence
from test_control.discharge_sequence import DischargeSequence
from test_control.safety_monitor import SafetyMonitor
from utils.cancellation import check_cancellation, interruptible_sleep


class CycleSequence(BatteryOperationSequence):
    def __init__(self, smu, dmm, relay, safety: SafetyMonitor, storage, settings, daq=None,
                 group_name=None, ntc_daq_name=None, sense_router=None, sense_channel=None):
        # Identical constructor shape to ChargeSequence/DischargeSequence
        # -- see module docstring deviation (2). `source="cycle_battery"`
        # is this sequence's OWN event_log/log-message source; each
        # internal Charge/Discharge phase keeps using its own usual
        # "charge_battery"/"discharge_battery" source, unchanged.
        super().__init__(smu=smu, relay=relay, safety=safety, storage=storage, settings=settings,
                          source="cycle_battery", dmm=dmm, daq=daq, group_name=group_name,
                          sense_router=sense_router, sense_channel=sense_channel)
        self.ntc_daq_name = ntc_daq_name

    def _make_phase_storage(self, *, test_type: str, channel: int) -> DataStorage:
        """
        A fresh, opened DataStorage for one Charge or Discharge phase --
        see docs/architecture.md Section 67 "storage_factory concept".
        Never reused across repetitions or between the charge/discharge
        halves of one repetition -- reusing one instance/run_id across
        phases would let finish_run_summary()'s `UPDATE ... WHERE
        run_id=?` silently overwrite one phase's row with the other's
        (last-write-wins). A brand-new instance per phase eliminates that
        risk structurally, at zero cost to ChargeSequence/DischargeSequence,
        which are never modified.

        Also starts this phase's run_summary row (`start_run_summary()`).
        ChargeSequence/DischargeSequence deliberately never call
        start_run_summary() themselves -- that has always been the
        CALLER's responsibility (see test.py::
        _run_one_charge_or_discharge_position(), which calls it before
        constructing the sequence). For CycleSequence's internal phases,
        CycleSequence itself IS that caller, so it takes on the same
        responsibility here -- otherwise the phase's own finish_run_summary()
        (inside its run_guarded()/complete()) would have no row to update.

        KNOWN, DOCUMENTED LIMITATION: the HardwareManager audit-trail
        run_id_provider is wired ONCE by the caller (test.py's
        open_storage_guarded(), via hw_mgr.attach_run_id_provider()) to
        THIS CycleSequence's own storage.run_id -- CycleSequence has no
        HardwareManager reference to re-point it at each phase's fresh
        run_id. raw_hardware_log rows recorded during a Charge/Discharge
        phase therefore carry the CYCLE-level run_id, not that phase's
        own. run_summary/event_log/measurements are UNAFFECTED (each
        phase's own storage instance correctly scopes those to that
        phase's own run_id) -- only raw_hardware_log's run_id column is
        imprecise for the duration of a Cycle Battery run. Accepted as an
        explicit tradeoff rather than adding a second, CycleSequence-only
        wiring path into HardwareManager for this one column.
        """
        phase_storage = DataStorage(settings=self.s)
        phase_storage.open()
        phase_storage.start_run_summary(
            test_type=test_type, group_name=self.group_name, position_in_group=channel,
        )
        return phase_storage

    def run(self, channel: int, relay_address: int, battery_cfg: dict,
            test_setpoints: dict, ntc_channel: str = None, token=None) -> bool:
        """
        Run `test_setpoints.get("cycle_count", 1)` repetitions of
        charge -> rest -> discharge on `channel`/`relay_address`. See
        module docstring deviation (1) for why `cycle_count` is read from
        `test_setpoints` rather than being a separate parameter.

        Each repetition: a fresh ChargeSequence.run() (complete,
        self-contained -- own run_guarded(), own complete(), own
        emergency_output_off(), own relay.open()) -> a passive
        interruptible rest dwell (SMU already off, relay already open by
        this point) -> a fresh DischargeSequence.run() (same guarantees).
        If either phase raises, NO further repetitions run and no attempt
        is made to "finish the other half" of the current repetition --
        direct extension of "unknown state = unsafe state" to the cycle
        level (see docs/architecture.md Section 67 "Stop conditions").

        Returns True once every repetition completes. Raises on any
        abnormal exit -- handled by run_guarded() below exactly like
        Charge/Discharge (relay close, traceability, and safety shutdown
        are its own sub-phases' responsibility, not this method's own;
        CycleSequence adds no new safety check of its own).
        """
        self.log.info("Cycle Sequence starting. Channel: %d  Relay: %d", channel, relay_address)
        cycle_count = int(test_setpoints.get("cycle_count", 1))
        rest_s = test_setpoints.get("cycle_rest_s", self.s.CYCLE_REST_S)

        def _diagnostic_fields():
            return {"cycle_count": cycle_count}

        def _run_cycle():
            for repetition in range(1, cycle_count + 1):
                check_cancellation(token)
                self.storage.log_event(
                    level="INFO", source="cycle_battery", channel=channel, relay=relay_address,
                    message=f"Cycle repetition {repetition}/{cycle_count}: charge phase starting",
                )
                charge_storage = self._make_phase_storage(test_type="charge_battery", channel=channel)
                try:
                    charge_phase = ChargeSequence(
                        smu=self.smu, dmm=self.dmm, relay=self.relay, safety=self.safety,
                        storage=charge_storage, settings=self.s, daq=self.daq,
                        group_name=self.group_name, ntc_daq_name=self.ntc_daq_name,
                        sense_router=self.sense_router, sense_channel=self.sense_channel,
                    )
                    charge_phase.run(
                        channel=channel, relay_address=relay_address, battery_cfg=battery_cfg,
                        test_setpoints=test_setpoints, ntc_channel=ntc_channel, token=token,
                    )
                finally:
                    charge_storage.close()

                check_cancellation(token)
                self.storage.log_event(
                    level="INFO", source="cycle_battery", channel=channel, relay=relay_address,
                    message=f"Cycle repetition {repetition}/{cycle_count}: resting {rest_s:.0f}s before discharge",
                )
                interruptible_sleep(rest_s, token=token)

                self.storage.log_event(
                    level="INFO", source="cycle_battery", channel=channel, relay=relay_address,
                    message=f"Cycle repetition {repetition}/{cycle_count}: discharge phase starting",
                )
                discharge_storage = self._make_phase_storage(test_type="discharge_battery", channel=channel)
                try:
                    discharge_phase = DischargeSequence(
                        smu=self.smu, dmm=self.dmm, relay=self.relay, safety=self.safety,
                        storage=discharge_storage, settings=self.s, daq=self.daq,
                        group_name=self.group_name, ntc_daq_name=self.ntc_daq_name,
                        sense_router=self.sense_router, sense_channel=self.sense_channel,
                    )
                    discharge_phase.run(
                        channel=channel, relay_address=relay_address, battery_cfg=battery_cfg,
                        test_setpoints=test_setpoints, ntc_channel=ntc_channel, token=token,
                    )
                finally:
                    discharge_storage.close()

                self.storage.log_event(
                    level="INFO", source="cycle_battery", channel=channel, relay=relay_address,
                    message=f"Cycle repetition {repetition}/{cycle_count}: complete",
                )
            return True

        self.run_guarded(
            _run_cycle, channel=channel, relay_address=relay_address,
            label="Cycle Battery", verb="cycling",
            cancel_message="Cycle stopped by operator",
            extra_run_summary_fields_fn=_diagnostic_fields,
        )
        self.complete(
            channel=channel, relay_address=relay_address,
            log_message=f"Cycle complete on channel {channel} ({cycle_count} repetition(s))",
            **_diagnostic_fields(),
        )
        return True
