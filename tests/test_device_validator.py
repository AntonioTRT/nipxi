"""
Tests for utils/device_validator.py's Matrix + Relay-Range Ownership
static validation (Phase 3 -- see orchestration/resource_ownership.py's
module docstring for the full picture). Exercises
_check_relay_range_definitions() directly against synthetic dev_cfg-shaped
namespaces, never touching real hardware or the real config/devices.py
module (so these tests can never be affected by, or accidentally mutate,
production BATTERY_GROUPS).
"""

import unittest
from types import SimpleNamespace

from utils.device_validator import _check_relay_range_definitions


def _dev_cfg(battery_groups, matrix_configs=None):
    return SimpleNamespace(
        BATTERY_GROUPS=battery_groups,
        NUMATO_RELAY_MATRIX_CONFIGS=matrix_configs or {},
    )


class NoRelayRangeDeclaredTests(unittest.TestCase):
    """Every group in production today leaves relay_range unset -- this
    must remain a complete no-op, including the existing B1-B4-share-one-
    matrix pattern that is valid ONLY because none of them declare a
    range yet."""

    def test_groups_sharing_a_bare_matrix_with_no_ranges_is_fine(self):
        groups = {
            "B1": {"relay_matrix": "MATRIX_NUMATO_202", "positions": {1: {"relay_address": 1}}},
            "B2": {"relay_matrix": "MATRIX_NUMATO_202", "positions": {}},
            "B3": {"relay_matrix": "MATRIX_NUMATO_202", "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])

    def test_group_with_no_relay_matrix_is_ignored(self):
        groups = {"A1": {"relay_matrix": None, "positions": {}}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])


class InvalidRangeShapeTests(unittest.TestCase):
    def test_non_pair_relay_range_is_rejected(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 2, 3), "positions": {}}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("must be an (lo, hi) pair", errors[0])

    def test_non_integer_relay_range_is_rejected(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (1.5, 8), "positions": {}}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)

    def test_lo_greater_than_hi_is_rejected(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (8, 1), "positions": {}}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid range", errors[0])

    def test_lo_below_one_is_rejected(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (0, 8), "positions": {}}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)

    def test_range_exceeding_matrix_channel_count_is_rejected(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 40), "positions": {}}}
        matrix_configs = {"MATRIX_A": {"channel_count": 32}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups, matrix_configs), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("exceeds relay", errors[0])

    def test_valid_range_within_channel_count_passes(self):
        groups = {"B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 8), "positions": {}}}
        matrix_configs = {"MATRIX_A": {"channel_count": 32}}
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups, matrix_configs), errors)
        self.assertEqual(errors, [])


class PositionOutsideOwnRangeTests(unittest.TestCase):
    def test_position_relay_address_outside_group_range_is_rejected(self):
        groups = {
            "B1": {
                "relay_matrix": "MATRIX_A", "relay_range": (1, 8),
                "positions": {1: {"relay_address": 12}},
            },
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("outside this group's own relay_range", errors[0])

    def test_position_relay_address_inside_group_range_passes(self):
        groups = {
            "B1": {
                "relay_matrix": "MATRIX_A", "relay_range": (1, 8),
                "positions": {1: {"relay_address": 3}},
            },
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])


class CrossGroupOverlapTests(unittest.TestCase):
    def test_disjoint_ranges_on_one_matrix_pass(self):
        groups = {
            "B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 8), "positions": {}},
            "B2": {"relay_matrix": "MATRIX_A", "relay_range": (9, 16), "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])

    def test_overlapping_ranges_on_one_matrix_are_rejected(self):
        groups = {
            "B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 8), "positions": {}},
            "B2": {"relay_matrix": "MATRIX_A", "relay_range": (5, 12), "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("overlaps", errors[0])

    def test_whole_matrix_owner_alongside_ranged_owner_is_rejected(self):
        """Partial segmentation -- one group with no relay_range (whole
        matrix) and another declaring one on the SAME matrix -- must be
        rejected at config-load time, not just at runtime ownership
        validation."""
        groups = {
            "B1": {"relay_matrix": "MATRIX_A", "positions": {}},  # no relay_range
            "B2": {"relay_matrix": "MATRIX_A", "relay_range": (9, 16), "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("partial segmentation is not valid", errors[0])

    def test_different_matrices_never_conflict_regardless_of_ranges(self):
        groups = {
            "B1": {"relay_matrix": "MATRIX_A", "relay_range": (1, 8), "positions": {}},
            "C1": {"relay_matrix": "MATRIX_B", "relay_range": (1, 8), "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])

    def test_four_way_disjoint_option_b_example_passes(self):
        groups = {
            "B1": {"relay_matrix": "MATRIX_NUMATO_202", "relay_range": (1, 8), "positions": {}},
            "B2": {"relay_matrix": "MATRIX_NUMATO_202", "relay_range": (9, 16), "positions": {}},
            "B3": {"relay_matrix": "MATRIX_NUMATO_202", "relay_range": (17, 24), "positions": {}},
            "B4": {"relay_matrix": "MATRIX_NUMATO_202", "relay_range": (25, 32), "positions": {}},
        }
        errors = []
        _check_relay_range_definitions(_dev_cfg(groups), errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
