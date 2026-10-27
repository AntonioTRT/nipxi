"""
Runs the relay-driver conformance suite (tests/_relay_conformance.py) against
every relay driver, plus tests of RelayBase's own fail-closed behavior and
negative controls proving the suite actually rejects unsafe drivers.

Drivers covered:
  - SimulatedRelay                      (in-memory)
  - NumatoRelayMatrix                   (the REAL driver class, with only its
                                         transport -- connect/disconnect/
                                         _send_and_capture -- replaced by an
                                         in-memory model of the Numato
                                         firmware, so the production safety
                                         sequence and response parsing run
                                         unmodified; includes fault injection)
  - NIPXIe2569RelayMatrix               (the real driver over an in-memory fake
                                         of the `niswitch` module and card;
                                         includes fault injection)
SerialRelay is not covered: it is diagnostic-only with no interlock or
verification, so it intentionally does not satisfy this contract.
"""

import io
import sys
import unittest
from unittest import mock

from hardware import relay_serial
from hardware.relay import RelayBase
from hardware.relay_eth import NumatoRelayMatrix
from hardware.relay_niswitch import NIPXIe2569RelayMatrix
from hardware.simulated import SimulatedRelay
from tests._fake_niswitch import FakeCard, make_module
from tests._relay_conformance import RelayDriverContract
from utils.errors import RelayError, RelayStateVerificationError, ValidationError


# ----------------------------------------------------------------------------
# SimulatedRelay
# ----------------------------------------------------------------------------

class SimulatedRelayConformance(RelayDriverContract, unittest.TestCase):
    def make_driver(self):
        driver = SimulatedRelay({"name": "SIM", "channel_count": 8})
        driver.connect()
        return driver

    def physical_closed(self):
        active = self.driver._active_channel
        return set() if active is None else {active}


# ----------------------------------------------------------------------------
# NumatoRelayMatrix over an in-memory model of the Numato firmware
# ----------------------------------------------------------------------------

class _FakeNumatoFirmware:
    """32-relay bank. Speaks the exact command/response shapes the real
    firmware was confirmed to use (see hardware/relay_eth.py's parsing
    notes): echoed command, then the value, before the prompt."""

    def __init__(self, count=32):
        self.count = count
        self.mask = 0
        self.stuck_closed = 0   # bits that stay 1 regardless of commands
        self.stuck_open = 0     # bits that stay 0 regardless of commands

    def _apply(self, mask):
        self.mask = (mask & ~self.stuck_open) | self.stuck_closed

    def handle(self, cmd: str) -> bytes:
        parts = cmd.split()
        if parts[:2] == ["relay", "readall"]:
            return b"relay readall\n\r" + format(self.mask, "08X").encode() + b"\n\r"
        if parts[:2] == ["relay", "writeall"]:
            self._apply(int(parts[2], 16))
            return b"relay writeall " + parts[2].encode() + b"\n\r"
        if parts[:2] in (["relay", "on"], ["relay", "off"], ["relay", "read"]):
            bit = 1 << int(parts[2], 32)  # Numato addresses: 0-9 then A-V
            if parts[1] == "on":
                self._apply(self.mask | bit)
                return b"relay on " + parts[2].encode() + b"\n\r"
            if parts[1] == "off":
                # a welded contact survives an 'off'
                self._apply(self.mask & ~bit)
                return b"relay off " + parts[2].encode() + b"\n\r"
            state = b"on" if self.mask & bit else b"off"
            return b"relay read " + parts[2].encode() + b"\n\r" + state + b"\n\r"
        return b"invalid command\n\r"


class _FakeTransportNumato(NumatoRelayMatrix):
    """The real NumatoRelayMatrix; only the wire is replaced."""

    def __init__(self, cfg, firmware):
        super().__init__(cfg)
        self.firmware = firmware

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _send_and_capture(self, cmd: str) -> bytes:
        return self.firmware.handle(cmd)


class NumatoRelayMatrixConformance(RelayDriverContract, unittest.TestCase):
    supports_fault_injection = True

    def make_driver(self):
        self.firmware = _FakeNumatoFirmware()
        driver = _FakeTransportNumato(
            {"name": "FAKE_NUMATO", "ip": "127.0.0.1", "channel_count": 32}, self.firmware)
        driver.connect()
        return driver

    def physical_closed(self):
        return {ch for ch in range(1, self.firmware.count + 1) if self.firmware.mask & (1 << (ch - 1))}

    def inject_stuck_closed(self, channel):
        bit = 1 << (channel - 1)
        self.firmware.stuck_closed |= bit
        self.firmware.mask |= bit

    def inject_stuck_open(self, channel):
        bit = 1 << (channel - 1)
        self.firmware.stuck_open |= bit
        self.firmware.mask &= ~bit


# ----------------------------------------------------------------------------
# NIPXIe2569RelayMatrix over a fake NI-SWITCH card
# ----------------------------------------------------------------------------

class NIPXIe2569RelayMatrixConformance(RelayDriverContract, unittest.TestCase):
    supports_fault_injection = True

    def make_driver(self):
        self.card = FakeCard(100)
        patcher = mock.patch.dict(sys.modules, {"niswitch": make_module(self.card)})
        patcher.start()
        self.addCleanup(patcher.stop)
        driver = NIPXIe2569RelayMatrix(
            {"name": "FAKE_2569", "resource": "PXI1Slot11", "channel_count": 100})
        driver.connect()
        return driver

    def physical_closed(self):
        return self.card.physical_closed_channels()

    def inject_stuck_closed(self, channel):
        self.card.inject_stuck_closed(channel)

    def inject_stuck_open(self, channel):
        self.card.inject_stuck_open(channel)


# ----------------------------------------------------------------------------
# RelayBase fail-closed behavior
# ----------------------------------------------------------------------------

class _MinimalDriver(RelayBase):
    """Implements only what the base class forces -- nothing else."""

    def __init__(self):
        super().__init__("MINIMAL", 4)
        self.closed = set()

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _open_impl(self, channel):
        self._validate_channel(channel)
        self.closed.discard(channel)

    def _close_impl(self, channel):
        self._validate_channel(channel)
        self.closed.add(channel)

    def query(self, channel):
        self._validate_channel(channel)
        return channel in self.closed

    def open_all(self):
        self.closed.clear()


class RelayBaseFailClosedTests(unittest.TestCase):
    def test_driver_without_open_all_cannot_be_instantiated(self):
        class NoOpenAll(RelayBase):
            def connect(self): ...
            def disconnect(self): ...
            def _open_impl(self, channel): ...
            def _close_impl(self, channel): ...
            def query(self, channel): return False

        with self.assertRaises(TypeError):
            NoOpenAll("X", 4)

    def test_close_all_raises_unless_driver_overrides_it(self):
        driver = _MinimalDriver()
        driver.connect()
        with self.assertRaises(RelayError):
            driver.close_all()
        self.assertEqual(driver.closed, set())

    def test_overriding_open_close_or_settle_is_rejected_at_class_definition(self):
        for name in ("open", "close", "settle"):
            with self.subTest(method=name):
                with self.assertRaises(TypeError):
                    type("Bad", (_MinimalDriver,), {name: lambda self, *a: None})

    def test_serial_relay_still_defines_open_all_explicitly(self):
        self.assertIn("open_all", relay_serial.SerialRelay.__dict__)
        self.assertNotIn("close_all", relay_serial.SerialRelay.__dict__)

    def test_closed_channels_and_verifiers_are_read_only(self):
        driver = _MinimalDriver()
        driver.connect()
        with mock.patch.object(driver, "_close_impl") as close_impl, \
                mock.patch.object(driver, "_open_impl") as open_impl, \
                mock.patch.object(driver, "open_all") as open_all:
            driver.closed_channels()
            driver.verify_all_open()
            with self.assertRaises(ValidationError):
                driver.verify_only_closed(99)
        close_impl.assert_not_called()
        open_impl.assert_not_called()
        open_all.assert_not_called()


# ----------------------------------------------------------------------------
# Negative controls -- the conformance suite must REJECT unsafe drivers
# ----------------------------------------------------------------------------

def _run_contract_test(harness_cls, test_name) -> unittest.TestResult:
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(unittest.TestSuite([harness_cls(test_name)]))


class _NoInterlockDriver(_MinimalDriver):
    """close() never opens the others -> multiple relays can be closed."""


class _AllowsCloseAllDriver(_MinimalDriver):
    def close_all(self):
        for ch in range(1, self.num_channels + 1):
            self.close(ch)


def _harness_for(driver_cls):
    """Build (not define at module level, so test discovery never collects
    them) a conformance harness around a deliberately unsafe driver."""
    class Harness(RelayDriverContract, unittest.TestCase):
        def make_driver(self):
            driver = driver_cls()
            driver.connect()
            return driver

        def physical_closed(self):
            return set(self.driver.closed)

    return Harness


class ConformanceSuiteRejectsUnsafeDriversTests(unittest.TestCase):
    def test_missing_interlock_fails_the_interlock_tests(self):
        for name in ("test_close_second_channel_enforces_interlock",
                     "test_close_closes_exactly_that_channel",
                     "test_never_more_than_one_relay_closed_over_any_sequence"):
            with self.subTest(test=name):
                result = _run_contract_test(_harness_for(_NoInterlockDriver), name)
                self.assertFalse(result.wasSuccessful(), f"{name} should reject a no-interlock driver")

    def test_driver_that_allows_close_all_fails_the_close_all_test(self):
        result = _run_contract_test(_harness_for(_AllowsCloseAllDriver), "test_close_all_fails_closed")
        self.assertFalse(result.wasSuccessful())

    def test_real_drivers_pass_the_same_tests(self):
        for harness in (SimulatedRelayConformance, NumatoRelayMatrixConformance,
                        NIPXIe2569RelayMatrixConformance):
            for name in ("test_close_second_channel_enforces_interlock",
                         "test_close_all_fails_closed"):
                with self.subTest(harness=harness.__name__, test=name):
                    self.assertTrue(_run_contract_test(harness, name).wasSuccessful())


if __name__ == "__main__":
    unittest.main()
