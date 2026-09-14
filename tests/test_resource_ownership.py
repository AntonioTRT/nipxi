"""
Tests for orchestration/resource_ownership.py::ResourceOwnershipValidator
-- the startup gate for Multi-Group Concurrent Execution (see that
module's docstring). Uses synthetic battery_groups dicts shaped like a
bigger future rack, exactly as tests/test_main_show_topology.py and
orchestration/topology.py's own docstring establish as this codebase's
convention for exercising multi-group logic against today's real
(single-group) config with zero hardware.
"""

import unittest

import config.devices as dev_cfg
from orchestration.resource_ownership import (
    ResourceOwnershipConflict,
    ResourceOwnershipValidator,
    check_ownership,
)


def _group(relay_matrix, smu=None, dmm=None, daq=None, sense_channel=None):
    return {
        "enabled": True,
        "relay_matrix": relay_matrix,
        "smu": smu,
        "dmm": dmm,
        "daq": daq,
        "ntc_daq": None,
        "sense_channel": sense_channel,
    }


class NoConflictTests(unittest.TestCase):
    def test_groups_on_distinct_matrices_pass(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", daq="DAQ_1"),
            "B2": _group("MATRIX_B", smu="SMU_2", daq="DAQ_2"),
        }
        report = ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        self.assertTrue(report.ok)
        self.assertEqual(report.conflicts, {})

    def test_single_requested_group_never_conflicts_with_itself(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_A", smu="SMU_2"),  # would conflict with B1 if both requested
        }
        report = ResourceOwnershipValidator.validate(["B1"], battery_groups=groups)
        self.assertTrue(report.ok)

    def test_non_requested_groups_are_ignored_even_if_they_would_conflict(self):
        groups = {
            "B1": _group("MATRIX_A"),
            "B2": _group("MATRIX_A"),  # shares with B1, but not requested below
            "B3": _group("MATRIX_B"),
        }
        report = ResourceOwnershipValidator.validate(["B1", "B3"], battery_groups=groups)
        self.assertTrue(report.ok)


class MatrixConflictTests(unittest.TestCase):
    def test_two_requested_groups_sharing_a_relay_matrix_is_rejected(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1"),
            "B2": _group("MATRIX_A", smu="SMU_2"),
        }
        with self.assertRaises(ResourceOwnershipConflict) as ctx:
            ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        conflicts = ctx.exception.conflicts
        self.assertEqual(len(conflicts), 1)
        (key, names), = conflicts.items()
        self.assertEqual(key.role, "relay_matrix_name")
        self.assertEqual(key.resource_name, "MATRIX_A")
        self.assertEqual(names, {"B1", "B2"})

    def test_sharing_a_dmm_or_daq_across_different_smus_is_rejected(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", dmm="DMM_1", daq="DAQ_1"),
            "B2": _group("MATRIX_B", smu="SMU_2", dmm="DMM_1", daq="DAQ_1"),
        }
        report = check_ownership(["B1", "B2"], battery_groups=groups)
        self.assertFalse(report.ok)
        roles = {key.role for key in report.conflicts}
        # ntc_daq_name is included too: discover_topology() falls back
        # ntc_daq to daq when a group's ntc_daq is unset (both groups
        # here leave it unset), so the shared daq is also flagged under
        # the ntc_daq_name role -- see topology.py's own docstring.
        self.assertEqual(roles, {"dmm_name", "daq_name", "ntc_daq_name"})

    def test_two_groups_on_the_same_smu_anchored_worker_never_conflict(self):
        """
        Regression test for a real bug caught during development: an
        earlier version of check_ownership() compared raw groups
        directly and would have wrongly flagged B1/B2 here as
        conflicting on relay_matrix/dmm/daq -- but B1 and B2 share ONE
        smu, which orchestration/workers.py::discover_workers() treats
        as ONE worker that runs its groups strictly sequentially (never
        concurrently). Sharing hardware within that single worker is by
        construction never a real concurrency conflict, so this MUST
        report ok=True (and must have no smu_name entries in usage,
        matching orchestration/resource_graph.py's own exclusion).
        """
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", dmm="DMM_1", daq="DAQ_1"),
            "B2": _group("MATRIX_A", smu="SMU_1", dmm="DMM_1", daq="DAQ_1"),
        }
        report = check_ownership(["B1", "B2"], battery_groups=groups)
        self.assertTrue(report.ok)

    def test_channel_range_style_names_are_treated_as_distinct_today(self):
        """
        Documents the CURRENT (matrix-level, exact-string) rule: two
        groups naming DIFFERENT channel-range-style strings on the same
        physical matrix do NOT conflict under today's equality check --
        this is expected, not a bug, because channel-range ownership is
        explicitly deferred (see module docstring's "Future: matrix
        segmentation"). This test exists to make that boundary explicit
        and to break loudly the day someone adds real range-overlap
        logic without updating this test.
        """
        groups = {
            "B1": _group("MATRIX_A:1-8"),
            "B2": _group("MATRIX_A:9-16"),
        }
        report = ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        self.assertTrue(report.ok)


class SenseRoutingConflictTests(unittest.TestCase):
    def test_two_groups_sharing_a_sense_routing_matrix_is_rejected(self):
        # Distinct smus -- distinct workers -- so this exercises the
        # cross-worker sense-routing conflict check, not the (legitimate,
        # never-flagged) same-worker case.
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", sense_channel=1),
            "B2": _group("MATRIX_B", smu="SMU_2", sense_channel=2),
        }
        sense_routing = {
            1: {"relay_matrix": "MATRIX_SENSE", "relay": 1},
            2: {"relay_matrix": "MATRIX_SENSE", "relay": 2},  # same physical matrix
        }
        orig = dev_cfg.SENSE_ROUTING
        dev_cfg.SENSE_ROUTING = sense_routing
        try:
            with self.assertRaises(ResourceOwnershipConflict) as ctx:
                ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        finally:
            dev_cfg.SENSE_ROUTING = orig
        (key, names), = ctx.exception.conflicts.items()
        self.assertEqual(key.role, "sense_relay_matrix_name")
        self.assertEqual(key.resource_name, "MATRIX_SENSE")
        self.assertEqual(names, {"B1", "B2"})

    def test_groups_on_distinct_sense_routing_matrices_pass(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", sense_channel=1),
            "B2": _group("MATRIX_B", smu="SMU_2", sense_channel=2),
        }
        sense_routing = {
            1: {"relay_matrix": "MATRIX_SENSE_1", "relay": 1},
            2: {"relay_matrix": "MATRIX_SENSE_2", "relay": 1},
        }
        orig = dev_cfg.SENSE_ROUTING
        dev_cfg.SENSE_ROUTING = sense_routing
        try:
            report = ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        finally:
            dev_cfg.SENSE_ROUTING = orig
        self.assertTrue(report.ok)

    def test_two_groups_on_the_same_worker_sharing_a_sense_matrix_never_conflict(self):
        groups = {
            "B1": _group("MATRIX_A", smu="SMU_1", sense_channel=1),
            "B2": _group("MATRIX_A", smu="SMU_1", sense_channel=2),
        }
        sense_routing = {
            1: {"relay_matrix": "MATRIX_SENSE", "relay": 1},
            2: {"relay_matrix": "MATRIX_SENSE", "relay": 2},
        }
        orig = dev_cfg.SENSE_ROUTING
        dev_cfg.SENSE_ROUTING = sense_routing
        try:
            report = ResourceOwnershipValidator.validate(["B1", "B2"], battery_groups=groups)
        finally:
            dev_cfg.SENSE_ROUTING = orig
        self.assertTrue(report.ok)

    def test_group_with_no_sense_channel_contributes_no_sense_entry(self):
        groups = {"B1": _group("MATRIX_A", sense_channel=None)}
        report = check_ownership(["B1"], battery_groups=groups)
        roles = {key.role for key in report.usage}
        self.assertNotIn("sense_relay_matrix_name", roles)


class RealConfigSmokeTest(unittest.TestCase):
    def test_validate_against_real_devices_py_does_not_raise(self):
        """Today's real config (only B1 enabled) must pass trivially --
        this is the regression guard that this module never accidentally
        flags today's single-group reality as a conflict."""
        report = ResourceOwnershipValidator.validate(["B1"])
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
