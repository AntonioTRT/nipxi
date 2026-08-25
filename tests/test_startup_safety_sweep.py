"""
Tests for Change 2 ("Startup Safety Sweep") -- see
test_control/safety_sweep.py and docs/architecture.md "Startup Safety
Sweep". Uses synthetic SMU/relay factories (no real hardware, no real
config/devices.py) -- mirrors hardware/sense_router.py::
ConfigDrivenSenseRouter's own testability convention.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from config.system_mode import SystemMode
from test_control.safety_sweep import SafetyFaultBlocked, run_startup_safety_sweep


class _FakeSettings:
    SYSTEM_MODE = SystemMode.DEVELOPMENT.value


class _FakeSmu:
    def __init__(self, cfg, connect_fails=False, verify_ok=True):
        self.cfg = cfg
        self._connect_fails = connect_fails
        self._verify_ok = verify_ok
        self.connected = False
        self.disconnect_called = False

    def connect(self):
        if self._connect_fails:
            raise RuntimeError("simulated: no NI-DCPower session available")
        self.connected = True

    def emergency_output_off(self, reason, on_event=None):
        return self._verify_ok

    def disconnect(self):
        self.disconnect_called = True
        self.connected = False


class _FakeRelay:
    def __init__(self, cfg, connect_fails=False, open_all_fails=False):
        self.cfg = cfg
        self._connect_fails = connect_fails
        self._open_all_fails = open_all_fails
        self.connected = False
        self.disconnect_called = False

    def connect(self):
        if self._connect_fails:
            raise RuntimeError("simulated: relay matrix unreachable")
        self.connected = True

    def open_all(self):
        if self._open_all_fails:
            raise RuntimeError("simulated: relay bank verification mismatch")

    def disconnect(self):
        self.disconnect_called = True
        self.connected = False


def _run(smu_factory, relay_factory_create, smu_names=("SMU1",), relay_names=("MATRIX1",)):
    smu_assignments = {name: {"nickname": name} for name in smu_names}
    relay_matrix_configs = {name: {"nickname": name} for name in relay_names}
    with mock.patch("builtins.input", return_value=""):
        run_startup_safety_sweep(
            settings=_FakeSettings, smu_assignments=smu_assignments,
            relay_matrix_configs=relay_matrix_configs,
            smu_factory=smu_factory, relay_factory_create=relay_factory_create,
        )


class StartupSafetySweepTests(unittest.TestCase):
    def test_every_device_verified_safe_completes_without_error(self):
        smus, relays = [], []

        def smu_factory(cfg):
            smu = _FakeSmu(cfg, verify_ok=True)
            smus.append(smu)
            return smu

        def relay_factory(cfg):
            relay = _FakeRelay(cfg, open_all_fails=False)
            relays.append(relay)
            return relay

        _run(smu_factory, relay_factory)
        self.assertTrue(all(s.disconnect_called for s in smus))
        self.assertTrue(all(r.disconnect_called for r in relays))

    def test_missing_hardware_is_tolerated_in_development_mode(self):
        # A device that simply fails to connect must not block startup --
        # "do not break development environments".
        def smu_factory(cfg):
            return _FakeSmu(cfg, connect_fails=True)

        def relay_factory(cfg):
            return _FakeRelay(cfg, connect_fails=True)

        _run(smu_factory, relay_factory)  # must not raise

    def test_smu_that_connects_but_fails_verification_blocks_startup(self):
        def smu_factory(cfg):
            return _FakeSmu(cfg, verify_ok=False)

        def relay_factory(cfg):
            return _FakeRelay(cfg)

        with self.assertRaises(SafetyFaultBlocked):
            _run(smu_factory, relay_factory)

    def test_relay_that_connects_but_fails_open_all_blocks_startup(self):
        def smu_factory(cfg):
            return _FakeSmu(cfg, verify_ok=True)

        def relay_factory(cfg):
            return _FakeRelay(cfg, open_all_fails=True)

        with self.assertRaises(SafetyFaultBlocked):
            _run(smu_factory, relay_factory)

    def test_unsafe_smu_displays_the_safety_fault_screen_before_raising(self):
        def smu_factory(cfg):
            return _FakeSmu(cfg, verify_ok=False)

        def relay_factory(cfg):
            return _FakeRelay(cfg)

        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SafetyFaultBlocked):
                _run(smu_factory, relay_factory)
        self.assertIn("SAFETY FAULT", buf.getvalue())

    def test_device_is_always_disconnected_even_after_a_blocking_fault(self):
        captured = {}

        def smu_factory(cfg):
            smu = _FakeSmu(cfg, verify_ok=False)
            captured["smu"] = smu
            return smu

        def relay_factory(cfg):
            return _FakeRelay(cfg)

        with self.assertRaises(SafetyFaultBlocked):
            _run(smu_factory, relay_factory)
        self.assertTrue(captured["smu"].disconnect_called)

    def test_every_configured_smu_and_relay_is_swept_not_just_the_first(self):
        seen = []

        def smu_factory(cfg):
            seen.append(cfg["nickname"])
            return _FakeSmu(cfg, verify_ok=True)

        def relay_factory(cfg):
            seen.append(cfg["nickname"])
            return _FakeRelay(cfg)

        _run(smu_factory, relay_factory, smu_names=("SMU1", "SMU2"), relay_names=("MATRIX1", "MATRIX2"))
        self.assertEqual(seen, ["SMU1", "SMU2", "MATRIX1", "MATRIX2"])


if __name__ == "__main__":
    unittest.main()
