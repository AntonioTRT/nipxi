"""
Device-level configuration and channel mapping.
Maps physical hardware to logical battery channels.
"""

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

# Relay matrix -- Ethernet (Numato Lab 32 Channel Ethernet Relay Module)
# Set "type": "ethernet" to use EthernetRelay instead of SerialRelay.
# RelayFactory.create(RELAY_ETH_CONFIG) will return an EthernetRelay instance.
# Reference protocol: utils/ethernet_relay_python.py (manufacturer example).
RELAY_ETH_CONFIG = {
    "type":         "ethernet",
    "driver":       "RELAY32ETHRL00",
    "name":         "MAIN_MATRIX_ETH",
    "ip":           "169.254.1.1",      # lab default -- Numato factory IP (link-local)
    "port":         23,                 # default Numato Telnet port
    "username":     "admin",            # Telnet login (factory default: admin/admin)
    "user":         "admin",            # legacy alias, kept for compat -- same value
    "password":     "admin",
    "timeout":      5.0,
    "num_channels":  32,                # physical relay count on the 32-ch module
    "channel_count": 32,                # alias -- same value, used by the matrix scan test
}

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

RELAY_ETH_CONFIGS = {
    RELAY_ETH_CONFIG["name"]: RELAY_ETH_CONFIG,
}
