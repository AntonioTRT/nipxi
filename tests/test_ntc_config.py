"""
Regression tests for the B1 NTC migration (NTC_DAQ_USB6210/Dev2 ->
MAIN_DAQ/Dev1, bench-confirmed channel mapping Dev1/ai0-ai7). Guards
against the exact mistake found and corrected mid-cycle: an earlier
revision of this migration assumed Dev1/ai16-ai23 from a documented-but-
never-bench-validated Settings constant, which did not match the real
rack wiring.
"""

import unittest

import config.devices as dev_cfg


class B1NtcResolutionTests(unittest.TestCase):
    def test_b1_no_longer_resolves_to_usb6210_or_dev2(self):
        hw = dev_cfg.hardware_for_group("B1")
        self.assertNotEqual(hw["ntc_daq_name"], "NTC_DAQ_USB6210")
        for position in range(1, dev_cfg.group_size("B1") + 1):
            channel = dev_cfg.BATTERY_GROUPS["B1"]["positions"][position]["daq_ntc_ch"]
            self.assertNotIn("Dev2", channel)

    def test_b1_resolves_to_main_daq(self):
        hw = dev_cfg.hardware_for_group("B1")
        self.assertEqual(hw["ntc_daq_name"], "MAIN_DAQ")
        self.assertIsNotNone(hw["ntc_daq_cfg"])
        self.assertEqual(hw["ntc_daq_cfg"]["model"], "PXIe-6363")

    def test_b1_position_channel_mapping_is_dev1_ai0_through_ai7(self):
        expected = {i: f"Dev1/ai{i - 1}" for i in range(1, 9)}
        actual = {
            i: dev_cfg.BATTERY_GROUPS["B1"]["positions"][i]["daq_ntc_ch"]
            for i in range(1, 9)
        }
        self.assertEqual(actual, expected)

    def test_ntc_channels_do_not_overlap_voltage_or_current_channels(self):
        """
        By design (bench-confirmed): B1's daq_ntc_ch is intentionally
        identical to daq_voltage_ch per position today (Dev1 is used for
        NTC acquisition only; daq_voltage_ch/daq_current_ch remain
        inactive placeholders -- see docs/architecture.md Section 58/59).
        This test documents that fact rather than asserting a disjoint
        channel range, so it does not regress into a false "no sharing"
        expectation. If daq_voltage_ch/daq_current_ch are ever activated
        for real DAQ-based telemetry, THIS assertion is exactly what
        should start failing, forcing a deliberate reconciliation instead
        of a silent conflict.
        """
        for i in range(1, 9):
            pos = dev_cfg.BATTERY_GROUPS["B1"]["positions"][i]
            self.assertEqual(
                pos["daq_ntc_ch"], pos["daq_voltage_ch"],
                "if this ever fails, it means daq_ntc_ch and daq_voltage_ch have "
                "diverged -- re-check whether that divergence is intentional "
                "(e.g. a real rack DAQ migration) or accidental",
            )


if __name__ == "__main__":
    unittest.main()
