"""
Tests specific to hardware/relay_niswitch.py::NIPXIe2569RelayMatrix, over the
in-memory NI-SWITCH fake (tests/_fake_niswitch.py). The shared safety
contract (one relay closed, interlock, verification, settle, ...) is covered
by tests/test_relay_conformance.py; this file covers what is specific to this
driver: pure construction, session handling, relay discovery, NI-SWITCH
error mapping, factory/config/startup-sweep integration.
"""

import sys
import unittest
from unittest import mock

import test as test_module  # noqa: F401 -- importing this calls logging.disable(logging.CRITICAL)
from config import devices as dev_cfg
from hardware import relay as relay_module
from hardware.relay_factory import RelayFactory
from hardware.relay_niswitch import NIPXIe2569RelayMatrix
from tests._fake_niswitch import FakeCard, FakeError, make_module
from utils.errors import RelayError, RelayStateVerificationError, ValidationError

CFG = {"name": "FAKE_2569", "resource": "PXI1Slot11", "channel_count": 100}


class _Base(unittest.TestCase):
    def setUp(self):
        self.card = FakeCard(100)
        patcher = mock.patch.dict(sys.modules, {"niswitch": make_module(self.card)})
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch.object(relay_module.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def connected(self, **overrides):
        driver = NIPXIe2569RelayMatrix({**CFG, **overrides})
        driver.connect()
        return driver


class ConstructionTests(unittest.TestCase):
    def test_constructor_does_no_io_and_does_not_import_niswitch(self):
        with mock.patch.dict(sys.modules, {"niswitch": None}):  # import would raise
            driver = NIPXIe2569RelayMatrix(CFG)
        self.assertFalse(driver.connected)
        self.assertEqual(driver.num_channels, 100)
        self.assertEqual(driver.resource, "PXI1Slot11")

    def test_missing_configuration_is_a_validation_error(self):
        for cfg in ({"name": "X", "resource": "PXI1Slot11"},
                    {"name": "X", "channel_count": 100},
                    {**CFG, "relay_names": ["ch0", "ch1"]}):
            with self.subTest(cfg=cfg):
                with self.assertRaises(ValidationError):
                    NIPXIe2569RelayMatrix(cfg)

    def test_factory_builds_it_and_lists_the_type(self):
        self.assertIn("niswitch", RelayFactory.supported_types())
        with mock.patch.dict(sys.modules, {"niswitch": None}):
            driver = RelayFactory.create({**CFG, "type": "niswitch"})
        self.assertIsInstance(driver, NIPXIe2569RelayMatrix)

    def test_connect_without_the_library_is_a_relay_error(self):
        driver = NIPXIe2569RelayMatrix(CFG)
        with mock.patch.dict(sys.modules, {"niswitch": None}):
            with self.assertRaises(RelayError) as ctx:
                driver.connect()
        self.assertIn("niswitch", str(ctx.exception))
        self.assertFalse(driver.connected)


class SessionTests(_Base):
    def test_session_is_opened_with_topology_and_without_resetting_the_card(self):
        self.connected()
        self.assertEqual(self.card.session_args, {
            "resource_name": "PXI1Slot11", "topology": "2569/100-SPST",
            "simulate": False, "reset_device": False})

    def test_topology_and_simulate_are_configurable(self):
        self.connected(topology="Configured Topology", simulate=True)
        self.assertEqual(self.card.session_args["topology"], "Configured Topology")
        self.assertTrue(self.card.session_args["simulate"])

    def test_connect_does_not_move_any_relay(self):
        self.card.closed.add(6)
        driver = self.connected()
        self.assertEqual(self.card.control_calls, [])
        self.assertEqual(driver.last_known_closed, [7])
        self.assertEqual(self.card.closed, {6})

    def test_relay_count_mismatch_refuses_to_connect_and_closes_the_session(self):
        self.card.count = 50
        driver = NIPXIe2569RelayMatrix(CFG)
        with self.assertRaises(RelayError):
            driver.connect()
        self.assertFalse(driver.connected)
        self.assertEqual(self.card.closed_sessions, 1)

    def test_session_failure_is_a_relay_error(self):
        self.card.fail_on["Session"] = FakeError("driver not installed")
        driver = NIPXIe2569RelayMatrix(CFG)
        with self.assertRaises(RelayError):
            driver.connect()
        self.assertFalse(driver.connected)

    def test_explicit_relay_names_override_discovery(self):
        names = [f"ch{i}" for i in reversed(range(100))]  # channel 1 -> ch99
        driver = self.connected(relay_names=names)
        driver.close(1)
        self.assertEqual(self.card.closed, {99})

    def test_disconnect_closes_the_session_and_is_idempotent(self):
        driver = self.connected()
        driver.disconnect()
        driver.disconnect()
        self.assertFalse(driver.connected)
        self.assertEqual(self.card.closed_sessions, 1)
        with self.assertRaises(RelayError):
            driver.query(1)

    def test_discovery_maps_channel_n_to_relay_index_n(self):
        driver = self.connected()
        driver.close(5)
        self.assertEqual(self.card.closed, {4})  # channel 5 -> relay "ch4"


class SafetySequenceTests(_Base):
    def test_close_first_drives_every_relay_open_then_closes_only_the_target(self):
        driver = self.connected()
        self.card.closed.update({1, 2})
        self.card.control_calls.clear()
        driver.close(5)
        calls = self.card.control_calls
        close_calls = [i for i, (_, a) in enumerate(calls) if a.name == "CLOSE"]
        self.assertEqual(len(close_calls), 1)
        self.assertEqual(calls[close_calls[0]][0], "ch4")
        # every relay was commanded OPEN before the single CLOSE
        opens_before = {name for name, a in calls[:close_calls[0]] if a.name == "OPEN"}
        self.assertEqual(len(opens_before), 100)
        self.assertEqual(self.card.closed, {4})

    def test_debounce_is_awaited_after_driving_relays(self):
        driver = self.connected()
        before = self.card.debounce_calls
        driver.close(3)
        self.assertGreaterEqual(self.card.debounce_calls - before, 2)  # open-all, then close

    def test_closed_channels_reads_the_card_once_per_relay(self):
        driver = self.connected()
        driver.close(7)
        self.card.position_reads = 0
        self.assertEqual(driver.closed_channels(), [7])
        self.assertEqual(self.card.position_reads, 100)

    def test_verification_failure_attempts_emergency_open_and_says_so(self):
        driver = self.connected()
        self.card.inject_stuck_open(2)
        with self.assertRaises(RelayStateVerificationError) as ctx:
            driver.close(2)
        self.assertIn("Emergency shutdown succeeded", str(ctx.exception))
        self.assertEqual(self.card.closed, set())

    def test_failure_to_confirm_the_emergency_open_is_reported(self):
        driver = self.connected()
        self.card.inject_stuck_closed(9)
        with self.assertRaises(RelayStateVerificationError) as ctx:
            driver.open_all()
        self.assertIn("FAILED", str(ctx.exception))
        self.assertIn("Physically disconnect power", str(ctx.exception))

    def test_niswitch_error_becomes_relay_error_after_an_emergency_open(self):
        driver = self.connected()
        self.card.fail_close_control = FakeError("relay driver fault")
        with self.assertRaises(RelayError) as ctx:
            driver.close(3)
        self.assertNotIsInstance(ctx.exception, RelayStateVerificationError)
        self.assertEqual(self.card.closed, set())

    def test_read_failure_is_a_relay_error(self):
        driver = self.connected()
        self.card.fail_on["get_relay_position"] = FakeError("read fault")
        with self.assertRaises(RelayError):
            driver.query(1)

    def test_a_failed_preaction_read_does_not_stop_the_forced_open(self):
        driver = self.connected()
        self.card.closed.add(5)
        real = driver._raw_read_closed
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise FakeError("transient")
            return real()

        driver._raw_read_closed = flaky
        driver.open_all()
        self.assertEqual(self.card.closed, set())

    def test_diagnostic_compatibility_with_numato(self):
        driver = self.connected()
        driver.close(3)
        self.assertEqual(driver.last_known_mask, 1 << 2)
        self.assertTrue(driver.read(3))
        self.assertFalse(driver.read(4))

    def test_open_close_settle_cannot_be_bypassed(self):
        for name in ("open", "close", "settle"):
            self.assertNotIn(name, NIPXIe2569RelayMatrix.__dict__)


class ConfigAndStartupIntegrationTests(_Base):
    def test_slot_11_is_an_enabled_niswitch_relay_matrix(self):
        cfg = dev_cfg.RELAY_MATRIX_CONFIGS["CHASSIS_RELAY_MATRIX"]
        self.assertEqual(cfg["type"], "niswitch")
        self.assertEqual(cfg["channel_count"], 100)
        self.assertEqual(cfg["topology"], "2569/100-SPST")
        self.assertEqual(cfg["resource"], "PXI1Slot11")
        self.assertNotIn("CHASSIS_RELAY_MATRIX", dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS)

    def test_startup_device_validation_accepts_the_card(self):
        from utils import device_validator as v
        cfg = dev_cfg.RELAY_MATRIX_CONFIGS["CHASSIS_RELAY_MATRIX"]
        errors = []
        v._check_required_fields("relay_niswitch", "CHASSIS_RELAY_MATRIX", cfg, errors)
        v._check_factory_type("relay_niswitch", "CHASSIS_RELAY_MATRIX", cfg, errors)
        with mock.patch.dict(sys.modules, {"niswitch": None}):  # no library needed to construct
            v._check_instantiable("relay_niswitch", "CHASSIS_RELAY_MATRIX", cfg, errors)
        self.assertEqual(errors, [])

    def test_whole_device_validation_still_passes(self):
        from utils import device_validator as v
        errors = v.validate_devices(dev_cfg)
        self.assertEqual([e for e in errors if "CHASSIS" in e], [])

    def test_run_summary_snapshot_records_the_niswitch_matrix(self):
        from test_control.storage_session import hardware_snapshot_fields
        cfg = dev_cfg.RELAY_MATRIX_CONFIGS["CHASSIS_RELAY_MATRIX"]
        fields = hardware_snapshot_fields(
            "S", {"resource": "r", "model": "m"}, None, None, "D", {"resource": "r", "model": "m"}, cfg)
        self.assertEqual(fields["relay_matrix_name"], "CHASSIS_RELAY_MATRIX")
        self.assertEqual(fields["relay_matrix_model"], "PXIe-2569")
        self.assertEqual(fields["relay_matrix_resource"], "PXI1Slot11")

    def test_startup_sweep_opens_the_card_and_releases_the_session(self):
        from test_control.safety_sweep import run_startup_safety_sweep
        self.card.closed.add(3)
        cfg = {**CFG, "type": "niswitch"}
        run_startup_safety_sweep(smu_assignments={}, relay_matrix_configs={"CHASSIS_RELAY_MATRIX": cfg})
        self.assertEqual(self.card.closed, set())
        self.assertEqual(self.card.closed_sessions, 1)

    def test_startup_sweep_tolerates_a_machine_without_the_library(self):
        from test_control.safety_sweep import run_startup_safety_sweep
        cfg = {**CFG, "type": "niswitch"}
        with mock.patch.dict(sys.modules, {"niswitch": None}):
            run_startup_safety_sweep(smu_assignments={}, relay_matrix_configs={"CHASSIS_RELAY_MATRIX": cfg})


class RelayRangeValidationOn2569Tests(_Base):
    """The same Relay Range Validation the Numato has, run on the 2569."""

    def run_range(self, start, end):
        import io
        from contextlib import redirect_stdout
        from config.settings import Settings
        cfg = dev_cfg.RELAY_MATRIX_CONFIGS["CHASSIS_RELAY_MATRIX"]
        answers = iter([str(start), str(end)])
        with mock.patch.object(Settings, "RELAY_RANGE_VALIDATION_DWELL_S", 0.01),                 mock.patch.object(test_module, "_instrumented_relay", RelayFactory.create),                 mock.patch("builtins.input", lambda *_: next(answers)), redirect_stdout(io.StringIO()):
            return test_module._functional_switch("CHASSIS_RELAY_MATRIX", cfg)

    def test_range_75_to_77_passes_and_ends_all_open(self):
        results = self.run_range(75, 77)
        by = {r.device: r for r in results}
        for n in (75, 76, 77):
            self.assertEqual(by[f"Relay {n}"].status, test_module.Status.PASS)
        self.assertEqual(by["Final open_all"].status, test_module.Status.PASS)
        self.assertEqual(self.card.closed, set())
        self.assertEqual(by["Relay 76"].raw["closed_verify_only_closed"], [76])

    def test_range_beyond_100_is_rejected_without_operating_a_relay(self):
        results = self.run_range(99, 101)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, test_module.Status.WARNING)
        self.assertEqual(self.card.control_calls, [])

    def test_a_relay_that_does_not_close_fails_the_validation(self):
        self.card.stuck_open.add(75)  # channel 76
        results = self.run_range(75, 78)
        by = {r.device: r for r in results}
        self.assertEqual(by["Relay 75"].status, test_module.Status.PASS)
        self.assertEqual(by["Relay 76"].status, test_module.Status.FAIL)
        self.assertNotIn("Relay 77", by)
        self.assertEqual(by["Final open_all"].status, test_module.Status.PASS)


if __name__ == "__main__":
    unittest.main()
