"""
Tests for scripts/export_run.py -- the forensic export feature (see
docs/architecture.md "Forensic Export"). Uses a real, temp-directory
DataStorage (no mocking of sqlite3 internals) -- mirrors
tests/test_run_sequence.py's established convention.
"""

import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings
from data.rotation import index_database_file, telemetry_database_file
from data.storage import DataStorage
from scripts.export_run import (
    ExportError, build_export, main, write_export,
)
from utils.safety_fault import acknowledge_safety_fault, report_safety_fault


class _TempSettings:
    def __init__(self, base_dir):
        self.DATA_DIR = base_dir
        self.CSV_DIR = os.path.join(base_dir, "csv")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.settings = _TempSettings(self.tmp_dir)

    def _make_plain_run(self, *, result="PASS", stop_reason="COMPLETED"):
        storage = DataStorage(settings=self.settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge", battery_type="HUB", group_name="B1", position_in_group=1)
        storage.log_event(level="INFO", source="charge_battery", message="Run started")
        storage.record_execution_state(relay=1, state="ACTIVE", channel=1)
        storage.record_measurement(test_type="charge", channel=1, voltage_v=3.8, current_a=0.5)
        storage.record_execution_state(relay=1, state=stop_reason if stop_reason != "COMPLETED" else "COMPLETED", channel=1)
        storage.finish_run_summary(stop_reason=stop_reason, result=result)
        storage.close()
        return run_id

    def _make_faulted_run(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge", battery_type="HUB", group_name="B1", position_in_group=5)
        storage.log_event(level="INFO", source="charge_battery", message="Run started")
        storage.record_execution_state(relay=5, state="ACTIVE", channel=5)
        fault_id = report_safety_fault(
            reason="Channel 5: SMU output could not be verified OFF after charge sequence "
                   "(verification_result=still_enabled).",
            source_method="emergency_output_off", context="in_run_escalation",
            device_name="SMU_PXI1Slot5", device_type="SMU", position=5, run_id=run_id,
            verification_result="still_enabled", storage=storage, settings=self.settings,
        )
        acknowledge_safety_fault(fault_id=fault_id, storage=storage, run_id=run_id, settings=self.settings)
        storage.record_execution_state(relay=5, state="STATION_FAULT", channel=5)
        storage.record_measurement(test_type="charge", channel=5, voltage_v=3.9, current_a=1.0)
        storage.finish_run_summary(stop_reason="STATION_FAULT", result="STATION_FAULT")
        storage.close()
        return run_id, fault_id


class ReadOnlyExportPathTests(_Base):
    def test_export_never_allocates_a_new_sequence_number(self):
        run_id = self._make_plain_run()
        conn = sqlite3.connect(index_database_file(self.settings))
        next_value_before = conn.execute("SELECT next_value FROM run_sequence WHERE id = 1").fetchone()[0]
        conn.close()

        build_export(run_id, settings=self.settings)

        conn = sqlite3.connect(index_database_file(self.settings))
        next_value_after = conn.execute("SELECT next_value FROM run_sequence WHERE id = 1").fetchone()[0]
        conn.close()
        self.assertEqual(next_value_before, next_value_after)

    def test_export_never_creates_a_csv_directory(self):
        run_id = self._make_plain_run()
        shutil.rmtree(os.path.join(self.tmp_dir, "csv"), ignore_errors=True)
        build_export(run_id, settings=self.settings)
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "csv")))

    def test_export_does_not_create_a_new_telemetry_file(self):
        run_id = self._make_plain_run()
        existing_files = set(os.listdir(self.tmp_dir))
        build_export(run_id, settings=self.settings)
        self.assertEqual(set(os.listdir(self.tmp_dir)), existing_files)

    def test_missing_run_id_raises_export_error(self):
        self._make_plain_run()
        with self.assertRaises(ExportError):
            build_export("RACK01-99999999", settings=self.settings)

    def test_missing_index_database_raises_export_error(self):
        with self.assertRaises(ExportError):
            build_export("RACK01-00000001", settings=self.settings)


class TelemetryDbLookupTests(_Base):
    def test_export_uses_the_stored_telemetry_db_column_not_the_current_month(self):
        run_id = self._make_plain_run()
        # Simulate "current month" telemetry differing from the run's own
        # telemetry_db by renaming the actual file the run used.
        real_telemetry_path = telemetry_database_file(self.settings)
        renamed_path = os.path.join(self.tmp_dir, "nipxi_2020_01.db")
        os.rename(real_telemetry_path, renamed_path)
        conn = sqlite3.connect(index_database_file(self.settings))
        conn.execute("UPDATE run_summary SET telemetry_db = 'nipxi_2020_01.db' WHERE run_id = ?", (run_id,))
        conn.commit()
        conn.close()

        export = build_export(run_id, settings=self.settings)
        self.assertTrue(export["metadata"]["telemetry_available"])
        self.assertEqual(export["metadata"]["telemetry_db"], "nipxi_2020_01.db")
        self.assertEqual(export["measurements"]["row_count"], 1)

    def test_legacy_run_with_missing_telemetry_db_exports_available_sections_only(self):
        run_id = self._make_plain_run()
        conn = sqlite3.connect(index_database_file(self.settings))
        conn.execute("UPDATE run_summary SET telemetry_db = NULL WHERE run_id = ?", (run_id,))
        conn.commit()
        conn.close()

        export = build_export(run_id, settings=self.settings)
        self.assertFalse(export["metadata"]["telemetry_available"])
        self.assertIsNotNone(export["metadata"]["telemetry_unavailable_reason"])
        self.assertIn("predates", export["metadata"]["telemetry_unavailable_reason"])
        # run_summary/execution_state (index database) are still fully available.
        self.assertEqual(export["run_summary"]["run_id"], run_id)
        self.assertGreater(len(export["execution_state"]), 0)
        # Safety fault count must be None (unknown), never 0 (falsely "clean").
        self.assertFalse(export["safety"]["available"])
        self.assertIsNone(export["safety"]["fault_count"])
        self.assertIsNone(export["incident_summary"]["safety_fault_count"])
        self.assertIsNone(export["measurements"]["row_count"])

    def test_telemetry_file_deleted_after_recording_is_reported_not_guessed(self):
        run_id = self._make_plain_run()
        os.remove(telemetry_database_file(self.settings))
        export = build_export(run_id, settings=self.settings)
        self.assertFalse(export["metadata"]["telemetry_available"])
        self.assertIn("not found", export["metadata"]["telemetry_unavailable_reason"])


class IncidentSummaryTests(_Base):
    def test_plain_run_has_no_fault_and_a_plain_narrative(self):
        run_id = self._make_plain_run(result="PASS", stop_reason="COMPLETED")
        export = build_export(run_id, settings=self.settings)
        summary = export["incident_summary"]
        self.assertEqual(summary["result"], "PASS")
        self.assertEqual(summary["safety_fault_count"], 0)
        self.assertIsNone(summary["primary_failure_mode"])
        self.assertIsNone(summary["root_cause_caveat"])
        self.assertNotIn("SAFETY FAULT", summary["narrative"])

    def test_faulted_run_has_the_full_expected_shape(self):
        run_id, fault_id = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        summary = export["incident_summary"]
        self.assertEqual(summary["result"], "STATION_FAULT")
        self.assertEqual(summary["stop_reason"], "STATION_FAULT")
        self.assertEqual(summary["final_execution_state"], "STATION_FAULT")
        self.assertEqual(summary["safety_fault_count"], 1)
        self.assertEqual(summary["acknowledged_fault_count"], 1)
        self.assertTrue(summary["operator_acknowledged"])
        self.assertFalse(summary["testing_continued_after_fault"])
        self.assertEqual(summary["primary_failure_mode"], "still_enabled")
        self.assertEqual(summary["primary_device"], "SMU_PXI1Slot5")
        self.assertEqual(summary["additional_fault_count"], 0)
        self.assertIsNotNone(summary["root_cause_caveat"])
        self.assertNotIn("root_cause", str(summary).lower().replace("root_cause_caveat", ""))

    def test_narrative_matches_the_approved_template(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        narrative = export["incident_summary"]["narrative"]
        self.assertEqual(
            narrative,
            "SAFETY FAULT: Output could not be verified OFF (STILL_ENABLED). "
            "Operator acknowledged. Testing did not continue.",
        )

    def test_station_id_and_station_name_are_populated(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        summary = export["incident_summary"]
        self.assertEqual(summary["station_id"], "RACK01")
        self.assertEqual(summary["station_name"], "FIN-RACK01")


class TimelineTests(_Base):
    def test_timeline_is_chronologically_sorted_and_merges_both_sources(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        timeline = export["timeline"]
        timestamps = [e["timestamp"] for e in timeline]
        self.assertEqual(timestamps, sorted(timestamps))
        sources = {e["source"] for e in timeline}
        self.assertEqual(sources, {"event_log", "station_state"})

    def test_timeline_excludes_raw_hardware_log(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        self.assertTrue(all(e["source"] != "raw_hardware_log" for e in export["timeline"]))

    def test_safety_fault_events_appear_in_timeline_with_correct_severity(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        raised = [e for e in export["timeline"] if e["event"] == "SAFETY_FAULT_RAISED"]
        self.assertEqual(len(raised), 1)
        self.assertEqual(raised[0]["severity"], "CRITICAL")


class CriticalEventsTests(_Base):
    def test_critical_events_is_a_subset_of_timeline(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        timeline_keys = {(e["timestamp"], e["event"]) for e in export["timeline"]}
        for entry in export["critical_events"]:
            self.assertIn((entry["timestamp"], entry["event"]), timeline_keys)

    def test_acknowledgement_is_included_despite_info_level(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        events = [e["event"] for e in export["critical_events"]]
        self.assertIn("SAFETY_FAULT_ACKNOWLEDGED", events)

    def test_plain_run_has_no_critical_events(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        self.assertEqual(export["critical_events"], [])


class SafetyCorrelationTests(_Base):
    def test_raised_and_acknowledged_are_joined_by_fault_id(self):
        run_id, fault_id = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        fault = export["safety"]["fault_timeline"][0]
        self.assertEqual(fault["fault_id"], fault_id)
        self.assertIsNotNone(fault["raised_at"])
        self.assertIsNotNone(fault["acknowledged_at"])
        self.assertLess(fault["raised_at"], fault["acknowledged_at"])

    def test_fault_carries_device_position_context_source_method(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        fault = export["safety"]["fault_timeline"][0]
        self.assertEqual(fault["device_name"], "SMU_PXI1Slot5")
        self.assertEqual(fault["device_type"], "SMU")
        self.assertEqual(fault["position"], 5)
        self.assertEqual(fault["context"], "in_run_escalation")
        self.assertEqual(fault["source_method"], "emergency_output_off")

    def test_unacknowledged_fault_has_null_acknowledged_at(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge")
        report_safety_fault(
            reason="reason", source_method="open_all", context="startup_sweep",
            device_name="MATRIX1", device_type="RELAY", run_id=run_id,
            storage=storage, settings=self.settings,
        )
        storage.finish_run_summary(stop_reason="STATION_FAULT", result="STATION_FAULT")
        storage.close()

        export = build_export(run_id, settings=self.settings)
        fault = export["safety"]["fault_timeline"][0]
        self.assertIsNone(fault["acknowledged_at"])
        self.assertFalse(export["incident_summary"]["operator_acknowledged"])


class VerificationResultMappingTests(_Base):
    def _run_with_verification_result(self, verification_result):
        storage = DataStorage(settings=self.settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge")
        fault_id = report_safety_fault(
            reason="reason", source_method="emergency_output_off", context="in_run_escalation",
            device_name="SMU1", device_type="SMU", run_id=run_id,
            verification_result=verification_result, storage=storage, settings=self.settings,
        )
        acknowledge_safety_fault(fault_id=fault_id, storage=storage, run_id=run_id, settings=self.settings)
        storage.finish_run_summary(stop_reason="STATION_FAULT", result="STATION_FAULT")
        storage.close()
        return run_id

    def test_still_enabled_maps_to_high_confidence_no_comm_failure(self):
        run_id = self._run_with_verification_result("still_enabled")
        export = build_export(run_id, settings=self.settings)
        finding = export["forensic_findings"][0]
        self.assertEqual(finding["physical_state_confidence"], "HIGH")
        self.assertFalse(finding["communication_failure"])
        fault = export["safety"]["fault_timeline"][0]
        self.assertEqual(fault["verification_result_meaning"],
                          "SMU output was electrically confirmed as still enabled.")

    def test_verification_comm_failure_maps_to_unknown_confidence_and_comm_failure_true(self):
        run_id = self._run_with_verification_result("verification_comm_failure")
        export = build_export(run_id, settings=self.settings)
        finding = export["forensic_findings"][0]
        self.assertEqual(finding["physical_state_confidence"], "UNKNOWN")
        self.assertTrue(finding["communication_failure"])

    def test_comm_failure_is_never_reported_as_low_confidence(self):
        run_id = self._run_with_verification_result("verification_comm_failure")
        export = build_export(run_id, settings=self.settings)
        finding = export["forensic_findings"][0]
        self.assertNotEqual(finding["physical_state_confidence"], "LOW")


class SummaryOnlyModeTests(_Base):
    def test_summary_only_omits_bulky_sections(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings, summary_only=True)
        self.assertEqual(export["timeline"], [])
        self.assertEqual(export["critical_events"], [])
        self.assertEqual(export["execution_state"], [])
        self.assertEqual(export["event_log"], [])

    def test_summary_only_keeps_incident_summary_and_safety(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings, summary_only=True)
        self.assertEqual(export["incident_summary"]["safety_fault_count"], 1)
        self.assertEqual(len(export["safety"]["fault_timeline"]), 1)
        self.assertEqual(len(export["forensic_findings"]), 1)
        self.assertIsNotNone(export["run_summary"])


class MeasurementsModeTests(_Base):
    def test_default_mode_has_stats_but_no_full_rows(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        m = export["measurements"]
        self.assertEqual(m["row_count"], 1)
        self.assertIsNotNone(m["first_sample"])
        self.assertIsNotNone(m["last_sample"])
        self.assertIn("1", m["per_channel_statistics"])
        self.assertFalse(m["full_rows_included"])
        self.assertIsNone(m["rows"])

    def test_include_measurements_embeds_full_rows(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings, include_measurements=True)
        m = export["measurements"]
        self.assertTrue(m["full_rows_included"])
        self.assertEqual(len(m["rows"]), 1)

    def test_per_channel_statistics_are_correct(self):
        storage = DataStorage(settings=self.settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge")
        for v in (3.0, 4.0, 3.5):
            storage.record_measurement(test_type="charge", channel=1, voltage_v=v)
        storage.finish_run_summary(stop_reason="COMPLETED", result="PASS")
        storage.close()

        export = build_export(run_id, settings=self.settings)
        stats = export["measurements"]["per_channel_statistics"]["1"]
        self.assertEqual(stats["min_voltage_v"], 3.0)
        self.assertEqual(stats["max_voltage_v"], 4.0)
        self.assertAlmostEqual(stats["avg_voltage_v"], 3.5)


class RawHardwareLogModeTests(_Base):
    def test_default_mode_has_counts_but_no_full_rows(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings)
        r = export["raw_hardware_log"]
        self.assertGreaterEqual(r["hardware_failure_count"], 1)
        self.assertIn("still_enabled", r["unique_error_types"])
        self.assertFalse(r["full_rows_included"])
        self.assertIsNone(r["rows"])

    def test_include_raw_hardware_log_embeds_and_decodes_json_fields(self):
        run_id, _ = self._make_faulted_run()
        export = build_export(run_id, settings=self.settings, include_raw_hardware_log=True)
        r = export["raw_hardware_log"]
        self.assertTrue(r["full_rows_included"])
        self.assertGreater(len(r["rows"]), 0)
        fault_row = next(row for row in r["rows"] if row["command"] == "safety_fault_raised")
        self.assertIsInstance(fault_row["additional_metadata"], dict)
        self.assertIn("fault_id", fault_row["additional_metadata"])


class GzipOutputTests(_Base):
    def test_no_gzip_forces_plain_json_even_when_flagged(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        path = write_export(export, run_id=run_id, output_path=os.path.join(self.tmp_dir, "out"), gzip_flag=False)
        self.assertTrue(path.endswith(".json"))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["metadata"]["run_id"], run_id)

    def test_gzip_flag_forces_compressed_output(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        path = write_export(export, run_id=run_id, output_path=os.path.join(self.tmp_dir, "out"), gzip_flag=True)
        self.assertTrue(path.endswith(".json.gz"))
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["metadata"]["run_id"], run_id)

    def test_auto_gzip_triggers_above_size_threshold(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        path = write_export(
            export, run_id=run_id, output_path=os.path.join(self.tmp_dir, "out"),
            size_threshold_bytes=1,  # anything is "large" at this threshold
        )
        self.assertTrue(path.endswith(".json.gz"))

    def test_auto_gzip_stays_plain_json_below_threshold(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        path = write_export(export, run_id=run_id, output_path=os.path.join(self.tmp_dir, "out"))
        self.assertTrue(path.endswith(".json"))
        self.assertFalse(path.endswith(".gz"))


class DeterministicOutputTests(_Base):
    def test_two_exports_of_the_same_run_are_identical_except_exported_at(self):
        run_id, _ = self._make_faulted_run()
        first = build_export(run_id, settings=self.settings)
        second = build_export(run_id, settings=self.settings)
        first["metadata"]["exported_at"] = None
        second["metadata"]["exported_at"] = None
        self.assertEqual(first, second)

    def test_top_level_key_order_matches_the_approved_order(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        self.assertEqual(
            list(export.keys()),
            ["metadata", "incident_summary", "timeline", "safety", "forensic_findings",
             "critical_events", "run_summary", "execution_state", "event_log",
             "measurements", "raw_hardware_log"],
        )

    def test_written_json_preserves_top_level_key_order(self):
        run_id = self._make_plain_run()
        export = build_export(run_id, settings=self.settings)
        path = write_export(export, run_id=run_id, output_path=os.path.join(self.tmp_dir, "out"))
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        # Match the TOP-LEVEL key pattern specifically (2-space indent,
        # colon immediately after) -- a bare '"safety"' substring search
        # would also match "safety" appearing as an array ELEMENT inside
        # metadata.sections_included, earlier in the document.
        keys = ["metadata", "incident_summary", "timeline", "safety", "forensic_findings",
                "critical_events", "run_summary", "execution_state", "event_log",
                "measurements", "raw_hardware_log"]
        positions = [raw.index(f'\n  "{k}":') for k in keys]
        self.assertEqual(positions, sorted(positions))


class CliEntryPointTests(unittest.TestCase):
    """
    Exercises the actual `main()` CLI entry point (argument parsing +
    orchestration), not just build_export()/write_export() directly.
    Mutates the REAL config.settings.Settings class in place (save/
    restore), the same pattern already established in
    tests/test_testpy_extraction_parity.py -- necessary because
    build_export()'s `settings=Settings` default argument is bound once,
    at module-import time, to that same class object; mutating its
    attributes (not replacing the reference) is what a subprocess-based
    CLI invocation would see too, but without the cost/flakiness of an
    actual subprocess.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self._orig_data_dir = Settings.DATA_DIR
        self._orig_csv_dir = Settings.CSV_DIR
        Settings.DATA_DIR = self.tmp_dir
        Settings.CSV_DIR = os.path.join(self.tmp_dir, "csv")
        self.addCleanup(self._restore_settings)
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp_dir)
        self.addCleanup(os.chdir, self._orig_cwd)

    def _restore_settings(self):
        Settings.DATA_DIR = self._orig_data_dir
        Settings.CSV_DIR = self._orig_csv_dir

    def _make_run(self):
        storage = DataStorage(settings=Settings)
        storage.open()
        run_id = storage.run_id
        storage.start_run_summary(test_type="charge")
        storage.log_event(level="INFO", source="charge_battery", message="Run started")
        storage.finish_run_summary(stop_reason="COMPLETED", result="PASS")
        storage.close()
        return run_id

    def test_default_invocation_writes_run_id_dot_json_in_cwd(self):
        run_id = self._make_run()
        exit_code = main([run_id])
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(f"{run_id}.json"))
        with open(f"{run_id}.json", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["metadata"]["run_id"], run_id)

    def test_unknown_run_id_prints_fail_and_returns_nonzero(self):
        exit_code = main(["RACK01-99999999"])
        self.assertEqual(exit_code, 1)

    def test_summary_only_flag_reaches_build_export(self):
        run_id = self._make_run()
        main([run_id, "--summary-only"])
        with open(f"{run_id}.json", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["event_log"], [])
        self.assertTrue(data["metadata"]["flags"]["summary_only"])


if __name__ == "__main__":
    unittest.main()
