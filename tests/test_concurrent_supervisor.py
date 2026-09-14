"""
Tests for orchestration/concurrent_supervisor.py::ConcurrentSupervisor
and its collaborators (GroupRuntime, GroupWorker, GlobalFaultBus,
GlobalEmergencyStop) -- see concurrent_supervisor.py's module docstring
for the full architecture. HardwareManager is patched with a fake, in
every test here -- these tests are about the ORCHESTRATION logic
(threading, fault propagation, shutdown ordering, resource-ownership
gating), never about real hardware I/O, which is out of scope for a
CI-safe unit test.
"""

import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from orchestration.concurrent_supervisor import ConcurrentSupervisor
from orchestration.resource_ownership import ResourceOwnershipConflict
from utils.errors import HardwareInitError


class _TempSettings:
    """
    Pointed at a fresh temp dir -- GroupRuntime.connect() now opens a
    real DataStorage (see group_runtime.py's docstring on why storage
    is per-instance, not shared), so these orchestration-logic tests
    must not touch the real DATA_DIR/CSV_DIR. Mirrors the established
    _TempSettings convention already used in tests/test_run_sequence.py
    and friends.
    """
    def __init__(self, base_dir):
        self.DATA_DIR = base_dir
        self.CSV_DIR = f"{base_dir}/csv"


def _group(relay_matrix, smu, dmm=None, daq=None):
    return {
        "enabled": True,
        "relay_matrix": relay_matrix,
        "smu": smu,
        "dmm": dmm,
        "daq": daq,
        "ntc_daq": None,
        "sense_channel": None,
    }


class _FakeHardwareManager:
    """
    Stands in for test_control.hardware_manager.HardwareManager.
    `connect_behavior` is a zero-arg callable invoked by connect_all();
    raising simulates a real HardwareInitError, returning normally
    simulates success. `on_connect`/`on_disconnect` (optional) let a
    test observe call order/timing.
    """
    instances: list = []

    def __init__(self, settings, relay_cfg, smu_cfg=None, daq_cfg=None,
                 dmm_cfg=None, ntc_daq_cfg=None):
        self.settings = settings
        self.relay_cfg = relay_cfg
        self.connect_behavior = _FakeHardwareManager.default_connect_behavior
        self.disconnect_calls = 0
        _FakeHardwareManager.instances.append(self)

    default_connect_behavior = staticmethod(lambda: None)

    def connect_all(self):
        self.connect_behavior()

    def disconnect_all(self):
        self.disconnect_calls += 1


def _run_group_ok(group_name, runtime):
    return True


def _run_group_fails(group_name, runtime):
    return False


def _run_group_raises(group_name, runtime):
    raise RuntimeError(f"simulated failure in {group_name}")


class _Base(unittest.TestCase):
    def setUp(self):
        _FakeHardwareManager.instances = []
        _FakeHardwareManager.default_connect_behavior = staticmethod(lambda: None)
        patcher = patch("orchestration.group_runtime.HardwareManager", _FakeHardwareManager)
        patcher.start()
        self.addCleanup(patcher.stop)

        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.settings = _TempSettings(tmp_dir)


class ResourceGateTests(_Base):
    def test_construction_rejects_conflicting_groups_before_any_hardware_manager_is_built(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_A", smu="SMU_2"),
        }
        with self.assertRaises(ResourceOwnershipConflict):
            ConcurrentSupervisor(["B1", "B2"], self.settings, _run_group_ok, battery_groups=groups)
        self.assertEqual(_FakeHardwareManager.instances, [])

    def test_group_with_no_smu_is_rejected_at_construction(self):
        groups = {"B1": _group("MATRIX_A", smu=None)}
        with self.assertRaises(ValueError):
            ConcurrentSupervisor(["B1"], self.settings, _run_group_ok, battery_groups=groups)


class NormalCompletionTests(_Base):
    def test_two_independent_groups_run_concurrently_and_complete_without_fault(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_B", smu="SMU_2"),
        }
        sup = ConcurrentSupervisor(["B1", "B2"], self.settings, _run_group_ok, battery_groups=groups)
        result = sup.run()
        self.assertFalse(result.faulted)
        self.assertEqual(result.shutdown, {})
        self.assertEqual(len(_FakeHardwareManager.instances), 2)
        for hw in _FakeHardwareManager.instances:
            self.assertEqual(hw.disconnect_calls, 1)

    def test_each_group_gets_its_own_independent_datastorage_and_run_id(self):
        """
        Regression test for a real bug caught during development: an
        earlier design shared ONE DataStorage instance across every
        GroupRuntime for "global sequence/traceability" reasons -- but
        DataStorage.run_id/_sequence_number are per-CURRENT-RUN instance
        state (see data/storage.py's own docstring, "One DataStorage
        instance == one run"), so two concurrently-running groups
        sharing one instance would clobber each other's run_id
        attribution. Each GroupRuntime must open its OWN DataStorage
        (group_runtime.py::GroupRuntime.connect()) -- safely sharing
        the same underlying index/telemetry FILES via WAL, never the
        same in-memory instance.
        """
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_B", smu="SMU_2"),
        }
        seen = {}

        def run_group(group_name, runtime):
            seen[group_name] = runtime.storage
            return True

        sup = ConcurrentSupervisor(["B1", "B2"], self.settings, run_group, battery_groups=groups)
        result = sup.run()

        self.assertFalse(result.faulted)
        self.assertEqual(set(seen), {"B1", "B2"})
        self.assertIsNot(seen["B1"], seen["B2"])
        self.assertIsNotNone(seen["B1"].run_id)
        self.assertIsNotNone(seen["B2"].run_id)
        self.assertNotEqual(seen["B1"].run_id, seen["B2"].run_id)


class FaultPropagationTests(_Base):
    def test_one_group_failing_stops_the_other_and_is_reported(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_B", smu="SMU_2"),
        }
        release = threading.Event()

        def run_group(group_name, runtime):
            if group_name == "B1":
                return _run_group_fails(group_name, runtime)
            # B2 blocks until told to stop, checking its OWN cancellation
            # token -- mirrors a real long-running battery operation's
            # cooperative-cancellation checkpoint loop.
            while not runtime.cancellation.requested:
                if release.wait(timeout=0.05):
                    break
            return False

        sup = ConcurrentSupervisor(["B1", "B2"], self.settings, run_group, battery_groups=groups)
        result = sup.run()

        self.assertTrue(result.faulted)
        self.assertIn("B1", result.fault_reason)
        self.assertEqual(set(result.shutdown), {"SMU_1", "SMU_2"})
        self.assertEqual(result.shutdown["SMU_1"], "stopped")
        self.assertEqual(result.shutdown["SMU_2"], "stopped")
        release.set()

    def test_connect_failure_reports_to_bus_and_does_not_hang(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_B", smu="SMU_2"),
        }

        def flaky_connect():
            raise HardwareInitError("simulated: relay not reachable")

        _FakeHardwareManager.default_connect_behavior = staticmethod(flaky_connect)

        release = threading.Event()

        def run_group(group_name, runtime):
            while not runtime.cancellation.requested:
                if release.wait(timeout=0.05):
                    break
            return False

        sup = ConcurrentSupervisor(["B1", "B2"], self.settings, run_group, battery_groups=groups)
        start = time.monotonic()
        result = sup.run()
        elapsed = time.monotonic() - start

        self.assertTrue(result.faulted)
        self.assertLess(elapsed, 5.0, "connect failure must not block on the 30s join timeout")
        release.set()

    def test_uncaught_exception_in_run_group_is_reported_not_swallowed(self):
        """
        SequentialSupervisor.start() catches run_group's exception
        internally and treats it identically to a falsy return (see its
        own docstring) -- the exception TEXT is not preserved past that
        point, only "this group failed" is. This is the documented
        KNOWN LIMITATION in group_worker.py's module docstring, not a
        bug: the point of this test is that the failure is still
        reported to the fault bus at all (not silently swallowed),
        not that the original exception text survives.
        """
        groups = {"B1": _group("MATRIX_A", smu="SMU_1")}
        sup = ConcurrentSupervisor(["B1"], self.settings, _run_group_raises, battery_groups=groups)
        result = sup.run()
        self.assertTrue(result.faulted)
        self.assertIn("B1", result.fault_reason)


if __name__ == "__main__":
    unittest.main()
