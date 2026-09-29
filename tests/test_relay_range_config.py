"""
Tests for config/devices.py::compose_relay_resource_name() -- Matrix +
Relay-Range Ownership, Phase 1. Pure logic only; no hardware access.
"""

import unittest

import config.devices as dev_cfg


class ComposeRelayResourceNameTests(unittest.TestCase):
    def test_no_range_returns_bare_matrix_name_unchanged(self):
        self.assertEqual(dev_cfg.compose_relay_resource_name("MATRIX_NUMATO_202"), "MATRIX_NUMATO_202")
        self.assertEqual(dev_cfg.compose_relay_resource_name("MATRIX_NUMATO_202", None), "MATRIX_NUMATO_202")

    def test_none_matrix_returns_none(self):
        self.assertIsNone(dev_cfg.compose_relay_resource_name(None))
        self.assertIsNone(dev_cfg.compose_relay_resource_name(None, (1, 8)))

    def test_range_is_composed_as_suffix(self):
        self.assertEqual(
            dev_cfg.compose_relay_resource_name("MATRIX_NUMATO_202", (1, 8)),
            "MATRIX_NUMATO_202:1-8",
        )
        self.assertEqual(
            dev_cfg.compose_relay_resource_name("MATRIX_NUMATO_202", (25, 32)),
            "MATRIX_NUMATO_202:25-32",
        )


class HardwareForGroupBackwardCompatibilityTests(unittest.TestCase):
    """
    The existing B1 deployment declares no 'relay_range' at all (not even
    key=None) -- hardware_for_group()'s relay_matrix_name for B1 must
    remain byte-for-byte "MATRIX_NUMATO_202", exactly as before Phase 1.
    """

    def test_b1_relay_matrix_name_is_unchanged(self):
        hw = dev_cfg.hardware_for_group("B1")
        self.assertEqual(hw["relay_matrix_name"], "MATRIX_NUMATO_202")
        self.assertEqual(hw["relay_matrix_cfg"], dev_cfg.ETHERNET_DEVICES.get("MATRIX_NUMATO_202"))

    def test_every_production_group_has_no_relay_range_declared(self):
        """Documents today's actual production state: not a single group
        in BATTERY_GROUPS uses relay_range yet -- this is the config-level
        guarantee that makes Phase 1-3 a pure no-op against production."""
        for group_name, grp in dev_cfg.BATTERY_GROUPS.items():
            self.assertIsNone(
                grp.get("relay_range"),
                f"BATTERY_GROUPS[{group_name!r}] unexpectedly declares a relay_range",
            )


if __name__ == "__main__":
    unittest.main()
