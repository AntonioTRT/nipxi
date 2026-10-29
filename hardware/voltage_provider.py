"""
Battery Voltage Provider -- FUTURE PLANNED ARCHITECTURE. NOT wired into any
charge/discharge/monitor/safety path: today every one of those still calls
`dmm.measure_dc_voltage()` (directly, or via hardware/sense_router.py::
read_battery_voltage_via_sense()). Nothing imports this module outside its
own test.

Purpose: let a future sequence ask "what is the battery voltage?" without
knowing whether the answer comes from the DMM or from the PSU (SMU) remote
sense, so a system without a DMM (or with the DMM assigned elsewhere) only
needs a different provider, not different EOC/EOD/safety code. Mirrors the
interface-now/implementation-later pattern of hardware/sense_router.py.

See docs/architecture.md "Battery Voltage Provider (Future Architecture)"
for the migration path and the limitations of PSU sense that must be
resolved before PSUSenseVoltageProvider can be enabled.
"""

from __future__ import annotations


class BatteryVoltageProvider:
    """Abstract source of the authoritative battery voltage."""

    #: Short label recorded with each reading ("DMM", "PSU_SENSE").
    source_name = "UNKNOWN"

    #: True if a reading is possible while the PSU output is disabled.
    #: Sequences use this to decide whether the pre-enable polarity/presence
    #: check can use this provider (the DMM can; the PSU cannot).
    available_with_output_disabled = False

    def read_voltage(self) -> float:
        """Return the battery voltage in volts. Raise on failure -- never
        return a placeholder value."""
        raise NotImplementedError


class DMMVoltageProvider(BatteryVoltageProvider):
    """Today's behavior: DMM DC volts via the optional sense router."""

    source_name = "DMM"
    available_with_output_disabled = True

    def __init__(self, dmm, sense_router=None, sense_channel=None):
        self.dmm = dmm
        self.sense_router = sense_router
        self.sense_channel = sense_channel

    def read_voltage(self) -> float:
        from hardware.sense_router import read_battery_voltage_via_sense
        return read_battery_voltage_via_sense(self.dmm, self.sense_router, self.sense_channel)


class PSUSenseVoltageProvider(BatteryVoltageProvider):
    """
    Future: SMU voltage measured with remote sense, observing the battery
    side of the fuse/relay/harness. NOT IMPLEMENTED -- read_voltage() raises
    on purpose so it cannot be selected by accident.

    Requirements before this can be implemented (details in docs/
    architecture.md): SMU.sense = REMOTE configured in
    SMU._configure_current_source() with sense leads physically routed to
    the battery side (switched per position); a pre-enable polarity/
    presence strategy, since SMU.measure() needs the output enabled
    (available_with_output_disabled stays False); a cross-check or
    plausibility check replacing the DMM-vs-SMU comparison; bench
    validation of accuracy against the DMM.
    """

    source_name = "PSU_SENSE"
    available_with_output_disabled = False

    def __init__(self, smu):
        self.smu = smu

    def read_voltage(self) -> float:
        raise NotImplementedError(
            "PSUSenseVoltageProvider is a documented future extension point -- "
            "see docs/architecture.md 'Battery Voltage Provider (Future Architecture)'."
        )
