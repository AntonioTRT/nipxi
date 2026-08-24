"""
Tests for "Battery Removal During Charge Detection" -- see
test_control/battery_diagnostics.py::charge_transition_suggests_battery_removed(),
test_control/charge_sequence.py, utils/errors.py::
BatteryRemovedDuringChargeError, and docs/architecture.md "Battery Removal
During Charge Detection".

Covers the pure detector function directly, then a full ChargeSequence.run()
integration proof (scripted fake hardware, no real hardware/sleeps) that a
battery physically removed mid-charge can never be reported as a passing
end-of-charge -- the core requirement this feature exists to satisfy.
"""

import unittest

from config.settings import Settings
from test_control.battery_diagnostics import charge_transition_suggests_battery_removed
from test_control.charge_sequence import ChargeSequence
from test_control.safety_monitor import SafetyStatus
from utils.errors import BatteryRemovedDuringChargeError, SafetyViolationError


class ErrorHierarchyTests(unittest.TestCase):
    def test_is_a_safety_violation_error_subclass(self):
        self.assertTrue(issubclass(BatteryRemovedDuringChargeError, SafetyViolationError))


class DetectorBoundaryTests(unittest.TestCase):
    """Direct tests of the pure detector function -- no hardware, no
    ChargeSequence involved."""

    def _detect(self, prev_v, prev_i, v, i, current_a=1.0, voltage_limit_v=4.0, cutoff_a=0.15):
        return charge_transition_suggests_battery_removed(
            prev_v=prev_v, prev_i=prev_i, v=v, i=i,
            current_a=current_a, voltage_limit_v=voltage_limit_v, cutoff_a=cutoff_a,
        )

    def test_first_sample_is_never_flagged_regardless_of_values(self):
        # No prior sample to compare against -- see the function's own
        # docstring on why this is a deliberate, disclosed boundary
        # (matches the existing ALREADY_CHARGED classification, which also
        # accepts a battery that starts at/near the CV target).
        self.assertFalse(self._detect(prev_v=None, prev_i=None, v=4.0, i=0.05))

    def test_eoc_not_yet_satisfied_is_never_flagged(self):
        # Previous sample was full CC current, but THIS sample hasn't
        # reached the EOC condition yet -- nothing to evaluate.
        self.assertFalse(self._detect(prev_v=3.9, prev_i=1.0, v=3.95, i=0.9))

    def test_genuine_gradual_taper_is_not_flagged(self):
        # Previous sample already well into the CV taper (current far
        # below the CC-phase fraction of commanded current) -- a real
        # cell's natural completion.
        self.assertFalse(self._detect(prev_v=4.0, prev_i=0.2, v=4.0, i=0.14))

    def test_abrupt_jump_from_full_cc_current_is_flagged(self):
        # Previous sample still at the full commanded current, THIS sample
        # already satisfies EOC -- physically impossible for a real cell,
        # consistent with the battery having been removed.
        self.assertTrue(self._detect(prev_v=3.6, prev_i=1.0, v=4.0, i=0.05))

    def test_exactly_at_the_cc_phase_boundary_is_flagged(self):
        # prev_i == current_a * CC_PHASE_CURRENT_FRACTION (0.5) exactly --
        # the boundary is inclusive.
        self.assertTrue(self._detect(prev_v=3.8, prev_i=0.5, v=4.0, i=0.1, current_a=1.0))

    def test_just_below_the_cc_phase_boundary_is_not_flagged(self):
        self.assertFalse(self._detect(prev_v=3.9, prev_i=0.49, v=4.0, i=0.1, current_a=1.0))

    def test_sign_does_not_matter_current_is_compared_by_magnitude(self):
        # ChargeSequence always commands a positive current_a, but the
        # detector itself should not assume a sign convention it doesn't
        # need to.
        self.assertTrue(self._detect(prev_v=3.6, prev_i=-1.0, v=4.0, i=-0.05, current_a=-1.0))


# ---------------------------------------------------------------------------
# ChargeSequence.run() integration -- scripted fake hardware, no real sleeps.
# ---------------------------------------------------------------------------

class _ScriptedDmm:
    """Returns one voltage per call, in order -- StopIteration if the
    script runs out (a test bug, not a production concern)."""
    model = "NI-4065"
    resource = "DMM1"

    def __init__(self, voltages):
        self._voltages = list(voltages)
        self._idx = 0

    def measure_dc_voltage(self):
        v = self._voltages[self._idx]
        self._idx += 1
        return v


class _ScriptedSmu:
    """Returns one current per call, in order. voltage_v in the returned
    dict is display-only (see charge_sequence.py) -- never used for a
    decision, so a fixed placeholder is fine here."""
    model = "PXI-4130"
    resource = "SMU1"

    def __init__(self, currents):
        self._currents = list(currents)
        self._idx = 0
        self.enabled = False

    def set_charge_mode(self, current_a, voltage_limit_v):
        pass

    def output_enable(self):
        self.enabled = True

    def measure(self):
        i = self._currents[self._idx]
        self._idx += 1
        return {"voltage_v": 0.0, "current_a": i}

    def emergency_output_off(self, reason, on_event=None):
        self.enabled = False
        return True

    def zero_output_setpoint_best_effort(self, reason, on_event=None):
        return True


class _FakeRelay:
    name = "TEST_RELAY_MATRIX"

    def close(self, channel):
        pass

    def open(self, channel):
        pass


class _RecordingSafety:
    def __init__(self):
        self.calls = []

    def set_battery_limits(self, battery_cfg):
        pass

    def check(self, v, i, t_c, mode=None):
        return SafetyStatus(safe=True)

    def emergency_stop(self, smu, relay, reason, on_event=None):
        self.calls.append(("emergency_stop", reason))

    def safe_cancel_shutdown(self, smu, relay, reason, on_event=None):
        self.calls.append(("safe_cancel_shutdown", reason))


class _RecordingStorage:
    def __init__(self):
        self.run_id = "test-run"
        self.events = []
        self.finish_calls = []

    def get_run_summary(self, run_id):
        return None

    def log_event(self, **kwargs):
        self.events.append(kwargs)

    def record_execution_state(self, **kwargs):
        pass

    def record_measurement(self, **kwargs):
        pass

    def finish_run_summary(self, **kwargs):
        self.finish_calls.append(kwargs)

    def get_first_measurement(self, **kwargs):
        return None

    def get_measurements(self, **kwargs):
        return []

    def get_recent_events(self, **kwargs):
        return []

    def event_messages(self):
        return [e["message"] for e in self.events]


BATTERY_CFG = {
    "voltage_max_v": 4.2, "voltage_min_v": 3.0,
    "max_charge_current_a": 1.5, "max_discharge_current_a": 1.05,
    "max_temp_c": 45.0,
}
TEST_SETPOINTS = {"charge_current_a": 1.0, "charge_voltage_v": 4.0,
                   "discharge_current_a": 0.1, "discharge_cutoff_v": 3.0}


class _FastSettings:
    """Same values as config.settings.Settings for what these tests need,
    except STABILIZATION_S=0 and a very high SAMPLE_RATE_HZ -- keeps a
    multi-sample scripted run instant instead of waiting out real
    stabilization/inter-sample sleeps (mirrors
    tests/test_sense_routing_live_wiring.py's identical _FastSettings,
    extended here since these tests need MULTIPLE samples per run, not
    just one)."""
    STABILIZATION_S = 0.0
    SAMPLE_RATE_HZ = 100_000.0
    CHARGE_CUTOFF_A = 0.15
    CHARGE_TIMEOUT_S = Settings.CHARGE_TIMEOUT_S
    REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V


def _make_sequence(dmm, smu, safety=None, storage=None):
    seq = ChargeSequence(
        smu=smu, dmm=dmm, relay=_FakeRelay(), safety=safety or _RecordingSafety(),
        storage=storage or _RecordingStorage(), settings=_FastSettings, group_name="TEST",
    )
    seq.log.disabled = True
    return seq


class GenuineTaperCompletesNormallyTests(unittest.TestCase):
    def test_gradual_cv_taper_completes_as_a_pass(self):
        # CC phase (1.0 A) rising toward 4.0 V, then a genuine, gradual
        # multi-sample CV taper down to the cutoff -- pre_enable_v read is
        # the first DMM call, then one DMM+SMU pair per loop iteration.
        dmm = _ScriptedDmm([3.5, 3.5, 3.9, 4.0, 4.0, 4.0, 4.0])
        smu = _ScriptedSmu([1.0, 1.0, 0.9, 0.5, 0.2, 0.14])
        safety = _RecordingSafety()
        storage = _RecordingStorage()
        seq = _make_sequence(dmm, smu, safety=safety, storage=storage)

        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)

        self.assertTrue(result)
        self.assertEqual(safety.calls, [])  # no emergency_stop/safe_cancel_shutdown
        self.assertEqual(storage.finish_calls[-1]["result"], "PASS")

    def test_already_charged_from_the_first_sample_completes_as_a_pass(self):
        # First sample already satisfies EOC -- prev_v/prev_i are None,
        # so this must be accepted (matches the existing ALREADY_CHARGED
        # classification), not flagged as removal.
        dmm = _ScriptedDmm([4.0, 4.0])
        smu = _ScriptedSmu([0.05])
        safety = _RecordingSafety()
        storage = _RecordingStorage()
        seq = _make_sequence(dmm, smu, safety=safety, storage=storage)

        result = seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)

        self.assertTrue(result)
        self.assertEqual(safety.calls, [])
        self.assertEqual(storage.finish_calls[-1]["result"], "PASS")


class BatteryRemovedMidChargeNeverPassesTests(unittest.TestCase):
    """The core requirement: a battery physically removed mid-charge must
    never be reported as a passing end-of-charge."""

    def test_abrupt_removal_raises_and_never_reports_pass(self):
        # CC phase at full current for two samples, then an abrupt jump
        # straight to the CV target with near-zero current -- the
        # removal signature.
        dmm = _ScriptedDmm([3.5, 3.5, 3.6, 4.0])
        smu = _ScriptedSmu([1.0, 1.0, 0.05])
        safety = _RecordingSafety()
        storage = _RecordingStorage()
        seq = _make_sequence(dmm, smu, safety=safety, storage=storage)

        with self.assertRaises(BatteryRemovedDuringChargeError):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)

        # Routed through the SafetyViolationError shutdown path, not a
        # cancellation, and never reported as a pass.
        self.assertEqual(len(safety.calls), 1)
        self.assertEqual(safety.calls[0][0], "emergency_stop")
        self.assertEqual(storage.finish_calls[-1]["result"], "FAIL")
        self.assertEqual(storage.finish_calls[-1]["stop_reason"], "SAFETY_VIOLATION")
        self.assertNotEqual(storage.finish_calls[-1]["result"], "PASS")
        # The SMU's own shutdown path actually ran (emergency_output_off())
        # as part of the same safety handling -- output left disabled.
        self.assertFalse(smu.enabled)

    def test_abrupt_removal_logs_a_clear_reason(self):
        dmm = _ScriptedDmm([3.5, 3.5, 3.6, 4.0])
        smu = _ScriptedSmu([1.0, 1.0, 0.05])
        storage = _RecordingStorage()
        seq = _make_sequence(dmm, smu, storage=storage)

        with self.assertRaises(BatteryRemovedDuringChargeError):
            seq.run(channel=1, relay_address=1, battery_cfg=BATTERY_CFG, test_setpoints=TEST_SETPOINTS)

        messages = storage.event_messages()
        self.assertTrue(any("abrupt CC->EOC transition" in m for m in messages))
        self.assertTrue(any("battery being physically removed" in m for m in messages))
        # run_guarded()'s own generic SafetyViolationError event must also
        # be present, unchanged from the existing pattern (e.g.
        # ReversePolarityError already produces both a detailed event and
        # this generic one) -- not a replacement for it.
        self.assertTrue(any(m.startswith("Safety violation --") for m in messages))


if __name__ == "__main__":
    unittest.main()
