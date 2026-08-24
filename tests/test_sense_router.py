"""
Tests for hardware/sense_router.py -- the future battery-sense-routing
abstraction (see docs/architecture.md "Future Architecture: Battery
Sense Routing"). Pure, hardware-free: uses fake relay drivers, never a
real NumatoRelayMatrix/TCP connection.

Also covers config/devices.py::hardware_for_group()'s new "sense_channel"
resolution and SENSE_ROUTING's default-empty state.

The single most important test in this file is
`test_no_sense_channel_is_a_pure_passthrough` -- it is the entire
backward-compatibility guarantee this future architecture rests on: with
no sense_channel configured (every real group today), reading battery
voltage must be byte-for-byte identical to calling
`dmm.measure_dc_voltage()` directly.
"""

import unittest

import config.devices as dev_cfg
from hardware.sense_router import (
    ConfigDrivenSenseRouter,
    NumatoSenseRouter,
    read_battery_voltage_via_sense,
)
from utils.errors import ConfigurationError


class _FakeRelay:
    """Minimal RelayBase-shaped fake -- records open()/close()/connect()/
    disconnect() calls, no real hardware."""

    def __init__(self):
        self.calls = []
        self.connected = False

    def connect(self):
        self.connected = True
        self.calls.append("connect")

    def disconnect(self):
        self.connected = False
        self.calls.append("disconnect")

    def close(self, channel):
        self.calls.append(("close", channel))

    def open(self, channel):
        self.calls.append(("open", channel))


class _FakeDmm:
    def __init__(self, voltage_v=3.65):
        self.voltage_v = voltage_v
        self.measure_calls = 0

    def measure_dc_voltage(self):
        self.measure_calls += 1
        return self.voltage_v


class HardwareForGroupSenseChannelTests(unittest.TestCase):
    def test_b1_real_config_has_no_sense_channel(self):
        hw = dev_cfg.hardware_for_group("B1")
        self.assertIsNone(hw["sense_channel"])

    def test_sense_routing_is_empty_by_default(self):
        self.assertEqual(dev_cfg.SENSE_ROUTING, {})


class NumatoSenseRouterTests(unittest.TestCase):
    def test_connect_closes_the_mapped_relay(self):
        relay = _FakeRelay()
        router = NumatoSenseRouter(relay, {1: 5, 2: 6})
        router.connect(1)
        self.assertEqual(relay.calls, [("close", 5)])

    def test_disconnect_opens_the_mapped_relay(self):
        relay = _FakeRelay()
        router = NumatoSenseRouter(relay, {1: 5, 2: 6})
        router.disconnect(2)
        self.assertEqual(relay.calls, [("open", 6)])

    def test_unknown_channel_raises_configuration_error(self):
        relay = _FakeRelay()
        router = NumatoSenseRouter(relay, {1: 5})
        with self.assertRaises(ConfigurationError):
            router.connect(99)


class ConfigDrivenSenseRouterTests(unittest.TestCase):
    def _synthetic(self):
        """Two logical channels on one matrix, one channel on a second --
        proves per-matrix connection sharing/caching and multi-matrix
        dispatch without touching real config or real hardware."""
        sense_routing = {
            1: {"relay_matrix": "MATRIX_A", "relay": 10},
            2: {"relay_matrix": "MATRIX_A", "relay": 11},
            3: {"relay_matrix": "MATRIX_B", "relay": 1},
        }
        relay_matrix_configs = {
            "MATRIX_A": {"name": "MATRIX_A"},
            "MATRIX_B": {"name": "MATRIX_B"},
        }
        relays_by_name = {}

        def fake_create(cfg):
            relay = _FakeRelay()
            relays_by_name[cfg["name"]] = relay
            return relay

        router = ConfigDrivenSenseRouter(
            sense_routing=sense_routing,
            relay_matrix_configs=relay_matrix_configs,
            relay_factory_create=fake_create,
        )
        return router, relays_by_name

    def test_connect_dispatches_to_the_correct_matrix_and_relay(self):
        router, relays = self._synthetic()
        router.connect(1)
        self.assertEqual(relays["MATRIX_A"].calls, ["connect", ("close", 10)])
        self.assertNotIn("MATRIX_B", relays)  # never constructed -- not used yet

    def test_two_channels_on_the_same_matrix_share_one_connection(self):
        router, relays = self._synthetic()
        router.connect(1)
        router.connect(2)
        self.assertEqual(relays["MATRIX_A"].calls.count("connect"), 1)
        self.assertEqual(relays["MATRIX_A"].calls[1:], [("close", 10), ("close", 11)])

    def test_channels_on_different_matrices_use_different_connections(self):
        router, relays = self._synthetic()
        router.connect(1)
        router.connect(3)
        self.assertIn("MATRIX_A", relays)
        self.assertIn("MATRIX_B", relays)
        self.assertEqual(relays["MATRIX_B"].calls, ["connect", ("close", 1)])

    def test_disconnect_opens_the_correct_relay(self):
        router, relays = self._synthetic()
        router.disconnect(3)
        self.assertEqual(relays["MATRIX_B"].calls, ["connect", ("open", 1)])

    def test_unconfigured_channel_raises_configuration_error(self):
        router, _ = self._synthetic()
        with self.assertRaises(ConfigurationError):
            router.connect(999)

    def test_matrix_with_no_config_entry_raises_configuration_error(self):
        sense_routing = {1: {"relay_matrix": "MISSING_MATRIX", "relay": 1}}
        router = ConfigDrivenSenseRouter(
            sense_routing=sense_routing, relay_matrix_configs={},
            relay_factory_create=lambda cfg: _FakeRelay(),
        )
        with self.assertRaises(ConfigurationError):
            router.connect(1)

    def test_shutdown_disconnects_every_opened_matrix_and_never_raises(self):
        router, relays = self._synthetic()
        router.connect(1)
        router.connect(3)
        router.shutdown()  # must not raise
        self.assertIn("disconnect", relays["MATRIX_A"].calls)
        self.assertIn("disconnect", relays["MATRIX_B"].calls)

    def test_shutdown_is_safe_to_call_with_nothing_connected(self):
        router, _ = self._synthetic()
        router.shutdown()  # must not raise


class ReadBatteryVoltageViaSenseTests(unittest.TestCase):
    def test_no_sense_channel_is_a_pure_passthrough(self):
        """The core backward-compatibility guarantee: with sense_channel
        omitted (every real group today), this must be indistinguishable
        from calling dmm.measure_dc_voltage() directly -- no sense_router
        interaction at all, not even a None-check side effect visible to
        the router."""
        dmm = _FakeDmm(voltage_v=3.71)
        result = read_battery_voltage_via_sense(dmm, sense_router=None, sense_channel=None)
        self.assertEqual(result, 3.71)
        self.assertEqual(dmm.measure_calls, 1)

    def test_configured_channel_connects_reads_then_disconnects_in_order(self):
        calls = []

        class _RecordingRouter:
            def connect(self, channel):
                calls.append(("connect", channel))

            def disconnect(self, channel):
                calls.append(("disconnect", channel))

        dmm = _FakeDmm(voltage_v=3.5)
        result = read_battery_voltage_via_sense(dmm, sense_router=_RecordingRouter(), sense_channel=1)
        self.assertEqual(result, 3.5)
        self.assertEqual(calls, [("connect", 1), ("disconnect", 1)])

    def test_disconnect_still_happens_if_the_dmm_read_raises(self):
        calls = []

        class _RecordingRouter:
            def connect(self, channel):
                calls.append(("connect", channel))

            def disconnect(self, channel):
                calls.append(("disconnect", channel))

        class _FailingDmm:
            def measure_dc_voltage(self):
                raise RuntimeError("simulated DMM read failure")

        with self.assertRaises(RuntimeError):
            read_battery_voltage_via_sense(_FailingDmm(), sense_router=_RecordingRouter(), sense_channel=1)
        self.assertEqual(calls, [("connect", 1), ("disconnect", 1)])


if __name__ == "__main__":
    unittest.main()
