"""
Tests for the future-architecture Battery Voltage Provider (hardware/
voltage_provider.py -- not wired into any real path) and the PSU sense
comparison used by the SMU "Sense Voltage Validation" test menu entry
(hardware/smu.py::SMU.measure_sense_comparison()).

Uses a fake NI-DCPower session and a fake `nidcpower` module -- no real
hardware, no nidcpower install required.
"""

import sys
import types
import unittest
from unittest import mock

from hardware.smu import SMU
from hardware.voltage_provider import (
    BatteryVoltageProvider, DMMVoltageProvider, PSUSenseVoltageProvider,
)


def _fake_nidcpower():
    mod = types.ModuleType("nidcpower")
    mod.OutputFunction = types.SimpleNamespace(DC_VOLTAGE="DC_VOLTAGE", DC_CURRENT="DC_CURRENT")
    mod.SourceMode = types.SimpleNamespace(SINGLE_POINT="SINGLE_POINT")
    mod.MeasurementTypes = types.SimpleNamespace(VOLTAGE="V", CURRENT="I")
    mod.Sense = types.SimpleNamespace(LOCAL="LOCAL", REMOTE="REMOTE")
    return mod


class _Initiated:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeSession:
    """Local reads set_v; remote reads set_v - remote_drop_v. Optionally
    raises when REMOTE is selected, like an open/unsupported sense."""

    def __init__(self, remote_drop_v=0.0, fail_remote=False):
        self.current_level = 0.0
        self.voltage_limit = 0.0
        self.remote_drop_v = remote_drop_v
        self.fail_remote = fail_remote
        self.sense_writes = []
        self._sense = "LOCAL"
        self.output_enabled = False
        self.voltage_level = 0.0
        self.current_limit = 0.0

    @property
    def sense(self):
        return self._sense

    @sense.setter
    def sense(self, value):
        self.sense_writes.append(value)
        self._sense = value

    def abort(self):
        pass

    def commit(self):
        if self.fail_remote and self._sense == "REMOTE":
            raise RuntimeError("simulated remote sense failure")

    def initiate(self):
        return _Initiated()

    def query_in_compliance(self):
        return False

    def measure(self, kind):
        if kind == "I":
            return 0.001
        return self.voltage_level - (self.remote_drop_v if self._sense == "REMOTE" else 0.0)


def _smu(session):
    smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130"})
    smu._session = session
    smu.log.disabled = True
    return smu


class VoltageProviderTests(unittest.TestCase):
    def test_dmm_provider_passthrough_without_sense_channel(self):
        dmm = mock.Mock()
        dmm.measure_dc_voltage.return_value = 3.71
        p = DMMVoltageProvider(dmm)
        self.assertEqual(p.read_voltage(), 3.71)
        self.assertEqual(p.source_name, "DMM")
        self.assertTrue(p.available_with_output_disabled)

    def test_psu_sense_provider_is_not_implemented(self):
        p = PSUSenseVoltageProvider(mock.Mock())
        with self.assertRaises(NotImplementedError):
            p.read_voltage()
        self.assertFalse(p.available_with_output_disabled)
        self.assertIsInstance(p, BatteryVoltageProvider)


class SenseComparisonTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"nidcpower": _fake_nidcpower()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_call_never_touches_sense(self):
        session = _FakeSession()
        reading = _smu(session).source_dc_voltage_point(4.0, 1.0, 4.7)
        self.assertEqual(session.sense_writes, [])
        self.assertIsNone(reading["sense"])

    def test_comparison_returns_local_and_remote_and_restores_local(self):
        session = _FakeSession(remote_drop_v=0.05)
        result = _smu(session).measure_sense_comparison(4.0, 1.0, 4.7)
        self.assertAlmostEqual(result["local"]["measured_v"], 4.0)
        self.assertAlmostEqual(result["remote"]["measured_v"], 3.95)
        self.assertIsNone(result["remote_error"])
        self.assertEqual(session.sense_writes, ["LOCAL", "REMOTE", "LOCAL"])
        self.assertFalse(session.output_enabled)

    def test_remote_failure_is_reported_not_raised(self):
        session = _FakeSession(fail_remote=True)
        result = _smu(session).measure_sense_comparison(4.0, 1.0, 4.7)
        self.assertIsNotNone(result["local"])
        self.assertIsNone(result["remote"])
        self.assertIn("remote sense failure", result["remote_error"])
        self.assertFalse(session.output_enabled)
        self.assertEqual(session.sense_writes[-1], "LOCAL")


class RemoteSensePreparationTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"nidcpower": _fake_nidcpower()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _smu_with(self, session, **cfg):
        smu = SMU({"resource": "PXI1SlotTest", "model": "PXI-4130", **cfg})
        smu._session = session
        smu.log.disabled = True
        return smu

    def test_default_config_never_touches_sense_in_charge_discharge_config(self):
        session = _FakeSession()
        smu = self._smu_with(session)
        self.assertIsNone(smu.sense_mode)
        smu.set_charge_mode(1.0, 4.0)
        smu.set_discharge_mode(1.0, 4.2)
        self.assertEqual(session.sense_writes, [])

    def test_configured_remote_is_applied_and_verified(self):
        session = _FakeSession()
        self._smu_with(session, sense_mode="remote").set_charge_mode(1.0, 4.0)
        self.assertEqual(session.sense_writes, ["REMOTE"])

    def test_invalid_sense_mode_rejected(self):
        from utils.errors import SMUError
        with self.assertRaises(SMUError):
            SMU({"resource": "x", "sense_mode": "kelvin"})

    def test_acceptance_check_restores_previous_sense(self):
        session = _FakeSession()
        result = self._smu_with(session).check_remote_sense_accepted()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["previous"], "local")
        self.assertEqual(session.sense_writes, ["REMOTE", "LOCAL"])

    def test_acceptance_check_reports_rejection(self):
        session = _FakeSession(fail_remote=True)
        result = self._smu_with(session).check_remote_sense_accepted()
        self.assertFalse(result["accepted"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(session.sense_writes[-1], "LOCAL")


if __name__ == "__main__":
    unittest.main()
