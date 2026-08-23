"""
Tests for the configurable validation timeout (see docs/architecture.md
"Configurable Validation Timeout"): a group's `test_setpoints` may declare
`charge_timeout_s`/`discharge_timeout_s` to override
`Settings.CHARGE_TIMEOUT_S`/`DISCHARGE_TIMEOUT_S` for that group only,
validated by `utils/validators.py::validate_group_test_config()`'s new
Stage 4 before any hardware is touched.

Scope note: this file covers (1) Stage 4's validation rules directly and
completely (real code, no fakes -- these are pure config-validation
checks), and (2) source-level checks that `ChargeSequence`/
`DischargeSequence` actually resolve and use the override in their
timeout comparison, not just that the config value is accepted.
Deliberately NOT included: a full fake-hardware `ChargeSequence.run()`
integration test reaching real EOC. `_run_charge()`'s full body threads
through storage/DMM/SMU/safety/execution-frame-rendering machinery this
test suite has no existing fake harness for, and the actual code change
under test is a single `dict.get(key, default)` call -- the source-level
check below already catches the realistic failure mode (wrong key name,
wrong default, or a comparison left reading the un-overridden Settings
constant directly) with high precision, matching the same
source-inspection technique already established in this suite (see
tests/test_cancellation.py::SigintInstalledBeforeHardwareInitTests and
tests/test_post_isolation_zeroing_ordering.py) for exactly this class of
"prove the wiring, not the whole hardware path" concern.

No hardware access anywhere in this file.
"""

import inspect
import re
import unittest

import config.devices as dev_cfg
from config.settings import Settings
from test_control import charge_sequence, discharge_sequence
from utils.errors import ConfigurationError, GroupConfigurationError
from utils.validators import validate_group_test_config


class _MutatesB1TestSetpoints(unittest.TestCase):
    """Shared snapshot/restore harness -- same pattern as
    tests/test_group_validation.py::ValidationRejectsOutOfRangeSetpointsTests,
    since Stage 4 (like every other validate_group_test_config() stage)
    reads the real, global config/devices.py::BATTERY_GROUPS directly."""

    def setUp(self):
        self._original_test_setpoints = dict(dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"])
        self._original_system_mode = Settings.SYSTEM_MODE
        self.addCleanup(self._restore)

    def _restore(self):
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = self._original_test_setpoints
        Settings.SYSTEM_MODE = self._original_system_mode

    def _set_setpoints(self, **overrides):
        setpoints = dict(self._original_test_setpoints)
        setpoints.update(overrides)
        dev_cfg.BATTERY_GROUPS["B1"]["test_setpoints"] = setpoints


class BackwardCompatibilityTests(_MutatesB1TestSetpoints):
    """Timeout enabled (default): no override present -- Stage 4 must be a
    complete no-op, exactly matching pre-existing behavior."""

    def test_b1_real_config_has_no_override_and_validates_cleanly(self):
        self.assertNotIn("charge_timeout_s", self._original_test_setpoints)
        self.assertNotIn("discharge_timeout_s", self._original_test_setpoints)
        result = validate_group_test_config("B1")  # must not raise
        self.assertNotIn("charge_timeout_s", result["test_setpoints"])

    def test_production_mode_with_no_override_is_unaffected(self):
        Settings.SYSTEM_MODE = "PRODUCTION"
        validate_group_test_config("B1")  # must not raise -- no override, nothing to reject


class ValidOverrideAcceptedInNonProductionTests(_MutatesB1TestSetpoints):
    def setUp(self):
        super().setUp()
        Settings.SYSTEM_MODE = "VALIDATION"

    def test_valid_charge_timeout_override_is_accepted(self):
        self._set_setpoints(charge_timeout_s=3600)
        result = validate_group_test_config("B1")
        self.assertEqual(result["test_setpoints"]["charge_timeout_s"], 3600)

    def test_valid_discharge_timeout_override_is_accepted(self):
        self._set_setpoints(discharge_timeout_s=3600)
        validate_group_test_config("B1")  # must not raise

    def test_override_exactly_at_the_ceiling_is_accepted(self):
        self._set_setpoints(charge_timeout_s=Settings.MAX_TIMEOUT_OVERRIDE_S)
        validate_group_test_config("B1")  # must not raise -- boundary is inclusive

    def test_development_mode_also_accepts_a_valid_override(self):
        Settings.SYSTEM_MODE = "DEVELOPMENT"
        self._set_setpoints(charge_timeout_s=3600)
        validate_group_test_config("B1")  # must not raise


class InvalidOverrideValueRejectedTests(_MutatesB1TestSetpoints):
    def setUp(self):
        super().setUp()
        Settings.SYSTEM_MODE = "VALIDATION"

    def test_zero_is_rejected(self):
        self._set_setpoints(charge_timeout_s=0)
        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")

    def test_negative_is_rejected(self):
        self._set_setpoints(discharge_timeout_s=-100)
        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")

    def test_non_numeric_is_rejected(self):
        self._set_setpoints(charge_timeout_s="a very long time")
        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")

    def test_bool_is_rejected_despite_being_an_int_subclass(self):
        self._set_setpoints(charge_timeout_s=True)
        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")

    def test_value_above_ceiling_is_rejected(self):
        self._set_setpoints(charge_timeout_s=Settings.MAX_TIMEOUT_OVERRIDE_S + 1)
        with self.assertRaises(ConfigurationError):
            validate_group_test_config("B1")


class ProductionModeRefusesOverrideTests(_MutatesB1TestSetpoints):
    """Prevent accidental use in production (design requirement #3): a
    timeout override present in a PRODUCTION-mode group's config is
    itself a configuration error, refused outright -- never silently
    ignored/falls back to the default."""

    def setUp(self):
        super().setUp()
        Settings.SYSTEM_MODE = "PRODUCTION"

    def test_charge_timeout_override_is_rejected_in_production(self):
        self._set_setpoints(charge_timeout_s=3600)
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")

    def test_discharge_timeout_override_is_rejected_in_production(self):
        self._set_setpoints(discharge_timeout_s=3600)
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")

    def test_rejected_even_when_the_value_itself_would_otherwise_be_valid(self):
        # Must fail on the PRODUCTION-mode check, not be let through because
        # the number itself is well-formed.
        self._set_setpoints(charge_timeout_s=1)
        with self.assertRaises(GroupConfigurationError):
            validate_group_test_config("B1")


class _ResolutionWiringMixin:
    """Parses `charge_timeout_s = test_setpoints.get("charge_timeout_s", self.s.CHARGE_TIMEOUT_S)`
    -style assignment out of source and confirms the SAME variable name
    (not the raw Settings constant) is what the timeout comparison
    actually reads."""

    def _assert_override_is_wired_into_comparison(self, src: str, override_key: str, settings_const: str):
        assign_pattern = re.compile(
            r"(\w+)\s*=\s*test_setpoints\.get\(\s*[\"']" + re.escape(override_key)
            + r"[\"']\s*,\s*self\.s\." + re.escape(settings_const) + r"\s*\)"
        )
        match = assign_pattern.search(src)
        self.assertIsNotNone(
            match,
            f"expected a 'X = test_setpoints.get({override_key!r}, self.s.{settings_const})' "
            f"assignment in source",
        )
        variable_name = match.group(1)

        compare_pattern = re.compile(r"if\s+elapsed\s*>\s*(\w+)\s*:")
        compare_match = compare_pattern.search(src)
        self.assertIsNotNone(compare_match, "expected an 'if elapsed > <var>:' timeout comparison")
        self.assertEqual(
            compare_match.group(1), variable_name,
            f"the timeout comparison must use the resolved override variable "
            f"({variable_name!r}), not a direct Settings reference",
        )


class ChargeSequenceTimeoutWiringTests(_ResolutionWiringMixin, unittest.TestCase):
    def test_charge_timeout_override_is_resolved_and_used_in_the_comparison(self):
        src = inspect.getsource(charge_sequence.ChargeSequence.run)
        self._assert_override_is_wired_into_comparison(src, "charge_timeout_s", "CHARGE_TIMEOUT_S")


class DischargeSequenceTimeoutWiringTests(_ResolutionWiringMixin, unittest.TestCase):
    def test_discharge_timeout_override_is_resolved_and_used_in_the_comparison(self):
        src = inspect.getsource(discharge_sequence.DischargeSequence.run)
        self._assert_override_is_wired_into_comparison(src, "discharge_timeout_s", "DISCHARGE_TIMEOUT_S")


if __name__ == "__main__":
    unittest.main()
