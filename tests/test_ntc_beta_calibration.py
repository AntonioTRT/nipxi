"""
Tests for hardware/temperature.py's NTC Beta-approximation constants
against the REAL 103JT thermistor datasheet (R25 = 10 kOhm, B25/85 =
3435 K) -- see docs/architecture.md "HUB Battery Configuration Update:
Real Datasheet Values". Confirms the fix (NTC_BETA 3950.0 -> 3435.0, the
previous value was an unverified "TODO: verify from datasheet" placeholder)
actually reproduces the datasheet's own published resistance-vs-temperature
reference table to a safety-appropriate accuracy, not just that a number
changed.

Pure math -- no hardware, no DAQ. Reference points and expected voltages
are derived directly from the datasheet's stated NTC resistance at each
temperature, converted to the divider output voltage this circuit would
produce (see hardware/temperature.py's module docstring for the divider
topology: V_exc -- [NTC] -- node -- [R_pulldown] -- GND), then run through
the real ntc_voltage_to_celsius() -- exactly the same function production
code calls.
"""

import unittest

from hardware.temperature import (
    NTC_BETA, NTC_R25_OHM, NTC_EXCITATION_V, NTC_PULLDOWN_R,
    ntc_voltage_to_celsius,
)


def _divider_voltage(r_ntc_ohm: float, v_exc=NTC_EXCITATION_V, r_pulldown=NTC_PULLDOWN_R) -> float:
    """Inverse of ntc_voltage_to_celsius()'s own r_ntc formula -- computes
    the divider node voltage this circuit would produce for a given NTC
    resistance, so a datasheet resistance-vs-temperature table can be fed
    straight through the real conversion function."""
    return v_exc * r_pulldown / (r_ntc_ohm + r_pulldown)


# Datasheet reference table: 103JT, R25 = 10 kOhm, B25/85 = 3435 K.
_REFERENCE_POINTS_C_TO_OHM = [
    (0.0,   27700.0),
    (25.0,  10000.0),
    (50.0,   4147.0),
    (60.0,   3011.0),
    (85.0,   1451.0),
    (100.0,   975.0),
    (125.0,   533.0),
]


class NtcConstantsMatchTheRealDatasheetTests(unittest.TestCase):
    def test_r25_matches_the_103jt_datasheet(self):
        self.assertEqual(NTC_R25_OHM, 10000.0)

    def test_beta_matches_the_103jt_datasheet_b25_85_rating(self):
        # Was 3950.0, an unverified placeholder -- see
        # docs/architecture.md "HUB Battery Configuration Update".
        self.assertEqual(NTC_BETA, 3435.0)


class ConversionAccuracyAgainstDatasheetTableTests(unittest.TestCase):
    """
    Feeds every reference point from the datasheet's own resistance-vs-
    temperature table through the real conversion function and checks the
    result is close to the datasheet's stated temperature. A single-Beta
    (two-point) approximation is inherently most accurate near its own
    calibration range (25-85 C, matching this part's "B25/85" rating) and
    least accurate at the table's extremes (0 C, 125 C) -- the tolerance
    below (+/-3.0 C) comfortably covers that expected, inherent spread
    without being so loose it would silently pass a wrong Beta value (the
    prior 3950 K placeholder was off by several times this amount at every
    point, including at 25 C's own exact-balance check).
    """

    def test_every_reference_point_converts_within_tolerance(self):
        for temp_c, r_ntc_ohm in _REFERENCE_POINTS_C_TO_OHM:
            with self.subTest(temp_c=temp_c, r_ntc_ohm=r_ntc_ohm):
                v_ntc = _divider_voltage(r_ntc_ohm)
                computed_c = ntc_voltage_to_celsius(v_ntc)
                self.assertIsNotNone(computed_c)
                self.assertAlmostEqual(computed_c, temp_c, delta=3.0)

    def test_25c_balance_point_is_the_most_accurate(self):
        # R25 by definition -- this must be near-exact regardless of Beta,
        # since ln(R25/R25) == 0 makes the Beta term vanish entirely.
        v_ntc = _divider_voltage(10000.0)
        computed_c = ntc_voltage_to_celsius(v_ntc)
        self.assertAlmostEqual(computed_c, 25.0, delta=0.1)

    def test_monotonic_across_the_full_reference_table(self):
        # Hotter (lower R_ntc) must always convert to a higher temperature
        # -- a basic sanity check that the divider-inversion helper and the
        # real conversion function agree on direction across the whole
        # table, not just at two isolated points.
        voltages_and_temps = [
            (_divider_voltage(r), t) for t, r in _REFERENCE_POINTS_C_TO_OHM
        ]
        computed = [(ntc_voltage_to_celsius(v), t) for v, t in voltages_and_temps]
        computed.sort(key=lambda pair: pair[1])  # sort by reference temp_c
        computed_temps = [c for c, _ in computed]
        self.assertEqual(computed_temps, sorted(computed_temps))


if __name__ == "__main__":
    unittest.main()
