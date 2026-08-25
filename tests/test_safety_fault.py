"""
Tests for utils/safety_fault.py -- the shared SAFETY_FAULT_RAISED/
SAFETY_FAULT_ACKNOWLEDGED persistence, correlation, and console-screen
primitives (see docs/architecture.md "Safety Fault Lifecycle"). Reuses
tests/test_raw_hardware_log.py's established real-temp-SQLite-database
convention (no mocking of sqlite3 internals) for the raw_hardware_log
assertions.
"""

import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from utils.safety_fault import (
    acknowledge_safety_fault, display_safety_fault_screen, extract_verification_result,
    new_fault_id, report_safety_fault,
)


class _TempSettings:
    def __init__(self, base_dir, database_file):
        self.DATA_DIR = base_dir
        self.DATABASE_FILE = database_file


class _FakeStorage:
    """Minimal storage double -- only log_event() is exercised here."""

    def __init__(self, run_id="test-run", raise_on_log=False):
        self.run_id = run_id
        self.events = []
        self._raise_on_log = raise_on_log

    def log_event(self, **kwargs):
        if self._raise_on_log:
            raise RuntimeError("DataStorage.log_event() called before open()")
        self.events.append(kwargs)
        return len(self.events)  # mimics cursor.lastrowid, 1-indexed


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.db_path = os.path.join(self.tmp_dir, "safety_fault_test.db")
        self.settings = _TempSettings(self.tmp_dir, self.db_path)

    def _raw_rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT run_id, position, device_type, device_name, command, response, "
                "success, error_type, error_message, additional_metadata "
                "FROM raw_hardware_log ORDER BY id"
            )
            return cur.fetchall()
        finally:
            conn.close()


class ExtractVerificationResultTests(unittest.TestCase):
    def test_extracts_disabled_on_success_message(self):
        self.assertEqual(
            extract_verification_result("output disabled verification result: disabled"),
            "disabled",
        )

    def test_extracts_still_enabled_with_trailing_text(self):
        message = "output disabled verification result: still_enabled (exhausted 3 attempt(s))"
        self.assertEqual(extract_verification_result(message), "still_enabled")

    def test_extracts_verification_comm_failure(self):
        message = "output disabled verification result: verification_comm_failure (exhausted 3 attempt(s))"
        self.assertEqual(extract_verification_result(message), "verification_comm_failure")

    def test_returns_none_for_unrelated_message(self):
        self.assertIsNone(extract_verification_result("emergency_output_off requested (reason)"))

    def test_returns_none_for_empty_message(self):
        self.assertIsNone(extract_verification_result(""))


class ReportSafetyFaultTests(_Base):
    def test_persists_to_both_event_log_and_raw_hardware_log(self):
        storage = _FakeStorage(run_id="run-42")
        fault_id = report_safety_fault(
            reason="SMU output could not be verified OFF.",
            source_method="emergency_output_off", context="in_run_escalation",
            device_name="SMU_PXI1Slot5", device_type="SMU", position=3,
            run_id="run-42", verification_result="still_enabled",
            storage=storage, settings=self.settings,
        )
        self.assertTrue(fault_id.startswith("faultevt_"))

        self.assertEqual(len(storage.events), 1)
        event = storage.events[0]
        self.assertEqual(event["level"], "CRITICAL")
        self.assertEqual(event["source"], "SAFETY")
        self.assertIn("EVENT_TYPE=SAFETY_FAULT_RAISED", event["message"])
        self.assertIn(f"FAULT_ID={fault_id}", event["message"])
        self.assertIn("DEVICE_NAME=SMU_PXI1Slot5", event["message"])
        self.assertIn("DEVICE_TYPE=SMU", event["message"])
        self.assertIn("POSITION=3", event["message"])
        self.assertIn("VERIFICATION_RESULT=still_enabled", event["message"])

        rows = self._raw_rows()
        self.assertEqual(len(rows), 1)
        run_id, position, device_type, device_name, command, response, success, error_type, \
            error_message, additional_metadata = rows[0]
        self.assertEqual(run_id, "run-42")
        self.assertEqual(position, 3)
        self.assertEqual(device_type, "SMU")
        self.assertEqual(device_name, "SMU_PXI1Slot5")
        self.assertEqual(command, "safety_fault_raised")
        self.assertEqual(success, 0)
        self.assertEqual(error_type, "still_enabled")  # OutputVerificationResult, not lost
        self.assertIn(fault_id, additional_metadata)
        self.assertIn("in_run_escalation", additional_metadata)
        self.assertIn("linked_event_log_id", additional_metadata)

    def test_works_with_no_storage_available(self):
        # Startup-sweep / HardwareManager case: raw_hardware_log must still
        # record the fault even with no open DataStorage session (see
        # data/raw_hardware_log.py's own module docstring).
        fault_id = report_safety_fault(
            reason="Relay open_all() failed.", source_method="open_all",
            context="startup_sweep", device_name="MATRIX_NUMATO_201", device_type="RELAY",
            settings=self.settings,
        )
        rows = self._raw_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "MATRIX_NUMATO_201")
        self.assertIn(fault_id, rows[0][9])

    def test_survives_storage_log_event_raising(self):
        # A DataStorage not yet open() raises RuntimeError from log_event() --
        # report_safety_fault() must still persist to raw_hardware_log.
        storage = _FakeStorage(raise_on_log=True)
        report_safety_fault(
            reason="reason", source_method="emergency_output_off", context="startup_sweep",
            device_name="SMU1", device_type="SMU", storage=storage, settings=self.settings,
        )
        self.assertEqual(len(self._raw_rows()), 1)


class AcknowledgeSafetyFaultTests(_Base):
    def test_persists_correlation_to_the_raised_fault(self):
        storage = _FakeStorage()
        fault_id = new_fault_id()
        acknowledge_safety_fault(fault_id=fault_id, storage=storage, settings=self.settings, run_id="run-1")

        self.assertEqual(len(storage.events), 1)
        event = storage.events[0]
        self.assertEqual(event["level"], "INFO")
        self.assertIn("EVENT_TYPE=SAFETY_FAULT_ACKNOWLEDGED", event["message"])
        self.assertIn(f"ACKNOWLEDGES_FAULT_ID={fault_id}", event["message"])

        rows = self._raw_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], "operator_acknowledged_safety_fault")
        self.assertEqual(rows[0][6], 1)  # success
        self.assertIn(fault_id, rows[0][9])

    def test_raised_and_acknowledged_share_the_same_fault_id_end_to_end(self):
        storage = _FakeStorage()
        fault_id = report_safety_fault(
            reason="reason", source_method="emergency_output_off", context="in_run_escalation",
            device_name="SMU1", device_type="SMU", storage=storage, settings=self.settings,
        )
        acknowledge_safety_fault(fault_id=fault_id, storage=storage, settings=self.settings)
        raised_msg = storage.events[0]["message"]
        ack_msg = storage.events[1]["message"]
        self.assertIn(f"FAULT_ID={fault_id}", raised_msg)
        self.assertIn(f"ACKNOWLEDGES_FAULT_ID={fault_id}", ack_msg)


class DisplaySafetyFaultScreenTests(unittest.TestCase):
    def test_screen_shows_states_and_reason_and_blocks_until_acknowledged(self):
        buf = io.StringIO()
        with mock.patch("builtins.input", return_value="") as mocked_input, redirect_stdout(buf):
            display_safety_fault_screen(smu_state="UNKNOWN", relay_state="VERIFIED OPEN", reason="test reason")
        output = buf.getvalue()
        self.assertIn("SAFETY FAULT", output)
        self.assertIn("UNKNOWN", output)
        self.assertIn("VERIFIED OPEN", output)
        self.assertIn("test reason", output)
        self.assertIn("Physically inspect the station.", output)
        mocked_input.assert_called_once()

    def test_never_raises_on_eof_or_keyboard_interrupt(self):
        buf = io.StringIO()
        with mock.patch("builtins.input", side_effect=EOFError), redirect_stdout(buf):
            display_safety_fault_screen(reason="test reason")  # must not raise


if __name__ == "__main__":
    unittest.main()
