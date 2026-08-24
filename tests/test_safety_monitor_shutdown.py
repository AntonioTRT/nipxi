"""
Tests for test_control/safety_monitor.py::SafetyMonitor.emergency_stop()/
safe_cancel_shutdown() -- specifically the new post-isolation SMU
setpoint-zeroing step (see docs/architecture.md "Post-Isolation SMU
Setpoint Zeroing"), and that the existing shutdown contract (SMU-off
before relay-open, never raises) is unchanged by adding it.

Uses fake smu/relay_matrix doubles recording call order -- no hardware,
no real NI-DCPower/relay driver.
"""

import unittest

from config.settings import Settings
from test_control.safety_monitor import SafetyMonitor


class _FakeSmu:
    def __init__(self, disable_returns=True, zero_raises=False, zero_returns=True):
        self.calls = []
        self._disable_returns = disable_returns
        self._zero_raises = zero_raises
        self._zero_returns = zero_returns

    def emergency_output_off(self, reason, on_event=None):
        self.calls.append(("emergency_output_off", reason))
        self._emit(on_event, f"emergency_output_off requested ({reason})")
        return self._disable_returns

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        self.calls.append(("zero_output_setpoint_best_effort", reason))
        if self._zero_raises:
            raise RuntimeError("simulated unexpected failure in zeroing")
        self._emit(on_event, "current_level zeroed (was 0.500 A)")
        return self._zero_returns

    @staticmethod
    def _emit(on_event, message):
        # Mirrors the real hardware/smu.py contract: a raising on_event
        # callback must never propagate out of the SMU driver call it was
        # attached to -- see hardware/smu.py::emergency_output_off()'s
        # docstring ("Any exception from on_event itself is swallowed
        # here").
        if on_event is not None:
            try:
                on_event(message)
            except Exception:
                pass


class _FakeRelayMatrix:
    def __init__(self, open_all_raises=False):
        self.calls = []
        self._open_all_raises = open_all_raises

    def open_all(self):
        self.calls.append("open_all")
        if self._open_all_raises:
            raise RuntimeError("simulated relay open_all failure")


class EmergencyStopTests(unittest.TestCase):
    def test_call_order_is_smu_off_then_relay_open_then_zero(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")
        self.assertEqual([c[0] for c in smu.calls], ["emergency_output_off", "zero_output_setpoint_best_effort"])
        self.assertEqual(relay.calls, ["open_all"])
        # zeroing must be the LAST SMU call, after relay.open_all() already ran.
        self.assertEqual(smu.calls[-1][0], "zero_output_setpoint_best_effort")

    def test_zeroing_still_runs_when_relay_open_all_raises(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix(open_all_raises=True)
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")  # must not raise
        self.assertIn(("zero_output_setpoint_best_effort", "test reason"), smu.calls)

    def test_zeroing_still_runs_when_emergency_output_off_returns_false(self):
        smu = _FakeSmu(disable_returns=False)
        relay = _FakeRelayMatrix()
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")  # must not raise
        self.assertIn(("zero_output_setpoint_best_effort", "test reason"), smu.calls)

    def test_zeroing_raising_unexpectedly_does_not_propagate(self):
        smu = _FakeSmu(zero_raises=True)
        relay = _FakeRelayMatrix()
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")  # must not raise

    def test_never_raises_even_when_everything_fails(self):
        smu = _FakeSmu(disable_returns=False, zero_raises=True)
        relay = _FakeRelayMatrix(open_all_raises=True)
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")  # must not raise


class SafeCancelShutdownTests(unittest.TestCase):
    def test_call_order_is_smu_off_then_relay_open_then_zero(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        SafetyMonitor(Settings).safe_cancel_shutdown(smu, relay, "operator cancel")
        self.assertEqual([c[0] for c in smu.calls], ["emergency_output_off", "zero_output_setpoint_best_effort"])
        self.assertEqual(relay.calls, ["open_all"])

    def test_zeroing_still_runs_when_relay_open_all_raises(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix(open_all_raises=True)
        SafetyMonitor(Settings).safe_cancel_shutdown(smu, relay, "operator cancel")  # must not raise
        self.assertIn(("zero_output_setpoint_best_effort", "operator cancel"), smu.calls)

    def test_never_raises_even_when_everything_fails(self):
        smu = _FakeSmu(disable_returns=False, zero_raises=True)
        relay = _FakeRelayMatrix(open_all_raises=True)
        SafetyMonitor(Settings).safe_cancel_shutdown(smu, relay, "operator cancel")  # must not raise


class ShutdownTraceOnEventTests(unittest.TestCase):
    """
    on_event -- see docs/architecture.md "Shutdown Trace Logging". Proves
    emergency_stop()/safe_cancel_shutdown() forward the caller-supplied
    callback into smu.emergency_output_off()/zero_output_setpoint_best_effort()
    unchanged, AND call it directly themselves around relay_matrix.open_all()
    and at the very end -- without requiring a real DataStorage/event_log
    (that DB-write wiring is covered separately by
    tests/test_shutdown_trace_logging.py against
    BatteryOperationSequence._shutdown_trace_logger()).
    """

    def test_emergency_stop_reports_relay_open_and_completion(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        events = []
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason", on_event=events.append)
        self.assertIn("relay_matrix.open_all executed", events)
        self.assertIn("shutdown completed", events)
        # The standardized EVENT_TYPE=... line is now the final event --
        # see docs/architecture.md "Standardized Hardware Event Logging".
        self.assertTrue(events[-1].startswith("EVENT_TYPE=EMERGENCY_STOP_COMPLETED"))

    def test_emergency_stop_reports_relay_open_failure(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix(open_all_raises=True)
        events = []
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason", on_event=events.append)
        self.assertTrue(any(e.startswith("relay_matrix.open_all FAILED") for e in events))
        self.assertIn("shutdown completed", events)
        self.assertTrue(events[-1].startswith("EVENT_TYPE=EMERGENCY_STOP_COMPLETED"))

    def test_emergency_stop_forwards_on_event_into_smu_calls(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        events = []
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason", on_event=events.append)
        self.assertTrue(any("emergency_output_off requested" in e for e in events))
        self.assertTrue(any("current_level zeroed" in e for e in events))

    def test_safe_cancel_shutdown_reports_relay_open_and_completion(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        events = []
        SafetyMonitor(Settings).safe_cancel_shutdown(smu, relay, "operator cancel", on_event=events.append)
        self.assertIn("relay_matrix.open_all executed", events)
        self.assertIn("shutdown completed", events)
        self.assertTrue(events[-1].startswith("EVENT_TYPE=EMERGENCY_STOP_COMPLETED"))

    def test_on_event_defaults_to_none_and_is_optional(self):
        # Existing/other callers that don't pass on_event at all must be
        # completely unaffected -- this is the exact backward-compatibility
        # guarantee the default=None param depends on.
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason")  # no on_event, must not raise
        SafetyMonitor(Settings).safe_cancel_shutdown(smu, relay, "test reason")  # ditto

    def test_a_raising_on_event_does_not_propagate_or_break_shutdown(self):
        smu = _FakeSmu()
        relay = _FakeRelayMatrix()

        def _raising_on_event(message):
            raise RuntimeError("simulated logging failure")

        # Must not raise, and the real shutdown steps must still all run.
        SafetyMonitor(Settings).emergency_stop(smu, relay, "test reason", on_event=_raising_on_event)
        self.assertEqual([c[0] for c in smu.calls], ["emergency_output_off", "zero_output_setpoint_best_effort"])
        self.assertEqual(relay.calls, ["open_all"])


if __name__ == "__main__":
    unittest.main()
