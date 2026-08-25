"""
Tests for test_control/hardware_manager.py's Hardware Audit Trail wiring
(see docs/architecture.md "Hardware Audit Trail" -- "Best Interception
Point"). Confirms instrumentation is applied automatically at
construction time for every device HardwareManager owns, that the
shared-NTC-DAQ-instance case is not double-instrumented, and that
attach_run_id_provider()/instrument_external_device() work as the rest
of this feature's wiring (test_control/storage_session.py, test.py's
SenseRouter construction sites) depends on.

HardwareManager.__init__() never touches real hardware (device objects
are constructed, not connected -- see its own docstring/module
docstring) -- real config/devices.py config dicts are used here exactly
as the module's own usage example does, with connect_all() never called,
so this needs no fakes/mocking of hardware I/O at all.

Every Settings subclass below points DATA_DIR/DATABASE_FILE at a
throwaway temp directory (mirrors tests/test_storage_measurement_
scoping.py's established convention) -- RawHardwareLogWriter's own
connection is lazy (opened only on the first actual .log() call), but
using a real dev-mode Settings here regardless would risk one test
(InstrumentExternalDeviceTests, which DOES trigger a real write) writing
into this project's real data_output/ database.
"""

import os
import shutil
import tempfile
import unittest

from config import devices as dev_cfg
from config.settings import Settings
from test_control.hardware_manager import HardwareManager


def _temp_settings(tmp_dir, enable_raw_hw_logging=True):
    return type("_TempAuditSettings", (Settings,), {
        "DATA_DIR": tmp_dir,
        "CSV_DIR": os.path.join(tmp_dir, "csv"),
        "DATABASE_FILE": os.path.join(tmp_dir, "audit_test.db"),
        "ENABLE_RAW_HARDWARE_LOGGING": enable_raw_hw_logging,
    })


class _AuditWiringTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _build(self, settings=None, with_dmm=True, with_ntc_daq_same_as_daq=False):
        smu_cfg = next(iter(dev_cfg.SMU_ASSIGNMENTS.values()))
        kwargs = dict(
            relay_cfg=dev_cfg.NUMATO_RELAY_MATRIX_CONFIG, smu_cfg=smu_cfg, daq_cfg=dev_cfg.DAQ_CONFIG,
        )
        if with_dmm:
            kwargs["dmm_cfg"] = dev_cfg.DMM_CONFIG
        if with_ntc_daq_same_as_daq:
            kwargs["ntc_daq_cfg"] = dev_cfg.DAQ_CONFIG  # identity match -> shared instance
        hw = HardwareManager(settings or _temp_settings(self.tmp_dir), **kwargs)
        self.addCleanup(hw._audit_writer.close)
        return hw


class ConstructionTimeInstrumentationTests(_AuditWiringTestCase):
    def test_smu_daq_relay_dmm_are_all_instrumented(self):
        hw = self._build()
        for dev in (hw.smu, hw.daq, hw.relay, hw.dmm):
            self.assertTrue(getattr(dev, "_nipxi_audit_instrumented", False))

    def test_no_dmm_configured_does_not_error(self):
        hw = self._build(with_dmm=False)
        self.assertIsNone(hw.dmm)
        self.assertTrue(getattr(hw.smu, "_nipxi_audit_instrumented", False))

    def test_shared_ntc_daq_instance_is_not_double_wrapped_or_broken(self):
        hw = self._build(with_ntc_daq_same_as_daq=True)
        self.assertIs(hw.ntc_daq, hw.daq)
        self.assertTrue(getattr(hw.daq, "_nipxi_audit_instrumented", False))
        # Identity must survive instrumentation exactly as it did before --
        # this is the real invariant disconnect_all()/health_check() rely
        # on (`self._ntc_daq is not self._daq` checks).
        self.assertIs(hw.ntc_daq, hw.daq)

    def test_disabled_setting_leaves_devices_unwrapped(self):
        hw = self._build(settings=_temp_settings(self.tmp_dir, enable_raw_hw_logging=False))
        for dev in (hw.smu, hw.daq, hw.relay, hw.dmm):
            self.assertFalse(getattr(dev, "_nipxi_audit_instrumented", False))


class AttachRunIdProviderTests(_AuditWiringTestCase):
    def test_default_run_id_provider_returns_none(self):
        hw = self._build()
        self.assertIsNone(hw._run_id_provider())

    def test_attach_run_id_provider_updates_what_wrapped_calls_see(self):
        hw = self._build()
        hw.attach_run_id_provider(lambda: "some_run_id")
        self.assertEqual(hw._run_id_provider(), "some_run_id")


class InstrumentExternalDeviceTests(_AuditWiringTestCase):
    """SenseRouter is constructed outside HardwareManager (see test.py) --
    instrument_external_device() lets it share this HardwareManager's
    writer/run_id provider without HardwareManager owning its
    construction."""

    class _FakeSenseRouter:
        name = "SENSE_ROUTER_1"

        def connect(self, channel):
            return True

    def test_instrument_external_device_wraps_it_and_shares_run_id_provider(self):
        hw = self._build()
        hw.attach_run_id_provider(lambda: "run_x")
        sense_router = self._FakeSenseRouter()
        hw.instrument_external_device(sense_router, "SENSE_ROUTER")
        self.assertTrue(getattr(sense_router, "_nipxi_audit_instrumented", False))
        # Exercise it through the SAME writer HardwareManager itself uses,
        # confirming the run_id provider wiring reaches an externally
        # constructed device identically to hw.smu/hw.daq/etc.
        original_log = hw._audit_writer.log
        seen = []
        hw._audit_writer.log = lambda **kwargs: (seen.append(kwargs), original_log(**kwargs))[0]
        sense_router.connect(1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["run_id"], "run_x")
        self.assertEqual(seen[0]["device_type"], "SENSE_ROUTER")


if __name__ == "__main__":
    unittest.main()
