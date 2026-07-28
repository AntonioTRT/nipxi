"""
Numato Relay Matrix driver (Numato Lab 32 Channel Ethernet Relay Module).
Uses a raw TCP socket instead of telnetlib (deprecated in Python 3.11).

This driver is built entirely around Numato's own native command set --
no custom protocol is invented. Reference: numato.com/docs/32-channel-
ethernet-relay-module/ and numato.com/kb/understanding-readallwriteall-
commands-for-relay-modules/.

Native Numato commands used:
    relay on <n>          energize relay n
    relay off <n>          de-energize relay n
    relay read <n>          "on"/"off" state of a single relay
    relay readall            hex bitmask of every relay's state
    relay writeall <hex>      force the whole bank to a hex bitmask
    reset                    reboot the module (maintenance only, drops session)

Numato addressing is natively 0-based: relay 0 is the first relay, relay
31 is the 32nd. 0-9 are addressed as "0".."9"; 10-31 as "A".."V".

Two API layers are exposed:

  1. Native/raw primitives (Numato's own 0-based numbering) --
     write(relay_number, state), read_relay(relay_number), write_all(mask),
     read_all() -> mask, verify_single(relay_number, expected_state),
     verify_all(expected_mask), reset(). These are thin wrappers around the
     literal command strings above and do no channel remapping.

  2. Public RelayBase API (1-based, matches BATTERY_CHANNELS / ACTIVE_CHANNELS
     elsewhere in this app) -- connect(), disconnect(), open(channel),
     close(channel), query(channel)/read(channel), open_all(), close_all().
     open()/close() are implemented ON TOP of the native primitives and are
     the only methods that ever change relay state; both always run the
     mandatory Read All -> Verify Current Status -> Force All OFF -> Verify
     All OFF -> Action -> Verify Action sequence (see
     _force_all_off_and_verify()/check_current_relay_state(), and
     docs/architecture.md "Relay Safety Verification Pattern"). They never
     call the Numato command layer directly -- the requested relay is
     never activated without first reading/logging the pre-existing state,
     then forcing and verifying an all-off baseline.

=====================================================================
Authentication debugging -- CONFIRMED FIXED against the physical unit
=====================================================================
Root cause confirmed by a live run against the actual Numato Lab 32
Channel Ethernet Relay Module at 169.254.1.1 (login + connection
verification + a full 32-channel ON/READ/OFF matrix scan + Hardware
Discovery all PASSED end-to-end; every relay confirmed OFF afterward):

The firmware sends a Telnet IAC option-negotiation request ("IAC DO 45")
mid-handshake (observed right as the client is waiting for the password
prompt). A REAL Telnet client -- what manual testing used, and what
succeeded -- always auto-answers this per RFC 854; the previous
implementation did zero IAC handling and never replied, which is why the
framework's login diverged from the manually-validated Telnet conversation
even though network reachability and credentials were never the problem.
Fixed by _handle_iac(): every inbound chunk is scanned for IAC sequences,
stripped from the text stream (so prompt matching only ever sees visible
banner/prompt bytes), and answered with a blanket decline (IAC DONT/WONT)
-- the same safe default a plain terminal-mode Telnet client negotiates to.

A second, complementary fix from the same investigation: the previous
implementation waited for the EXACT byte strings b"login", b"Password: ",
b"successfully" (lifted from Numato's own reference script,
utils/ethernet_relay_python.py). The real firmware's actual login prompt is
"User Name: " (confirmed in the live transcript) -- the old exact "login"
match happened to still succeed only because the word "login" incidentally
appears in the banner's instructional sentence ("Enter your user name and
password to login"), which is fragile. Now matched case-insensitively
against a set of known-plausible prompt words (_read_until_any()) instead
of one exact byte string, and success is determined by waiting for the ">"
command prompt (the authoritative signal, confirmed in the live transcript)
rather than requiring one specific banner sentence.

Every step of the login handshake is logged at DEBUG level (RX chunks,
detected prompts, TX sent, final response, IAC negotiation replies, and the
resulting PASS/FAIL classification) -- this is exactly the transcript that
was compared against the manual Telnet conversation to find the divergence
above. See docs/architecture.md Section 6c and README.md Section 9.4c for
how to enable and read this output.

Login handshake (Telnet-based ASCII):
    1. Connect to host:port (default port 23)
    2. Wait for a login/username prompt (case-insensitive, tolerant of
       "login:"/"Login:"/"username:"/"Username:"/"User Name:") -> send
       username + CRLF
    3. Wait for a password prompt (tolerant of "password:"/"Password:")
       -> send password + CRLF
    4. Wait for the ">" command prompt (the authoritative success signal)
       or an explicit rejection banner ("incorrect"/"denied"/"invalid"/
       "failed") -- whichever appears first
    5. Immediately issue "relay readall" once to verify the command/response
       loop and log the bank's initial state (connection verification).

Telnet layer guarantees:
    - Every raw chunk received is logged (DEBUG level) before any parsing,
      independent of which higher-level step it belongs to.
    - Telnet IAC option negotiation (RFC 854) is stripped from the text
      stream and answered with a blanket decline -- see hypothesis 2 above.
    - Every command waits for the ">" prompt (acknowledgement) and is
      checked for an "invalid" rejection from the firmware.
    - Every read/write goes through one automatic reconnect-and-retry if
      the connection drops or times out (bounded to a single attempt --
      never a retry loop). This is safe because every Numato relay command
      is idempotent (resending "relay on N" or "relay writeall X" twice
      has no different effect than sending it once) and because every
      safety-critical write in this driver is always followed by an
      independent hardware readback verification regardless of whether a
      reconnect happened in between.
    - A successful command send is NEVER treated as success on its own --
      every write in the public open()/close() path is verified against a
      subsequent relay read/readall before the call returns.

=====================================================================
Emergency Shutdown Strategy
=====================================================================
Design principle: an unknown relay state is an unsafe state. Therefore,
whenever this driver cannot POSITIVELY CONFIRM the relay bank is in the
state it is supposed to be in, its reflex is to force every relay off and
verify that, rather than propagate an exception while leaving hardware in
whatever state it happened to be in. FAIL SAFE, never fail-and-leave-
energized.

This is implemented at the lowest level that can still communicate with
the hardware, so it applies uniformly no matter which higher-level call
triggered the failure:

  - verify_single()/verify_all() mismatch (the relay didn't reach the
    state we just commanded, or the bank doesn't match what we expect) --
    _emergency_all_off() is attempted BEFORE the RelayStateVerificationError
    is raised; its outcome (succeeded / failed) is appended to the message.
  - _call_with_reconnect() terminal failure (communication error and the
    one permitted reconnect attempt also fails, or the retried command
    fails again after a successful reconnect) -- same _emergency_all_off()
    attempt before the RelayError propagates.
  - Authentication failure during a reconnect attempt -- covered by the
    same _call_with_reconnect() path above; a failure during the VERY
    FIRST connect() (no prior session) has nothing to force off yet, so no
    emergency attempt is meaningful there.

_emergency_all_off() is a single, non-recursive, best-effort attempt
(native "relay writeall 00.../relay readall", called directly, never
through write_all()/read_all()/verify_all()) -- it can never itself trigger
another emergency attempt. It NEVER raises a normal exception; it returns
True/False so the ORIGINAL failure is always what the caller sees, now
annotated with whether hardware was actually made safe. If the emergency
attempt also fails (most commonly: no working connection at all), that is
logged as CRITICAL, explicitly stating hardware may still be energized --
there is no way to force relay state from software with zero communication
path, and this is reported honestly rather than silently swallowed.

This driver-level reflex is the innermost layer of a multi-layer strategy;
the outer layers (startup safe-state enforcement, application-exit
protection, and BatteryTestSequence/SafetyMonitor's own emergency_stop())
are documented in docs/architecture.md's "Emergency Shutdown Strategy"
section and test_control/hardware_manager.py / test_control/
safety_monitor.py.

Configuration keys (from config/devices.py NUMATO_RELAY_MATRIX_CONFIG --
the config dict itself, and its "name" field, are unaffected by this class
being renamed; "type": "ethernet" is the RelayFactory dispatch key and is
unchanged -- it denotes the transport interface, not the brand):
    type          "ethernet"
    driver        "RELAY32ETHRL00"  (informational label)
    name          human-readable label, e.g. "MATRIX_NUMATO_201"
    ip            IP address string, e.g. "169.254.1.201"
    port          TCP port integer, default 23
    username      Telnet username, default "admin"  ("user" also accepted for compat)
    password      Telnet password, default "admin"
    timeout       float seconds for socket operations, default 5.0
    num_channels  integer, default 8  -- 32 for the real Numato 32-ch module
    channel_count alias for num_channels (checked first if both are present)

Applies to every configured Numato relay, not just one device: nothing in
this file references a specific device name -- every improvement here
(login tolerance, IAC handling, logging, safety sequence) is exercised by
whichever device's cfg dict RelayFactory.create(cfg) is given, so it
automatically covers every entry under config/devices.py's
NUMATO_RELAY_MATRIX_CONFIGS (formerly RELAY_ETH_CONFIGS) with zero
per-device code.
"""

import re
import socket
import time

from hardware.relay import RelayBase
from utils.errors import (
    RelayError,
    NIPXITimeoutError,
    ValidationError,
    RelayStateVerificationError,
)


class NumatoRelayMatrix(RelayBase):
    """
    TCP/Telnet relay controller for the Numato Lab 32 Channel Ethernet Relay
    Module ("Numato Relay Matrix"). One persistent socket connection per
    session; a dropped connection is transparently reconnected once and the
    failing command retried.
    """

    DEFAULT_PORT     = 23
    DEFAULT_USER     = "admin"
    DEFAULT_PASSWORD = "admin"
    RECV_BUFSIZE     = 1024

    # Telnet IAC (RFC 854) option-negotiation command bytes.
    IAC, WILL, WONT, DO, DONT, SB, SE = 255, 251, 252, 253, 254, 250, 240

    # Login handshake prompt candidates -- case-insensitive substring match
    # against the accumulated buffer. Deliberately tolerant (no colon/casing
    # assumed) since the exact firmware wording is not yet hardware-confirmed
    # from this environment -- see the module docstring's "Authentication
    # debugging" section.
    LOGIN_PROMPTS    = (b"login", b"username", b"user name")
    PASSWORD_PROMPTS = (b"password",)
    FAILURE_MARKERS  = (b"incorrect", b"denied", b"invalid", b"failed")
    PROMPT           = b">"

    def __init__(self, cfg: dict):
        name = cfg.get("name", "NUMATO_RELAY_MATRIX")
        num_channels = cfg.get("channel_count", cfg.get("num_channels", 8))
        super().__init__(name, num_channels)

        self._driver   = cfg.get("driver", "RELAY32ETHRL00")
        self._host     = cfg.get("ip", "")
        self._port     = int(cfg.get("port", self.DEFAULT_PORT))
        self._user     = cfg.get("username", cfg.get("user", self.DEFAULT_USER))
        self._password = cfg.get("password", self.DEFAULT_PASSWORD)
        self._timeout  = float(cfg.get("timeout", 5.0))
        self._sock: socket.socket | None = None
        # Last relay bank state actually read from hardware (via read_all()),
        # or None if never read yet -- see check_current_relay_state()/the
        # "Relay Safety Verification Pattern" in docs/architecture.md. Purely
        # a diagnostic record; nothing in this driver's own logic depends on
        # this value being fresh or even present.
        self.last_known_mask: int | None = None

        if not self._host:
            raise ValidationError(
                f"NUMATO_RELAY_MATRIX_CONFIG missing 'ip' for Numato relay matrix '{self.name}'"
            )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        # Discard any stale socket from a previous session before reconnecting.
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connected = False

        self.log.info("Connecting to %s at %s:%d",
                      self._driver, self._host, self._port)

        # Open the TCP connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect((self._host, self._port))
        except socket.timeout:
            raise RelayError(self._conn_error("Connection timeout")) from None
        except ConnectionRefusedError:
            raise RelayError(self._conn_error("Connection refused")) from None
        except OSError as e:
            raise RelayError(self._conn_error(str(e))) from e

        self._sock = sock
        self.log.debug("TCP connected to %s:%d", self._host, self._port)

        try:
            self._login()
        except NIPXITimeoutError as e:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            raise RelayError(
                self._conn_error(f"Timeout during login sequence: {e}")
            ) from None
        except RelayError:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            raise

        self.connected = True
        self.log.info("Connected: %s (%s:%d)", self._driver, self._host, self._port)

        # Connection verification: issue one "relay readall" immediately so a
        # bad command/response loop is caught at connect time, not on the
        # first real relay operation. Calls the transport directly (not via
        # read_all()/_call_with_reconnect) to avoid recursing back into connect().
        try:
            response = self._send_and_capture("relay readall")
            mask = self._parse_readall_response(response)
            self.log.info(
                "Connection verified: initial relay bank state 0x%0*X (active: %s)",
                self._hex_digits(), mask, self._mask_to_channels(mask),
            )
        except Exception as e:
            self.connected = False
            raise RelayError(
                self._conn_error(f"Connected and logged in, but initial 'relay readall' "
                                 f"verification failed: {e}")
            ) from e

    def _login(self):
        """
        Telnet login handshake, fully instrumented for commissioning/
        debugging. Every raw chunk received is logged by _recv_until() as
        "RX: ..." regardless of which step below is waiting on it -- this
        method adds the semantic milestones (prompt detected / TX sent /
        final classification) around that automatic transcript. See the
        module docstring's "Authentication debugging" section for the two
        hypotheses this implements (tolerant prompt matching + Telnet IAC
        negotiation handling).
        """
        login_prompt, _ = self._read_until_any(self.LOGIN_PROMPTS, "login prompt")
        self.log.debug("Login prompt detected: %r", login_prompt)

        self._send_raw(self._user.encode("ascii") + b"\r\n")
        self.log.debug("TX (username): %s", self._user)

        password_prompt, resp_after_user = self._read_until_any(
            self.PASSWORD_PROMPTS, "password prompt"
        )
        self.log.debug("RX (response after username): %r", resp_after_user)
        self.log.debug("Password prompt detected: %r", password_prompt)

        self._send_raw(self._password.encode("ascii") + b"\r\n")
        self.log.debug("TX (password): %s", self._password)

        marker, final_buf = self._read_until_any(
            self.FAILURE_MARKERS + (self.PROMPT,), "command prompt or failure banner"
        )
        self.log.debug("RX (final response): %r", final_buf)

        if marker != self.PROMPT:
            self.log.debug("Prompt detection result: FAILED (matched failure marker %r)", marker)
            raise RelayError(self._conn_error(
                f"Login rejected by the relay firmware -- matched failure marker "
                f"{marker!r} in the response.\n"
                f"Full response received: {final_buf!r}\n"
                f"Username used: {self._user!r} (password intentionally not included "
                f"in this exception text -- enable DEBUG logging to see the full "
                f"Telnet transcript, including 'TX (password): ...')."
            ))

        self.log.debug("Prompt detection result: SUCCESS (command prompt %r seen)", self.PROMPT)
        if b"success" in final_buf.lower():
            self.log.debug("Login banner confirms success: %r", final_buf)

    def disconnect(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connected = False
        self.log.info("Disconnected: %s (%s:%d)", self._driver, self._host, self._port)

    def reset(self):
        """
        Native Numato "reset" command -- reboots the relay module.

        This is a maintenance operation, NOT part of the mandatory safety
        sequence, and is never called anywhere in the battery test path.
        The module drops the Telnet session as part of rebooting, so this
        method marks the driver disconnected afterward -- callers must
        explicitly connect() again before any further relay operation.
        """
        self.log.warning("Sending: reset  (relay module will reboot and drop this session)")
        try:
            self._send_raw(b"reset\r\n")
        except RelayError:
            pass  # the module may close the socket immediately on reset
        self.disconnect()

    # ------------------------------------------------------------------
    # Public RelayBase API (1-based) -- the ONLY methods that change state.
    # Both compose the native primitives below and always verify by readback.
    # ------------------------------------------------------------------

    def open(self, channel: int):
        """
        De-energize relay for `channel` (1-based).

        Safety-critical sequence (NO EXCEPTIONS -- never touch a relay
        without first forcing a known, verified all-off baseline):
            1. Turn OFF all relays.
            2. Read back and verify all relays are OFF.
        Since the target state for open() IS all-off, step 2's verification
        is the final verification -- there is no further activation step.
        """
        self._validate_channel(channel)
        self.log.info("Requested relay: %d  Action: OPEN", channel)
        self._force_all_off_and_verify()
        self.log.info("Verification: PASS (channel %d confirmed OFF, all others OFF)", channel)

    def close(self, channel: int):
        """
        Energize relay for `channel` (1-based).

        Safety-critical sequence (NO EXCEPTIONS -- the requested relay is
        NEVER activated directly):
            1. Turn OFF all relays.
            2. Read back and verify all relays are OFF (raise
               RelayStateVerificationError and stop if not).
            3. Activate the requested relay ("relay on <n>", native 0-based).
            4. Verify the requested relay individually via "relay read <n>"
               (per the individual-verification requirement).
            5. Verify the whole bank via "relay readall" -- confirms the
               requested relay is ON and every other relay is OFF (catches
               anything a single "relay read" cannot see).
            If any verification fails: raise RelayStateVerificationError
            and stop -- never continue.
        """
        self._validate_channel(channel)
        relay0 = channel - 1
        self.log.info("Requested relay: %d  Action: CLOSE", channel)

        # Steps 1-2: force + verify known-safe baseline before touching anything.
        self._force_all_off_and_verify()

        # Step 3: activate only the requested relay (native 0-based write).
        self.write(relay0, True)

        # Step 4: individual verification via "relay read <n>".
        self.verify_single(relay0, True)

        # Step 5: bulk verification via "relay readall".
        expected_mask = 1 << relay0
        self.verify_all(expected_mask)

        self.log.info("Verification: PASS (channel %d only)", channel)

    def query(self, channel: int) -> bool:
        """Return True if relay is closed (energized). 1-based public API."""
        self._validate_channel(channel)
        return self.read_relay(channel - 1)

    def read(self, channel: int) -> bool:
        """Alias for query() -- matches the manufacturer's "relay read N" naming."""
        return self.query(channel)

    def open_all(self):
        """Open (de-energize) every relay: force-all-off + verify."""
        self._force_all_off_and_verify()

    def close_all(self):
        """
        Disallowed on this interlocked relay bank: the mandatory safety
        sequence only ever allows a single channel to be energized at a
        time. Energizing every channel simultaneously is never a valid
        target state, so this must not silently loop close() per channel.
        """
        raise RelayError(
            f"close_all() is not permitted on {self.name}: only one relay "
            f"may be energized at a time under the mandatory safety sequence"
        )

    # ------------------------------------------------------------------
    # Native Numato primitives (0-based addressing, per Numato's own
    # numbering). Every write here is idempotent and every read/write is
    # protected by the single automatic reconnect in _call_with_reconnect().
    # ------------------------------------------------------------------

    def write_all(self, mask: int = 0):
        """Native "relay writeall <hex>" -- force the whole bank in one write."""
        hexstr = format(mask, f"0{self._hex_digits()}X")
        cmd = f"relay writeall {hexstr}"
        self.log.info("Sending: %s", cmd)
        self._call_with_reconnect(self._send_and_capture, cmd)

    def read_all(self) -> int:
        """Native "relay readall" -- hex bitmask of every relay's state."""
        response = self._call_with_reconnect(self._send_and_capture, "relay readall")
        mask = self._parse_readall_response(response)
        self.last_known_mask = mask
        return mask

    def write(self, relay_number: int, state: bool):
        """Native "relay on/off <n>" for a single 0-based relay number."""
        self._validate_relay_number(relay_number)
        addr = self._addr_str(relay_number)
        cmd = f"relay {'on' if state else 'off'} {addr}"
        self.log.info("Sending: %s", cmd)
        self._call_with_reconnect(self._send_and_capture, cmd)

    def read_relay(self, relay_number: int) -> bool:
        """Native "relay read <n>" for a single 0-based relay number."""
        self._validate_relay_number(relay_number)
        addr = self._addr_str(relay_number)
        response = self._call_with_reconnect(self._send_and_capture, f"relay read {addr}")
        return self._parse_read_response(response, relay_number)

    def verify_single(self, relay_number: int, expected_state: bool):
        """
        Individual verification per spec: uses "relay read <n>" (not readall).
        Raises RelayStateVerificationError and stops on mismatch -- but
        FIRST attempts an emergency all-off (see _emergency_all_off() /
        the module docstring's "Emergency Shutdown Strategy" section),
        since a mismatch here means the bank is in an unknown/unexpected
        state. The original mismatch is always what gets raised; the
        emergency attempt's own outcome is appended to the message.
        """
        actual = self.read_relay(relay_number)
        if actual != expected_state:
            self.log.error(
                "Verification: FAIL  Relay: %d  Expected: %s  Actual: %s",
                relay_number, "ON" if expected_state else "OFF", "ON" if actual else "OFF",
            )
            shutdown_ok = self._emergency_all_off(
                f"relay {relay_number} verification mismatch (expected "
                f"{'ON' if expected_state else 'OFF'}, got {'ON' if actual else 'OFF'})"
            )
            raise RelayStateVerificationError(
                f"Relay {relay_number} verification FAILED: expected "
                f"{'ON' if expected_state else 'OFF'}, got {'ON' if actual else 'OFF'}. "
                f"Execution stopped. Emergency shutdown "
                f"{'succeeded -- all relays forced OFF and confirmed.' if shutdown_ok else 'FAILED -- hardware may still be energized. Physically disconnect power.'}"
            )

    def verify_all(self, expected_mask: int):
        """
        Global verification per spec: uses "relay readall". Detects
        unexpected relay states and multiple relays active simultaneously
        in a single round trip. Raises RelayStateVerificationError and
        stops on mismatch -- but FIRST attempts an emergency all-off (see
        _emergency_all_off() / the module docstring's "Emergency Shutdown
        Strategy" section). The original mismatch is always what gets
        raised; the emergency attempt's own outcome is appended.
        """
        mask = self.read_all()
        if mask != expected_mask:
            digits = self._hex_digits()
            self.log.error(
                "Verification: FAIL  Expected mask: 0x%0*X  Actual mask: 0x%0*X  "
                "Active channels: %s",
                digits, expected_mask, digits, mask, self._mask_to_channels(mask),
            )
            shutdown_ok = self._emergency_all_off(
                f"relay bank verification mismatch (expected mask "
                f"0x{expected_mask:0{digits}X}, got 0x{mask:0{digits}X})"
            )
            raise RelayStateVerificationError(
                f"Relay bank verification FAILED: expected mask "
                f"0x{expected_mask:0{digits}X} (active: {self._mask_to_channels(expected_mask)}), "
                f"got 0x{mask:0{digits}X} (active: {self._mask_to_channels(mask)}). "
                f"Execution stopped. Emergency shutdown "
                f"{'succeeded -- all relays forced OFF and confirmed.' if shutdown_ok else 'FAILED -- hardware may still be energized. Physically disconnect power.'}"
            )

    # ------------------------------------------------------------------
    # Mandatory safety sequence (used by open()/close()/open_all())
    # ------------------------------------------------------------------
    #
    # Full sequence, per docs/architecture.md "Relay Safety Verification
    # Pattern":
    #     Read All -> Verify Current Status -> Force All OFF -> Verify
    #     All OFF -> [caller's requested action] -> Verify Requested Action
    #
    # check_current_relay_state() implements the first two steps;
    # _force_all_off_and_verify() calls it, then performs the next two
    # (unchanged from before this pattern was added). Every real relay path
    # in this codebase (open()/close()/open_all(), and therefore every
    # caller of them -- MonitorBatterySequence, ProtoTestSequence,
    # BatteryTestSequence, and every commissioning test in test.py that
    # uses the public API) converges on this ONE function, so this single
    # change brings all of them into compliance simultaneously. The one
    # deliberate exception is test.py::test_relay_ethernet_test(), which
    # exercises the native command layer directly (bypassing open()/
    # close() on purpose, to test that layer independently) -- it calls
    # check_current_relay_state() itself, explicitly, immediately before
    # its own native write_all(0) (see that function).
    # ------------------------------------------------------------------

    def check_current_relay_state(self, context: str = "") -> int | None:
        """
        STEP 1 + STEP 2 of the relay safety sequence: read the relay
        bank's CURRENT state (BEFORE any force-off or action is attempted)
        and log/report if anything is unexpectedly already active. This is
        a diagnostic checkpoint, not a gate -- it never raises and never
        changes any relay state; the mandatory force-off-and-verify step
        that always follows it is what actually enforces safety regardless
        of what this read finds. Exists to surface "hidden routing issues"
        (a relay found active that nothing here expected) in the log/
        console BEFORE it gets silently corrected, rather than never being
        recorded at all.

        `context` is a short label (e.g. "close()", "RelayEthernetTest")
        included in the log lines so a reader can tell which caller
        triggered this check.

        Stores the result on `self.last_known_mask` (None if the read
        itself failed) for later inspection -- e.g. a future caller
        wiring this into `event_log` traceability. Never raises: a read
        failure here is logged and the caller proceeds to force-off
        regardless (fail-safe -- the subsequent write_all(0)/verify_all(0)
        is the real safety net, not this diagnostic read).

        Returns the mask read (0 = all off), or None if the read failed.
        """
        prefix = f"{context}: " if context else ""
        try:
            mask = self.read_all()
        except Exception as e:
            self.log.warning(
                "%sPre-action relay state check FAILED (%s) -- proceeding to "
                "force-off regardless (fail-safe).", prefix, e,
            )
            self.last_known_mask = None
            return None

        if mask != 0:
            self.log.warning(
                "%sPre-action state check: relay bank NOT all-off before this "
                "operation (mask=0x%0*X, active=%s) -- forcing safe state now.",
                prefix, self._hex_digits(), mask, self._mask_to_channels(mask),
            )
        else:
            self.log.info(
                "%sPre-action state check: relay bank already all-off (verified).",
                prefix,
            )
        return mask

    def _force_all_off_and_verify(self):
        """
        Full mandatory safety sequence: Read All -> Verify Current Status
        (check_current_relay_state(), new) -> Force All OFF -> Verify All
        OFF (write_all(0)/verify_all(0), unchanged from before this
        pattern was added).

        Raises RelayStateVerificationError and stops execution if any
        relay is still active after the force-off -- no continuation, no
        retry, no exceptions.
        """
        self.check_current_relay_state(context="close()/open()/open_all()")
        self.write_all(0)
        self.verify_all(0)

    # ------------------------------------------------------------------
    # Response parsing
    #
    # NOTE ON FIRMWARE FORMAT: CONFIRMED against the physical unit -- a live
    # 32-channel matrix scan (relay writeall/on/off/readall, every channel)
    # passed end-to-end, and the RAW/MASK/ACTIVE log lines matched the
    # physically observed relay state at every step. The Numato firmware
    # does echo each command followed by its documented response (hex
    # bitmask for readall, "on"/"off" for read) before the next ">" prompt,
    # exactly as assumed here.
    # ------------------------------------------------------------------

    def _parse_readall_response(self, response: bytes) -> int:
        text = re.split(rb"[>&]", response)[0]
        match = re.search(rb"([0-9A-Fa-f]{2,8})\s*$", text.strip())
        if not match:
            self.log.error("RAW: %r  -- unparseable relay readall response", response)
            raise RelayError(
                f"relay readall returned unparseable response: {text!r}"
            )
        mask = int(match.group(1), 16)
        self.log.info(
            "RAW: %s  MASK: 0x%0*X  ACTIVE: %s",
            match.group(1).decode(), self._hex_digits(), mask, self._mask_to_channels(mask),
        )
        return mask

    def _parse_read_response(self, response: bytes, relay_number: int) -> bool:
        # Response contains the echoed command + "on\r\n>" or "off\r\n>"
        text = re.split(rb"[>&]", response)[0].decode(errors="replace").lower()
        if "on" in text:
            return True
        if "off" in text:
            return False
        raise RelayError(
            f"Relay read returned invalid response for relay {relay_number}: {text.strip()!r}"
        )

    def _mask_to_channels(self, mask: int) -> list:
        """Decode a relay bitmask into the 1-based channel numbers that are ON."""
        return [ch for ch in range(1, self.num_channels + 1) if mask & (1 << (ch - 1))]

    def _hex_digits(self) -> int:
        """Hex digits Numato uses for writeall/readall on this channel count (min 2)."""
        return max(2, (self.num_channels + 3) // 4)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_relay_number(self, relay_number: int):
        """0-based range check for the native primitives (Numato addressing)."""
        if not (0 <= relay_number < self.num_channels):
            raise ValidationError(
                f"Relay number {relay_number} out of range (0..{self.num_channels - 1}) "
                f"for relay '{self.name}' (Numato native 0-based addressing)"
            )

    def _addr_str(self, relay_number: int) -> str:
        """
        Convert a native 0-based relay number to its Numato address string.
        0-9 map to "0"-"9"; 10-31 map to "A"-"V".
        """
        if relay_number < 10:
            return str(relay_number)
        return chr(ord("A") + relay_number - 10)

    _MAX_RECONNECT_ATTEMPTS = 1

    def _call_with_reconnect(self, fn, *args):
        """
        Call `fn(*args)`; on a communication failure, reconnect exactly once
        and retry `fn(*args)` exactly once more before giving up.

        Safe to retry blindly because every Numato command used here is
        idempotent (resending "relay on N" / "relay writeall X" has the same
        effect as sending it once), and because every safety-critical write
        in open()/close() is always independently re-verified by a
        subsequent hardware readback regardless of whether a reconnect
        happened in between.

        If BOTH the retry and the reconnect itself fail, this is a terminal
        communication breakdown -- see the module docstring's "Emergency
        Shutdown Strategy" section. An emergency all-off is attempted before
        the exception propagates (best-effort: with no working connection,
        it will usually also fail, which is logged as CRITICAL rather than
        silently swallowed -- there is no way to force hardware off in
        software with no communication path, so this is reported honestly
        instead of pretending otherwise).
        """
        try:
            return fn(*args)
        except (RelayError, NIPXITimeoutError) as e:
            self.log.warning(
                "Communication error during relay command (%s) -- "
                "attempting one automatic reconnect and retry", e,
            )
            try:
                self.connect()
            except Exception as reconnect_err:
                self._emergency_all_off(
                    f"communication failure and automatic reconnect both failed "
                    f"({e}; reconnect: {reconnect_err})"
                )
                raise RelayError(
                    f"Automatic reconnect failed after communication error "
                    f"({e}): {reconnect_err}"
                ) from e
            try:
                return fn(*args)   # retry exactly once
            except (RelayError, NIPXITimeoutError) as retry_err:
                self._emergency_all_off(
                    f"communication failure persisted after reconnect: {retry_err}"
                )
                raise

    def _emergency_all_off(self, reason: str) -> bool:
        """
        Single, non-recursive, best-effort attempt to force every relay off
        and confirm it -- the FAIL SAFE reflex used whenever any relay
        operation fails in a way that could leave the bank in an unknown or
        energized state (see the module docstring's "Emergency Shutdown
        Strategy" section). Never raises a normal RelayStateVerificationError
        itself -- returns True/False so the caller can always still raise
        the ORIGINAL failure, now annotated with whether this emergency
        attempt also succeeded.

        Uses the lowest-level transport call (_send_and_capture) directly,
        NOT write_all()/read_all()/verify_all() -- this is what makes it
        safe to call from inside verify_all()/verify_single()/
        _call_with_reconnect() without any risk of recursion.
        """
        self.log.critical("EMERGENCY RELAY SHUTDOWN triggered: %s -- forcing all relays OFF", reason)
        try:
            hexstr = format(0, f"0{self._hex_digits()}X")
            self._send_and_capture(f"relay writeall {hexstr}")
            response = self._send_and_capture("relay readall")
            mask = self._parse_readall_response(response)
        except Exception as e:
            self.log.critical(
                "EMERGENCY SHUTDOWN FAILED: could not force/verify relays OFF "
                "after (%s): %s. Hardware may still be energized -- "
                "physically disconnect power if this cannot be resolved immediately.",
                reason, e,
            )
            return False

        if mask != 0:
            self.log.critical(
                "EMERGENCY SHUTDOWN FAILED: relays not confirmed OFF after forced "
                "shutdown (mask=0x%0*X active=%s). Reason for shutdown: %s. "
                "Hardware may still be energized -- physically disconnect power "
                "if this cannot be resolved immediately.",
                self._hex_digits(), mask, self._mask_to_channels(mask), reason,
            )
            return False

        self.log.warning("Emergency relay shutdown succeeded: all relays confirmed OFF.")
        return True

    def _send_and_capture(self, cmd: str) -> bytes:
        """
        Send a command, wait for the ">" prompt (acknowledgement), validate
        the firmware did not reject it, and return the raw bytes before the
        prompt for the caller to parse.
        """
        self._send_raw((cmd + "\r\n").encode())
        response = self._read_until(b">")
        if b"invalid" in response.lower():
            raise RelayError(f"Relay controller rejected command {cmd!r}: {response!r}")
        return response

    def _send_raw(self, data: bytes):
        if self._sock is None:
            raise RelayError(f"Numato relay matrix {self.name} is not connected")
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise RelayError(
                f"Send failed to {self._host}: {e}"
            ) from e

    def _read_until(self, marker: bytes) -> bytes:
        """Accumulate socket data until `marker` appears. See _recv_until()."""
        try:
            _, buf = self._recv_until(lambda b, m=marker: m if m in b else None)
        except NIPXITimeoutError as e:
            raise NIPXITimeoutError(
                self._conn_error(f"Timeout waiting for {marker!r}: {e}")
            ) from e
        return buf

    def _read_until_any(self, candidates: tuple, label: str = "expected response"):
        """
        Accumulate socket data until ANY of `candidates` appears
        (case-insensitive substring match). Returns (matched_candidate, buf).
        Used by the login handshake to tolerate prompt-wording variants
        ("login:"/"Login:"/"username:"/"Username:"/"User Name:", etc.) --
        see the module docstring's "Authentication debugging" section.
        """
        def match_fn(buf: bytes):
            low = buf.lower()
            for c in candidates:
                if c.lower() in low:
                    return c
            return None

        try:
            return self._recv_until(match_fn)
        except NIPXITimeoutError as e:
            raise NIPXITimeoutError(
                f"Timeout waiting for {label} -- expected one of "
                f"{[c.decode(errors='replace') for c in candidates]}. {e}"
            ) from e

    def _recv_until(self, match_fn, timeout_s: float = None):
        """
        Low-level receive loop shared by _read_until()/_read_until_any().

        Every inbound chunk is passed through _handle_iac() (stripping and
        answering any Telnet option-negotiation bytes -- see the module
        docstring's "Authentication debugging" section, hypothesis 2) and
        then logged at DEBUG level as "RX: ..." BEFORE any prompt matching
        is attempted -- this is what makes the full Telnet conversation
        (including the very first bytes received right after connect)
        visible regardless of which higher-level step is waiting.

        Calls `match_fn(accumulated_buffer)` after each chunk; a truthy
        return value is treated as a match and returned as
        `(match_fn_result, accumulated_buffer)`. Raises NIPXITimeoutError
        (with the full buffer received so far embedded in the message) if
        the deadline passes with no match -- callers never see a bare
        "Authentication failed" with no diagnostic content.
        """
        buf = b""
        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self._timeout)
        while time.monotonic() < deadline:
            self._sock.settimeout(0.2)
            try:
                chunk = self._sock.recv(self.RECV_BUFSIZE)
                if not chunk:
                    raise RelayError(
                        f"Connection closed by {self._host} -- received so far: {buf!r}"
                    )
                chunk = self._handle_iac(chunk)
                if chunk:
                    self.log.debug("RX: %r", chunk)
                buf += chunk
                result = match_fn(buf)
                if result:
                    return result, buf
            except socket.timeout:
                continue  # keep waiting until deadline
        raise NIPXITimeoutError(
            f"received so far: {buf!r}"
        )

    def _handle_iac(self, chunk: bytes) -> bytes:
        """
        Strip and answer Telnet IAC (RFC 854 option negotiation) sequences.

        A raw socket client that never answers option negotiation can stall
        a server that is waiting for a reply before it sends its login
        banner -- a real Telnet client (used for the manually-validated
        login) always auto-answers these. This declines every proposed
        option (IAC DONT/WONT) -- a safe default for a line-oriented
        command/response protocol that needs no special terminal features --
        which is enough to unblock most embedded Telnet servers.

        Returns `chunk` with all IAC sequences removed, so prompt/text
        matching (and the "RX: ..." debug log) only ever sees the visible
        banner/prompt bytes, never raw negotiation bytes.
        """
        if self.IAC not in chunk:
            return chunk

        out = bytearray()
        replies = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            b = chunk[i]
            if b != self.IAC:
                out.append(b)
                i += 1
                continue

            if i + 1 >= n:
                # IAC split across reads -- vanishingly unlikely for a 2-3
                # byte control sequence; drop rather than risk corrupting
                # a login prompt with a stray 0xFF.
                i += 1
                continue

            cmd = chunk[i + 1]
            if cmd == self.IAC:
                out.append(self.IAC)   # escaped literal 0xFF byte
                i += 2
                continue

            if cmd in (self.WILL, self.WONT, self.DO, self.DONT):
                if i + 2 >= n:
                    i += 1  # incomplete sequence at end of chunk -- drop
                    continue
                opt = chunk[i + 2]
                if cmd in (self.WILL, self.DO):
                    decline = self.DONT if cmd == self.WILL else self.WONT
                    replies += bytes([self.IAC, decline, opt])
                    self.log.debug(
                        "Telnet IAC: server %s option %d -> declining with %s",
                        "WILL" if cmd == self.WILL else "DO", opt,
                        "DONT" if decline == self.DONT else "WONT",
                    )
                i += 3
                continue

            if cmd == self.SB:
                # Subnegotiation -- skip through to the matching IAC SE.
                end = chunk.find(bytes([self.IAC, self.SE]), i + 2)
                i = n if end == -1 else end + 2
                continue

            # Other 2-byte IAC commands (NOP/DM/BRK/IP/AO/AYT/EC/EL/GA).
            i += 2

        if replies:
            try:
                self._sock.sendall(bytes(replies))
            except OSError:
                pass  # best-effort -- a real send failure surfaces on the next command anyway

        return bytes(out)

    def _conn_error(self, reason: str) -> str:
        """Format the standardized connection error message."""
        return (
            f"[ERROR]\n"
            f"Numato relay matrix controller not reachable\n\n"
            f"Driver:\n{self._driver}\n\n"
            f"Host:\n{self._host}\n\n"
            f"Reason:\n{reason}"
        )


# Backward-compat alias -- this class was previously named EthernetRelay.
# Existing code importing EthernetRelay (or referencing it via
# RelayFactory) continues to work unchanged.
EthernetRelay = NumatoRelayMatrix
