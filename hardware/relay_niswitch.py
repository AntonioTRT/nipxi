"""
NI PXIe-2569 relay matrix driver (NI-SWITCH, 100 independent SPST relays).

This driver exists to behave EXACTLY like hardware/relay_eth.py::
NumatoRelayMatrix from the application's point of view -- nothing above
RelayBase (HardwareManager, the battery sequences, the safety sweep) knows
which relay technology is underneath. Same safety model:

  close(ch)  = verified all-open  ->  close ch  ->  verify ch closed
               ->  verify ch is the ONLY closed relay  ->  settle
  open(ch)   = verified all-open  ->  settle
  open_all() = verified all-open
  close_all() = not supported (RelayBase fails closed)

Same exceptions: RelayStateVerificationError when a readback disagrees
(after a best-effort emergency all-open, whose outcome is appended to the
message), RelayError for session/communication failures, ValidationError
for a bad channel or bad configuration. The settle delay is applied by
RelayBase.open()/close() (Settings.RELAY_SETTLE_TIME_S), never here.

NI-SWITCH calls used (python `niswitch` 1.5.0 / NI-SWITCH driver):
    niswitch.Session(resource_name, topology, simulate, reset_device)
    Session.number_of_relays, Session.get_relay_name(index)   (1-based index)
    Session.relay_control(relay_name, niswitch.RelayAction.OPEN | CLOSE)
    Session.get_relay_position(relay_name) -> niswitch.RelayPosition.OPEN | CLOSED
    Session.wait_for_debounce(maximum_time_ms)
    Session.close()
Individual relay control (relay_control) is used rather than paths
(connect/disconnect) because the matrix is a bank of independent relays and
only relay names are needed -- no channel/COM pairing to guess.

Constructing the driver does no I/O and does not import `niswitch`, so
startup validation and the startup safety sweep work on a machine without
the NI-SWITCH package or the card; `niswitch` is imported in connect().

Configuration (config/devices.py PXI_SLOTS[11] -> RELAY_MATRIX_CONFIGS):
    resource            "PXI1Slot11"                       (required)
    channel_count       100                                 (required; must equal
                                                             the card's relay count)
    topology            "2569/100-SPST"                    (default)
    debounce_timeout_ms 5000                               (default)
    relay_names         optional explicit list, logical channel 1..N order
    simulate            False -- True uses NI-SWITCH's simulated device

BENCH-CONFIRM (not verifiable without the card): the logical channel N maps
to the relay at index N in the device's own relay list (get_relay_name);
the connect-time log prints the first/last mapped names, and Relay Range
Validation with the dwell (operator hears/sees relay N click) is the check.
Whether get_relay_position reports the physical or only the commanded
position is also a bench question; verification here is only as strong as
that readback.
"""

import contextlib

from hardware.relay import RelayBase
from utils.errors import RelayError, RelayStateVerificationError, ValidationError


class NIPXIe2569RelayMatrix(RelayBase):
    DEFAULT_TOPOLOGY = "2569/100-SPST"
    DEFAULT_DEBOUNCE_TIMEOUT_MS = 5000

    def __init__(self, cfg: dict):
        name = cfg.get("name", "NI_PXIE_2569")
        channel_count = cfg.get("channel_count", cfg.get("num_channels"))
        if not channel_count:
            raise ValidationError(f"NI PXIe-2569 relay matrix '{name}': missing 'channel_count'")
        super().__init__(name, int(channel_count))

        self.resource = cfg.get("resource")
        if not self.resource:
            raise ValidationError(f"NI PXIe-2569 relay matrix '{name}': missing 'resource'")

        self._topology = cfg.get("topology", self.DEFAULT_TOPOLOGY)
        self._debounce_timeout_ms = int(cfg.get("debounce_timeout_ms", self.DEFAULT_DEBOUNCE_TIMEOUT_MS))
        self._simulate = bool(cfg.get("simulate", False))
        self._configured_relay_names = cfg.get("relay_names")
        if self._configured_relay_names is not None and len(self._configured_relay_names) != self.num_channels:
            raise ValidationError(
                f"NI PXIe-2569 relay matrix '{name}': relay_names has "
                f"{len(self._configured_relay_names)} entries but channel_count is {self.num_channels}"
            )

        self._ni = None                 # the `niswitch` module, set by connect()
        self._session = None
        self._relay_names: list = []    # logical channel N -> self._relay_names[N - 1]
        self._in_emergency = False
        # Last closed-channel list actually read back from the card, or None
        # if never read -- diagnostic record only, same role as
        # NumatoRelayMatrix.last_known_mask.
        self.last_known_closed: list | None = None

    # ------------------------------------------------------------------
    # Diagnostic compatibility with NumatoRelayMatrix (read-only)
    # ------------------------------------------------------------------

    @property
    def last_known_mask(self) -> int | None:
        """Bitmask (bit N-1 set = channel N closed) of last_known_closed, the
        same shape NumatoRelayMatrix.last_known_mask has, so diagnostics that
        record it work unchanged on either driver."""
        if self.last_known_closed is None:
            return None
        mask = 0
        for ch in self.last_known_closed:
            mask |= 1 << (ch - 1)
        return mask

    def read(self, channel: int) -> bool:
        """Alias for query(), matching NumatoRelayMatrix.read()."""
        return self.query(channel)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        try:
            import niswitch
        except ImportError as e:
            raise RelayError(
                "Library 'niswitch' is not installed. Run: pip install niswitch "
                "(and install the NI-SWITCH driver from ni.com)"
            ) from e
        self._ni = niswitch

        self._close_session()
        self.connected = False
        self.log.info("Opening NI-SWITCH session: %s (topology %s, simulate=%s)",
                      self.resource, self._topology, self._simulate)
        try:
            # reset_device=False: opening the session must not itself move any
            # relay; the safe state is established explicitly (and verified)
            # by open_all(), exactly as for the Numato.
            session = niswitch.Session(self.resource, topology=self._topology,
                                       simulate=self._simulate, reset_device=False)
        except niswitch.Error as e:
            raise RelayError(self._conn_error(str(e))) from e
        self._session = session

        # Connection verification: discover the relay list and read every
        # position once, so a bad session/mapping is caught at connect time
        # (the counterpart of the Numato's connect-time "relay readall").
        try:
            self._discover_relays()
            initial_closed = self._raw_read_closed()
        except Exception as e:
            self._close_session()
            raise RelayError(self._conn_error(
                f"Session opened but relay discovery/readback failed: {e}")) from e

        self.last_known_closed = initial_closed
        self.connected = True
        self.log.info("Connected: %s (%s). Relays mapped: channel 1 -> %s ... channel %d -> %s. "
                      "Initial closed channels: %s",
                      self.name, self.resource, self._relay_names[0], self.num_channels,
                      self._relay_names[-1], initial_closed)

    def disconnect(self):
        self._close_session()
        self.connected = False
        self.log.info("Disconnected: %s (%s)", self.name, self.resource)

    def _close_session(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def _conn_error(self, reason: str) -> str:
        return (f"[ERROR]\nRelay controller not reachable\n\n"
                f"Driver:\nNI-SWITCH ({self._topology})\n\n"
                f"Resource:\n{self.resource}\n\nReason:\n{reason}")

    def _discover_relays(self):
        session = self._session
        count = session.number_of_relays
        if count != self.num_channels:
            raise RelayError(
                f"card '{self.resource}' reports {count} relays (topology {self._topology!r}) "
                f"but channel_count is configured as {self.num_channels}"
            )
        if self._configured_relay_names is not None:
            self._relay_names = list(self._configured_relay_names)
        else:
            self._relay_names = [session.get_relay_name(i) for i in range(1, count + 1)]

    # ------------------------------------------------------------------
    # Public RelayBase API (1-based) -- the ONLY methods that change state.
    # Same sequences as NumatoRelayMatrix._open_impl/_close_impl/open_all.
    # ------------------------------------------------------------------

    def _open_impl(self, channel: int):
        self._validate_channel(channel)
        self.log.info("Requested relay: %d  Action: OPEN", channel)
        self._force_all_open_and_verify()
        self.log.info("Verification: PASS (channel %d confirmed OPEN, all others OPEN)", channel)

    def _close_impl(self, channel: int):
        self._validate_channel(channel)
        self.log.info("Requested relay: %d  Action: CLOSE", channel)

        # 1-2: known-safe baseline before touching anything.
        self._force_all_open_and_verify()

        # 3: close only the requested relay.
        with self._ni_errors(f"closing relay {channel}"):
            self._raw_drive(channel, closed=True)
            self._raw_debounce()

        # 4: individual verification of the requested relay.
        self._verify_single(channel, expected_closed=True)

        # 5: whole-bank verification -- the requested relay is the ONLY one closed.
        self._verify_exact([channel])

        self.log.info("Verification: PASS (channel %d only)", channel)

    def open_all(self):
        """Open every relay: force-all-open + verify."""
        self._force_all_open_and_verify()

    def query(self, channel: int) -> bool:
        """True if relay `channel` (1-based) reads closed."""
        self._validate_channel(channel)
        with self._ni_errors(f"reading relay {channel}"):
            return self._raw_read_position(channel)

    def closed_channels(self) -> list:
        """1-based channels currently reading closed (one pass over the card)."""
        with self._ni_errors("reading relay positions"):
            closed = self._raw_read_closed()
        self.last_known_closed = closed
        return closed

    # ------------------------------------------------------------------
    # Safety sequence
    # ------------------------------------------------------------------

    def _force_all_open_and_verify(self):
        """Read state (logged) -> force every relay open -> verify all open.
        Raises RelayStateVerificationError if any relay is still closed."""
        self._check_current_state("close()/open()/open_all()")
        with self._ni_errors("opening all relays"):
            self._raw_drive_all_open()
        self._verify_exact([])

    def _check_current_state(self, context: str):
        """Pre-action read of the bank, logged. A failed read is not fatal
        here -- the force-open that follows is the fail-safe -- same policy as
        NumatoRelayMatrix.check_current_relay_state()."""
        try:
            closed = self._raw_read_closed()
        except Exception as e:
            self.log.warning("%s: pre-action state check FAILED (%s) -- proceeding to force-open "
                             "regardless (fail-safe).", context, e)
            self.last_known_closed = None
            return
        self.last_known_closed = closed
        if closed:
            self.log.warning("%s: pre-action state check: bank NOT all-open before this operation "
                             "(closed=%s) -- forcing safe state now.", context, closed)
        else:
            self.log.info("%s: pre-action state check: bank already all-open (verified).", context)

    def _verify_exact(self, expected_closed: list):
        """Global verification: the set of closed relays must be exactly
        `expected_closed`. On mismatch: emergency all-open first, then raise."""
        with self._ni_errors("verifying relay bank"):
            actual = self._raw_read_closed()
        self.last_known_closed = actual
        if actual != expected_closed:
            self.log.error("Verification: FAIL  Expected closed: %s  Actual closed: %s",
                           expected_closed, actual)
            shutdown_ok = self._emergency_all_open(
                f"relay bank verification mismatch (expected closed {expected_closed}, got {actual})")
            raise RelayStateVerificationError(
                f"Relay bank verification FAILED: expected closed {expected_closed}, got {actual}. "
                f"Execution stopped. Emergency shutdown "
                f"{self._shutdown_text(shutdown_ok)}"
            )

    def _verify_single(self, channel: int, expected_closed: bool):
        with self._ni_errors(f"verifying relay {channel}"):
            actual = self._raw_read_position(channel)
        if actual != expected_closed:
            want = "CLOSED" if expected_closed else "OPEN"
            got = "CLOSED" if actual else "OPEN"
            self.log.error("Verification: FAIL  Relay: %d  Expected: %s  Actual: %s", channel, want, got)
            shutdown_ok = self._emergency_all_open(
                f"relay {channel} verification mismatch (expected {want}, got {got})")
            raise RelayStateVerificationError(
                f"Relay {channel} verification FAILED: expected {want}, got {got}. "
                f"Execution stopped. Emergency shutdown {self._shutdown_text(shutdown_ok)}"
            )

    @staticmethod
    def _shutdown_text(ok: bool) -> str:
        return ("succeeded -- all relays forced OPEN and confirmed." if ok else
                "FAILED -- hardware may still be energized. Physically disconnect power.")

    def _emergency_all_open(self, reason: str) -> bool:
        """Single, non-recursive, best-effort force-open + confirm. Never
        raises; returns True only if every relay is confirmed open. Uses the
        raw primitives directly (like NumatoRelayMatrix._emergency_all_off()
        uses the raw transport) so it cannot recurse into verification."""
        if self._in_emergency:
            return False
        self._in_emergency = True
        self.log.critical("EMERGENCY RELAY SHUTDOWN triggered: %s -- forcing all relays OPEN", reason)
        try:
            self._raw_drive_all_open()
            closed = self._raw_read_closed()
        except Exception as e:
            self.log.critical("EMERGENCY SHUTDOWN FAILED: could not force/verify relays OPEN after "
                              "(%s): %s. Hardware may still be energized -- physically disconnect "
                              "power if this cannot be resolved immediately.", reason, e)
            return False
        finally:
            self._in_emergency = False
        if closed:
            self.log.critical("EMERGENCY SHUTDOWN FAILED: relays %s not confirmed OPEN after forced "
                              "shutdown. Reason: %s. Hardware may still be energized.", closed, reason)
            return False
        self.log.warning("Emergency relay shutdown succeeded: all relays confirmed OPEN.")
        return True

    @contextlib.contextmanager
    def _ni_errors(self, what: str):
        """Map NI-SWITCH errors to RelayError, attempting an emergency
        all-open first (the counterpart of the Numato's terminal-
        communication-failure reflex)."""
        try:
            yield
        except Exception as e:
            if self._ni is None or not isinstance(e, self._ni.Error):
                raise
            self.log.error("NI-SWITCH error during %s: %s", what, e)
            self._emergency_all_open(f"NI-SWITCH error during {what}: {e}")
            raise RelayError(f"NI-SWITCH error during {what} on '{self.name}': {e}") from e

    # ------------------------------------------------------------------
    # Raw NI-SWITCH primitives (no verification, no error mapping)
    # ------------------------------------------------------------------

    def _require_session(self):
        if self._session is None:
            raise RelayError(f"NI PXIe-2569 relay matrix {self.name} is not connected")
        return self._session

    def _raw_read_position(self, channel: int) -> bool:
        session = self._require_session()
        return session.get_relay_position(self._relay_names[channel - 1]) == self._ni.RelayPosition.CLOSED

    def _raw_read_closed(self) -> list:
        return [ch for ch in range(1, self.num_channels + 1) if self._raw_read_position(ch)]

    def _raw_drive(self, channel: int, closed: bool):
        session = self._require_session()
        action = self._ni.RelayAction.CLOSE if closed else self._ni.RelayAction.OPEN
        session.relay_control(self._relay_names[channel - 1], action)

    def _raw_drive_all_open(self):
        session = self._require_session()
        for name in self._relay_names:
            session.relay_control(name, self._ni.RelayAction.OPEN)
        self._raw_debounce()

    def _raw_debounce(self):
        self._require_session().wait_for_debounce(self._debounce_timeout_ms)
