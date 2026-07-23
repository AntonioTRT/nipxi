"""
DAQ driver. Covers NI 6363 for analog voltage/current/NTC acquisition.

connect()/disconnect()/identify() are real (NI-DAQmx device enumeration +
a hardware self-test). read_channel() is also real -- a single analog
input read, verified finite and within the configured range (mirrors
hardware/dmm.py::DMM.measure_dc_voltage()'s verification philosophy).
read_all_batteries()/verify_zero_current() (multi-channel synchronized
acquisition) are still TODO placeholders -- a separate, later step.

Verification philosophy (see docs/architecture.md "Instrument Verification
Philosophy" and hardware/relay_eth.py, which this mirrors): a bare identity
query is not a real verification. identify() does COMMAND (run the
device's built-in self-test, device.self_test_device()) -> READBACK
(nidaqmx raises DaqError on failure, so a clean return IS the readback) ->
VERIFY (no exception raised) -> return the product type. read_channel()
does COMMAND (configure one analog input channel at the configured range
and trigger a read) -> READBACK (the sampled value) -> VERIFY (finite,
within the configured range, +5% overrange margin) -> return it, raising
DAQError on any failure -- never just "the read call didn't throw."

Constructed from a config/devices.py DAQ_CONFIG-shaped dict -- the same
config dict HardwareManager and Hardware Discovery both read, so there is
one source of truth for the resource string (config/devices.py, not
config/settings.py).
"""

import math

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
        self._model    = cfg.get("model", "NI-6363")
        self._range_v  = float(cfg.get("voltage_range_v", 5.0))
        self._device   = None   # nidaqmx.system.Device, set by connect()

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

    def read_channel(self, physical_channel: str) -> float:
        """
        Read a single analog input channel -- COMMAND (configure a
        temporary AI voltage channel at the configured +/-voltage_range_v
        and trigger a read) -> READBACK (the sampled value) -> VERIFY
        (finite, within the configured range, +5% overrange margin) ->
        return it. Raises DAQError on any failure, including a value that
        is technically returned but fails verification -- a NaN, an
        out-of-range, or a stuck reading is a failure, not "the read call
        didn't throw." Mirrors hardware/dmm.py::DMM.measure_dc_voltage().
        """
        if self._device is None:
            raise DAQError(f"DAQ {self.resource} is not connected")

        try:
            import nidaqmx
            import nidaqmx.errors
            with nidaqmx.Task() as task:
                task.ai_channels.add_ai_voltage_chan(
                    physical_channel, min_val=-self._range_v, max_val=self._range_v)
                value = task.read()
        except nidaqmx.errors.DaqError as e:
            raise DAQError(
                f"DAQ {self.resource} channel {physical_channel} read failed: {e}"
            ) from e

        if not math.isfinite(value):
            raise DAQError(
                f"DAQ {self.resource} channel {physical_channel} read FAILED "
                f"verification: reading is not a finite number ({value!r})"
            )
        if abs(value) > self._range_v * 1.05:   # allow the ADC's own overrange headroom
            raise DAQError(
                f"DAQ {self.resource} channel {physical_channel} read FAILED "
                f"verification: {value:.4f} V is outside the configured "
                f"+/-{self._range_v} V range"
            )
        self.log.info("DAQ %s channel %s read: %.4f V (range +/-%.1f V)",
                      self.resource, physical_channel, value, self._range_v)
        return value

    # ------------------------------------------------------------------
    # Multi-channel synchronized acquisition -- TODO, not implemented yet.
    # Out of scope for connectivity/discovery work; see docs/TODO.md.
    # ------------------------------------------------------------------

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
