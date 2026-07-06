"""
Ethernet relay driver for Numato RELAY32ETHRL00.
Uses a raw TCP socket instead of telnetlib (deprecated in Python 3.11).

Protocol (Numato Ethernet relay, Telnet-based ASCII):
    1. Connect to host:port (default port 23)
    2. Wait for "login" prompt
    3. Send username + CRLF
    4. Wait for "Password:" prompt
    5. Send password + CRLF
    6. Wait for "successfully" -- confirms login OK
    7. Wait for ">" -- the command prompt
    8. Send commands like "relay on 0\\r\\n", read until ">" to confirm
    9. For queries: "relay read 0\\r\\n", parse "on" or "off" from response

Channel numbering:
    The Numato module addresses channels 0..9 with "0".."9" and
    channels 10..31 with "A".."V" (in uppercase).
    We accept 1-based channel numbers and convert internally:
        channel 1  -> address "0"
        channel 2  -> address "1"
        channel 10 -> address "9"
        channel 11 -> address "A"

Configuration keys (from config/devices.py RELAY_ETH_CONFIG):
    type          "ethernet"
    driver        "RELAY32ETHRL00"  (informational label)
    name          human-readable label, e.g. "MAIN_MATRIX_ETH"
    ip            IP address string, e.g. "192.168.1.50"
    port          TCP port integer, default 23
    user          Telnet username, default "admin"
    password      Telnet password, default "admin"
    timeout       float seconds for socket operations, default 5.0
    num_channels  integer, default 8

Extension notes:
    If Numato releases a module with a different login prompt, adjust
    the marker bytes in connect(). The rest of the protocol is identical.
"""

import re
import socket
import time

from hardware.relay import RelayBase
from utils.errors import RelayError, NIPXITimeoutError, ValidationError


class EthernetRelay(RelayBase):
    """
    TCP/Telnet relay controller (Numato RELAY32ETHRL00).
    One persistent socket connection per session -- re-login not required
    between relay commands.
    """

    DEFAULT_PORT     = 23
    DEFAULT_USER     = "admin"
    DEFAULT_PASSWORD = "admin"
    RECV_BUFSIZE     = 1024

    def __init__(self, cfg: dict):
        name = cfg.get("name", "ETH_RELAY")
        num_channels = cfg.get("num_channels", 8)
        super().__init__(name, num_channels)

        self._driver   = cfg.get("driver", "RELAY32ETHRL00")
        self._host     = cfg.get("ip", "")
        self._port     = int(cfg.get("port", self.DEFAULT_PORT))
        self._user     = cfg.get("user", self.DEFAULT_USER)
        self._password = cfg.get("password", self.DEFAULT_PASSWORD)
        self._timeout  = float(cfg.get("timeout", 5.0))
        self._sock: socket.socket | None = None

        if not self._host:
            raise ValidationError(
                f"RELAY_ETH_CONFIG missing 'ip' for ethernet relay '{self.name}'"
            )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
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

        # Telnet login sequence
        try:
            self._read_until(b"login")
            self._send_raw(self._user.encode("ascii") + b"\r\n")
            self._read_until(b"Password: ")
            self._send_raw(self._password.encode("ascii") + b"\r\n")
            result = self._read_until(b"successfully")
            if b"successfully" not in result:
                raise RelayError(
                    self._conn_error("Login failed -- check user/password in RELAY_ETH_CONFIG")
                )
            self._read_until(b">")   # consume the first command prompt
        except NIPXITimeoutError:
            self._sock.close()
            self._sock = None
            raise RelayError(
                self._conn_error("Timeout during login sequence")
            ) from None

        self.connected = True
        self.log.info("Connected: %s (%s:%d)", self._driver, self._host, self._port)

    def disconnect(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connected = False
        self.log.info("Disconnected: %s (%s:%d)", self._driver, self._host, self._port)

    # ------------------------------------------------------------------
    # Relay operations
    # ------------------------------------------------------------------

    def open(self, channel: int):
        """De-energize relay: "relay off N"."""
        self._validate_channel(channel)
        self._send_cmd(f"relay off {self._ch_str(channel)}")
        self.log.debug("Relay open ch%d (%s)", channel, self._ch_str(channel))

    def close(self, channel: int):
        """Energize relay: "relay on N"."""
        self._validate_channel(channel)
        self._send_cmd(f"relay on {self._ch_str(channel)}")
        self.log.debug("Relay close ch%d (%s)", channel, self._ch_str(channel))

    def query(self, channel: int) -> bool:
        """Return True if relay is closed. Uses "relay read N" command."""
        self._validate_channel(channel)
        self._send_raw(f"relay read {self._ch_str(channel)}\r\n".encode())
        response = self._read_until(b">")
        # Response contains the echoed command + "on\r\n>" or "off\r\n>"
        # Strip everything after the last prompt marker and look for "on"
        text = re.split(rb"[>&]", response)[0].decode(errors="replace").lower()
        return "on" in text

    def open_all(self):
        """
        Open all channels using individual commands.
        Could be replaced with "relay writeall 00000000" for speed if needed,
        but individual commands are safer and easier to verify.
        """
        for ch in range(1, self.num_channels + 1):
            self.open(ch)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ch_str(self, channel: int) -> str:
        """
        Convert 1-based channel to Numato address string.
        Channels 1-10 map to "0"-"9"; channels 11-32 map to "A"-"V".
        """
        idx = channel - 1
        if idx < 10:
            return str(idx)
        return chr(ord("A") + idx - 10)

    def _send_cmd(self, cmd: str):
        """Send a command and wait for the ">" prompt that signals completion."""
        self._send_raw((cmd + "\r\n").encode())
        self._read_until(b">")

    def _send_raw(self, data: bytes):
        if self._sock is None:
            raise RelayError(f"Ethernet relay {self.name} is not connected")
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise RelayError(
                f"Send failed to {self._host}: {e}"
            ) from e

    def _read_until(self, marker: bytes) -> bytes:
        """
        Accumulate socket data until marker bytes appear.
        Raises NIPXITimeoutError if the deadline passes first.
        Uses short recv loops (0.2 s) to avoid blocking past the deadline.
        """
        buf = b""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            self._sock.settimeout(0.2)
            try:
                chunk = self._sock.recv(self.RECV_BUFSIZE)
                if not chunk:
                    raise RelayError(
                        f"Connection closed by {self._host}"
                    )
                buf += chunk
                if marker in buf:
                    return buf
            except socket.timeout:
                continue  # keep waiting until deadline
        raise NIPXITimeoutError(
            self._conn_error(f"Timeout waiting for {marker!r}")
        )

    def _conn_error(self, reason: str) -> str:
        """Format the standardized connection error message."""
        return (
            f"[ERROR]\n"
            f"Relay controller not reachable\n\n"
            f"Driver:\n{self._driver}\n\n"
            f"Host:\n{self._host}\n\n"
            f"Reason:\n{reason}"
        )
