"""
Simulation extension points -- FOUNDATIONS ONLY.

This module exists so DEVELOPMENT-mode software work (UI, database, test-
sequence logic) can eventually run without any physical hardware attached,
without inventing the extension points later under time pressure. See
docs/architecture.md "System Modes" and config/system_mode.py's
ModePolicy.allow_simulated_devices.

NOT wired into the live dispatch path yet -- HardwareManager and
RelayFactory do not construct any of these today. None of these classes do
anything real: connect() just marks the device connected, and every other
method returns a fixed placeholder value or does simple in-memory
bookkeeping. This is deliberate; the request that added this module was
explicit that only the extension points should exist yet, not full
simulation behavior.

How these are expected to be wired in later (NOT implemented yet):
    - hardware/relay_factory.py: RelayFactory._DRIVERS would gain a
      "simulated" key -> SimulatedRelay, selected when a relay cfg's
      "type" is "simulated" (today only "serial"/"ethernet" exist).
    - test_control/hardware_manager.py: when
      config.system_mode.get_mode_policy(settings).allow_simulated_devices
      is True (DEVELOPMENT only) and a real device fails to connect,
      HardwareManager's lenient connect path could fall back to
      constructing the matching Simulated* class instead of just recording
      the device as unavailable in self.hardware_status.
    - config/devices.py would need a per-device "simulated" fallback cfg
      or a blanket "simulate missing devices" toggle -- not designed yet.

SimulatedBattery is intentionally the least fleshed-out: it is not a
HardwareBase device, it is a virtual battery MODEL (voltage/current/
temperature behavior under charge or discharge) that SimulatedSMU/
SimulatedDAQ would eventually read from instead of returning fixed
placeholder values. The real design work (a charge/discharge curve model,
time-stepping, thermal behavior) is still ahead of it.
"""

from hardware.base import HardwareBase
from hardware.relay import RelayBase


class SimulatedSMU(HardwareBase):
    """Extension point only -- see module docstring. Not wired in yet."""

    def __init__(self, cfg: dict):
        super().__init__(f"SIMULATED_SMU_{cfg.get('resource', '?')}")

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def identify(self) -> str:
        return "Simulated SMU (no real hardware)"

    def set_charge_mode(self, current_a: float, voltage_limit_v: float):
        pass

    def set_discharge_mode(self, current_a: float, voltage_limit_v: float):
        pass

    def output_enable(self):
        pass

    def output_disable(self):
        pass

    def measure(self) -> dict:
        # TODO: read from a SimulatedBattery model instead of a fixed value.
        return {"voltage_v": 3.7, "current_a": 0.0}


class SimulatedDAQ(HardwareBase):
    """Extension point only -- see module docstring. Not wired in yet."""

    def __init__(self, cfg: dict):
        super().__init__(f"SIMULATED_DAQ_{cfg.get('resource', '?')}")

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def identify(self) -> str:
        return "Simulated DAQ (no real hardware)"

    def read_channel(self, physical_channel: str) -> float:
        return 3.7

    def read_all_batteries(self) -> dict:
        # TODO: read from SimulatedBattery models instead of fixed values.
        return {
            i: {"voltage_v": 3.7, "current_a": 0.0, "ntc_v": 1.65}
            for i in range(1, 9)
        }

    def verify_zero_current(self, channel: int, threshold_a: float = 0.01) -> bool:
        return True


class SimulatedRelay(RelayBase):
    """
    Extension point only -- see module docstring. Not wired into
    RelayFactory yet. Unlike SimulatedSMU/SimulatedDAQ, this one already
    tracks real open/closed state in memory (cheap to do, and useful for
    exercising BatteryTestSequence's relay-interlock logic in DEVELOPMENT
    mode without real hardware) -- but it does no readback verification
    against anything physical, obviously, and is not a safety device.
    """

    def __init__(self, cfg: dict):
        super().__init__(
            cfg.get("name", "SIMULATED_RELAY"),
            cfg.get("channel_count", cfg.get("num_channels", 8)),
        )
        self._active_channel = None

    def connect(self):
        self.connected = True

    def disconnect(self):
        self._active_channel = None
        self.connected = False

    def open(self, channel: int):
        self._validate_channel(channel)
        if self._active_channel == channel:
            self._active_channel = None

    def close(self, channel: int):
        self._validate_channel(channel)
        self._active_channel = channel

    def query(self, channel: int) -> bool:
        self._validate_channel(channel)
        return self._active_channel == channel

    def open_all(self):
        self._active_channel = None

    def close_all(self):
        raise NotImplementedError(
            "close_all() is intentionally not supported -- matches the "
            "interlock behavior of the production NumatoRelayMatrix driver "
            "(hardware/relay_eth.py), where only one relay may ever be "
            "active at a time."
        )


class SimulatedBattery:
    """
    Extension point only, and the least fleshed-out of these -- see the
    module docstring. A virtual battery MODEL (not a HardwareBase device)
    that SimulatedSMU/SimulatedDAQ would eventually read from instead of
    returning fixed placeholder values.
    """

    def __init__(self, voltage_v: float = 3.7, capacity_ah: float = 2.0):
        self.voltage_v = voltage_v
        self.capacity_ah = capacity_ah
        self.current_a = 0.0
        self.temp_c = 25.0

    def step(self, elapsed_s: float, current_a: float):
        """TODO: integrate current over time and update voltage_v/temp_c."""
        self.current_a = current_a
