"""
Regression tests for utils/validators.py::validate_group_test_config()
behavior confirmed during this validation cycle -- Group B1's battery_type
switch (SB -> HUB) and the 0.5 A charge-current setpoint, plus the
negative case that caught the original SB-ceiling mismatch in the first
place.

These assert BEHAVIOR (does validation pass/fail, and with what battery
config), not the specific numeric values in config/devices.py today --
if B1's setpoints or battery_type change again during ongoing hardware
validation, only the fixtures below need updating, not the test logic.
"""

import unittest

import config.devices as dev_cfg
from utils.errors import ConfigurationError, HardwareConfigurationError
from utils.validators import validate_group_test_config


class B1CurrentConfigTests(unittest.TestCase):
    """Asserts against B1's actual, current config/devices.py values --
    documents "what is true today" and fails loudly if that config changes
    without an accompanying test update, which is the point."""

    def test_b1_validates_cleanly_with_current_config(self):
        result = validate_group_test_config("B1")
        self.assertIn(result["battery_type"], dev_cfg.BATTERY_CONFIGS)
        battery_cfg = dev_cfg.BATTERY_CONFIGS[result["battery_type"]]
        setpoints = result["test_setpoints"]
        self.assertLessEqual(setpoints["charge_current_a"], battery_cfg["max_charge_current_a"])
        self.assertLessEqual(setpoints["charge_voltage_v"], battery_cfg["voltage_max_v"])
        self.assertLessEqual(setpoints["discharge_current_a"], battery_cfg["max_discharge_current_a"])
        self.assertGreaterEqual(setpoints["discharge_cutoff_v"], battery_cfg["voltage_min_v"])

    def test_b1_smu_capability_satisfied(self):
        result = validate_group_test_config("B1")
        hw = dev_cfg.hardware_for_group("B1")
        smu_max_a = hw["smu_cfg"]["max_current_a"]
        self.assertLessEqual(result["test_setpoints"]["charge_current_a"], smu_max_a)
        self.assertLessEqual(result["test_setpoints"]["discharge_current_a"], smu_max_a)


class ValidationRejectsOutOfRangeSetpointsTests(unittest.TestCase):
    """
    The exact class of mistake found and corrected mid-cycle: a commanded
    current that exceeds the declared battery_type's own ceiling must be
    rejected BEFORE any hardware is touched, never silently clamped or
    allowed through.
    """

    def setUp(self):
        # Snapshot and restore -- these tests intentionally mutate the
        # live config dicts to construct an out-of-range scenario, then
        # must leave B1 exactly as they found it for every other test in
        # this suite (and for real hardware validation, if run against a
        # live checkout).
        self._original_test_setpoints = dict(dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"])
        self._original_battery_type = dev_cfg.BATTERY_GROUPS["B1"]["battery_type"]
        self._original_hub_max_charge_current_a = dev_cfg.BATTERY_CONFIGS["HUB"]["max_charge_current_a"]

    def tearDown(self):
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = self._original_test_setpoints
        dev_cfg.BATTERY_GROUPS["B1"]["battery_type"] = self._original_battery_type
        dev_cfg.BATTERY_CONFIGS["HUB"]["max_charge_current_a"] = self._original_hub_max_charge_current_a

    def test_charge_current_exceeding_battery_ceiling_is_rejected(self):
        dev_cfg.BATTERY_GROUPS["B1"]["battery_type"] = "SB"
        setpoints = dict(self._original_test_setpoints)
        setpoints["charge_current_a"] = dev_cfg.BATTERY_CONFIGS["SB"]["max_charge_current_a"] + 0.1
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = setpoints

        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")

    def test_charge_current_exceeding_smu_capability_is_rejected(self):
        hw = dev_cfg.hardware_for_group("B1")
        smu_max_a = hw["smu_cfg"]["max_current_a"]
        setpoints = dict(self._original_test_setpoints)
        setpoints["charge_current_a"] = smu_max_a + 0.1
        # Make sure the battery ceiling itself would not also reject this,
        # so the failure is unambiguously attributable to SMU capability.
        dev_cfg.BATTERY_GROUPS["B1"]["battery_type"] = "HUB"
        dev_cfg.BATTERY_CONFIGS["HUB"]["max_charge_current_a"] = smu_max_a + 1.0
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = setpoints
        with self.assertRaises(HardwareConfigurationError):
            validate_group_test_config("B1")

    def test_discharge_cutoff_below_safety_floor_is_rejected(self):
        dev_cfg.BATTERY_GROUPS["B1"]["battery_type"] = "SB"
        setpoints = dict(self._original_test_setpoints)
        setpoints["discharge_cutoff_v"] = dev_cfg.BATTERY_CONFIGS["SB"]["voltage_min_v"] - 0.5
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = setpoints

        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")


if __name__ == "__main__":
    unittest.main()
