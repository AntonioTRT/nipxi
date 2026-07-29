"""
Device-level configuration and channel mapping.
Maps physical hardware to logical battery channels.
"""

import os
import re
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
        "smu_channel":   "0",   # NI-DCPower channel name -- single-channel card, always "0"
        "channels_per_card": 1,   # physical NI-DCPower channel count on this card -- drives
                                  # device_display_name()'s "-Ch<n>" suffix (only shown for
                                  # multi-channel cards, where which channel matters)
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
        "smu_channel":   "0",   # NI-DCPower channel name -- single-channel card, always "0"
        "channels_per_card": 1,
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
        # NI-DCPower channel name -- PXI-4130 has two channels ("0", "1"); this
        # unit's confirmed hardware wiring uses channel "1". Opening the
        # session scoped to exactly this channel (hardware/smu.py::connect())
        # avoids the "single channel must be specified" NI-DCPower error that
        # an ambiguous multi-channel session raises on any repeated-capability
        # property/method (voltage_level, output_enabled, measure(), etc.).
        "smu_channel":   "1",
        "channels_per_card": 2,   # physical NI-DCPower channel count on this card
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
        # NI-DCPower channel name -- confirmed on physical hardware during
        # rack validation: this unit is wired to channel 1, same as AUX_SMU_1.
        "smu_channel":   "1",
        "channels_per_card": 2,   # physical NI-DCPower channel count on this card
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
    # -------------------------------------------------------------------
    # Hardware cleanup (post-Milestone-II Phase 3 review): TEMP_MODULE
    # (slot 15), EXPANSION_DAQ (slot 17), and PRECISION_DAQ (slot 18) are
    # NOT physically installed in this PXI system. Commented out --
    # deliberately not deleted -- so they disappear from Hardware
    # Discovery/Test DAQ/Test Temperature Module's device lists (both
    # already handle an empty category gracefully: "(no ... devices
    # configured)", not a crash) instead of reporting [FAIL] for hardware
    # that was never there. Re-enable by uncommenting if/when this
    # hardware is actually installed -- nothing else needs to change
    # (SMU_ASSIGNMENTS/DAQ_CONFIGS/DMM_CONFIGS below are all *derived*
    # from PXI_SLOTS, so removing an entry here automatically removes it
    # from every downstream dict too). See docs/CONFIGURATION.md
    # "Installed vs. Disabled Hardware" for the current inventory.
    #
    # 15: {
    #     "slot":            15,
    #     "resource":        "PXI1Slot15",
    #     "model":           "PXIe-4353",
    #     "nickname":        "TEMP_MODULE",
    #     "driver_family":   "nidaqmx",   # NI-4353 is an NI-DAQmx universal TC/RTD input module
    #     "category":        "temperature",
    #     "role":            "Per-channel battery temperature acquisition "
    #                         "(thermocouple/RTD). Terminal block TB-4353 (connector "
    #                         "0) attached at this slot.",
    #     "enabled":         False,
    #     "terminal_block":  "TB-4353",
    #     "terminal_block_connector": 0,
    #     "validation_notes": "Not present in the original VI plan equipment list -- "
    #                          "a new finding from the real rack inventory. This is "
    #                          "the most likely real hardware source for the "
    #                          "per-channel temperature readings that "
    #                          "charge_cycle.py/discharge_cycle.py currently stub as "
    #                          "t_c = None (see the 'TODO: get temperature from NTC "
    #                          "channel' comments there). No driver class exists yet "
    #                          "(would be a future NI-DAQmx-based hardware/"
    #                          "temperature.py) -- not wired into any code path today.",
    # },
    # 17: {
    #     "slot":            17,
    #     "resource":        "PXI1Slot17",
    #     "model":           "PXIe-6368",
    #     "nickname":        "EXPANSION_DAQ",
    #     "driver_family":   "nidaqmx",
    #     "category":        "daq",
    #     "role":            "Additional high-speed multifunction DAQ -- candidate "
    #                         "for future channel-count scaling or higher sample "
    #                         "rates. Not wired into HardwareManager today (which "
    #                         "uses MAIN_DAQ only).",
    #     "enabled":         False,
    #     "sample_rate_hz":  1.0,
    #     "voltage_range_v": 5.0,
    #     "validation_notes": "Not present in the original VI plan equipment list -- "
    #                          "a new finding from the real rack inventory.",
    # },
    # 18: {
    #     "slot":            18,
    #     "resource":        "PXI1Slot18",
    #     "model":           "PXIe-6365",
    #     "nickname":        "PRECISION_DAQ",
    #     "driver_family":   "nidaqmx",
    #     "category":        "daq",
    #     "role":            "Additional multifunction DAQ (16-bit precision "
    #                         "variant) -- candidate for future expansion. Not "
    #                         "wired into HardwareManager today (which uses "
    #                         "MAIN_DAQ only).",
    #     "enabled":         False,
    #     "sample_rate_hz":  1.0,
    #     "voltage_range_v": 5.0,
    #     "validation_notes": "Not present in the original VI plan equipment list -- "
    #                          "a new finding from the real rack inventory.",
    # },
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
        "type":              "PXIe",
        "slot":              cfg["slot"],
        "resource":          cfg["resource"],
        "model":             cfg["model"],
        "channels":          cfg.get("channels", []),
        "smu_channel":       cfg.get("smu_channel", "0"),
        "channels_per_card": cfg.get("channels_per_card", 1),
    }
    for cfg in _slots_by_category("smu").values()
}

# DAQ cards. DAQ_CONFIG (singular) is the one HardwareManager actually
# connects by default; DAQ_CONFIGS enumerates every DAQ-category slot for
# Hardware Discovery / individual testing via test.py.
DAQ_CONFIGS = {
    cfg["nickname"]: {
        "type":            "PXIe",
        "slot":            cfg["slot"],
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
        "slot":     cfg["slot"],
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
# Operator-selectable battery types: HUB and SB. Battery selection is
# explicit and operator-controlled (see test.py's battery-type selection
# prompt, Monitor Battery workflow) -- BATTERY_CHANNELS below deliberately
# does NOT reference a battery type; it is physical wiring information only.
#
# CONFIRMED vs ASSUMED -- read before relying on these for safety enforcement:
#   CONFIRMED (from the source spec):
#     - nominal_voltage_v (3.7 V, both types)
#     - capacity_ah (HUB = 1.05 Ah / 1050 mAh, SB = 0.16 Ah / 160 mAh)
#   ASSUMED / NOT YET CONFIRMED against a real datasheet (marked inline
#   "unconfirmed placeholder"/"unconfirmed"):
#     - chemistry, form_factor
#     - voltage_max_v, voltage_min_v -- assumed standard Li-ion window
#     - max_charge_current_a, max_discharge_current_a -- assumed 0.5C/1C
#       ratios applied to capacity_ah, not measured/specified
#     - max_temp_c -- assumed standard Li-ion ceiling
#   These assumed values are placeholders derived from the two confirmed
#   values above using the same standard Li-ion voltage window and 0.5C/1C
#   charge/discharge ratios the previous generic entry used. They must be
#   confirmed against the real BLOSS Hub/SB datasheet before being treated
#   as production limits -- until then, the global Settings.BAT_* limits
#   (config/settings.py) remain the enforced safety ceiling regardless of
#   what's listed here (see docs/architecture.md "Operational Limit
#   Resolution"). Do not silently treat any "unconfirmed" value as verified.
BATTERY_CONFIGS = {
    "HUB": {
        "chemistry":               "Li-ion",   # unconfirmed -- inferred from nominal_voltage_v
        "form_factor":             None,       # unconfirmed
        "nominal_voltage_v":       3.7,        # confirmed
        "voltage_max_v":           4.2,        # unconfirmed placeholder -- assumed standard Li-ion window
        "voltage_min_v":           3.0,        # unconfirmed placeholder -- assumed standard Li-ion window
        "capacity_ah":             1.05,       # confirmed -- 1050 mAh
        "max_charge_current_a":    0.525,      # unconfirmed placeholder -- assumed 0.5C
        "max_discharge_current_a": 1.05,       # unconfirmed placeholder -- assumed 1C
        "max_temp_c":              45.0,       # unconfirmed placeholder -- assumed standard Li-ion ceiling
    },
    "SB": {
        "chemistry":               "Li-ion",   # unconfirmed -- inferred from nominal_voltage_v
        "form_factor":             None,       # unconfirmed
        "nominal_voltage_v":       3.7,        # confirmed
        "voltage_max_v":           4.2,        # unconfirmed placeholder -- assumed standard Li-ion window
        "voltage_min_v":           3.0,        # unconfirmed placeholder -- assumed standard Li-ion window
        "capacity_ah":             0.16,       # confirmed -- 160 mAh
        "max_charge_current_a":    0.08,       # unconfirmed placeholder -- assumed 0.5C
        "max_discharge_current_a": 0.16,       # unconfirmed placeholder -- assumed 1C
        "max_temp_c":              45.0,       # unconfirmed placeholder -- assumed standard Li-ion ceiling
    },
}

# Battery channel definitions -- PHYSICAL WIRING ONLY.
# Key: global battery position (1-based, see BATTERY_GROUPS below for how
# a position maps to a group + relay matrix). Deliberately does NOT
# reference a battery type -- which battery is being tested is an explicit
# operator choice at run start (test.py's battery-type selection prompt),
# never inferred from which position/relay was picked. See docs/
# architecture.md "Battery Group / Position Architecture".
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
        "enabled":        True,
    }
    for i in range(1, 9)
}

# =============================================================================
# Battery groups -- relay routing architecture.
#
# Battery groups are NOT a purely logical grouping: each group of
# Settings.GROUP_SIZE (8) battery positions corresponds to a distinct relay
# routing section, physically one Ethernet relay matrix per group. Group A
# (positions 1-8) is the only group with real hardware today -- MATRIX_NUMATO_201
# (see ETHERNET_DEVICES above). Group B is pre-wired to MATRIX_NUMATO_202
# (already configured, Milestone 1) but not yet enabled for battery routing.
# Groups C/D are placeholders for future relay matrices that don't exist yet
# (`relay_matrix: None`) -- present so the operator workflow and Relay
# Functional Validation's group-scope menu are future-proof without a later
# redesign; selecting a disabled/unconfigured group reports "no relays
# configured for this group" (same graceful pattern as the hardware-cleanup
# disabled-device entries above), never a crash.
#
# position_start/position_end are GLOBAL battery position numbers (matching
# BATTERY_CHANNELS' keys); relay_offset is subtracted from a global position
# to get the 1-based position *within* the group (e.g. global position 11
# in Group B -> position_start=9 -> in-group position 11-9+1 = 3).
# =============================================================================
# "smu"/"dmm"/"daq" are name-keys into SMU_ASSIGNMENTS/DMM_CONFIGS/DAQ_CONFIGS
# below -- same reference-by-name pattern "relay_matrix" already uses against
# ETHERNET_DEVICES, not a second copy of any hardware config. None means "no
# device of this role assigned to this group yet" (same meaning as
# "relay_matrix": None for groups C/D): hardware_for_group() below returns
# None for that role rather than guessing a default, and callers must refuse
# to activate hardware for a role that resolves to None. Only MAIN_DMM/
# MAIN_DAQ physically exist today, so every group currently shares them --
# multiple DMMs/DAQs is a future scaling step, same as multiple SMUs.
BATTERY_GROUPS = {
    "A": {
        "relay_matrix":   "MATRIX_NUMATO_201",
        "position_start": 1,
        "position_end":   8,
        "enabled":        True,
        "smu":            "PRIMARY_SMU",
        "dmm":            "MAIN_DMM",
        "daq":            "MAIN_DAQ",
    },
    "B": {
        "relay_matrix":   "MATRIX_NUMATO_202",
        "position_start": 9,
        "position_end":   16,
        "enabled":        False,   # matrix exists in config, not yet wired for battery routing
        "smu":            None,    # no SMU assigned to this group yet
        "dmm":            "MAIN_DMM",
        "daq":            "MAIN_DAQ",
    },
    "C": {
        "relay_matrix":   None,    # no relay matrix assigned yet
        "position_start": 17,
        "position_end":   24,
        "enabled":        False,
        "smu":            None,
        "dmm":            None,
        "daq":            None,
    },
    "D": {
        "relay_matrix":   None,    # no relay matrix assigned yet
        "position_start": 25,
        "position_end":   32,
        "enabled":        False,
        "smu":            None,
        "dmm":            None,
        "daq":            None,
    },
}


def resolve_group_position(group: str, position_in_group: int) -> int:
    """
    Convert (group, position-within-group) -- what the operator selects,
    e.g. "Group A, Position 3" -- to the global battery position number
    BATTERY_CHANNELS is keyed by. Raises KeyError for an unknown group,
    ValueError if position_in_group is out of range for that group.
    """
    grp = BATTERY_GROUPS[group]
    size = grp["position_end"] - grp["position_start"] + 1
    if not (1 <= position_in_group <= size):
        raise ValueError(
            f"Position {position_in_group} out of range for group {group!r} "
            f"(1..{size})"
        )
    return grp["position_start"] + position_in_group - 1


def group_for_position(global_position: int):
    """Return the group name a global battery position belongs to, or None."""
    for name, grp in BATTERY_GROUPS.items():
        if grp["position_start"] <= global_position <= grp["position_end"]:
            return name
    return None


def hardware_for_group(group: str) -> dict:
    """
    Centralized hardware-resolution model: Group -> Relay Matrix -> SMU ->
    DMM -> DAQ. The single place every workflow (Monitor Battery, Monitor
    Battery Scan, Charge/Discharge/Cycle Battery, the future Workflow
    Simulator) resolves which physical devices a group uses -- no workflow
    should look up SMU_ASSIGNMENTS/DMM_CONFIGS/DAQ_CONFIGS/ETHERNET_DEVICES
    directly or pick a device positionally (e.g. next(iter(...))).

    Returns a dict with one "<role>_name"/"<role>_cfg" pair per role
    (relay_matrix/smu/dmm/daq). A role's cfg is None if BATTERY_GROUPS[group]
    has no device assigned for it (e.g. Group C/D today) -- this is never
    silently substituted with another device; callers must check for None
    and refuse to activate hardware for that role.

    Raises KeyError if `group` is not a key in BATTERY_GROUPS.
    """
    grp = BATTERY_GROUPS[group]
    return {
        "relay_matrix_name": grp["relay_matrix"],
        "relay_matrix_cfg":  ETHERNET_DEVICES.get(grp["relay_matrix"]),
        "smu_name": grp["smu"],
        "smu_cfg":  SMU_ASSIGNMENTS.get(grp["smu"]),
        "dmm_name": grp["dmm"],
        "dmm_cfg":  DMM_CONFIGS.get(grp["dmm"]),
        "daq_name": grp["daq"],
        "daq_cfg":  DAQ_CONFIGS.get(grp["daq"]),
    }

# Relay matrix -- serial (COM port) -- MAIN_MATRIX / COM13.
#
# Hardware cleanup (post-Milestone-II Phase 3 review): this serial relay
# controller is NOT physically installed/connected -- commented out,
# deliberately not deleted, so it disappears from Hardware Discovery's
# "Relay (Serial)" group and Startup Device Validation's registry instead
# of reporting [FAIL] for hardware that isn't there. Production is, and
# has always been, the Numato Ethernet relay (ETHERNET_DEVICES/
# NUMATO_RELAY_MATRIX_CONFIG below) -- this serial path was diagnostic-only
# even when present (see hardware/relay_serial.py's module docstring).
#
# IMPORTANT -- do not uncomment RELAY_CONFIG alone: RELAY_SERIAL_CONFIGS
# below references RELAY_CONFIG by name unconditionally at import time.
# Both must be restored together, or every entry point that imports
# config.devices (main.py, test.py, everything) fails immediately with
# NameError. See docs/CONFIGURATION.md "Installed vs. Disabled Hardware".
#
# RELAY_CONFIG = {
#     "type":         "serial",
#     "name":         "MAIN_MATRIX",
#     "port":         "COM13",
#     "baud_rate":    9600,
#     "timeout":      2.0,
#     "num_channels": 8,
#     # TODO: replace these with the real protocol strings from your relay controller datasheet
#     "command_open":  "OPEN {ch}\r\n",
#     "command_close": "CLOSE {ch}\r\n",
#     "command_query": "QUERY {ch}\r\n",
# }

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
# DHCP disabled on both devices. 169.254.1.1 wa0s the Numato factory default
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

# Empty while RELAY_CONFIG (MAIN_MATRIX, COM13) is commented out above --
# see the "hardware cleanup" note there. Hardware Discovery's "Relay
# (Serial)" group and Startup Device Validation both already handle an
# empty dict here gracefully (report "no devices configured" / skip,
# never crash). Restore to `{RELAY_CONFIG["name"]: RELAY_CONFIG}` only
# when RELAY_CONFIG itself is uncommented again.
RELAY_SERIAL_CONFIGS = {}

# Every Numato Relay Matrix device, name -> config. Hardware Discovery,
# startup device validation, RelayEthernetTest, and every test_relay_*()
# function all iterate this dict -- add a new Numato unit to ETHERNET_DEVICES
# above and every one of those automatically covers it too, with no
# per-device code anywhere.
NUMATO_RELAY_MATRIX_CONFIGS = dict(ETHERNET_DEVICES)

# Backward-compat alias -- this dict was previously named RELAY_ETH_CONFIGS.
RELAY_ETH_CONFIGS = NUMATO_RELAY_MATRIX_CONFIGS

# =============================================================================
# Operator-facing display names -- config-driven, derived from each device's
# own cfg dict (model/slot/ip/channel), never a hand-authored string per
# device. This is purely a presentation label for menus/logs/test output
# (test.py) -- internal identifiers (PXI_SLOTS/SMU_ASSIGNMENTS/ETHERNET_DEVICES
# nicknames and dict keys, "resource" strings, etc.) are completely unaffected
# and remain exactly as used elsewhere in the codebase today.
#
# Examples this produces from the current inventory:
#   PRIMARY_SMU      (PXIe-4141, slot 5)            -> "NI4141-Slot5"
#   HIGH_POWER_SMU    (PXIe-4139, slot 6)            -> "NI4139-Slot6"
#   AUX_SMU_1        (PXI-4130, slot 7, channel "1") -> "NI4130-Slot7-Ch1"
#   AUX_SMU_2        (PXI-4130, slot 8, channel "1") -> "NI4130-Slot8-Ch1"
#   MAIN_DMM         (PXI-4065, slot 3)              -> "NI4065-Slot3"
#   TEMP_MODULE      (PXIe-4353, slot 15)            -> "NI4353-Slot15"
#   CHASSIS_RELAY_MATRIX (PXIe-2569, slot 11)        -> "RelayMatrix-Slot11"
#   MATRIX_NUMATO_201 (ip 169.254.1.201)             -> "Numato-169.254.1.201"
# =============================================================================

def _model_number(model: str) -> str:
    """Strip the "PXIe-"/"PXI-" family prefix, e.g. "PXIe-4141" -> "4141".
    Falls back to the raw model string if it doesn't match that pattern."""
    return re.sub(r"^PXIe?-", "", model or "")


def device_display_name(cfg: dict) -> str:
    """
    Build an operator-facing hardware-identifying display name from a device
    cfg dict (a PXI_SLOTS entry, or one of SMU_ASSIGNMENTS/DAQ_CONFIGS/
    DMM_CONFIGS/ETHERNET_DEVICES' derived dicts) -- never a hardcoded string
    per device. Presentation only: does not affect any internal identifier
    (dict keys, "resource"/"nickname"/"name" fields) used elsewhere.
    """
    ip = cfg.get("ip")
    if ip:
        return f"Numato-{ip}"

    slot  = cfg.get("slot")
    model = cfg.get("model")

    if slot is None or not model:
        # Not a PXI-slot device with a known model (e.g. GPIB, serial relay) --
        # fall back to whatever identifier the caller already has.
        return cfg.get("nickname") or cfg.get("name") or "UNKNOWN_DEVICE"

    base = f"NI{_model_number(model)}-Slot{slot}"

    if cfg.get("category") == "switch":
        return f"RelayMatrix-Slot{slot}"

    # "channels_per_card" is only ever present on SMU config dicts (PXI_SLOTS
    # entries and SMU_ASSIGNMENTS, which doesn't carry "category") -- checking
    # for the key itself (rather than category == "smu") means this works
    # for both cfg shapes without needing "category" threaded through
    # SMU_ASSIGNMENTS too.
    if cfg.get("channels_per_card", 1) > 1:
        return f"{base}-Ch{cfg.get('smu_channel', '0')}"

    return base


def find_config_name(configs: dict, cfg: dict):
    """
    Reverse-lookup a device's dict key (e.g. "PRIMARY_SMU", "MAIN_DMM") given
    the enumeration dict it came from (SMU_ASSIGNMENTS/DAQ_CONFIGS/DMM_CONFIGS)
    and the resolved cfg dict itself (identity comparison, not equality --
    two distinct entries could otherwise have identical field values). Used
    for hardware traceability logging (see docs/architecture.md "Hardware
    Identity Traceability") so the run_summary/event_log name always matches
    the SAME config/devices.py dict key HardwareManager actually built the
    driver from -- avoids hardcoding a second copy of "MAIN_DMM"/"MAIN_DAQ"
    that could silently drift from the real default if it ever changes.
    Returns None if `cfg` is not (identically) one of `configs`' values.
    """
    for name, candidate in configs.items():
        if candidate is cfg:
            return name
    return None


def hardware_traceability_messages(snapshot: dict) -> list:
    """
    Build one human-readable event_log message per instrument present in
    `snapshot` (a run_summary hardware-identity dict -- smu_name/
    smu_resource/smu_model, dmm_*, daq_*, relay_matrix_* -- see
    data/storage.py's run_summary schema and docs/architecture.md "Hardware
    Identity Traceability"), plus a final confirmation message. A role
    missing from `snapshot` (e.g. no DMM configured for this run) is
    silently skipped, not reported as "N/A" -- an event_log entry implies
    "this instrument was in use", so an absent instrument gets no entry at
    all. Used identically by test_control/proto_test_sequence.py and
    test.py::_run_monitor_battery() so the traceability wording never
    drifts between test types.
    """
    messages = []
    for role, label in (("smu", "SMU"), ("dmm", "DMM"), ("daq", "DAQ"),
                        ("relay_matrix", "Relay matrix")):
        name = snapshot.get(f"{role}_name")
        if not name:
            continue
        model = snapshot.get(f"{role}_model") or "?"
        resource = snapshot.get(f"{role}_resource") or "?"
        messages.append(f"{label} in use: {name} ({model}, {resource})")
    messages.append("Hardware configuration snapshot recorded")
    return messages
