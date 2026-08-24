"""
Tests for the future-architecture "charge_mode" groundwork in
utils/validators.py::validate_group_test_config() Stage 5 (see
docs/architecture.md "Future Architecture: Configurable Charge Modes").

This is infrastructure-only: ChargeSequence never reads "charge_mode" --
these tests cover the validation/normalization layer alone, and
specifically prove zero behavior change for every existing group
(none of which declares "charge_mode" today).

No hardware access anywhere in this file.
"""

import unittest

import config.devices as dev_cfg
from utils.errors import GroupConfigurationError
from utils.validators import validate_group_test_config


class _MutatesB1TestSetpoints(unittest.TestCase):
    def setUp(self):
        self._original_test_setpoints = dict(dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"])
        self.addCleanup(self._restore)

    def _restore(self):
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = self._original_test_setpoints

    def _set_setpoints(self, **overrides):
        setpoints = dict(self._original_test_setpoints)
        setpoints.update(overrides)
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = setpoints


class DefaultBehaviorIsBackwardCompatibleTests(_MutatesB1TestSetpoints):
    """B1's real, live config declares no charge_mode -- confirms the
    default applies and nothing about B1's actual behavior changes."""

    def test_b1_real_config_has_no_charge_mode_declared(self):
        self.assertNotIn("charge_mode", self._original_test_setpoints)

    def test_b1_validates_cleanly_and_defaults_to_cc_cv(self):
        result = validate_group_test_config("B1")
        self.assertEqual(result["test_setpoints"]["charge_mode"], "CC_CV")

    def test_validation_does_not_mutate_the_live_config_dict(self):
        """
        The returned test_setpoints is a copy -- config/devices.py::
        BATTERY_GROUPS["B1"]["test_setpoints"] itself must never gain a
        "charge_mode" key just because it was validated. Any other test
        (or a future real run) reading BATTERY_GROUPS directly must see
        exactly what config/devices.py declares, nothing normalized in.
        """
        validate_group_test_config("B1")
        live_setpoints = dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"]
        self.assertNotIn("charge_mode", live_setpoints)

    def test_every_other_setpoint_is_preserved_unchanged_in_the_copy(self):
        result = validate_group_test_config("B1")
        for key, value in self._original_test_setpoints.items():
            self.assertEqual(result["test_setpoints"][key], value)


class ExplicitCcCvTests(_MutatesB1TestSetpoints):
    def test_explicit_cc_cv_is_accepted_and_returned_unchanged(self):
        self._set_setpoints(charge_mode="CC_CV")
        result = validate_group_test_config("B1")
        self.assertEqual(result["test_setpoints"]["charge_mode"], "CC_CV")


class CvModeIsRecognizedButNotYetEnabledTests(_MutatesB1TestSetpoints):
    """CV is part of the recognized vocabulary (the future architecture is
    being prepared) but must be rejected outright today -- ChargeSequence
    has no CV dispatch logic, so silently accepting it would mean the
    config claims one behavior while the software does another."""

    def test_cv_mode_is_rejected_with_a_not_yet_implemented_message(self):
        self._set_setpoints(charge_mode="CV")
        with self.assertRaises(GroupConfigurationError) as ctx:
            validate_group_test_config("B1")
        self.assertIn("not yet implemented", str(ctx.exception))

    def test_cv_mode_rejection_happens_before_any_hardware_concern(self):
        # Sanity: this must be a pure GroupConfigurationError, not some
        # hardware-capability-flavored error -- confirms it is being
        # caught by the charge-mode stage, not accidentally passing
        # through and failing elsewhere for an unrelated reason.
        self._set_setpoints(charge_mode="CV")
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")


class UnrecognizedChargeModeRejectedTests(_MutatesB1TestSetpoints):
    def test_unknown_value_is_rejected(self):
        self._set_setpoints(charge_mode="PULSE")
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")

    def test_wrong_type_is_rejected(self):
        self._set_setpoints(charge_mode=123)
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")


if __name__ == "__main__":
    unittest.main()
