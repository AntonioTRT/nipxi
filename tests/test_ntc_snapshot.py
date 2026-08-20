"""
Tests for test_control/ntc_snapshot.py -- the extraction/move of test.py's
_ntc_group_snapshot() into a shared module (see docs/architecture.md
"Preparation Phase: Six Resolved Decisions Before worker_runtime.py").

This function already had no print()/input() before the move -- these
tests confirm the move preserved every branch of its behavior exactly
(readable/presence classification, the no-op-on-daq=None short circuit,
log_summary opt-in) using a fake DAQ + a fake storage recorder, with real
hardware/temperature.py classification thresholds (no mocking of the
classification logic itself). No hardware access anywhere in this file.
"""

import unittest

from hardware.temperature import NTCPresence
from test_control.ntc_snapshot import ntc_group_snapshot
from utils.errors import DAQError

# Real classify_ntc_presence() thresholds (see hardware/temperature.py):
# 2.5V -> PRESENT (25.0 C); 0.02V -> ABSENT; 4.9V -> FAULT (near excitation rail).
_PRESENT_V = 2.5
_ABSENT_V = 0.02
_FAULT_V = 4.9


class _FakeStorage:
    def __init__(self):
        self.measurements = []
        self.events = []

    def record_measurement(self, **kwargs):
        self.measurements.append(kwargs)

    def log_event(self, **kwargs):
        self.events.append(kwargs)


class _FakeDaq:
    def __init__(self, voltages_by_channel=None, raise_channels=None):
        self._voltages = voltages_by_channel or {}
        self._raise_channels = raise_channels or set()

    def read_channel(self, channel):
        if channel in self._raise_channels:
            raise DAQError(f"simulated DAQ failure on {channel}")
        return self._voltages[channel]


def _group_config(size, ntc_channels):
    """A minimal synthetic BATTERY_GROUPS-shaped group dict, positions
    1..size, with `ntc_channels` mapping position -> daq_ntc_ch (or None
    to simulate an unconfigured position)."""
    return {
        "positions": {
            i: {"daq_ntc_ch": ntc_channels.get(i)}
            for i in range(1, size + 1)
        }
    }


class NoDaqIsANoOpTests(unittest.TestCase):
    def test_returns_empty_list_and_touches_nothing_when_daq_is_none(self):
        storage = _FakeStorage()
        result = ntc_group_snapshot(storage, None, "B1", 4, source="charge_battery")
        self.assertEqual(result, [])
        self.assertEqual(storage.measurements, [])
        self.assertEqual(storage.events, [])


class PresenceClassificationTests(unittest.TestCase):
    def setUp(self):
        import config.devices as dev_cfg
        self._orig_groups = dev_cfg.BATTERY_GROUPS
        dev_cfg.BATTERY_GROUPS = {
            "B1": _group_config(3, {1: "Dev1/ai0", 2: "Dev1/ai1", 3: None}),
        }
        self.addCleanup(self._restore, dev_cfg)

    def _restore(self, dev_cfg):
        dev_cfg.BATTERY_GROUPS = self._orig_groups

    def test_present_absent_and_unconfigured_positions(self):
        daq = _FakeDaq({"Dev1/ai0": _PRESENT_V, "Dev1/ai1": _ABSENT_V})
        storage = _FakeStorage()
        results = ntc_group_snapshot(storage, daq, "B1", 3, source="charge_battery")

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["presence"], NTCPresence.PRESENT)
        self.assertTrue(results[0]["readable"])
        self.assertAlmostEqual(results[0]["temp_c"], 25.0, places=1)

        self.assertEqual(results[1]["presence"], NTCPresence.ABSENT)
        self.assertTrue(results[1]["readable"])
        self.assertIsNone(results[1]["temp_c"])

        # Position 3 has no daq_ntc_ch configured -- FAULT, unreadable.
        self.assertEqual(results[2]["presence"], NTCPresence.FAULT)
        self.assertFalse(results[2]["readable"])
        self.assertEqual(len(storage.events), 1)
        self.assertIn("no daq_ntc_ch configured", storage.events[0]["message"])

    def test_daq_error_is_fault_and_unreadable_not_absent(self):
        daq = _FakeDaq({"Dev1/ai1": _PRESENT_V}, raise_channels={"Dev1/ai0"})
        storage = _FakeStorage()
        results = ntc_group_snapshot(storage, daq, "B1", 2, source="charge_battery")
        self.assertEqual(results[0]["presence"], NTCPresence.FAULT)
        self.assertFalse(results[0]["readable"], "a DAQ comms failure must not be reported as readable")
        self.assertTrue(any("DAQ read failed" in e["message"] for e in storage.events))

    def test_fault_reading_from_a_real_value_near_excitation_rail(self):
        daq = _FakeDaq({"Dev1/ai0": _FAULT_V, "Dev1/ai1": _PRESENT_V})
        storage = _FakeStorage()
        results = ntc_group_snapshot(storage, daq, "B1", 2, source="charge_battery")
        self.assertEqual(results[0]["presence"], NTCPresence.FAULT)
        self.assertTrue(results[0]["readable"], "a real (if implausible) reading is still 'readable'")

    def test_phase_detail_override_is_recorded_instead_of_presence(self):
        daq = _FakeDaq({"Dev1/ai0": _PRESENT_V, "Dev1/ai1": _PRESENT_V})
        storage = _FakeStorage()
        ntc_group_snapshot(storage, daq, "B1", 2, source="charge_battery",
                            phase_detail="NTC_PRECHECK")
        self.assertTrue(all(m["phase_detail"] == "NTC_PRECHECK" for m in storage.measurements[:2]))

    def test_log_summary_false_by_default_no_summary_events(self):
        daq = _FakeDaq({"Dev1/ai0": _PRESENT_V, "Dev1/ai1": _PRESENT_V})
        storage = _FakeStorage()
        ntc_group_snapshot(storage, daq, "B1", 2, source="charge_battery")
        self.assertEqual(storage.events, [])

    def test_log_summary_true_emits_one_event_per_position(self):
        daq = _FakeDaq({"Dev1/ai0": _PRESENT_V, "Dev1/ai1": _ABSENT_V})
        storage = _FakeStorage()
        ntc_group_snapshot(storage, daq, "B1", 2, source="charge_battery", log_summary=True)
        self.assertEqual(len(storage.events), 2)
        self.assertIn("NTC snapshot", storage.events[0]["message"])


if __name__ == "__main__":
    unittest.main()
