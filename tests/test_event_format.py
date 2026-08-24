"""
Tests for utils/event_format.py -- the shared EventType vocabulary +
format_event() formatter behind "Standardized Hardware Event Logging"
(see docs/architecture.md). Pure function, no hardware, no storage.
"""

import unittest

from utils.event_format import EventType, format_event


class FormatEventTests(unittest.TestCase):
    def test_event_type_always_comes_first(self):
        line = format_event(EventType.SMU_OUTPUT_ENABLED, device="PXI-4130", resource="SMU1")
        self.assertTrue(line.startswith("EVENT_TYPE=SMU_OUTPUT_ENABLED "))

    def test_fields_are_upper_cased_key_equals_value(self):
        line = format_event(EventType.RELAY_CLOSE, relay_matrix_name="MATRIX_NUMATO_202", relay_address=3)
        self.assertIn("RELAY_MATRIX_NAME=MATRIX_NUMATO_202", line)
        self.assertIn("RELAY_ADDRESS=3", line)

    def test_none_valued_fields_are_omitted_not_written_as_none(self):
        line = format_event(EventType.MATRIX_ROUTE_APPLIED, matrix_name=None, matrix_channel=None, source="DMM")
        self.assertNotIn("MATRIX_NAME", line)
        self.assertNotIn("None", line)
        self.assertIn("SOURCE=DMM", line)

    def test_field_order_matches_call_order(self):
        line = format_event(EventType.SMU_OUTPUT_ENABLED, channel=1, device="PXI-4130", resource="SMU1")
        self.assertEqual(
            line,
            "EVENT_TYPE=SMU_OUTPUT_ENABLED CHANNEL=1 DEVICE=PXI-4130 RESOURCE=SMU1",
        )

    def test_run_id_is_never_embedded_in_the_message_text(self):
        # event_log.run_id is already a real, populated column -- the
        # formatted message must never duplicate it.
        line = format_event(EventType.EMERGENCY_STOP_STARTED, reason="test")
        self.assertNotIn("RUN_ID", line)

    def test_no_fields_produces_just_the_event_type(self):
        self.assertEqual(format_event(EventType.GROUP_RUN_STARTED), "EVENT_TYPE=GROUP_RUN_STARTED")


class EventTypeVocabularyTests(unittest.TestCase):
    def test_every_required_event_type_from_the_review_exists(self):
        required = [
            "SMU_OUTPUT_ENABLED", "SMU_OUTPUT_DISABLED", "RELAY_OPEN", "RELAY_CLOSE",
            "RELAY_OPEN_ALL", "MATRIX_ROUTE_APPLIED", "MATRIX_ROUTE_CLEARED",
            "DMM_MEASUREMENT_FAILED", "DMM_MEASUREMENT_RECOVERED",
            "SAFETY_MONITOR_TRIGGERED", "SAFETY_MONITOR_RECOVERED",
            "EMERGENCY_STOP_STARTED", "EMERGENCY_STOP_COMPLETED",
        ]
        for name in required:
            with self.subTest(name=name):
                self.assertTrue(hasattr(EventType, name))
                self.assertEqual(getattr(EventType, name), name)

    def test_every_required_group_run_event_type_exists(self):
        required = [
            "GROUP_RUN_STARTED", "GROUP_SLOT_STARTED", "GROUP_SLOT_SKIPPED",
            "GROUP_SLOT_FAILED", "GROUP_SLOT_COMPLETED", "GROUP_RUN_COMPLETED",
        ]
        for name in required:
            with self.subTest(name=name):
                self.assertTrue(hasattr(EventType, name))
                self.assertEqual(getattr(EventType, name), name)


if __name__ == "__main__":
    unittest.main()
