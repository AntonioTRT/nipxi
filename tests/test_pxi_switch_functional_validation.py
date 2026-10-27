"""
Tests for the PXI-resident switch/relay card (PXI_SLOTS[11], nickname
CHASSIS_RELAY_MATRIX)'s Functional Validation path -- test.py::
_functional_switch(), wired into test_pxi_relay_matrix() via the shared
_run_hardware_category() workflow.

When the card is NOT enabled as a relay matrix (PXI_SLOTS[11]
enabled=False -> absent from RELAY_MATRIX_CONFIGS) there is no relay driver
to validate -- these tests confirm Functional Validation then reports an
honest WARNING/N/A result rather than _run_hardware_category()'s generic
"not yet implemented" placeholder, and never touches hardware. When it IS
enabled, Functional Validation runs Relay Range Validation against it.
"""

import unittest
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from config import devices as dev_cfg


def _chassis_relay_matrix_cfg():
    return next(c for c in dev_cfg.PXI_SLOTS.values() if c["nickname"] == "CHASSIS_RELAY_MATRIX")


class FunctionalSwitchTests(unittest.TestCase):
    def setUp(self):
        # Simulate the card NOT being enabled as a relay matrix.
        patcher = mock.patch.dict(dev_cfg.RELAY_MATRIX_CONFIGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        dev_cfg.RELAY_MATRIX_CONFIGS.pop("CHASSIS_RELAY_MATRIX", None)

    def test_reports_warning_not_pass_or_fail(self):
        cfg = _chassis_relay_matrix_cfg()
        results = test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, test_module.Status.WARNING)

    def test_device_label_uses_the_relay_matrix_display_name(self):
        cfg = _chassis_relay_matrix_cfg()
        result = test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)[0]
        self.assertEqual(result.device, "Switch/Relay (PXI): RelayMatrix-Slot11")

    def test_config_ref_matches_identity_validations_own_format(self):
        cfg = _chassis_relay_matrix_cfg()
        identity_ref = test_module._identify_switch("CHASSIS_RELAY_MATRIX", cfg).config_ref
        functional_ref = test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)[0].config_ref
        self.assertEqual(identity_ref, functional_ref)
        self.assertIn("PXI_SLOTS['CHASSIS_RELAY_MATRIX']", functional_ref)
        self.assertIn("PXI1Slot11", functional_ref)
        self.assertIn("PXIe-2569", functional_ref)

    def test_details_include_the_not_enabled_reason_and_validation_notes(self):
        cfg = _chassis_relay_matrix_cfg()
        result = test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)[0]
        self.assertIn("not enabled as a relay matrix", result.details)
        self.assertIn(cfg["validation_notes"], result.details)

    def test_never_imports_or_touches_any_hardware_driver(self):
        # This function never imports or constructs a driver itself -- the
        # enabled path delegates to test_relay_range_validation(). (The word
        # "niswitch" legitimately appears in prose, so check for actual
        # import/construction patterns, not the bare word.)
        import inspect
        src = inspect.getsource(test_module._functional_switch)
        for forbidden in ("import niswitch", "RelayFactory", "hardware.relay", "hardware.switch"):
            self.assertNotIn(forbidden, src)


class FunctionalSwitchRoutesToRangeValidationWhenEnabledTests(unittest.TestCase):
    def test_enabled_niswitch_matrix_runs_relay_range_validation(self):
        relay_cfg = dev_cfg.RELAY_MATRIX_CONFIGS["CHASSIS_RELAY_MATRIX"]
        self.assertEqual(relay_cfg["type"], "niswitch")
        sentinel = [object()]
        with mock.patch.object(test_module, "test_relay_range_validation", return_value=sentinel) as run:
            result = test_module._functional_switch("CHASSIS_RELAY_MATRIX", _chassis_relay_matrix_cfg())
        run.assert_called_once_with("CHASSIS_RELAY_MATRIX", relay_cfg)
        self.assertIs(result, sentinel)


class PxiRelayMatrixMenuWiringTests(unittest.TestCase):
    def test_functional_validation_is_wired_not_left_as_the_generic_placeholder(self):
        import inspect
        src = inspect.getsource(test_module.test_pxi_relay_matrix)
        self.assertIn("_functional_switch", src)
        self.assertIn(
            '_run_hardware_category("PXI Relay Matrix", devices, _identify_switch, _functional_switch)',
            src,
        )


if __name__ == "__main__":
    unittest.main()
