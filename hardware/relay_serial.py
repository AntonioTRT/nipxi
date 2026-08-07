"""
Serial relay driver.
Controls a relay matrix over a COM port using a text command protocol.

Configuration keys (from config/devices.py RELAY_CONFIG):
    type          "serial"
    name          human-readable label (e.g. "MAIN_MATRIX")
    port          COM port string, e.g. "COM3"
    baud_rate     integer, e.g. 9600
    timeout       float seconds, e.g. 2.0  (also accepts "timeout_s" for compat)
    num_channels  integer, default 8
    command_open  format string, e.g. "OPEN {ch}\\r\\n"
    command_close format string, e.g. "CLOSE {ch}\\r\\n"
    command_query format string, e.g. "QUERY {ch}\\r\\n"

The command strings use {ch} as the channel placeholder.
Replace them with the real protocol strings once the relay controller
datasheet is available.

Extension notes:
    If your relay controller uses a binary protocol instead of ASCII,
    override _send_cmd() to build raw bytes and _parse_query() to
    decode the binary response.
"""

# serial imported lazily inside connect() so the module loads even if pyserial
# is not installed -- the import error is reported at connect() time, not import time.
from hardware.relay import RelayBase
from utils.errors import RelayError, NIPXITimeoutError, ValidationError


class SerialRelay(RelayBase):
    """
    ASCII command relay controller connected via COM port.

    The actual command strings are loaded from config so this class
    works with any serial relay that uses a line-oriented text protocol.
    Fill in config/devices.py RELAY_CONFIG once you have the datasheet.
    """

    def __init__(self, cfg: dict):
        name = cfg.get("name", "SERIAL_RELAY")
        num_channels = cfg.get("num_channels", 8)
        super().__init__(name, num_channels)

        # Accept both "timeout" and legacy "timeout_s"
        timeout = cfg.get("timeout", cfg.get("timeout_s", 2.0))

        self._port     = cfg.get("port") or cfg.get("port", "COM3")
        self._baud     = cfg.get("baud_rate", 9600)
        self._timeout  = float(timeout)
        self._cmd_open  = cfg.get("command_open",  "OPEN {ch}\r\n")
        self._cmd_close = cfg.get("command_close", "CLOSE {ch}\r\n")
        self._cmd_query = cfg.get("command_query", "QUERY {ch}\r\n")
        self._serial: serial.Serial | None = None

        if not self._port:
            raise ValidationError("RELAY_CONFIG missing 'port' for serial relay")

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        self.log.info("Opening serial relay %s on %s @ %d baud",
                      self.name, self._port, self._baud)
        if self._uses_placeholder_protocol():
            self.log.warning(
                "Serial communication works, but protocol commands are not "
                "implemented because production hardware is Ethernet. "
                "(relay %s -- fill in RELAY_CONFIG command_open/close/query "
                "from the controller datasheet if Serial is ever promoted "
                "to production.)",
                self.name,
            )
        try:
            import serial as _serial
        except ImportError as e:
            raise RelayError(
                "Library 'pyserial' is not installed.  "
                "Run: pip install pyserial"
            ) from e
        try:
            self._serial = _serial.Serial(
                self._port, self._baud, timeout=self._timeout
            )
        except _serial.SerialException as e:
            raise RelayError(
                f"[ERROR]\nRelay controller not reachable\n\n"
                f"Driver:\nSerial\n\n"
                f"Port:\n{self._port}\n\n"
                f"Reason:\n{e}"
            ) from e
        self.connected = True
        self.log.info("Serial relay %s connected on %s", self.name, self._port)

    def disconnect(self):
        # Safe state: open all relays before cutting the serial line
        if self.connected:
            try:
                self.open_all()
            except Exception as e:
                self.log.warning("open_all() failed during disconnect: %s", e)
        if self._serial is not None:
            try:
                if self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass
        self._serial = None
        self.connected = False
        self.log.info("Serial relay %s disconnected", self.name)

    # ------------------------------------------------------------------
    # Relay operations
    # ------------------------------------------------------------------

    def _open_impl(self, channel: int):
        self._validate_channel(channel)
        self._send_cmd(self._cmd_open.format(ch=channel))
        self.log.debug("Relay open ch%d", channel)

    def _close_impl(self, channel: int):
        self._validate_channel(channel)
        self._send_cmd(self._cmd_close.format(ch=channel))
        self.log.debug("Relay close ch%d", channel)

    def query(self, channel: int) -> bool:
        """Return True if relay is closed. Relies on controller echoing 'ON' or 'CLOSED'."""
        self._validate_channel(channel)
        response = self._send_cmd(self._cmd_query.format(ch=channel), expect_reply=True)
        # Most ASCII relay controllers echo "ON", "1", or "CLOSED" for closed state.
        # Adjust the keyword list if your hardware uses a different response format.
        upper = response.upper().strip()
        return any(kw in upper for kw in ("ON", "CLOSED", "1"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _uses_placeholder_protocol(self) -> bool:
        """True if RELAY_CONFIG still has the default OPEN/CLOSE/QUERY strings."""
        return (
            self._cmd_open == "OPEN {ch}\r\n"
            and self._cmd_close == "CLOSE {ch}\r\n"
            and self._cmd_query == "QUERY {ch}\r\n"
        )

    def _send_cmd(self, cmd: str, expect_reply: bool = False) -> str:
        if self._serial is None or not self._serial.is_open:
            raise RelayError(f"Serial relay {self.name} is not connected")
        try:
            import serial as _serial
            self._serial.write(cmd.encode())
            if expect_reply:
                line = self._serial.readline()
                return line.decode(errors="replace")
            return ""
        except _serial.SerialTimeoutException as e:
            raise NIPXITimeoutError(
                f"[ERROR]\nRelay controller not reachable\n\n"
                f"Driver:\nSerial\n\n"
                f"Port:\n{self._port}\n\n"
                f"Reason:\nCommunication timeout"
            ) from e
        except _serial.SerialException as e:
            raise RelayError(
                f"Serial relay {self.name} command failed: {e}"
            ) from e
