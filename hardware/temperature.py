"""
Temperature sensor interface placeholder.
Each battery channel has a 10k NTC @ 25 deg C read via DAQ analog input.

NTC conversion: use Steinhart-Hart or Beta approximation.
TODO: Fill in Beta value and reference values from battery datasheet.
"""

import math


# NTC parameters (Li-ion battery thermistor, per BLOSS Hub spec)
NTC_R25_OHM    = 10000.0   # resistance at 25 deg C
NTC_BETA       = 3950.0    # Beta coefficient (K) - TODO: verify from datasheet
NTC_PULLDOWN_R = 10000.0   # pull-down resistor in the voltage divider
NTC_VCC        = 3.3       # supply voltage for the divider


def ntc_voltage_to_celsius(v_ntc: float) -> float:
    """
    Convert NTC divider output voltage to temperature in degrees Celsius.

    Circuit:
        VCC --- [R_pulldown] --- node --- [NTC] --- GND
        v_ntc is measured at the node.

    Returns float temperature in deg C, or None if voltage is out of range.
    """
    if v_ntc <= 0.0 or v_ntc >= NTC_VCC:
        return None

    r_ntc = NTC_PULLDOWN_R * v_ntc / (NTC_VCC - v_ntc)

    # Beta approximation: 1/T = 1/T0 + (1/B) * ln(R/R0)
    t0_k = 25.0 + 273.15
    t_k  = 1.0 / (1.0 / t0_k + (1.0 / NTC_BETA) * math.log(r_ntc / NTC_R25_OHM))
    return t_k - 273.15


class TemperatureSensor:
    """
    Wraps the NTC-to-temperature conversion for a single battery channel.
    The raw voltage comes from the DAQ.
    """

    def __init__(self, channel: int):
        self.channel = channel

    def read_celsius(self, daq_voltage_v: float) -> float | None:
        """Convert DAQ-measured NTC voltage to deg C."""
        return ntc_voltage_to_celsius(daq_voltage_v)
