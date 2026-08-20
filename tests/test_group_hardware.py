"""
Tests for utils/group_hardware.py -- the extraction of test.py's
_resolve_group_hardware()/_missing_hardware_roles()/position-bounds-check
logic into a shared, pure module (see docs/architecture.md "Preparation
Phase: Six Resolved Decisions Before worker_runtime.py").

No hardware access anywhere in this file -- everything here is pure
logic over config/devices.py.
"""

import unittest

import config.devices as dev_cfg
from utils.group_hardware import (
    missing_hardware_roles,
    resolve_group_hardware,
    validate_position_in_group,
)


class MissingHardwareRolesTests(unittest.TestCase):
    def test_no_missing_roles_for_fully_assigned_group(self):
        hw = dev_cfg.hardware_for_group("B1")
        self.assertEqual(missing_hardware_roles(hw), [])

    def test_reports_a_missing_role(self):
        hw = dict(dev_cfg.hardware_for_group("B1"))
        hw["dmm_cfg"] = None
        self.assertEqual(missing_hardware_roles(hw), ["dmm"])

    def test_respects_custom_required_roles(self):
        hw = dict(dev_cfg.hardware_for_group("B1"))
        hw["dmm_cfg"] = None
        # dmm not required -> not reported even though it's None
        self.assertEqual(missing_hardware_roles(hw, required_roles=("relay_matrix", "smu")), [])


class ResolveGroupHardwareTests(unittest.TestCase):
    def test_b1_resolves_successfully_with_no_on_fail_call(self):
        calls = []
        result = resolve_group_hardware("B1", on_fail=calls.append)
        self.assertIsNotNone(result)
        hw, battery_type, battery_cfg = result
        self.assertEqual(battery_type, "HUB")
        self.assertEqual(hw["smu_name"], "AUX_SMU_1")
        self.assertEqual(calls, [], "on_fail must not be called on a successful resolution")

    def test_on_fail_is_none_safe(self):
        result = resolve_group_hardware("B1")  # on_fail omitted entirely
        self.assertIsNotNone(result)

    def test_missing_required_role_calls_on_fail_and_returns_none(self):
        calls = []
        original = dev_cfg.hardware_for_group

        def _patched(group):
            hw = dict(original(group))
            hw["smu_cfg"] = None
            return hw

        dev_cfg.hardware_for_group = _patched
        try:
            result = resolve_group_hardware("B1", on_fail=calls.append)
        finally:
            dev_cfg.hardware_for_group = original

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertIn("[FAIL]", calls[0])
        self.assertIn("smu", calls[0])

    def test_unknown_battery_type_calls_on_fail_and_returns_none(self):
        calls = []
        original = dev_cfg.group_test_config

        def _patched(group):
            cfg = dict(original(group))
            cfg["battery_type"] = "NOT_A_REAL_BATTERY_TYPE"
            return cfg

        dev_cfg.group_test_config = _patched
        try:
            result = resolve_group_hardware("B1", on_fail=calls.append)
        finally:
            dev_cfg.group_test_config = original

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertIn("unknown battery_type", calls[0])


class ValidatePositionInGroupTests(unittest.TestCase):
    def test_in_range_position_is_valid(self):
        size = dev_cfg.group_size("B1")
        self.assertTrue(validate_position_in_group("B1", 1))
        self.assertTrue(validate_position_in_group("B1", size))

    def test_out_of_range_position_is_invalid(self):
        size = dev_cfg.group_size("B1")
        self.assertFalse(validate_position_in_group("B1", 0))
        self.assertFalse(validate_position_in_group("B1", size + 1))

    def test_is_a_pure_predicate_no_exception_no_side_effect(self):
        # Must never raise for an absurd out-of-range value -- just False.
        self.assertFalse(validate_position_in_group("B1", -999))
        self.assertFalse(validate_position_in_group("B1", 999999))


if __name__ == "__main__":
    unittest.main()
