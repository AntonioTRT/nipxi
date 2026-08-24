"""
Tests for test_control/battery_presence_precheck.py::
battery_and_ntc_presence_precheck() -- see docs/architecture.md "Battery
Presence + NTC Presence Diagnostics".

Fakes only -- no hardware, no real DataStorage/DAQ/DMM/relay. Mirrors the
established fake-hardware pattern used throughout this suite (e.g.
tests/test_sense_router.py, tests/test_ntc_snapshot.py).
"""

import unittest

from hardware.temperature import NTCPresence
from test_control.battery_diagnostics import BatteryPresence
from test_control.battery_presence_precheck import battery_and_ntc_presence_precheck

# classify_ntc_presence() thresholds (see hardware/temperature.py):
_NTC_PRESENT_V = 2.5
_NTC_ABSENT_V = 0.02
_NTC_FAULT_V = 4.9


class _FakeStorage:
    def __init__(self):
        self.events = []
        self.measurements = []

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def record_measurement(self, **kwargs):
        self.measurements.append(kwargs)

    def event_messages(self):
        return [e["message"] for e in self.events]


class _FakeRelay:
    def __init__(self):
        self.calls = []

    def close(self, relay_address):
        self.calls.append(("close", relay_address))

    def open(self, relay_address):
        self.calls.append(("open", relay_address))


class _FakeDmm:
    def __init__(self, voltage_v=None, raises=False):
        self.voltage_v = voltage_v
        self.raises = raises
        self.measure_calls = 0

    def measure_dc_voltage(self):
        self.measure_calls += 1
        if self.raises:
            raise RuntimeError("simulated DMM comms failure")
        return self.voltage_v


class _FakeNtcDaq:
    def __init__(self, voltages_by_channel):
        self._voltages = voltages_by_channel

    def read_channel(self, channel):
        return self._voltages[channel]


def _group_config(size, ntc_channels):
    return {
        "positions": {
            i: {"daq_ntc_ch": ntc_channels.get(i)}
            for i in range(1, size + 1)
        }
    }


def _run(*, battery_voltage_v=None, battery_raises=False, ntc_voltage_v=None, ntc_channels=None):
    import config.devices as dev_cfg
    orig_groups = dev_cfg.BATTERY_GROUPS
    size = 1
    dev_cfg.BATTERY_GROUPS = {
        "B1": _group_config(size, ntc_channels if ntc_channels is not None else {1: "Dev1/ai0"}),
    }
    try:
        storage = _FakeStorage()
        relay = _FakeRelay()
        dmm = _FakeDmm(voltage_v=battery_voltage_v, raises=battery_raises)
        ntc_daq = _FakeNtcDaq({"Dev1/ai0": ntc_voltage_v} if ntc_voltage_v is not None else {})
        result = battery_and_ntc_presence_precheck(
            storage=storage, dmm=dmm, relay=relay, ntc_daq=ntc_daq,
            group="B1", size=size, position=1, channel=1, relay_address=1,
            source="charge_battery", measurement_test_type="charge",
        )
        return result, storage, relay, dmm
    finally:
        dev_cfg.BATTERY_GROUPS = orig_groups


class FourScenarioTests(unittest.TestCase):
    """The four scenarios from the review this was built from."""

    def test_battery_present_ntc_present_is_ok(self):
        result, storage, relay, dmm = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["battery_presence"], BatteryPresence.PRESENT)
        self.assertEqual(result["ntc_presence"], NTCPresence.PRESENT)

    def test_battery_present_ntc_missing_aborts_with_ntc_reason_only(self):
        result, storage, relay, dmm = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_ABSENT_V)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["NTC Missing"])
        self.assertEqual(result["battery_presence"], BatteryPresence.PRESENT)

    def test_battery_missing_ntc_present_aborts_with_battery_reason_only(self):
        result, storage, relay, dmm = _run(battery_voltage_v=0.01, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["Battery Missing"])
        self.assertEqual(result["ntc_presence"], NTCPresence.PRESENT)

    def test_battery_missing_ntc_missing_aborts_with_both_reasons_in_order(self):
        result, storage, relay, dmm = _run(battery_voltage_v=0.0, ntc_voltage_v=_NTC_ABSENT_V)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["Battery Missing", "NTC Missing"])


class RelayHandlingTests(unittest.TestCase):
    def test_relay_is_closed_then_reopened_on_success(self):
        _, _, relay, _ = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertEqual(relay.calls, [("close", 1), ("open", 1)])

    def test_relay_is_closed_then_reopened_on_abort(self):
        _, _, relay, _ = _run(battery_voltage_v=0.0, ntc_voltage_v=_NTC_ABSENT_V)
        self.assertEqual(relay.calls, [("close", 1), ("open", 1)])

    def test_relay_is_reopened_even_if_the_dmm_read_raises(self):
        _, _, relay, _ = _run(battery_raises=True, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertEqual(relay.calls, [("close", 1), ("open", 1)])


class DmmFailureDegradesGracefullyTests(unittest.TestCase):
    def test_dmm_read_failure_is_not_treated_as_battery_missing(self):
        result, storage, _, dmm = _run(battery_raises=True, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertTrue(result["ok"])
        self.assertFalse(result["battery_readable"])
        self.assertIsNone(result["battery_presence"])
        self.assertEqual(dmm.measure_calls, 1)
        self.assertTrue(any("DMM read failed" in m for m in storage.event_messages()))


class ReversedPolarityIsNotTreatedAsMissingTests(unittest.TestCase):
    def test_reversed_voltage_does_not_abort(self):
        result, storage, _, _ = _run(battery_voltage_v=-3.7, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertTrue(result["ok"])
        self.assertEqual(result["battery_presence"], BatteryPresence.REVERSED)
        self.assertEqual(result["reasons"], [])

    def test_reversed_voltage_still_logs_an_informational_note(self):
        _, storage, _, _ = _run(battery_voltage_v=-3.7, ntc_voltage_v=_NTC_PRESENT_V)
        self.assertTrue(any("REVERSED polarity" in m for m in storage.event_messages()))


class NtcFaultVsAbsentWordingTests(unittest.TestCase):
    def test_ntc_fault_reads_ntc_fault_not_ntc_missing(self):
        result, _, _, _ = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_FAULT_V)
        self.assertEqual(result["reasons"], ["NTC Fault"])
        self.assertEqual(result["ntc_presence"], NTCPresence.FAULT)


class EventLoggingTests(unittest.TestCase):
    def test_test_aborted_event_lists_every_reason(self):
        _, storage, _, _ = _run(battery_voltage_v=0.0, ntc_voltage_v=_NTC_ABSENT_V)
        messages = storage.event_messages()
        self.assertTrue(any(m == "TEST ABORTED -- Reason: Battery Missing, NTC Missing" for m in messages))

    def test_no_test_aborted_event_on_success(self):
        _, storage, _, _ = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_PRESENT_V)
        messages = storage.event_messages()
        self.assertFalse(any("TEST ABORTED" in m for m in messages))

    def test_presence_check_summary_event_always_recorded(self):
        _, storage, _, _ = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_PRESENT_V)
        messages = storage.event_messages()
        self.assertTrue(any(m.startswith("Presence check -- Battery: PRESENT") for m in messages))

    def test_battery_voltage_is_recorded_as_a_measurement(self):
        _, storage, _, _ = _run(battery_voltage_v=3.67, ntc_voltage_v=_NTC_PRESENT_V)
        precheck_rows = [m for m in storage.measurements if m.get("phase_detail") == "BATTERY_PRECHECK"]
        self.assertEqual(len(precheck_rows), 1)
        self.assertEqual(precheck_rows[0]["voltage_v"], 3.67)


if __name__ == "__main__":
    unittest.main()
