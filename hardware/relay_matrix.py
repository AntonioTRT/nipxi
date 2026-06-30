"""
Relay matrix interface - COM port controlled (not NI).
Selects which battery channel is connected to the SMU.

TODO: Fill in the actual serial command protocol for your relay controller.
      Replace command templates in config/devices.py with real commands.
"""

import serial
import time
from hardware.base import HardwareBase
from utils.errors import RelayError


class RelayMatrix(HardwareBase):
    """
    Controls up to 8 relay channels via serial port.
    Only one channel should be closed at a time (multiplexed topology).

    Safety rule: verify current == 0 on the ACTIVE channel before switching.
    """

    def __init__(self, port: str, baud_rate: int = 9600, timeout_s: float = 2.0):
        super().__init__("RelayMatrix")
        self.port = port
        self.baud_rate = baud_rate
        self.timeout_s = timeout_s
        self._serial: serial.Serial | None = None
        self._active_channel: int | None = None

    def connect(self):
        self.log.info("Opening relay COM port: %s @ %d baud", self.port, self.baud_rate)
        # TODO: self._serial = serial.Serial(self.port, self.baud_rate, timeout=self.timeout_s)
        self.connected = True
        self.log.info("Relay matrix connected.")

    def disconnect(self):
        self.open_all()   # safe state: all relays open
        if self._serial and self._serial.is_open:
            self._serial.close()
        self.connected = False

    def close_channel(self, channel: int):
        """Close relay for the given channel (1-based). Opens all others first."""
        if channel == self._active_channel:
            return
        self.open_all()
        self.log.info("Closing relay channel %d", channel)
        # TODO: send CLOSE command via self._serial
        # cmd = f"CLOSE {channel}\r\n".encode()
        # self._serial.write(cmd)
        # response = self._serial.readline()
        # if not self._validate_response(response): raise RelayError(...)
        self._active_channel = channel

    def open_channel(self, channel: int):
        """Open a specific relay channel."""
        self.log.info("Opening relay channel %d", channel)
        # TODO: send OPEN command
        if self._active_channel == channel:
            self._active_channel = None

    def open_all(self):
        """Open all relay channels (safe state)."""
        self.log.info("Opening all relay channels.")
        # TODO: send OPEN ALL command or loop over channels
        self._active_channel = None

    def query_channel(self, channel: int) -> bool:
        """Return True if channel relay is closed."""
        # TODO: send QUERY command, parse response
        return False

    @property
    def active_channel(self) -> int | None:
        return self._active_channel
