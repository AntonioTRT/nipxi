"""
Tests for test_control/execution_screen.py::render_execution_frame() --
the "Current Execution" screen density pass (see docs/architecture.md
"Current Execution Screen: Information Density"). No tests existed for
this renderer before this change; these lock in the new compact layout
and the fields the request explicitly required to survive unchanged.

Captures printed output via redirect_stdout -- render_execution_frame()
is plain print()-based by design (see its own module docstring: "no
curses, no TUI framework"), so this is the natural way to assert on it.
"""

import io
import unittest
from contextlib import redirect_stdout

from test_control.execution_screen import ExecutionFrame, render_execution_frame


def _render(**kwargs):
    frame = ExecutionFrame.from_live(
        run_id=kwargs.pop("run_id", "run1"), test_type=kwargs.pop("test_type", "charge"),
        channel=kwargs.pop("channel", 1), **kwargs,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        render_execution_frame(frame)
    return buf.getvalue()


class RequiredFieldsSurviveTests(unittest.TestCase):
    """Requirement 7 -- explicit "do not remove" list."""

    def test_every_required_field_is_present(self):
        output = _render(
            run_number=12, relay=1, state="ACTIVE", phase_detail="CC_CV", elapsed_s=83,
            smu_voltage=3.7, smu_current=0.5, dmm_voltage=3.698, battery_temp=25.3,
        )
        for label in (
            "Run Number", "Run ID", "Test Type", "DUT / Channel", "Relay",
            "Current Status", "Elapsed", "Phase Detail",
            "DMM Voltage", "SMU Voltage", "SMU Current", "Battery Temp",
            "Recent Events", "Measurement History",
        ):
            self.assertIn(label, output, f"required field/section {label!r} missing from output")


class RemovedElementsTests(unittest.TestCase):
    """Requirements 2-4 -- what must no longer appear."""

    def test_standalone_ntc_header_is_gone(self):
        output = _render(ntc_device="MAIN_DAQ", ntc_channel="Dev1/ai0", ntc_status="present")
        self.assertNotIn("\nNTC\n", output)
        self.assertNotIn("NTC            :", output)
        # Second compactness pass: relabeled "NTC" -> "Temp Sensor", device/
        # resource dropped as operationally redundant once channel is shown.
        self.assertIn("Temp Sensor : Dev1/ai0 (PRESENT)", output)
        self.assertNotIn("MAIN_DAQ", output)

    def test_battery_current_line_is_removed(self):
        output = _render(battery_current=0.5)
        self.assertNotIn("Battery Current", output)

    def test_battery_metrics_is_a_single_merged_line(self):
        output = _render(capacity=1.5, energy=5.5, cycle_count=3)
        self.assertIn("Capacity: 1.5 | Energy: 5.5 | Cycles: 3", output)
        self.assertNotIn("Battery Metrics", output)
        self.assertNotIn("Cycle Count", output)

    def test_battery_metrics_line_present_even_when_all_na(self):
        output = _render()
        self.assertIn("Capacity: N/A | Energy: N/A | Cycles: N/A", output)


class TimeoutDisplayTests(unittest.TestCase):
    """Requirement 5 -- new timeout visibility."""

    def test_both_timeouts_shown_when_present(self):
        output = _render(charge_timeout_s=300, discharge_timeout_s=600)
        self.assertIn("Charge Timeout : 300 s", output)
        self.assertIn("Discharge Timeout : 600 s", output)

    def test_timeout_lines_omitted_entirely_when_not_applicable(self):
        # Monitor Battery/Proto Test have no timeout concept -- must not
        # show "N/A" clutter for a field that doesn't apply at all.
        output = _render()
        self.assertNotIn("Charge Timeout", output)
        self.assertNotIn("Discharge Timeout", output)

    def test_only_one_of_the_pair_still_shows_both_lines(self):
        # Both come from the same test_setpoints dict regardless of which
        # operation is active (see ExecutionFrame.charge_timeout_s's
        # docstring) -- charge_timeout_s alone must still surface both lines.
        output = _render(charge_timeout_s=300)
        self.assertIn("Charge Timeout : 300 s", output)
        self.assertIn("Discharge Timeout : N/A", output)


class VerticalDensityTests(unittest.TestCase):
    """Requirements 1/6/8/9 -- fewer blank lines, still terminal-friendly."""

    def test_no_blank_lines_between_run_identity_and_readings(self):
        output = _render(
            run_number=1, relay=1, state="ACTIVE", phase_detail="CC_CV", elapsed_s=1,
            charge_timeout_s=300, discharge_timeout_s=600,
        )
        lines = output.splitlines()
        start = lines.index("Current Execution")
        end = next(i for i, l in enumerate(lines) if l.startswith("SMU Voltage"))
        body = lines[start + 2:end]  # skip the "====" rule after the title
        self.assertNotIn("", body)

    def test_full_screen_is_shorter_than_the_old_layout(self):
        # Not a strict line budget (Recent Events/Measurement History are
        # unbounded by design) -- just confirms the top "Current Execution"
        # block (through the merged metrics line) shrank materially, which
        # is the whole point of this change.
        output = _render(
            run_number=1, relay=1, state="ACTIVE", phase_detail="CC_CV", elapsed_s=1,
            smu_voltage=3.7, smu_current=0.5, dmm_voltage=3.698, battery_voltage=3.698,
            battery_temp=25.3, charge_timeout_s=300, discharge_timeout_s=600,
            ntc_device="MAIN_DAQ", ntc_resource="PXI1Slot2", ntc_channel="Dev1/ai0",
            ntc_status="present", capacity=None, energy=None, cycle_count=None,
        )
        lines = output.splitlines()
        top_block_end = next(i for i, l in enumerate(lines) if l.startswith("Capacity:"))
        # Old layout needed ~39 lines to reach the equivalent point
        # (Run Number through Cycle Count, including headers/blanks/NTC
        # block/Battery Current/3-line metrics) -- see the review's own
        # line-by-line count. New layout must be well under that.
        self.assertLess(top_block_end, 25)


class TwoColumnLayoutTests(unittest.TestCase):
    """Second compactness pass, requirement 1 -- two-column packing for
    the live-readings block and DUT/Channel + Relay."""

    def test_dut_channel_and_relay_share_one_line(self):
        output = _render(channel=3, relay=2)
        self.assertIn("DUT / Channel  : 3", output)
        line = next(l for l in output.splitlines() if l.startswith("DUT / Channel"))
        self.assertIn("Relay : 2", line)

    def test_smu_voltage_and_current_share_one_line(self):
        output = _render(smu_voltage=3.7, smu_current=0.5)
        line = next(l for l in output.splitlines() if l.startswith("SMU Voltage"))
        self.assertIn("SMU Current : 0.500000 A", line)

    def test_dmm_voltage_and_route_share_one_line(self):
        output = _render(dmm_voltage=3.698)
        line = next(l for l in output.splitlines() if l.startswith("DMM Voltage"))
        self.assertIn("DMM Route :", line)

    def test_battery_voltage_and_temp_share_one_line(self):
        output = _render(battery_voltage=3.698, battery_temp=25.3)
        line = next(l for l in output.splitlines() if l.startswith("Battery Voltage"))
        self.assertIn("Battery Temp : 25.3 C", line)

    def test_run_number_and_run_id_stay_on_separate_lines(self):
        # Approved design: identity fields (Run Number/Run ID/Test Type/
        # Current Status) are NOT two-column-packed -- only the
        # fast-changing readings block and DUT/Channel+Relay are.
        output = _render(run_number=12)
        line = next(l for l in output.splitlines() if l.startswith("Run Number"))
        self.assertNotIn("Run ID", line)


class DmmRouteTests(unittest.TestCase):
    """Second compactness pass -- active DMM measurement route."""

    def test_direct_shown_when_no_sense_route_given(self):
        output = _render()
        self.assertIn("DMM Route : DIRECT", output)

    def test_configured_route_is_shown_verbatim(self):
        output = _render(dmm_route="MATRIX_NUMATO_201 CH1")
        self.assertIn("DMM Route : MATRIX_NUMATO_201 CH1", output)


class RemainingTimeCountdownTests(unittest.TestCase):
    """Second compactness pass -- timeout countdown, derived purely from
    existing fields (elapsed_s, charge_timeout_s/discharge_timeout_s,
    test_type) with no new plumbing into ChargeSequence/DischargeSequence."""

    def test_remaining_counts_down_during_a_charge_run(self):
        output = _render(test_type="charge", elapsed_s=249, charge_timeout_s=300)
        self.assertIn("Remaining : 00:51", output)

    def test_remaining_counts_down_during_a_discharge_run(self):
        output = _render(test_type="discharge", elapsed_s=100, discharge_timeout_s=600)
        self.assertIn("Remaining : 08:20", output)

    def test_remaining_clamped_at_zero_not_negative(self):
        output = _render(test_type="charge", elapsed_s=305, charge_timeout_s=300)
        self.assertIn("Remaining : 00:00", output)

    def test_remaining_na_when_no_timeout_configured(self):
        output = _render(test_type="charge", elapsed_s=10)
        self.assertIn("Remaining : N/A", output)

    def test_remaining_na_for_test_types_without_a_timeout_concept(self):
        output = _render(test_type="monitor", elapsed_s=10, charge_timeout_s=300)
        self.assertIn("Remaining : N/A", output)

    def test_elapsed_and_remaining_share_one_line(self):
        output = _render(elapsed_s=10, charge_timeout_s=300)
        line = next(l for l in output.splitlines() if l.startswith("Elapsed"))
        self.assertIn("Remaining :", line)


class RecentEventsCountTests(unittest.TestCase):
    """Second compactness pass, requirement 7 -- header reflects the
    reduced (5, down from 20) event count."""

    def test_header_shows_the_reduced_limit(self):
        output = _render()
        self.assertIn("Recent Events (last 5)", output)


if __name__ == "__main__":
    unittest.main()
