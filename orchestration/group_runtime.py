"""
GroupRuntime -- bundles one WORKER's (orchestration/workers.py::
WorkerPlan, SMU-anchored) HardwareManager/SafetyMonitor/
CancellationToken, for use by orchestration/group_worker.py under
orchestration/concurrent_supervisor.py (see that module's docstring for
the full Multi-Group Concurrent Execution architecture).

Keyed by WORKER, not by raw group name, because a WorkerPlan's `groups`
(orchestration/workers.py's own docstring: "a worker with more than one
group runs them SEQUENTIALLY, never simultaneously") already run on
ONE physical hardware set today -- this is the existing "Group -> ALL"
pattern (see data/storage.py::DataStorage.begin_new_run_id()'s
docstring), just given a name at the orchestration layer. Building a
GroupRuntime from `worker_plan.groups[0]`'s hardware_for_group()
config and reusing it for every group on that worker is therefore not
a new assumption -- it is the same assumption Group -> ALL already
depends on, made explicit here so orchestration/concurrent_supervisor.py
does not have to re-derive it. If a future config ever puts two groups
on the same SMU with genuinely DIFFERENT relay/DMM/DAQ, this class
would need reconnect-between-groups logic it does not have -- flagged
here rather than silently mishandled.

RELAY MATRIX OWNERSHIP -- see orchestration/resource_ownership.py's
module docstring for the full rationale (matrix-level, exclusive,
segmentation explicitly deferred). This class identifies its relay
matrix purely via the resource_name string HardwareManager/
config/devices.py already hands it -- it does not parse or assume
anything about that string's structure, so it needs no change on the
day matrix segmentation is introduced; only resource_ownership.py's
conflict rule and hardware/relay_eth.py's close()/write path would.
"""

from __future__ import annotations

import config.devices as dev_cfg
from config.settings import Settings
from orchestration.workers import WorkerPlan
from test_control.hardware_manager import HardwareManager
from test_control.safety_monitor import SafetyMonitor
from test_control.storage_session import open_storage_guarded
from utils.cancellation import CancellationToken
from utils.errors import HardwareInitError


class GroupRuntime:
    """
    One worker's full hardware + safety + storage + cancellation
    context. Owns:
      - `hardware`: a HardwareManager, NOT yet connected -- connect()
        below calls connect_all() explicitly, mirroring every existing
        single-worker workflow's own lifecycle (construct, then
        connect, then run, then disconnect).
      - `storage`: a data/storage.py::DataStorage instance, own OWN --
        NOT shared with any other GroupRuntime. DataStorage's own
        docstring ("One DataStorage instance == one run") means
        `run_id`/`_sequence_number`/`_csv_writers` are per-CURRENT-RUN
        instance state; sharing one instance across concurrently-
        running groups would let their writes clobber each other's
        run_id attribution -- a real bug caught during development,
        not a hypothetical. Multiple INSTANCES safely sharing the SAME
        underlying index/telemetry files is exactly what data/
        storage.py's WAL + busy_timeout + retry-on-OperationalError
        (see that module's docstring) is for -- the standard SQLite
        multi-connection pattern, not a new one invented here. Opened
        in connect() below via the SAME test_control/storage_session.py
        ::open_storage_guarded() every existing single-group workflow
        already uses (test.py's _open_storage_guarded() is a thin
        wrapper over it) -- this class does not reimplement storage-
        open error handling or the run_id-provider wiring back into
        `hardware`.
      - `safety`: a SafetyMonitor, unmodified from single-group use --
        this class does not change SafetyMonitor.emergency_stop()'s
        contract (it still only touches the smu/relay it is given,
        which are THIS worker's own -- see docs/architecture.md
        "Multi-Group Concurrency: Fault Propagation" for why that is
        correct and must stay that way).
      - `cancellation`: a CancellationToken owned EXCLUSIVELY by this
        worker's own thread (orchestration/group_worker.py) -- never
        written to from any other thread. See utils/cancellation.py's
        own docstring: it is deliberately still a plain, single-thread
        flag; cross-thread signaling is orchestration/fault_bus.py's
        job, not this token's.
    """

    def __init__(self, worker_plan: WorkerPlan, settings: Settings, battery_cfg: dict = None):
        self.worker_plan = worker_plan
        self.primary_group = worker_plan.groups[0]
        self.s = settings

        hw_cfg = dev_cfg.hardware_for_group(self.primary_group)
        self.hardware = HardwareManager(
            settings,
            relay_cfg=hw_cfg["relay_matrix_cfg"],
            smu_cfg=hw_cfg["smu_cfg"],
            daq_cfg=hw_cfg["daq_cfg"],
            dmm_cfg=hw_cfg["dmm_cfg"],
            ntc_daq_cfg=hw_cfg["ntc_daq_cfg"],
        )
        self.safety = SafetyMonitor(settings, battery_cfg)
        self.cancellation = CancellationToken(owner=worker_plan.smu_name)
        self.storage = None

    def connect(self) -> None:
        """
        Connect every device for this worker, then open this worker's
        OWN DataStorage (see class docstring) and wire its run_id back
        into `hardware`'s audit trail -- exactly the order every
        existing single-group workflow in test.py already uses. Raises
        HardwareInitError on a fatal hardware failure, or RuntimeError
        if storage fails to open (mirroring test.py's own guarded
        "abort, no hardware left connected" behavior instead of
        open_storage_guarded()'s console-print convention -- this
        method has no `on_fail` printer, so it disconnects hardware and
        raises instead). The caller (GroupWorker) is responsible for
        reporting either failure to the GlobalFaultBus; this method
        does not know the bus exists.
        """
        self.hardware.connect_all()
        self.storage = open_storage_guarded(self.s, hw_mgr=self.hardware)
        if self.storage is None:
            self.hardware.disconnect_all()
            raise HardwareInitError(
                f"GroupRuntime({self.worker_plan.smu_name}): storage failed to open -- "
                "hardware has been disconnected."
            )

    def disconnect(self) -> None:
        """Close storage, then disconnect every device for this worker --
        safe to call even if connect() partially failed (mirrors
        HardwareManager's own rollback-on-failure behavior)."""
        if self.storage is not None:
            self.storage.close()
            self.storage = None
        self.hardware.disconnect_all()

    def request_stop(self, reason: str) -> None:
        """
        Signal this worker's own run loop to stop at its next checkpoint
        -- via the SAME cooperative-cancellation mechanism (utils/
        cancellation.py) every existing workflow's Ctrl+C handling
        already uses, not a new shutdown path. This is deliberately the
        ONLY thing GroupRuntime does in response to a stop request: the
        actual hardware-safe-shutdown sequence (PMU off, relays open,
        verified) is already triggered by the existing
        OperationCancelledError -> SafetyMonitor.safe_cancel_shutdown()
        wiring in test_control/battery_operation_sequence.py::
        run_guarded() -- reused here, not reimplemented. Note this means
        a group stopped by ANOTHER group's fault is logged/recorded as a
        "safe cancellation", not an "emergency stop" -- functionally
        identical hardware sequence, different severity label. See
        orchestration/global_emergency_stop.py's docstring for why this
        is an accepted, documented nuance rather than a gap.
        """
        self.cancellation.request_cancel(reason)
