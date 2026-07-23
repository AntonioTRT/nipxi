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
# PXI chassis inventory -- single source of truth for every PXI-slot-resident
# device. Confirmed against the real rack (NI-MAX detection), not assumed --
# see the "cards" list in flowcharts/vi plan.md for what was originally
# anticipated, and the per-slot "validation_notes" below for where reality
# differs from that plan.
#
# This dict is the ONLY place a PXI resource string/model is hand-authored.
# SMU_ASSIGNMENTS / DAQ_CONFIG(S) / DMM_CONFIG(S) below are DERIVED from it
# by category, so there is exactly one place to edit when hardware changes,
# not several that could silently drift apart.
#
# Required fields on every entry (per-slot):
#   slot              int, matches the dict key (kept on the entry too so a
#                     single entry is self-contained if ever copied out)
#   resource          NI-MAX resource string, e.g. "PXI1Slot5"
#   model             real NI model string, e.g. "PXIe-4141"
#   nickname          human-readable label reflecting this device's intended
#                     ROLE in NIPXI, not just its model number
#   driver_family     the NI driver package that talks to this device
#                     ("nidcpower" | "nidaqmx" | "nidmm" | "niswitch")
#   category          "smu" | "daq" | "dmm" | "switch" | "temperature" --
#                     used below to derive the per-type config dicts
#   role              one-line description of what this device is for
#   enabled           bool -- whether this device currently participates in
#                     the active NIPXI hardware pipeline (HardwareManager /
#                     Hardware Discovery / test.py device pickers). A card
#                     can be physically present and real (enabled=False just
#                     means "not yet wired into a code path", never "not
#                     real").
#   validation_notes  optional -- discrepancies against the original VI plan,
#                     or anything a reader should not assume without checking
# =============================================================================

PXI_SLOTS = {
    2: {
        "slot":            2,
        "resource":        "PXI1Slot2",
        "model":           "PXIe-6363",
        "nickname":        "MAIN_DAQ",
        "driver_family":   "nidaqmx",
        "category":        "daq",
        "role":            "Primary DAQ -- battery voltage/current/NTC acquisition "
                            "across all channels (hardware/daq.py::DAQ).",
        "enabled":         True,
        "sample_rate_hz":  1.0,
        "voltage_range_v": 5.0,   # +/-5 V input range
        "validation_notes": "Confirmed match to the original VI plan "
                             "(flowcharts/vi plan.md: '6363 daq').",
    },
    3: {
        "slot":            3,
        "resource":        "PXI1Slot3",
        "model":           "PXI-4065",
        "nickname":        "MAIN_DMM",
        "driver_family":   "nidmm",
        "category":        "dmm",
        "role":            "Independent precision voltage verification, separate "
                            "from the DAQ's own reading (hardware/dmm.py::DMM).",
        "enabled":         True,
        "function":        "DC_VOLTS",
        "range_v":         10.0,
        "validation_notes": "Confirmed match to the original VI plan "
                             "(flowcharts/vi plan.md: '4065 dmm').",
    },
    5: {
        "slot":          5,
        "resource":      "PXI1Slot5",
        "model":         "PXIe-4141",
        "nickname":      "PRIMARY_SMU",
        "driver_family": "nidcpower",
        "category":      "smu",
        "role":          "Primary Source Measure Unit -- drives the active "
                          "charge/discharge cycle for all 8 battery channels "
                          "(hardware/smu.py::SMU). The one SMU HardwareManager "
                          "actually connects and cycles today.",
        "enabled":       True,
        "channels":      list(range(1, 9)),   # all 8 channels multiplexed through this SMU
        "validation_notes": "The original VI plan (flowcharts/vi plan.md: '4140 smu') "
                             "anticipated an NI-4140 in this slot/role. Real rack "
                             "hardware is a PXIe-4141 instead -- a functionally "
                             "compatible 4-quadrant precision SMU, not a discrepancy "
                             "that changes the architecture, just the model string. "
                             "NOTE: this card's 4-quadrant (bipolar) capability is not "
                             "used by NIPXI -- charging sources voltage+current, "
                             "discharging sources voltage and SINKS current (never a "
                             "negative source voltage); see docs/architecture.md "
                             "Section 12.6. Bipolar capability is also NOT documented "
                             "for HIGH_POWER_SMU (PXIe-4139) or AUX_SMU_1/AUX_SMU_2 "
                             "(PXI-4130) below -- do not assume it without checking "
                             "their datasheets first.",
    },
    6: {
        "slot":          6,
        "resource":      "PXI1Slot6",
        "model":         "PXIe-4139",
        "nickname":      "HIGH_POWER_SMU",
        "driver_family": "nidcpower",
        "category":      "smu",
        "role":          "High-power/high-current single-channel SMU -- candidate "
                          "for higher-capacity cells or a dedicated high-current "
                          "channel bank. Not yet assigned to any battery channel.",
        "enabled":       True,
        "channels":      [],
        "validation_notes": "Confirmed match to the original VI plan "
                             "(flowcharts/vi plan.md: '4139 smu'). Present, "
                             "connectable, and individually testable via test.py, "
                             "but not yet wired into BatteryTestSequence's "
                             "single-SMU channel assignment -- multi-SMU channel "
                             "assignment is a future scaling task, not implemented.",
    },
    7: {
        "slot":          7,
        "resource":      "PXI1Slot7",
        "model":         "PXI-4130",
        "nickname":      "AUX_SMU_1",
        "driver_family": "nidcpower",
        "category":      "smu",
        "role":          "Auxiliary 2-channel SMU bank -- candidate for future "
                          "channel-count scaling. Not yet assigned to any battery "
                          "channel.",
        "enabled":       True,
        "channels":      [],
        "validation_notes": "Confirmed match to one of the two identical "
                             "'4130 smu' entries in the original VI plan "
                             "(flowcharts/vi plan.md). Same scaling note as "
                             "HIGH_POWER_SMU above.",
    },
    8: {
        "slot":          8,
        "resource":      "PXI1Slot8",
        "model":         "PXI-4130",
        "nickname":      "AUX_SMU_2",
        "driver_family": "nidcpower",
        "category":      "smu",
        "role":          "Auxiliary 2-channel SMU bank -- candidate for future "
                          "channel-count scaling. Not yet assigned to any battery "
                          "channel.",
        "enabled":       True,
        "channels":      [],
        "validation_notes": "Confirmed match to the second identical '4130 smu' "
                             "entry in the original VI plan (flowcharts/vi plan.md). "
                             "Same scaling note as AUX_SMU_1 above.",
    },
    11: {
        "slot":          11,
        "resource":      "PXI1Slot11",
        "model":         "PXIe-2569",
        "nickname":      "CHASSIS_RELAY_MATRIX",
        "driver_family": "niswitch",
        "category":      "switch",
        "role":          "PXI-resident electromechanical relay/switch matrix -- "
                          "present in the chassis but NOT the active relay driver.",
        "enabled":       False,
        "validation_notes": "The original VI plan (flowcharts/vi plan.md: "
                             "'2569 relay') anticipated this card as THE relay "
                             "matrix. The implemented production relay path is "
                             "instead the Numato Lab 32-Channel Ethernet Relay "
                             "Module (see NUMATO_RELAY_MATRIX_CONFIG below), which "
                             "is what hardware/relay_eth.py actually drives. This "
                             "card has no driver class in this codebase today -- "
                             "repurposing it would need a future niswitch-based "
                             "hardware/switch.py, not implemented here.",
    },
    15: {
        "slot":            15,
        "resource":        "PXI1Slot15",
        "model":           "PXIe-4353",
        "nickname":        "TEMP_MODULE",
        "driver_family":   "nidaqmx",   # NI-4353 is an NI-DAQmx universal TC/RTD input module
        "category":        "temperature",
        "role":            "Per-channel battery temperature acquisition "
                            "(thermocouple/RTD). Terminal block TB-4353 (connector "
                            "0) attached at this slot.",
        "enabled":         False,
        "terminal_block":  "TB-4353",
        "terminal_block_connector": 0,
        "validation_notes": "Not present in the original VI plan equipment list -- "
                             "a new finding from the real rack inventory. This is "
                             "the most likely real hardware source for the "
                             "per-channel temperature readings that "
                             "charge_cycle.py/discharge_cycle.py currently stub as "
                             "t_c = None (see the 'TODO: get temperature from NTC "
                             "channel' comments there). No driver class exists yet "
                             "(would be a future NI-DAQmx-based hardware/"
                             "temperature.py) -- not wired into any code path today.",
    },
    17: {
        "slot":            17,
        "resource":        "PXI1Slot17",
        "model":           "PXIe-6368",
        "nickname":        "EXPANSION_DAQ",
        "driver_family":   "nidaqmx",
        "category":        "daq",
        "role":            "Additional high-speed multifunction DAQ -- candidate "
                            "for future channel-count scaling or higher sample "
                            "rates. Not wired into HardwareManager today (which "
                            "uses MAIN_DAQ only).",
        "enabled":         False,
        "sample_rate_hz":  1.0,
        "voltage_range_v": 5.0,
        "validation_notes": "Not present in the original VI plan equipment list -- "
                             "a new finding from the real rack inventory.",
    },
    18: {
        "slot":            18,
        "resource":        "PXI1Slot18",
        "model":           "PXIe-6365",
        "nickname":        "PRECISION_DAQ",
        "driver_family":   "nidaqmx",
        "category":        "daq",
        "role":            "Additional multifunction DAQ (16-bit precision "
                            "variant) -- candidate for future expansion. Not "
                            "wired into HardwareManager today (which uses "
                            "MAIN_DAQ only).",
        "enabled":         False,
        "sample_rate_hz":  1.0,
        "voltage_range_v": 5.0,
        "validation_notes": "Not present in the original VI plan equipment list -- "
                             "a new finding from the real rack inventory.",
    },
}

# =============================================================================
# GPIB instruments -- NOT PXI-slot devices (GPIB0 is a separate NI-488.2
# interface, not a chassis slot), kept in its own section rather than folded
# into PXI_SLOTS for that reason. Detected: an NI-488.2 interface enumerated
# as GPIB0, with no specific instrument model confirmed at that address yet.
# =============================================================================

GPIB_INSTRUMENTS = {
    "GPIB_INSTRUMENT_1": {
        "interface":     "GPIB0",
        "driver_family": "NI-488.2",
        "model":         None,   # unconfirmed -- see validation_notes
        "nickname":      "UNCONFIRMED_GPIB_INSTRUMENT",
        "role":          "Likely candidate for the 'Programmable Electronic Load' "
                          "or 'Programmable Power Supply' documented in "
                          "equipment_Requirement.md -- not confirmed.",
        "enabled":       False,
        "validation_notes": "NI-488.2 / GPIB0 was detected in the rack, but no "
                             "specific instrument model was identified at this "
                             "address. Confirm the connected instrument (e.g. via "
                             "an NI-MAX GPIB scan / *IDN? query) before enabling. "
                             "No driver class exists yet for a GPIB electronic "
                             "load or power supply in this codebase.",
    },
}

# =============================================================================
# Derived per-type config dicts -- built FROM PXI_SLOTS by category, not
# hand-authored, so there is exactly one place (PXI_SLOTS above) to edit when
# hardware changes. Shape/keys match exactly what hardware/smu.py,
# hardware/daq.py, hardware/dmm.py, and test.py already expect -- this
# refactor changes WHERE the data lives, not what any consumer reads.
# =============================================================================

def _slots_by_category(category: str) -> dict:
    """PXI_SLOTS entries matching `category`, in PXI_SLOTS' own (slot-number)
    order -- e.g. category="smu" yields PRIMARY_SMU (slot 5) before
    HIGH_POWER_SMU (slot 6), so `next(iter(...))` below always resolves to
    the intended primary device, not an arbitrary one."""
    return {slot: cfg for slot, cfg in PXI_SLOTS.items() if cfg["category"] == category}


# SMU (Source Measure Unit) assignment per channel. Every SMU-category slot
# is listed here (not just the primary) so Hardware Discovery, startup device
# validation, and test.py's device picker can see and individually test every
# physical SMU in the rack. HardwareManager itself still only ever connects
# ONE SMU for the active battery test sequence -- `next(iter(SMU_ASSIGNMENTS
# .values()))`, i.e. PRIMARY_SMU (slot 5) -- multi-SMU channel assignment is
# a future scaling task, not implemented by this config refactor.
SMU_ASSIGNMENTS = {
    cfg["nickname"]: {
        "type":     "PXIe",
        "resource": cfg["resource"],
        "model":    cfg["model"],
        "channels": cfg.get("channels", []),
    }
    for cfg in _slots_by_category("smu").values()
}

# DAQ cards. DAQ_CONFIG (singular) is the one HardwareManager actually
# connects by default; DAQ_CONFIGS enumerates every DAQ-category slot for
# Hardware Discovery / individual testing via test.py.
DAQ_CONFIGS = {
    cfg["nickname"]: {
        "type":            "PXIe",
        "resource":        cfg["resource"],
        "model":           cfg["model"],
        "sample_rate_hz":  cfg.get("sample_rate_hz", 1.0),
        "voltage_range_v": cfg.get("voltage_range_v", 5.0),
    }
    for cfg in _slots_by_category("daq").values()
}
DAQ_CONFIG = DAQ_CONFIGS["MAIN_DAQ"]

# DMM cards. Same pattern as DAQ above.
DMM_CONFIGS = {
    cfg["nickname"]: {
        "type":     "PXIe",
        "resource": cfg["resource"],
        "model":    cfg["model"],
        "function": cfg.get("function", "DC_VOLTS"),
        "range_v":  cfg.get("range_v", 10.0),
    }
    for cfg in _slots_by_category("dmm").values()
}
DMM_CONFIG = DMM_CONFIGS["MAIN_DMM"]

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
#
# daq_voltage_ch/daq_current_ch/daq_ntc_ch use "Dev1" as the NI-MAX device
# alias placeholder for MAIN_DAQ (PXI_SLOTS[2], resource "PXI1Slot2") --
# "Dev1" is whatever alias NI-MAX actually assigns that resource on this
# machine, not necessarily literally "Dev1". Confirm and update these
# strings against NI-MAX during hardware validation; PXI_SLOTS[2]["resource"]
# is the authoritative resource string for MAIN_DAQ itself.
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

# Numato Relay Matrices -- Ethernet (Numato Lab 32 Channel Ethernet Relay Module)
# Set "type": "ethernet" to use NumatoRelayMatrix instead of SerialRelay.
# RelayFactory.create(...) on any entry here will return a NumatoRelayMatrix
# instance ("ethernet" names the transport interface, same as "serial" does --
# the concrete driver is specifically for this Numato hardware, not a generic
# vendor-neutral Ethernet relay). NOT a PXI-slot device -- see PXI_SLOTS[11]
# ("CHASSIS_RELAY_MATRIX") for the PXI-resident relay/switch card that is
# physically present in the rack but not the active relay driver.
# Reference protocol: utils/ethernet_relay_python.py (manufacturer example).
#
# Finalized NIPXI network plan -- static IPs on the link-local Numato subnet,
# DHCP disabled on both devices. 169.254.1.1 was the Numato factory default
# (link-local, both units ship identical) -- no longer used now that each
# unit has its own reserved static address.
#
# Naming convention: each unit is named after its static IP's last octet
# (MATRIX_NUMATO_<octet>) so hardware ID, troubleshooting, rack labeling, and
# maintenance can all key off the same identifier -- no "MAIN"/"AUX" role
# implication, since both units are identical, currently-deployed hardware.
ETHERNET_DEVICES = {
    # Numato Relay Matrix at 169.254.1.201
    "MATRIX_NUMATO_201": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_201",
        "ip":            "169.254.1.201",
        "port":          23,                 # Numato Telnet port
        "username":      "admin",            # Telnet login (admin/admin, hardware-side)
        "user":          "admin",            # legacy alias, kept for compat -- same value
        "password":      "admin",
        "timeout":       5.0,
        # Settings.RELAY_COUNT is the single source of truth for relay count --
        # never hardcode 32 here or in any relay test.
        "num_channels":  Settings.RELAY_COUNT,
        "channel_count": Settings.RELAY_COUNT,  # alias -- same value, used by the matrix scan test
    },
    # Numato Relay Matrix at 169.254.1.202
    "MATRIX_NUMATO_202": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_202",
        "ip":            "169.254.1.202",
        "port":          23,
        "username":      "admin",
        "user":          "admin",
        "password":      "admin",
        "timeout":       5.0,
        "num_channels":  Settings.RELAY_COUNT,
        "channel_count": Settings.RELAY_COUNT,
    },
}

# Legacy compatibility -- old role-based names, kept only in case other code
# or external scripts still import these directly.
MAIN_MATRIX_ETH = ETHERNET_DEVICES["MATRIX_NUMATO_201"]
AUX_MATRIX_ETH_1 = ETHERNET_DEVICES["MATRIX_NUMATO_202"]

# Backward-compat alias -- primary Numato unit, previously the sole config dict.
NUMATO_RELAY_MATRIX_CONFIG = ETHERNET_DEVICES["MATRIX_NUMATO_201"]

# Backward-compat alias -- this dict was previously named RELAY_ETH_CONFIG.
RELAY_ETH_CONFIG = NUMATO_RELAY_MATRIX_CONFIG

# =============================================================================
# Device enumeration -- name -> config, same model as SMU_ASSIGNMENTS.
# Used by test.py to let the user pick which instance to test when more than
# one device of a given type is configured. Add entries here as hardware is
# added; nothing else needs to change.
# =============================================================================

RELAY_SERIAL_CONFIGS = {
    RELAY_CONFIG["name"]: RELAY_CONFIG,
}

# Every Numato Relay Matrix device, name -> config. Hardware Discovery,
# startup device validation, RelayEthernetTest, and every test_relay_*()
# function all iterate this dict -- add a new Numato unit to ETHERNET_DEVICES
# above and every one of those automatically covers it too, with no
# per-device code anywhere.
NUMATO_RELAY_MATRIX_CONFIGS = dict(ETHERNET_DEVICES)

# Backward-compat alias -- this dict was previously named RELAY_ETH_CONFIGS.
RELAY_ETH_CONFIGS = NUMATO_RELAY_MATRIX_CONFIGS
