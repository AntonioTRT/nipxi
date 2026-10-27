"""
Relay-driver conformance suite -- the behavioral contract every RelayBase
driver must satisfy (see hardware/relay.py's module docstring), written once
and run against each driver by a thin harness class.

HOW TO RUN IT AGAINST A DRIVER
==============================
Subclass RelayDriverContract together with unittest.TestCase (contract
first) and implement the hooks:

    class MyDriverConformance(RelayDriverContract, unittest.TestCase):
        def make_driver(self):          # a CONNECTED driver, no real hardware
        def physical_closed(self):      # ground truth: set of 1-based channels
                                        # that are physically closed, read from
                                        # the fake hardware -- NEVER from
                                        # driver.query(), or the driver would be
                                        # grading itself
        # optional, enables the fault-injection tests (otherwise skipped):
        supports_fault_injection = True
        def inject_stuck_closed(self, channel):  # contact welded: ignores open
        def inject_stuck_open(self, channel):    # never closes: ignores close

Harnesses live in tests/test_relay_conformance.py. A future
NIPXIe2569RelayMatrix harness fakes the niswitch session behind the driver
(no NI hardware or package needed) and plugs into these hooks unchanged.

Every test method below states the contract clause it enforces.
"""

from unittest import mock

from config.settings import Settings
from hardware import relay as relay_module
from utils.errors import RelayError, RelayStateVerificationError, ValidationError


class RelayDriverContract:
    """Mixin. Place BEFORE unittest.TestCase in the harness's bases."""

    supports_fault_injection = False

    # ---- hooks a harness must provide ----------------------------------

    def make_driver(self):
        raise NotImplementedError

    def physical_closed(self) -> set:
        raise NotImplementedError

    def inject_stuck_closed(self, channel: int):
        raise NotImplementedError

    def inject_stuck_open(self, channel: int):
        raise NotImplementedError

    # ---- fixture ---------------------------------------------------------

    def setUp(self):
        super().setUp()
        # open()/close() block for RELAY_SETTLE_TIME_S via time.sleep;
        # record it instead of actually waiting.
        patcher = mock.patch.object(relay_module.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        self.driver = self.make_driver()
        self.n = self.driver.num_channels

    def _require_fault_injection(self):
        if not self.supports_fault_injection:
            self.skipTest("harness has no fault injection for this driver")

    # ---- lifecycle -------------------------------------------------------

    def test_connect_disconnect_lifecycle(self):
        """connect() sets connected; disconnect() clears it; reconnect works."""
        self.assertTrue(self.driver.connected)
        self.driver.disconnect()
        self.assertFalse(self.driver.connected)
        self.driver.connect()
        self.assertTrue(self.driver.connected)

    def test_shutdown_sequence_is_safe(self):
        """The HardwareManager shutdown order -- open_all() then disconnect()
        -- leaves every relay physically open; disconnect() is idempotent."""
        self.driver.close(2)
        self.driver.open_all()
        self.driver.disconnect()
        self.assertEqual(self.physical_closed(), set())
        self.driver.disconnect()  # second call must not raise
        self.assertFalse(self.driver.connected)

    # ---- open_all ----------------------------------------------------------

    def test_open_all_leaves_all_relays_open(self):
        """open_all() leaves EVERY relay physically open."""
        self.driver.close(3)
        self.driver.open_all()
        self.assertEqual(self.physical_closed(), set())

    def test_open_all_is_idempotent(self):
        """open_all() on an already-open matrix succeeds and changes nothing."""
        self.driver.open_all()
        self.driver.open_all()
        self.assertEqual(self.physical_closed(), set())

    # ---- close / open / interlock -------------------------------------------

    def test_close_closes_exactly_that_channel(self):
        """After close(ch), ONLY ch is physically closed -- for every channel."""
        for ch in range(1, self.n + 1):
            self.driver.close(ch)
            self.assertEqual(self.physical_closed(), {ch}, f"after close({ch})")
        self.driver.open_all()

    def test_close_second_channel_enforces_interlock(self):
        """close(b) while a is closed never leaves both closed."""
        self.driver.close(1)
        self.driver.close(2)
        self.assertEqual(self.physical_closed(), {2})
        self.driver.open_all()

    def test_open_after_close_leaves_all_open(self):
        """open(ch) on the closed channel leaves the matrix all-open."""
        self.driver.close(3)
        self.driver.open(3)
        self.assertEqual(self.physical_closed(), set())

    def test_never_more_than_one_relay_closed_over_any_sequence(self):
        """Invariant: len(closed) <= 1 after every operation of a mixed sequence."""
        ops = [
            ("close", 1), ("close", 2), ("open", 2), ("close", 3), ("open", 1),
            ("open_all", None), ("close", self.n), ("close", 1), ("open_all", None),
            ("close", 2), ("open", 2), ("open", 2),
        ]
        for name, arg in ops:
            getattr(self.driver, name)(*(() if arg is None else (arg,)))
            self.assertLessEqual(len(self.physical_closed()), 1, f"after {name}({arg})")
        self.driver.open_all()

    def test_close_all_fails_closed(self):
        """close_all() raises RelayError and closes nothing."""
        self.driver.open_all()
        with self.assertRaises(RelayError):
            self.driver.close_all()
        self.assertEqual(self.physical_closed(), set())

    # ---- invalid channels ----------------------------------------------------

    def test_invalid_channels_are_rejected_without_side_effects(self):
        """Out-of-range channels raise ValidationError from close/open/query
        and do not change physical state."""
        self.driver.close(1)
        for bad in (0, -1, self.n + 1, 10 ** 6):
            for op in (self.driver.close, self.driver.open, self.driver.query):
                with self.assertRaises(ValidationError, msg=f"{op.__name__}({bad})"):
                    op(bad)
            self.assertEqual(self.physical_closed(), {1}, f"state changed by bad channel {bad}")
        self.driver.open_all()

    # ---- query / verification --------------------------------------------------

    def test_query_matches_physical_state(self):
        """query(ch) is True exactly for the physically closed channel."""
        self.driver.close(2)
        for ch in range(1, self.n + 1):
            self.assertEqual(self.driver.query(ch), ch == 2, f"query({ch})")
        self.driver.open_all()

    def test_verification_helpers_report_state_correctly(self):
        """closed_channels()/verify_only_closed()/verify_all_open() pass on the
        true state and raise RelayStateVerificationError on any other."""
        self.driver.close(2)
        self.assertEqual(self.driver.closed_channels(), [2])
        self.driver.verify_only_closed(2)
        with self.assertRaises(RelayStateVerificationError):
            self.driver.verify_only_closed(3)
        with self.assertRaises(RelayStateVerificationError):
            self.driver.verify_all_open()

        self.driver.open_all()
        self.assertEqual(self.driver.closed_channels(), [])
        self.driver.verify_all_open()
        with self.assertRaises(RelayStateVerificationError):
            self.driver.verify_only_closed(1)

    # ---- settle ------------------------------------------------------------------

    def test_settle_delay_is_applied_after_every_open_and_close(self):
        """open() and close() each block for Settings.RELAY_SETTLE_TIME_S."""
        self.sleep.reset_mock()
        self.driver.close(1)
        self.sleep.assert_any_call(Settings.RELAY_SETTLE_TIME_S)
        self.sleep.reset_mock()
        self.driver.open(1)
        self.sleep.assert_any_call(Settings.RELAY_SETTLE_TIME_S)

    # ---- fault injection (harness must opt in) --------------------------------

    def test_stuck_closed_relay_is_detected_by_open_all(self):
        """A relay that stays closed after open_all() -> RelayStateVerificationError."""
        self._require_fault_injection()
        self.inject_stuck_closed(3)
        with self.assertRaises(RelayStateVerificationError):
            self.driver.open_all()

    def test_stuck_closed_relay_blocks_close_and_requested_relay_stays_open(self):
        """With another relay stuck closed, close(ch) raises
        RelayStateVerificationError and never leaves ch energized."""
        self._require_fault_injection()
        self.inject_stuck_closed(3)
        with self.assertRaises(RelayStateVerificationError):
            self.driver.close(1)
        self.assertNotIn(1, self.physical_closed())

    def test_relay_that_does_not_close_is_detected(self):
        """A relay that ignores the close command -> RelayStateVerificationError."""
        self._require_fault_injection()
        self.inject_stuck_open(2)
        with self.assertRaises(RelayStateVerificationError):
            self.driver.close(2)
