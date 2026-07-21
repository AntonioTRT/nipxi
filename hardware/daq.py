"""
DAQ driver. Covers NI 6363 for analog voltage/current/NTC acquisition.

connect()/disconnect()/identify() are real (NI-DAQmx device enumeration +
self-test) -- this is the connectivity/identification surface exercised by
Hardware Discovery. No task is created and no channel is read here; actual
channel acquisition (read_channel, read_all_batteries, verify_zero_current)
is still a TODO placeholder -- a separate, later step.

Constructed from a config/devices.py DAQ_CONFIG-shaped dict -- the same
config dict HardwareManager and Hardware Discovery both read, so there is
one source of truth for the resource string (config/devices.py, not
config/settings.py).
"""

from hardware.base import HardwareBase
from utils.errors import DAQError


class DAQ(HardwareBase):
    """
    Reads analog inputs from NI 6363:
        ai0..ai7   - battery voltages (8 channels)
        ai8..ai15  - battery currents via shunt (8 channels)
        ai16..ai23 - NTC thermistor voltages (8 channels)
    """

    def __init__(self, cfg: dict):
        resource = cfg.get("resource", "")
        super().__init__(f"DAQ_{resource}")
        self.resource = resource
        self._model  = cfg.get("model", "NI-6363")
        self._device = None   # nidaqmx.system.Device, set by connect()

    def connect(self):
        self.log.info("Opening DAQ session: %s", self.resource)
        try:
            import nidaqmx.system
            import nidaqmx.errors
        except ImportError as e:
            raise DAQError(
                "Library 'nidaqmx' is not installed. Run: pip install nidaqmx"
            ) from e
        try:
            system = nidaqmx.system.System.local()
            dev_names = [d.name for d in system.devices]
            if self.resource not in dev_names:
                raise DAQError(
                    f"DAQ {self.resource!r} not found. "
                    f"Available devices: {dev_names if dev_names else 'none'}"
                )
            self._device = system.devices[self.resource]
        except nidaqmx.errors.DaqError as e:
            raise DAQError(f"DAQ {self.resource} failed to open: {e}") from e
        self.connected = True
        self.log.info("DAQ session open: %s", self.resource)

    def disconnect(self):
        # No persistent task is held open by connect()/identify() -- nothing
        # to close beyond dropping the device reference.
        self._device = None
        self.connected = False
        self.log.info("DAQ session closed: %s", self.resource)

    def identify(self) -> str:
        """
        Identification + built-in self-test only -- the connectivity/
        discovery surface. Does not create a task or read any channel.
        """
        if self._device is None:
            raise DAQError(f"DAQ {self.resource} is not connected")
        product_type = self._device.product_type
        self._device.self_test_device()
        return product_type

    # ------------------------------------------------------------------
    # Channel acquisition -- TODO, not implemented yet.
    # Out of scope for connectivity/discovery work; see docs/TODO.md.
    # ------------------------------------------------------------------

    def read_channel(self, physical_channel: str) -> float:
        """Read a single analog input. Returns voltage in V."""
        # TODO: create temporary task, configure ai channel, read one sample
        return 0.0

    def read_all_batteries(self) -> dict:
        """
        Read voltage, current, and NTC for all 8 channels simultaneously.
        Returns dict keyed by channel index (1-8).
        """
        # TODO: configure multi-channel task, read synchronized sample
        return {
            i: {"voltage_v": 0.0, "current_a": 0.0, "ntc_v": 0.0}
            for i in range(1, 9)
        }

    def verify_zero_current(self, channel: int, threshold_a: float = 0.01) -> bool:
        """Return True if channel current is below threshold (safe to switch relay)."""
        # TODO: read current channel, compare to threshold
        return True
