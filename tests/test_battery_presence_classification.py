"""
Tests for test_control/battery_diagnostics.py::classify_battery_presence()
-- the pure voltage classifier behind the Battery Presence Check (see
docs/architecture.md "Battery Presence + NTC Presence Diagnostics").

Pure math -- no hardware. Confirms the three-zone partition built from two
ALREADY-established thresholds (Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V
and battery_diagnostics.EMPTY_POSITION_VOLTAGE_V), not a new invented number.
"""

import unittest

from config.settings import Settings
from test_control.battery_diagnostics import (
    EMPTY_POSITION_VOLTAGE_V, BatteryPresence, classify_battery_presence,
)


class BatteryPresenceBoundaryTests(unittest.TestCase):
    def test_healthy_cell_voltage_is_present(self):
        self.assertEqual(classify_battery_presence(3.67), BatteryPresence.PRESENT)

    def test_deeply_discharged_but_real_cell_is_still_present(self):
        # A real cell well below any operating floor, but still far above
        # the "open circuit" band -- must not be misclassified as absent.
        self.assertEqual(classify_battery_presence(1.5), BatteryPresence.PRESENT)

    def test_just_above_the_empty_threshold_is_present(self):
        self.assertEqual(classify_battery_presence(EMPTY_POSITION_VOLTAGE_V + 0.01), BatteryPresence.PRESENT)

    def test_zero_volts_is_absent(self):
        self.assertEqual(classify_battery_presence(0.0), BatteryPresence.ABSENT)

    def test_exactly_at_the_empty_threshold_is_absent(self):
        self.assertEqual(classify_battery_presence(EMPTY_POSITION_VOLTAGE_V), BatteryPresence.ABSENT)

    def test_small_negative_noise_is_absent_not_reversed(self):
        # ADC/DMM offset noise on a near-zero (disconnected) position --
        # must not be misclassified as a reversed cell.
        self.assertEqual(classify_battery_presence(-0.01), BatteryPresence.ABSENT)

    def test_just_above_the_reverse_polarity_threshold_is_absent(self):
        just_above = Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V + 0.01
        self.assertEqual(classify_battery_presence(just_above), BatteryPresence.ABSENT)

    def test_exactly_at_the_reverse_polarity_threshold_is_reversed(self):
        self.assertEqual(
            classify_battery_presence(Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V),
            BatteryPresence.REVERSED,
        )

    def test_sharply_negative_voltage_is_reversed(self):
        self.assertEqual(classify_battery_presence(-3.7), BatteryPresence.REVERSED)


if __name__ == "__main__":
    unittest.main()
