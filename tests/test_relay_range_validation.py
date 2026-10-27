"""
Tests for test.py::test_relay_range_validation() and the Numato
closed_channels() single-readall optimization.

The validation runs against the REAL NumatoRelayMatrix driver class over the
in-memory firmware model from tests/test_relay_conformance.py (only the wire
is faked), so the production safety sequence executes unmodified.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from config.settings import Settings
from hardware import relay as relay_module
from tests.test_relay_conformance import _FakeNumatoFirmware, _FakeTransportNumato
from utils.errors import OperationCancelledError


def _make_numato(firmware):
    return _FakeTransportNumato(
        {"name": "FAKE_NUMATO", "ip": "127.0.0.1", "channel_count": 32}, firmware)


CFG = {"name": "FAKE_NUMATO", "type": "ethernet", "ip": "127.0.0.1", "port": 23, "channel_count": 32}


class RangeValidationBase(unittest.TestCase):
    def setUp(self):
        self.firmware = _FakeNumatoFirmware()
        self.driver = _make_numato(self.firmware)
        for patcher in (
            mock.patch.object(relay_module.time, "sleep"),
            mock.patch.object(Settings, "RELAY_RANGE_VALIDATION_DWELL_S", 0.01),
            mock.patch.object(test_module, "_instrumented_relay", lambda cfg: self.driver),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_validation(self, start, end):
        answers = iter([str(start), str(end)])
        with mock.patch("builtins.input", lambda *_: next(answers)), redirect_stdout(io.StringIO()) as out:
            results = test_module.test_relay_range_validation("FAKE_NUMATO", CFG)
        self.out = out.getvalue()
        return results

    @staticmethod
    def by_device(results):
        return {r.device: r for r in results}


class PassCaseTests(RangeValidationBase):
    def test_range_passes_per_relay_and_ends_verified_open(self):
        results = self.run_validation(3, 5)
        by = self.by_device(results)
        for n in (3, 4, 5):
            self.assertEqual(by[f"Relay {n}"].status, test_module.Status.PASS)
        self.assertEqual(by["Final open_all"].status, test_module.Status.PASS)
        self.assertNotIn("Validation aborted", by)
        self.assertEqual(self.firmware.mask, 0)

    def test_single_relay_range(self):
        results = self.run_validation(7, 7)
        self.assertEqual([r.device for r in results if r.device.startswith("Relay ")], ["Relay 7"])

    def test_raw_data_carries_expected_actual_and_durations(self):
        raw = self.by_device(self.run_validation(4, 4))["Relay 4"].raw
        self.assertEqual(raw["relay"], 4)
        self.assertEqual(raw["closed_verify_all_open"], [])
        self.assertEqual(raw["closed_verify_only_closed"], [4])
        self.assertEqual(raw["closed_verify_all_open_after_open"], [])
        self.assertEqual(raw["result"], "PASS")
        for phase in ("open_all", "close", "dwell", "open"):
            self.assertIn(phase, raw["durations_s"])

    def test_every_close_is_preceded_by_a_verified_all_open(self):
        order = []
        real_open_all, real_close = self.driver.open_all, self.driver.close
        self.driver.open_all = lambda: (order.append("open_all"), real_open_all())[1]
        self.driver.close = lambda n: (order.append(f"close{n}"), real_close(n))[1]
        self.run_validation(1, 3)
        # per relay: open_all immediately before each close
        for i, entry in enumerate(order):
            if entry.startswith("close"):
                self.assertEqual(order[i - 1], "open_all")


class FailCaseTests(RangeValidationBase):
    def test_relay_that_does_not_close_fails_and_stops_at_first_failure(self):
        self.firmware.stuck_open = 1 << 3  # relay 4 ignores 'on'
        results = self.run_validation(3, 6)
        by = self.by_device(results)
        self.assertEqual(by["Relay 3"].status, test_module.Status.PASS)
        self.assertEqual(by["Relay 4"].status, test_module.Status.FAIL)
        self.assertNotIn("Relay 5", by)
        self.assertNotIn("Relay 6", by)
        self.assertEqual(by["Validation aborted"].status, test_module.Status.FAIL)
        self.assertEqual(by["Final open_all"].status, test_module.Status.PASS)
        self.assertEqual(self.firmware.mask, 0)

    def test_relay_stuck_closed_fails_first_relay_and_final_open_all_reports_it(self):
        self.firmware.stuck_closed = 1 << 9
        self.firmware.mask = 1 << 9
        by = self.by_device(self.run_validation(1, 3))
        self.assertEqual(by["Relay 1"].status, test_module.Status.FAIL)
        self.assertEqual(by["Final open_all"].status, test_module.Status.FAIL)
        self.assertIn("Hardware may still be energized", by["Final open_all"].details)

    def test_independent_readback_catches_a_driver_that_lies(self):
        # driver claims success but physical state disagrees: close() is a no-op
        self.driver.close = lambda n: None
        by = self.by_device(self.run_validation(2, 2))
        self.assertEqual(by["Relay 2"].status, test_module.Status.FAIL)
        self.assertEqual(by["Relay 2"].raw["closed_verify_only_closed"], [])
        self.assertIn("verify_only_closed", by["Relay 2"].details)


class InputAndCancelTests(RangeValidationBase):
    def test_invalid_ranges_operate_no_relay(self):
        for start, end in ((5, 3), (0, 2), (1, 33), ("x", "2")):
            with self.subTest(start=start, end=end):
                with mock.patch.object(test_module, "_instrumented_relay",
                                       side_effect=AssertionError("relay must not be created")):
                    results = self.run_validation(start, end)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].status, test_module.Status.WARNING)

    def test_blank_start_cancels(self):
        with mock.patch("builtins.input", lambda *_: ""), redirect_stdout(io.StringIO()):
            results = test_module.test_relay_range_validation("FAKE_NUMATO", CFG)
        self.assertEqual(results[0].status, test_module.Status.WARNING)

    def test_ctrl_c_during_dwell_stops_and_final_open_all_runs(self):
        calls = {"n": 0}

        def cancel_on_second_relay(duration, token=None, poll_interval_s=0.2):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OperationCancelledError("Ctrl+C")

        with mock.patch("utils.cancellation.interruptible_sleep", cancel_on_second_relay):
            results = self.run_validation(1, 4)
        by = self.by_device(results)
        self.assertEqual(by["Relay 1"].status, test_module.Status.PASS)
        self.assertNotIn("Relay 2", by)
        self.assertEqual(by["Cancelled by operator"].status, test_module.Status.WARNING)
        self.assertEqual(by["Final open_all"].status, test_module.Status.PASS)
        self.assertEqual(self.firmware.mask, 0)  # relay 2 was closed during the dwell


class NumatoClosedChannelsOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.firmware = _FakeNumatoFirmware()
        self.driver = _make_numato(self.firmware)

    def test_uses_exactly_one_readall_and_no_writes(self):
        self.firmware.mask = (1 << 4) | (1 << 20)
        sent = []
        real = self.driver._send_and_capture
        self.driver._send_and_capture = lambda cmd: (sent.append(cmd), real(cmd))[1]
        self.assertEqual(self.driver.closed_channels(), [5, 21])
        self.assertEqual(sent, ["relay readall"])

    def test_matches_the_generic_per_channel_implementation(self):
        for mask in (0, 1, 1 << 31, 0b101010):
            self.firmware.mask = mask
            generic = [ch for ch in range(1, 33) if self.driver.query(ch)]
            self.assertEqual(self.driver.closed_channels(), generic)

    def test_write_path_is_unchanged(self):
        import inspect
        from hardware.relay_eth import NumatoRelayMatrix
        from hardware.relay import RelayBase
        self.assertIs(NumatoRelayMatrix.open, RelayBase.open)
        self.assertIs(NumatoRelayMatrix.close, RelayBase.close)
        self.assertIn("_force_all_off_and_verify", inspect.getsource(NumatoRelayMatrix.open_all))
        self.assertIn("_force_all_off_and_verify", inspect.getsource(NumatoRelayMatrix._close_impl))
        self.assertNotIn("closed_channels", inspect.getsource(NumatoRelayMatrix._close_impl))
        self.assertNotIn("closed_channels", inspect.getsource(NumatoRelayMatrix._force_all_off_and_verify))


if __name__ == "__main__":
    unittest.main()
