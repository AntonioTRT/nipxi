"""
Device-level configuration and channel mapping.
Maps physical hardware to logical battery channels.
"""

import os
import sys

# -- ensure the nipxi/ package root is importable ----------------------------
# Needed when this file is run/opened directly (e.g. via an IDE "Run" button):
# Python puts this file's own directory (nipxi/config/) on sys.path in that
# case, not nipxi/ itself, so "from config.settings import Settings" below
# would otherwise fail with "No module named 'config'". main.py and test.py
# already do the equivalent for themselves as real entry points; this file
# is not an entry point, but is resilient to being invoked like one anyway.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings

# =============================================================================
# Battery type/model catalog -- physical battery specs (chemistry, capacity,
# voltage/current/temperature limits), independent of which channel a
# battery currently occupies (see BATTERY_CHANNELS below for wiring). This
# is the foundation for the future data/battery_repository.py (see
# docs/DATABASE_ROADMAP.md Section 2) -- NOT wired into safety_monitor.py or
# charge_cycle.py/discharge_cycle.py yet, which still read the single
# global BAT_VOLTAGE_MAX/MIN/BAT_CURRENT_MAX/BAT_TEMP_MAX_C from
# config/settings.py for every channel regardless of what's actually
# installed there. Update BATTERY_CHANNELS[i]["battery_type"] below to
# record which of these is physically installed in each channel.
#
# IMPORTANT -- these are battery CAPABILITIES and RECOMMENDED operating
# ranges, NOT the operational authority. This dict describes what the
# battery itself can tolerate (e.g. max_charge_current_a is the highest
# current this battery model supports); it says nothing about what any
# specific PMU/SMU, DAQ, or safety configuration can actually deliver or
# permit. A battery may support a value the PMU cannot provide, and a PMU
# may support a value that exceeds the battery's limits.
#
# The value actually used to run a test (the "effective operational limit")
# must always be the most conservative value across ALL applicable limit
# sources -- Battery, PMU, DAQ, Safety, User/Test -- never this dict alone.
# See docs/architecture.md "Operational Limit Resolution" for the worked
# example (Battery 3.0 A / PMU 2.0 A / Safety 1.5 A -> effective 1.5 A) and
# the planned LimitResolver concept (documentation only -- not implemented).
# =============================================================================
BATTERY_CONFIGS = {
    "GENERIC_LIION_18650": {
        "chemistry":             "Li-ion",
        "form_factor":           "18650",
        "nominal_voltage_v":     3.7,
        "voltage_max_v":         4.2,
        "voltage_min_v":         3.0,
        "capacity_ah":           2.5,
        "max_charge_current_a":  1.25,   # 0.5C
        "max_discharge_current_a": 2.5,  # 1C
        "max_temp_c":            45.0,
    },
}

# Battery channel definitions
# Key: channel index (1-based), Value: metadata dict
BATTERY_CHANNELS = {
    i: {
        "id": f"BAT_{i}",
        "relay_address": i,          # relay matrix channel number
        "daq_voltage_ch": f"Dev1/ai{i - 1}",
        "daq_current_ch": f"Dev1/ai{i + 7}",
        "daq_ntc_ch":     f"Dev1/ai{i + 15}",
        "fuse_rating_a":  2.0,
        "battery_type":   "GENERIC_LIION_18650",  # key into BATTERY_CONFIGS --
                                                    # update when a different
                                                    # battery is installed here
        "enabled":        True,
    }
    for i in range(1, 9)
}

# SMU (Source Measure Unit) assignment per channel
# Each SMU can handle one channel at a time; extend if you add more SMU cards
SMU_ASSIGNMENTS = {
    "SMU1": {
        "type":     "PXIe",
        "resource": "PXI1Slot4",    # NI 4140 or 4139
        "model":    "NI-4140",
        "channels": list(range(1, 9)),
    }
}

# DAQ card
DAQ_CONFIG = {
    "type":     "PXIe",
    "resource": "PXI1Slot2",
    "model":    "NI-6363",
    "sample_rate_hz": 1.0,
    "voltage_range_v": 5.0,   # ±5 V input range
}

# DMM card
DMM_CONFIG = {
    "type":     "PXIe",
    "resource": "PXI1Slot3",
    "model":    "NI-4065",
    "function": "DC_VOLTS",
    "range_v":  10.0,
}

# Relay matrix -- serial (COM port)
# Set "type": "serial" and fill in the real command strings once you have the datasheet.
# RelayFactory.create(RELAY_CONFIG) will return a SerialRelay instance.
RELAY_CONFIG = {
    "type":         "serial",
    "name":         "MAIN_MATRIX",
    "port":         "COM13",
    "baud_rate":    9600,
    "timeout":      2.0,
    "num_channels": 8,
    # TODO: replace these with the real protocol strings from your relay controller datasheet
    "command_open":  "OPEN {ch}\r\n",
    "command_close": "CLOSE {ch}\r\n",
    "command_query": "QUERY {ch}\r\n",
}

# Numato Relay Matrix -- Ethernet (Numato Lab 32 Channel Ethernet Relay Module)
# Set "type": "ethernet" to use NumatoRelayMatrix instead of SerialRelay.
# RelayFactory.create(NUMATO_RELAY_MATRIX_CONFIG) will return a NumatoRelayMatrix
# instance ("ethernet" names the transport interface, same as "serial" does --
# the concrete driver is specifically for this Numato hardware, not a generic
# vendor-neutral Ethernet relay).
# Reference protocol: utils/ethernet_relay_python.py (manufacturer example).
NUMATO_RELAY_MATRIX_CONFIG = {
    "type":         "ethernet",
    "driver":       "RELAY32ETHRL00",
    "name":         "MAIN_MATRIX_ETH",
    "ip":           "169.254.1.1",      # lab default -- Numato factory IP (link-local)
    "port":         23,                 # default Numato Telnet port
    "username":     "admin",            # Telnet login (factory default: admin/admin)
    "user":         "admin",            # legacy alias, kept for compat -- same value
    "password":     "admin",
    "timeout":      5.0,
    # Settings.RELAY_COUNT is the single source of truth for relay count --
    # never hardcode 32 here or in any relay test.
    "num_channels":  Settings.RELAY_COUNT,
    "channel_count": Settings.RELAY_COUNT,  # alias -- same value, used by the matrix scan test
}

# Backward-compat alias -- this dict was previously named RELAY_ETH_CONFIG.
RELAY_ETH_CONFIG = NUMATO_RELAY_MATRIX_CONFIG

# =============================================================================
# Device enumeration -- name -> config, same model as SMU_ASSIGNMENTS.
# Used by test.py to let the user pick which instance to test when more than
# one device of a given type is configured. Add entries here as hardware is
# added; nothing else needs to change.
# =============================================================================

DMM_CONFIGS = {
    "DMM_MAIN": DMM_CONFIG,
}

DAQ_CONFIGS = {
    "DAQ_MAIN": DAQ_CONFIG,
}

RELAY_SERIAL_CONFIGS = {
    RELAY_CONFIG["name"]: RELAY_CONFIG,
}

# Every Numato Relay Matrix device, name -> config. Hardware Discovery,
# startup device validation, RelayEthernetTest, and every test_relay_*()
# function all iterate this dict -- add a second Numato unit here (a new
# "type": "ethernet" entry with its own "name"/"ip") and every one of those
# automatically covers it too, with no per-device code anywhere.
NUMATO_RELAY_MATRIX_CONFIGS = {
    NUMATO_RELAY_MATRIX_CONFIG["name"]: NUMATO_RELAY_MATRIX_CONFIG,
}

# Backward-compat alias -- this dict was previously named RELAY_ETH_CONFIGS.
RELAY_ETH_CONFIGS = NUMATO_RELAY_MATRIX_CONFIGS
