"""
DAQ interface placeholder.
Covers NI 6363 for analog voltage/current/NTC acquisition.

TODO: Implement using `nidaqmx` Python library.
"""

from hardware.base import HardwareBase


class DAQ(HardwareBase):
    """
    Reads analog inputs from NI 6363:
        ai0..ai7   - battery voltages (8 channels)
        ai8..ai15  - battery currents via shunt (8 channels)
        ai16..ai23 - NTC thermistor voltages (8 channels)
    """

    def __init__(self, resource: str):
        super().__init__(f"DAQ_{resource}")
        self.resource = resource
        self._task = None

    def connect(self):
        self.log.info("Opening DAQ session: %s", self.resource)
        # TODO: import nidaqmx; self._task = nidaqmx.Task()
        self.connected = True

    def disconnect(self):
        if self._task is not None:
            # TODO: self._task.close()
            pass
        self.connected = False
        self.log.info("DAQ session closed: %s", self.resource)

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
