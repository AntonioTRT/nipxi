"""
Regression tests for data/storage.py's measurement/event query scoping
fixes made during the first real-hardware validation cycle:

  - get_first_measurement(): must skip NTC_PRECHECK rows (even though
    those rows carry a real, non-NULL voltage_v -- the raw NTC divider
    voltage, not a battery voltage) and return the first row that is a
    genuine battery-telemetry sample, scoped to the requested channel.
  - get_measurements(recent_limit=...): must return the last N rows in
    chronological order via a real SQL LIMIT, not a full-table fetch.
  - get_recent_events(channel=...): must scope to the given channel,
    excluding both other channels' rows and unscoped (channel=None)
    run-level messages.

Uses a temporary on-disk SQLite database (via a throwaway Settings
subclass) -- no real hardware, no shared state with the project's real
data_output/ database.
"""

import os
import shutil
import tempfile
import unittest

from config.settings import Settings
from data.storage import DataStorage


class _TempSettings(Settings):
    pass  # DATA_DIR/CSV_DIR/DATABASE_FILE overridden per-test in setUp


class MeasurementScopingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        settings = type("_TempSettings", (Settings,), {
            "DATA_DIR": self._tmpdir,
            "CSV_DIR": os.path.join(self._tmpdir, "csv"),
            "DATABASE_FILE": os.path.join(self._tmpdir, "test.db"),
        })
        self.storage = DataStorage(settings=settings)
        self.storage.open()

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class GetFirstMeasurementTests(MeasurementScopingTestCase):
    def test_ntc_precheck_row_with_real_raw_divider_voltage_is_skipped(self):
        """
        The exact real-hardware bug: _ntc_group_snapshot() stores the raw
        NTC divider voltage in the voltage_v column (a real, non-NULL
        float) but never populates dmm_measured_v/smu_measured_v/current_a.
        get_first_measurement() must still recognize this as "not a real
        battery sample" via phase_detail, not just column-nullness.
        """
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="NTC_PRECHECK",
            voltage_v=2.13847, temp_c=27.0, group_name="B1", position_in_group=1,
        )
        self.assertIsNone(
            self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1),
            "an NTC_PRECHECK-only row must never be reported as the Initial Measurement",
        )

    def test_first_real_sample_is_found_once_it_exists(self):
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="NTC_PRECHECK",
            voltage_v=2.13847, temp_c=27.0, group_name="B1", position_in_group=1,
        )
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="CC_CV",
            voltage_v=3.568383, current_a=0.139728, temp_c=27.1,
            smu_measured_v=3.699169, smu_measured_i=0.139728, dmm_measured_v=3.568383,
        )
        initial = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1)
        self.assertIsNotNone(initial)
        self.assertEqual(initial["phase_detail"], "CC_CV")
        self.assertEqual(initial["dmm_measured_v"], 3.568383)

    def test_first_valid_measurement_stays_stable_across_later_samples(self):
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="CC_CV",
            voltage_v=3.55, current_a=0.1, dmm_measured_v=3.55, smu_measured_v=3.60,
        )
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="CC_CV",
            voltage_v=3.60, current_a=0.1, dmm_measured_v=3.60, smu_measured_v=3.65,
        )
        initial = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1)
        self.assertEqual(initial["dmm_measured_v"], 3.55, "must remain the FIRST valid sample, not the latest")

    def test_scoped_to_requested_channel_only(self):
        """
        Position 1's own NTC pre-check row must not leak in as the Initial
        Measurement for a run actually charging position 5, and vice
        versa -- this is the channel-scoping half of the fix.
        """
        for position in range(1, 9):
            self.storage.record_measurement(
                test_type="charge", channel=position, phase_detail="NTC_PRECHECK",
                voltage_v=2.0 + position * 0.01, temp_c=27.0, group_name="B1",
                position_in_group=position,
            )
        self.storage.record_measurement(
            test_type="charge", channel=5, phase_detail="CC_CV",
            voltage_v=3.6, current_a=0.1, dmm_measured_v=3.6, smu_measured_v=3.65,
        )
        initial_ch5 = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=5)
        self.assertIsNotNone(initial_ch5)
        self.assertEqual(initial_ch5["channel"], 5)

        initial_ch1 = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1)
        self.assertIsNone(initial_ch1, "position 1 only ever got an NTC_PRECHECK row -- no valid sample yet")

    def test_monitor_battery_scan_row_not_excluded(self):
        """
        MonitorBatteryScanSequence's own rows (OPEN_BEFORE/CLOSED/
        OPEN_AFTER) are never tagged NTC_PRECHECK and DO populate voltage_v
        for real (an averaged DMM reading) -- must not regress.
        """
        self.storage.record_measurement(
            test_type="monitor_scan", channel=1, phase_detail="OPEN_BEFORE",
            voltage_v=0.002, voltage_min_v=0.001, voltage_max_v=0.003,
        )
        initial = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1)
        self.assertIsNotNone(initial)
        self.assertEqual(initial["phase_detail"], "OPEN_BEFORE")

    def test_battery_precheck_row_with_real_voltage_is_skipped(self):
        """
        The SAME class of bug the NTC_PRECHECK test above covers, but for
        test_control/battery_presence_precheck.py::
        battery_and_ntc_presence_precheck()'s own pre-relay-close row
        (phase_detail="BATTERY_PRECHECK", voltage_v populated,
        dmm_measured_v/smu_measured_v/current_a left NULL) -- this
        regressed the exact same "Initial Measurement shows N/A" symptom
        under a different phase_detail string the filter didn't yet know
        about when that feature was added. See this method's own
        docstring "REGRESSED, then re-fixed here".
        """
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="BATTERY_PRECHECK",
            voltage_v=3.67, group_name="B1", position_in_group=1,
        )
        self.assertIsNone(
            self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1),
            "a BATTERY_PRECHECK-only row must never be reported as the Initial Measurement",
        )

    def test_first_real_sample_is_found_after_both_precheck_row_kinds(self):
        """
        A real run writes BOTH an NTC_PRECHECK row (from the group NTC
        snapshot) AND a BATTERY_PRECHECK row (from the battery presence
        check) before the relay ever closes -- both must be skipped, and
        the genuine first CC_CV sample must still be found.
        """
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="BATTERY_PRECHECK",
            voltage_v=3.67, group_name="B1", position_in_group=1,
        )
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="NTC_PRECHECK",
            voltage_v=2.13847, temp_c=27.0, group_name="B1", position_in_group=1,
        )
        self.storage.record_measurement(
            test_type="charge", channel=1, phase_detail="CC_CV",
            voltage_v=3.568383, current_a=0.139728, temp_c=27.1,
            smu_measured_v=3.699169, smu_measured_i=0.139728, dmm_measured_v=3.568383,
        )
        initial = self.storage.get_first_measurement(run_id=self.storage.run_id, channel=1)
        self.assertIsNotNone(initial)
        self.assertEqual(initial["phase_detail"], "CC_CV")
        self.assertEqual(initial["dmm_measured_v"], 3.568383)


class GetMeasurementsRecentLimitTests(MeasurementScopingTestCase):
    def test_recent_limit_returns_last_n_in_chronological_order(self):
        for i in range(12):
            self.storage.record_measurement(
                test_type="charge", channel=1, phase_detail="CC_CV",
                voltage_v=3.5 + i * 0.01, current_a=0.1,
            )
        recent = self.storage.get_measurements(run_id=self.storage.run_id, channel=1, recent_limit=5)
        self.assertEqual(len(recent), 5)
        voltages = [round(r["voltage_v"], 2) for r in recent]
        self.assertEqual(voltages, [3.57, 3.58, 3.59, 3.60, 3.61], "must be the LAST 5, oldest-first")

    def test_recent_limit_none_returns_full_history_unchanged(self):
        for i in range(3):
            self.storage.record_measurement(test_type="charge", channel=1, voltage_v=float(i))
        full = self.storage.get_measurements(run_id=self.storage.run_id, channel=1)
        self.assertEqual(len(full), 3, "omitting recent_limit must preserve the original unbounded behavior")


class GetRecentEventsChannelScopingTests(MeasurementScopingTestCase):
    def test_channel_filter_excludes_other_channels_and_unscoped_events(self):
        self.storage.log_event(level="INFO", source="charge_battery", message="Run started")
        for position in range(1, 4):
            self.storage.log_event(
                level="INFO", source="charge_battery", channel=position,
                message=f"NTC snapshot -- Position {position}",
            )
        self.storage.log_event(
            level="INFO", source="charge_battery", channel=2, relay=2,
            message="Relay 2 activated -- charging started",
        )

        events = self.storage.get_recent_events(run_id=self.storage.run_id, channel=2)
        messages = [e["message"] for e in events]

        self.assertIn("NTC snapshot -- Position 2", messages)
        self.assertIn("Relay 2 activated -- charging started", messages)
        self.assertNotIn("NTC snapshot -- Position 1", messages)
        self.assertNotIn("NTC snapshot -- Position 3", messages)
        self.assertNotIn("Run started", messages, "unscoped (channel=None) run-level messages must be excluded")

    def test_no_channel_filter_preserves_original_behavior(self):
        self.storage.log_event(level="INFO", source="charge_battery", message="Run started")
        self.storage.log_event(level="INFO", source="charge_battery", channel=1, message="channel 1 event")
        events = self.storage.get_recent_events(run_id=self.storage.run_id)
        messages = [e["message"] for e in events]
        self.assertIn("Run started", messages)
        self.assertIn("channel 1 event", messages)


if __name__ == "__main__":
    unittest.main()
