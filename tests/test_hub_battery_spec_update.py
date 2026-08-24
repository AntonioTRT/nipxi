"""
Tests for the HUB battery configuration update against the real cell
datasheet (2026-08-24 config review) -- see docs/architecture.md "HUB
Battery Configuration Update: Real Datasheet Values".

Documents what is now true (mirroring the established pattern in
tests/test_sense_router.py::HardwareForGroupSenseChannelTests): asserts
directly against the live config/settings values, so any future change to
these safety-relevant figures fails loudly here rather than silently.
"""

import unittest

import config.devices as dev_cfg
from config.settings import Settings
from utils.validators import validate_group_test_config


class HubBatteryConfigValuesTests(unittest.TestCase):
    """
    Every HUB value below was chosen using the nominal/recommended
    datasheet figure, never the absolute-maximum figure (see
    config/devices.py's inline rationale for each field) -- these tests
    pin the actual chosen numbers, not just "a number exists".
    """

    def setUp(self):
        self.hub = dev_cfg.BATTERY_CONFIGS["HUB"]

    def test_nominal_voltage_is_the_real_datasheet_value(self):
        self.assertEqual(self.hub["nominal_voltage_v"], 3.6)

    def test_voltage_window_matches_the_real_operating_range(self):
        self.assertEqual(self.hub["voltage_max_v"], 4.2)
        self.assertEqual(self.hub["voltage_min_v"], 2.75)

    def test_capacity_uses_the_conservative_rated_figure_not_the_higher_nominal_range(self):
        # Datasheet: "Rated capacity: 3050 mAh" vs "Nominal capacity:
        # 3120-3220 mAh" (the higher, typical/average figure) -- the
        # lower, guaranteed-minimum rated figure is used.
        self.assertEqual(self.hub["capacity_ah"], 3.05)

    def test_max_charge_current_uses_the_typical_figure_not_the_maximum(self):
        # Datasheet: "2000 mA typical / 2233 mA maximum" -- 2233 mA must
        # NOT be used directly as a runtime safety ceiling.
        self.assertEqual(self.hub["max_charge_current_a"], 2.0)
        self.assertNotEqual(self.hub["max_charge_current_a"], 2.233)

    def test_max_discharge_current_uses_the_conservative_end_of_the_nominal_range(self):
        # Datasheet: "Nominal discharge current: 2000-2700 mA" (a range,
        # not a typical/max pair) -- the lower, more conservative bound is
        # used as the enforced ceiling.
        self.assertEqual(self.hub["max_discharge_current_a"], 2.0)
        self.assertNotEqual(self.hub["max_discharge_current_a"], 2.7)

    def test_max_temp_c_was_not_guessed(self):
        # The provided battery spec gives no temperature rating -- this
        # remains the pre-existing unconfirmed placeholder, unchanged by
        # this review. This test exists to make that omission explicit
        # (not silently indistinguishable from a confirmed value).
        self.assertEqual(self.hub["max_temp_c"], 45.0)


class ChargeCutoffMatchesRealTerminationThresholdTests(unittest.TestCase):
    def test_charge_cutoff_a_is_the_real_150ma_termination_threshold(self):
        # Datasheet: "Termination threshold: 150 mA". Was 0.05 A (50 mA),
        # an unconfirmed placeholder.
        self.assertEqual(Settings.CHARGE_CUTOFF_A, 0.15)


class B1SetpointsStillValidateAgainstTheUpdatedHubLimitsTests(unittest.TestCase):
    """
    B1's test_setpoints (the deliberately CHOSEN validation operating
    point -- 1.0 A / 4.0 V charge, 0.08 A / 3.0 V discharge) were not
    changed by this review, but the HUB limits they are validated against
    were. This proves they remain comfortably valid against the new, real
    limits -- i.e. this config update did not silently invalidate the
    active validation run.
    """

    def test_b1_real_setpoints_pass_full_validation_against_updated_hub_limits(self):
        validated = validate_group_test_config("B1")
        setpoints = validated["test_setpoints"]
        self.assertEqual(setpoints["charge_current_a"], 1.0)
        self.assertEqual(setpoints["charge_voltage_v"], 4.0)

    def test_discharge_cutoff_is_now_above_not_exactly_at_the_real_safety_floor(self):
        # Previously discharge_cutoff_v (3.0) was exactly == voltage_min_v
        # (also 3.0, an unconfirmed placeholder). voltage_min_v is now the
        # real, confirmed 2.75 V floor -- discharge_cutoff_v was left
        # unchanged, which is now a deliberately conservative 0.25 V
        # margin above the real floor, not an exact match. A cutoff below
        # the real floor would be the actual safety concern; a cutoff
        # above it is strictly more conservative, not less safe.
        hub = dev_cfg.BATTERY_CONFIGS["HUB"]
        setpoints = dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"]
        self.assertGreater(setpoints["discharge_cutoff_v"], hub["voltage_min_v"])


if __name__ == "__main__":
    unittest.main()
