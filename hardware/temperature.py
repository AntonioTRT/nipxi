"""
Temperature sensor interface.
Each battery channel has a 10k NTC @ 25 deg C read via DAQ analog input.

NTC conversion: Beta approximation.
TODO: Fill in Beta value and reference values from battery datasheet.

Circuit (confirmed real wiring -- NOT the reciprocal divider a prior
revision of this file assumed):

    V_exc --- [NTC] --- node --- [R_pulldown] --- GND
    v_ntc is measured at the node.

The NTC sits on the EXCITATION side, the fixed pulldown resistor on the
GND side -- the opposite arrangement from the more common "NTC to GND"
divider. Getting this backwards silently inverts every temperature
reading (a hotter cell reads as colder and vice versa), so any change to
the divider math here must re-derive from this circuit, not copy a
formula from a generic NTC-divider reference.
"""

import math


# NTC parameters (Li-ion battery thermistor, per BLOSS Hub spec)
NTC_R25_OHM      = 10000.0   # resistance at 25 deg C
NTC_BETA         = 3950.0    # Beta coefficient (K) - TODO: verify from datasheet
NTC_PULLDOWN_R   = 10000.0   # pull-down resistor in the voltage divider (GND side)
NTC_EXCITATION_V = 5.0       # divider excitation supply (NTC side) -- was named
                             # NTC_VCC (3.3 V) in a prior revision; renamed because
                             # this is an excitation rail feeding the NTC leg of the
                             # divider, not a logic Vcc

# Presence/fault classification thresholds -- see classify_ntc_presence().
# An open NTC leg pulls the node to GND (v_ntc -> 0); a short across the NTC
# (or a short from the excitation rail to the node) pulls it to V_exc. Real
# ADC/wiring noise means neither rail is ever hit exactly, hence the margins.
# ABSENT_VOLTAGE_THRESHOLD is the single source of truth for "is this
# channel electrically open" -- every caller (test_sensors() Test 6, the
# group NTC pre-check, Monitor/Charge/Discharge's NTC read) goes through
# classify_ntc_presence() below rather than re-implementing this check.
ABSENT_VOLTAGE_THRESHOLD   = 0.05   # v_ntc <= this -> ABSENT (open circuit)
NTC_SHORT_VOLTAGE_MARGIN_V = 0.05   # v_ntc >= V_exc - this -> FAULT (shorted)
NTC_PLAUSIBLE_TEMP_MIN_C   = -20.0  # outside [MIN, MAX] with a valid divider
NTC_PLAUSIBLE_TEMP_MAX_C   = 80.0   # reading -> FAULT (implausible), not accepted


def ntc_voltage_to_celsius(v_ntc: float, v_exc: float = NTC_EXCITATION_V,
                           r_pulldown: float = NTC_PULLDOWN_R,
                           r25: float = NTC_R25_OHM, beta: float = NTC_BETA) -> float:
    """
    Convert NTC divider output voltage to temperature in degrees Celsius.

    Circuit (see module docstring):
        V_exc --- [NTC] --- node --- [R_pulldown] --- GND
        v_ntc is measured at the node.

    R_ntc = R_pulldown * (V_exc - v_ntc) / v_ntc -- as v_ntc rises toward
    V_exc, R_ntc falls toward 0 (hotter); as v_ntc falls toward 0, R_ntc
    rises (colder) -- the opposite direction from the reciprocal ("NTC to
    GND") divider a prior revision of this function assumed.

    Returns float temperature in deg C, or None if voltage is out of the
    divider's valid range (0 < v_ntc < v_exc).
    """
    if v_ntc <= 0.0 or v_ntc >= v_exc:
        return None

    r_ntc = r_pulldown * (v_exc - v_ntc) / v_ntc

    # Beta approximation: 1/T = 1/T0 + (1/B) * ln(R/R0)
    t0_k = 25.0 + 273.15
    t_k  = 1.0 / (1.0 / t0_k + (1.0 / beta) * math.log(r_ntc / r25))
    return t_k - 273.15


class NTCPresence:
    """Battery-presence classification for one NTC channel -- see
    classify_ntc_presence(). Plain string constants, same convention as
    utils/stop_reason.py::StopReason."""
    PRESENT = "present"
    ABSENT  = "absent"
    FAULT   = "fault"


def classify_ntc_presence(voltage_v: float, v_exc: float = NTC_EXCITATION_V) -> str:
    """
    Classify one NTC channel's raw divider voltage as PRESENT (a valid,
    plausible reading -- a battery/NTC is there), ABSENT (open circuit --
    no battery/NTC connected), or FAULT (shorted NTC/wiring fault, or a
    reading that doesn't correspond to a plausible temperature).

    FAULT is deliberately distinct from ABSENT: a shorted NTC can occur
    with a battery physically present, so a reading pinned at the
    excitation rail must not be silently reported as "no battery" -- that
    would hide a real wiring/sensor fault behind an empty-position reading.
    """
    if voltage_v <= ABSENT_VOLTAGE_THRESHOLD:
        return NTCPresence.ABSENT
    if voltage_v >= v_exc - NTC_SHORT_VOLTAGE_MARGIN_V:
        return NTCPresence.FAULT

    temp_c = ntc_voltage_to_celsius(voltage_v, v_exc=v_exc)
    if temp_c is None or not (NTC_PLAUSIBLE_TEMP_MIN_C <= temp_c <= NTC_PLAUSIBLE_TEMP_MAX_C):
        return NTCPresence.FAULT
    return NTCPresence.PRESENT


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
