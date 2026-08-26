"""
Pins the exact stop_reason/result pairing run_guarded() writes for each of
its five handled exception types -- see test_control/battery_operation_
sequence.py::run_guarded(). Written after the CycleSequence engineering
review identified that TIMEOUT collapsed into the same result="FAIL" as
FAILED/SAFETY_VIOLATION, making them indistinguishable by `result` alone
(only `stop_reason` disambiguated). Fixed to result="TIMEOUT". This file
exists so any future change to that table is a deliberate, visible diff
here rather than a silent regression discovered months later against real
hardware data.

No hardware, no real DataStorage -- fake doubles only, mirroring the
existing pattern in tests/test_shutdown_trace_logging.py.
"""

import unittest

from config.settings import Settings
from test_control.battery_operation_sequence import BatteryOperationSequence
from utils.errors import (
    NIPXITimeoutError, OperationCancelledError, RelayError, SafetyViolationError,
)


class _FakeStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.finish_calls = []
        self.execution_states = []

    def log_event(self, **kwargs):
        pass

    def get_run_summary(self, run_id):
        return None

    def record_execution_state(self, **kwargs):
        self.execution_states.append(kwargs)

    def finish_run_summary(self, **kwargs):
        self.finish_calls.append(kwargs)


class _FakeSmu:
    def emergency_output_off(self, reason, on_event=None):
        return True

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        return True


class _FakeRelay:
    def open_all(self):
        pass


class _RecordingSafety:
    def emergency_stop(self, smu, relay, reason, on_event=None):
        pass

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        pass


def _make_sequence():
    return BatteryOperationSequence(
        smu=_FakeSmu(), relay=_FakeRelay(), safety=_RecordingSafety(),
        storage=_FakeStorage(), settings=Settings, source="charge_battery",
    )


class RunGuardedResultClassificationTests(unittest.TestCase):
    def _run_and_get_finish_call(self, exc, expected_exc_type=None):
        seq = _make_sequence()

        def _raise():
            raise exc

        with self.assertRaises(expected_exc_type or type(exc)):
            seq.run_guarded(
                _raise, channel=1, relay_address=1, label="Charge Battery",
                verb="charging", cancel_message="Charging stopped by operator",
            )
        self.assertEqual(len(seq.storage.finish_calls), 1)
        return seq.storage.finish_calls[0]

    def test_cancellation_is_cancelled_stopped_by_operator(self):
        call = self._run_and_get_finish_call(OperationCancelledError("operator stop"))
        self.assertEqual(call["stop_reason"], "CANCELLED")
        self.assertEqual(call["result"], "STOPPED_BY_OPERATOR")

    def test_safety_violation_is_safety_violation_fail(self):
        call = self._run_and_get_finish_call(SafetyViolationError("overvoltage"))
        self.assertEqual(call["stop_reason"], "SAFETY_VIOLATION")
        self.assertEqual(call["result"], "FAIL")

    def test_relay_error_is_failed_fail(self):
        call = self._run_and_get_finish_call(RelayError("relay comms lost"))
        self.assertEqual(call["stop_reason"], "FAILED")
        self.assertEqual(call["result"], "FAIL")

    def test_timeout_is_timeout_timeout_not_fail(self):
        # The fix this file was written to pin: TIMEOUT no longer collapses
        # into the generic "FAIL" result -- it gets its own dedicated
        # result value, distinguishable without needing stop_reason too.
        call = self._run_and_get_finish_call(NIPXITimeoutError("charge timed out"))
        self.assertEqual(call["stop_reason"], "TIMEOUT")
        self.assertEqual(call["result"], "TIMEOUT")

    def test_unexpected_exception_is_failed_fail(self):
        call = self._run_and_get_finish_call(RuntimeError("unexpected"))
        self.assertEqual(call["stop_reason"], "FAILED")
        self.assertEqual(call["result"], "FAIL")

    def test_timeout_also_records_timeout_execution_state(self):
        seq = _make_sequence()

        def _raise():
            raise NIPXITimeoutError("charge timed out")

        with self.assertRaises(NIPXITimeoutError):
            seq.run_guarded(
                _raise, channel=1, relay_address=1, label="Charge Battery",
                verb="charging", cancel_message="Charging stopped by operator",
            )
        self.assertEqual(seq.storage.execution_states[-1]["state"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
