"""
Tests for the PXI-resident switch/relay card (PXI_SLOTS[11], nickname
CHASSIS_RELAY_MATRIX)'s Functional Validation path -- test.py::
_functional_switch(), wired into test_pxi_relay_matrix() via the shared
_run_hardware_category() workflow.

No niswitch-based driver exists in this codebase for this card (see
PXI_SLOTS[11]'s own validation_notes) -- these tests confirm Functional
Validation reports the same honest WARNING/N/A result Identity
Validation already does, rather than _run_hardware_category()'s generic
"not yet implemented" placeholder, and never touches hardware.
"""

import unittest

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from config import devices as dev_cfg


def _chassis_relay_matrix_cfg():
    return next(c for c in dev_cfg.PXI_SLOTS.values() if c["nickname"] == "CHASSIS_RELAY_MATRIX")


class FunctionalSwitchTests(unittest.TestCase):
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

    def test_details_include_the_no_driver_reason_and_validation_notes(self):
        cfg = _chassis_relay_matrix_cfg()
        result = test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)[0]
        self.assertIn("no niswitch-based driver exists", result.details)
        self.assertIn(cfg["validation_notes"], result.details)

    def test_never_imports_or_touches_any_hardware_driver(self):
        # No driver class exists for this category -- confirm this
        # function's source never imports or constructs one, matching
        # _identify_switch()'s identical, already-established guarantee.
        # (The word "niswitch" itself legitimately appears in prose --
        # explaining THAT no such driver exists -- so check for actual
        # import/construction patterns, not the bare word.)
        import inspect
        src = inspect.getsource(test_module._functional_switch)
        for forbidden in ("import niswitch", "RelayFactory", "hardware.relay", "hardware.switch"):
            self.assertNotIn(forbidden, src)


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
