"""
Tests for the future-architecture read-only orchestration layer
(orchestration/topology.py, workers.py, resource_graph.py, arbiter.py).

These are design-validation tests, not regression tests for existing
behavior -- the orchestration package is new, unused-by-anything-real
code. They exist to prove two properties before any of this is wired
into a real worker/supervisor:

  1. Against TODAY's real config/devices.py, discovery produces exactly
     today's single-group reality (one worker, no conflicts).
  2. Against SYNTHETIC configs shaped like bigger, not-yet-existing racks
     (Rack B/C-style), discovery scales correctly with zero code changes
     -- including correctly detecting the exact class of hidden conflict
     (two SMU-anchored workers sharing one relay matrix) that a naive
     "group by SMU" derivation would miss.

No hardware access anywhere in this file.
"""

import unittest

import config.devices as dev_cfg
from orchestration.arbiter import InProcessArbiter, ResourceBusyError
from orchestration.resource_graph import build_resource_graph
from orchestration.topology import ResourceKey, discover_topology
from orchestration.workers import discover_workers


def _placeholder(**overrides):
    base = {
        "relay_matrix": None, "smu": None, "dmm": None, "daq": None, "ntc_daq": None,
        "enabled": False, "positions": {},
    }
    base.update(overrides)
    return base


class TodaysRealConfigTests(unittest.TestCase):
    """Discovery against the real config/devices.py::BATTERY_GROUPS --
    must match today's known-single-group reality exactly."""

    def test_exactly_one_worker_discovered(self):
        workers = discover_workers()
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0].smu_name, "AUX_SMU_1")
        self.assertEqual(workers[0].groups, ["B1"])

    def test_no_conflicts_with_a_single_worker(self):
        graph = build_resource_graph()
        self.assertEqual(graph.conflicts, {}, "one worker can never conflict with itself")

    def test_b1_worker_depends_on_expected_shared_resources(self):
        graph = build_resource_graph()
        worker = graph.workers[0]
        # AUX_SMU_1 itself must never appear as a "shared" dependency --
        # it is the one resource this worker exclusively owns.
        self.assertNotIn("AUX_SMU_1", worker.shared_dependencies)
        self.assertIn("MATRIX_NUMATO_202", worker.shared_dependencies)
        self.assertIn("MAIN_DMM", worker.shared_dependencies)
        self.assertIn("MAIN_DAQ", worker.shared_dependencies)

    def test_disabled_and_smu_less_groups_produce_no_worker(self):
        workers = discover_workers()
        smu_names = {w.smu_name for w in workers}
        for group_name in ("A1", "A2", "A3", "A4", "B2", "B3", "B4", "C1", "C2", "C3", "C4"):
            grp = dev_cfg.BATTERY_GROUPS[group_name]
            if grp.get("smu") is not None and grp.get("enabled"):
                continue  # would legitimately be a worker; not true for any of these today
            self.assertNotIn(grp.get("smu"), smu_names - {None})


class SyntheticRackScalingTests(unittest.TestCase):
    """
    Rack B-shaped config: two SMU-anchored workers whose groups share one
    relay matrix -- exactly today's B1-B4-share-one-matrix topology, but
    with a SECOND group now also given its own SMU. This is the case a
    naive "group by SMU" derivation would present as two fully
    independent workers; the resource graph must say otherwise.
    """

    def _rack_b_config(self):
        return {
            "B1": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}, 2: {}},
            },
            "B2": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_2",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}, 2: {}},
            },
            "B3": _placeholder(relay_matrix="MATRIX_NUMATO_202"),
            "B4": _placeholder(relay_matrix="MATRIX_NUMATO_202"),
        }

    def test_two_workers_discovered(self):
        workers = discover_workers(self._rack_b_config())
        self.assertEqual({w.smu_name for w in workers}, {"AUX_SMU_1", "AUX_SMU_2"})

    def test_shared_relay_matrix_conflict_is_detected(self):
        graph = build_resource_graph(self._rack_b_config())
        self.assertEqual(len(graph.conflicts), 1)
        (pair, shared_resources), = graph.conflicts.items()
        self.assertEqual(set(pair), {"AUX_SMU_1", "AUX_SMU_2"})
        self.assertIn("MATRIX_NUMATO_202", shared_resources)
        self.assertIn("MAIN_DMM", shared_resources)
        self.assertIn("MAIN_DAQ", shared_resources)
        # The SMUs themselves must never show up as a "conflict" -- they
        # are each exclusively owned, which is the whole point of using
        # SMU ownership as the worker-partitioning axis in the first place.
        self.assertNotIn("AUX_SMU_1", shared_resources)
        self.assertNotIn("AUX_SMU_2", shared_resources)

    def test_disabled_placeholder_groups_contribute_nothing(self):
        usage = discover_topology(self._rack_b_config())
        matrix_key = ResourceKey("relay_matrix_name", "MATRIX_NUMATO_202")
        # B3/B4 are disabled placeholders -- must NOT appear as users of
        # the shared matrix even though the field is set.
        self.assertEqual(usage[matrix_key], {"B1", "B2"})

    def test_worker_with_no_shared_relay_matrix_has_no_conflict(self):
        """Rack C-shaped variant: two workers on genuinely separate relay
        matrices -- must produce zero conflicts, proving the graph
        doesn't over-report sharing that doesn't exist."""
        config = {
            "B1": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
            "C1": {
                "relay_matrix": "MATRIX_NUMATO_203", "smu": "AUX_SMU_2",
                "dmm": None, "daq": None, "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
        }
        graph = build_resource_graph(config)
        self.assertEqual(graph.conflicts, {})


class SequentialGroupsWithinOneWorkerTests(unittest.TestCase):
    """
    "Worker 1: B1, B2" (one SMU serving two groups) means B1/B2 run
    SEQUENTIALLY on that worker, never simultaneously -- both groups are
    real members of the SAME WorkerPlan, and the SMU is never treated as
    "shared between two workers" since there is only one worker here.
    """

    def test_two_groups_on_one_smu_produce_a_single_worker(self):
        config = {
            "B1": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
            "B2": {
                "relay_matrix": "MATRIX_NUMATO_202", "smu": "AUX_SMU_1",
                "dmm": "MAIN_DMM", "daq": "MAIN_DAQ", "ntc_daq": None,
                "enabled": True, "positions": {1: {}},
            },
        }
        workers = discover_workers(config)
        self.assertEqual(len(workers), 1)
        self.assertEqual(sorted(workers[0].groups), ["B1", "B2"])
        graph = build_resource_graph(config)
        self.assertEqual(graph.conflicts, {}, "a worker can never conflict with itself")


class InProcessArbiterTests(unittest.TestCase):
    def test_claim_and_release_round_trip(self):
        arbiter = InProcessArbiter()
        handle = arbiter.claim("MATRIX_NUMATO_202", owner="worker-1")
        self.assertTrue(arbiter.is_claimed("MATRIX_NUMATO_202"))
        arbiter.release(handle)
        self.assertFalse(arbiter.is_claimed("MATRIX_NUMATO_202"))

    def test_second_owner_claiming_busy_resource_raises(self):
        arbiter = InProcessArbiter()
        arbiter.claim("MATRIX_NUMATO_202", owner="worker-1")
        with self.assertRaises(ResourceBusyError):
            arbiter.claim("MATRIX_NUMATO_202", owner="worker-2")

    def test_same_owner_reclaiming_is_idempotent(self):
        arbiter = InProcessArbiter()
        arbiter.claim("MATRIX_NUMATO_202", owner="worker-1")
        arbiter.claim("MATRIX_NUMATO_202", owner="worker-1")  # must not raise
        self.assertTrue(arbiter.is_claimed("MATRIX_NUMATO_202"))

    def test_releasing_a_resource_you_do_not_hold_is_a_silent_no_op(self):
        arbiter = InProcessArbiter()
        handle = arbiter.claim("MATRIX_NUMATO_202", owner="worker-1")
        arbiter.release(handle)
        arbiter.release(handle)  # must not raise on a second release


if __name__ == "__main__":
    unittest.main()
