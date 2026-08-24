"""
Tests for the "Safest Shutdown Sequence" fix -- hardware/smu.py::
SMU.emergency_output_off() now actively zeros the commanded setpoint
(current_level/voltage_limit, or voltage_level in DC_VOLTAGE mode) WHILE
OUTPUT IS STILL ENABLED, before ever attempting output_disable(). See
docs/architecture.md "Safest Shutdown Sequence".

Root cause this fixes: writing to current_level/voltage_limit AFTER
output_enabled is already False (all zero_output_setpoint_best_effort()
alone -- and the earlier voltage_limit fix in commit 983fc64 -- ever did)
has no physical effect on the output terminals, since the active
regulation loop already stopped. Only a write made WHILE STILL ENABLED
can actually pull the output down before it goes high-impedance/inert --
which is what this test file proves actually happens, using a fake
NI-DCPower session that records the exact ORDER of every property write,
including whether output was still enabled at the moment of each write.

No real hardware -- nidcpower IS imported (confirmed importable in this
dev environment, matching the existing convention in
tests/test_smu_post_isolation_zeroing.py) so the fake session's
output_function values are the real enum members production code compares
against.
"""

import unittest

import nidcpower

from hardware.smu import SMU


class _OrderTrackingSession:
    """
    Records every output_enabled/current_level/voltage_limit/
    voltage_level write, in order, together with whether output_enabled
    was True at the moment of that specific write -- exactly what's
    needed to prove "zeroed while still enabled" vs "zeroed after already
    disabled".
    """

    def __init__(self, output_function=nidcpower.OutputFunction.DC_CURRENT):
        self.output_function = output_function
        self._output_enabled = True
        self._current_level = 1.0
        self._voltage_limit = 4.0
        self._voltage_level = 4.0
        self.commit_calls = 0
        self.events = []  # [(what, new_value, output_was_enabled_at_this_moment), ...]

    def abort(self):
        pass

    @property
    def output_enabled(self):
        return self._output_enabled

    @output_enabled.setter
    def output_enabled(self, value):
        self.events.append(("output_enabled", value, self._output_enabled))
        self._output_enabled = value

    @property
    def current_level(self):
        return self._current_level

    @current_level.setter
    def current_level(self, value):
        self.events.append(("current_level", value, self._output_enabled))
        self._current_level = value

    @property
    def voltage_limit(self):
        return self._voltage_limit

    @voltage_limit.setter
    def voltage_limit(self, value):
        self.events.append(("voltage_limit", value, self._output_enabled))
        self._voltage_limit = value

    @property
    def voltage_level(self):
        return self._voltage_level

    @voltage_level.setter
    def voltage_level(self, value):
        self.events.append(("voltage_level", value, self._output_enabled))
        self._voltage_level = value

    def commit(self):
        self.commit_calls += 1


def _make_smu(session) -> SMU:
    smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
    smu._session = session
    smu.log.disabled = True
    return smu


class SetpointZeroedBeforeDisableTests(unittest.TestCase):
    def test_current_level_is_zeroed_while_output_still_enabled(self):
        session = _OrderTrackingSession(output_function=nidcpower.OutputFunction.DC_CURRENT)
        smu = _make_smu(session)

        self.assertTrue(smu.emergency_output_off("ctrl-c cancellation"))

        setpoint_events = [e for e in session.events if e[0] in ("current_level", "voltage_limit")]
        self.assertTrue(setpoint_events, "expected the setpoint to be written at least once")
        for what, value, was_enabled in setpoint_events:
            self.assertEqual(value, 0.0)
            self.assertTrue(
                was_enabled,
                f"{what} was zeroed AFTER output_enabled was already False -- "
                f"this has no physical effect on the output terminals",
            )

    def test_setpoint_is_zeroed_before_the_first_output_disable_write(self):
        session = _OrderTrackingSession()
        smu = _make_smu(session)
        smu.emergency_output_off("test")

        setpoint_indices = [i for i, e in enumerate(session.events) if e[0] in ("current_level", "voltage_limit")]
        disable_indices = [i for i, e in enumerate(session.events) if e[0] == "output_enabled" and e[1] is False]
        self.assertTrue(setpoint_indices)
        self.assertTrue(disable_indices)
        self.assertLess(
            max(setpoint_indices), min(disable_indices),
            "the setpoint must be zeroed BEFORE output is ever commanded off, not after",
        )

    def test_voltage_level_is_zeroed_while_still_enabled_in_dc_voltage_mode(self):
        session = _OrderTrackingSession(output_function=nidcpower.OutputFunction.DC_VOLTAGE)
        smu = _make_smu(session)
        smu.emergency_output_off("test")

        voltage_events = [e for e in session.events if e[0] == "voltage_level"]
        self.assertTrue(voltage_events)
        for _, value, was_enabled in voltage_events:
            self.assertEqual(value, 0.0)
            self.assertTrue(was_enabled)

    def test_commit_is_called_for_the_pre_disable_zero(self):
        session = _OrderTrackingSession()
        smu = _make_smu(session)
        smu.emergency_output_off("test")
        self.assertGreaterEqual(session.commit_calls, 1)

    def test_a_failing_pre_disable_zero_does_not_prevent_output_disable(self):
        # The setpoint-zero step is best-effort -- a failure there must
        # never block the actually-critical output_disable() that follows.
        class _RaisingCurrentLevelSession(_OrderTrackingSession):
            @property
            def current_level(self):
                return self._current_level

            @current_level.setter
            def current_level(self, value):
                raise RuntimeError("simulated write failure")

        session = _RaisingCurrentLevelSession()
        smu = _make_smu(session)
        result = smu.emergency_output_off("test")  # must not raise
        self.assertTrue(result)
        self.assertFalse(session.output_enabled)  # output_disable() still ran and succeeded

    def test_on_event_reports_the_pre_disable_stage_distinctly(self):
        session = _OrderTrackingSession()
        smu = _make_smu(session)
        events = []
        smu.emergency_output_off("test", on_event=events.append)
        self.assertTrue(any(e.startswith("pre-disable:") and "current_level zeroed" in e for e in events))
        self.assertTrue(any(e.startswith("pre-disable:") and "voltage_limit zeroed" in e for e in events))

    def test_post_isolation_call_still_reports_its_own_distinct_stage(self):
        session = _OrderTrackingSession()
        smu = _make_smu(session)
        events = []
        smu.zero_output_setpoint_best_effort("test", on_event=events.append)
        self.assertTrue(any(e.startswith("post-isolation:") for e in events))

    def test_no_session_is_still_a_safe_no_op(self):
        smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
        smu.log.disabled = True
        self.assertTrue(smu.emergency_output_off("test"))  # must not raise


if __name__ == "__main__":
    unittest.main()
