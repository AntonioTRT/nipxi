"""
NIPXI Test Framework
====================
Modular hardware and system verification.
Loads all configuration from config/settings.py and config/devices.py.

Each test exercises:
  1. The actual implementation module (import + interface)
  2. The underlying hardware / library (if available)

Usage:
    python test.py
"""

import contextlib
import logging
import os
import sys
import time

# -- ensure package root is importable ----------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from config import devices as dev_cfg

# Suppress NI driver / serial noise during tests
logging.disable(logging.CRITICAL)


# =============================================================================
# Device selection -- config-driven, works for any device dict (name -> config)
# =============================================================================

def _select_device(devices: dict, label: str):
    """
    Prompt the user to pick one device from a {name: config_dict} mapping.
    Prints every configured field for each device (no hardcoded fields, so
    PXIe/USB/VISA/COM/Ethernet/Telnet devices all display correctly).
    Returns (name, config) or (None, None) if cancelled / invalid.
    """
    names = list(devices.keys())
    print(f"\nAvailable {label}\n")
    for i, name in enumerate(names, 1):
        cfg = devices[name]
        print(f"{i}. {name}")
        for key, value in cfg.items():
            if key == "name":
                continue
            print(f"   {key}: {value}")
        print()
    print("0. Cancel")

    try:
        raw = input("\nChoice: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None, None

    if raw == "0" or raw == "":
        return None, None
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(names):
            raise ValueError()
    except ValueError:
        print("Invalid choice.")
        return None, None

    name = names[idx]
    return name, devices[name]


def _discover_and_select(label: str, devices: dict, identify_fn):
    """
    Bring-up-focused device picker: shows every configured device of this
    category with a live reachability check BEFORE asking which one to
    test -- unlike _select_device() above, which lists raw config with no
    indication of whether anything actually answers.

    Step 1: list configured devices (from config/devices.py -- PXI_SLOTS for
            PXI categories, or the Numato/serial relay dicts).
    Step 2: run identify_fn (the SAME function Hardware Discovery uses) on
            each one, so this can never drift from what Hardware Discovery
            itself reports.
    Step 3: prompt for a selection -- any listed device, PASS or not, since
            an operator may want to select a failing one to investigate.

    Returns (name, cfg, discovery_results) -- discovery_results is the list
    of TestResult objects from step 2 (always returned, even on cancel, so
    the reachability scan itself is never lost from the test's report).
    Returns (None, None, discovery_results) if the user cancels.
    """
    discovery_results = []
    names = list(devices.keys())

    print(f"\n{label} Devices Found\n")
    if not names:
        print(f"  (none configured for this category in config/devices.py)")
        return None, None, discovery_results

    for i, name in enumerate(names, 1):
        cfg = devices[name]
        result = identify_fn(name, cfg)
        discovery_results.append(result)
        slot = cfg.get("slot")
        print(f"[{i}] {name}")
        if slot is not None:
            print(f"    Slot {slot}")
        if cfg.get("model"):
            print(f"    {cfg['model']}")
        print(f"    {result.status}")
        print()

    print("0. Cancel")
    try:
        raw = input("\nSelect device: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None, None, discovery_results

    if raw == "0" or raw == "":
        return None, None, discovery_results
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(names):
            raise ValueError()
    except ValueError:
        print("Invalid choice.")
        return None, None, discovery_results

    name = names[idx]
    return name, devices[name], discovery_results


def _run_hardware_category(label: str, devices: dict, identify_fn, functional_fn=None):
    """
    Shared bring-up workflow for every hardware category (SMU, DMM, DAQ,
    Temperature Module, Numato Relay Matrix, PXI Relay Matrix):

        1. List every device configured for this category in config/devices.py.
        2. Operator selects ONE device.
        3. Second-level menu: [1] Identity Validation  [2] Functional
           Validation (future)  [0] Back.

    Identity Validation always calls identify_fn(name, cfg) -- the SAME
    function Hardware Discovery uses, so this menu path can never drift from
    what Hardware Discovery reports. It never enables outputs, sources
    voltage/current, or closes relays (see identify_fn implementations).

    Functional Validation calls functional_fn(name, cfg) if one is provided
    for this category; otherwise it reports "not yet implemented" -- a
    deliberate placeholder, not a fake PASS (see docs/architecture.md,
    "Identity Validation vs Functional Validation").

    Selecting a device only ever touches that one device -- no other SMU,
    DMM, DAQ, Temperature Module, or relay is read or written by this
    function.
    """
    if not devices:
        print(f"\n  (no {label} devices configured in config/devices.py)")
        return []

    names = list(devices.keys())
    while True:
        print(f"\n{label}\n")
        for i, name in enumerate(names, 1):
            print(f"[{i}] {dev_cfg.device_display_name(devices[name])}  [{name}]")
        print("0. Back")
        try:
            raw = input("\nSelect device: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return []
        if raw == "0" or raw == "":
            return []
        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(names):
                raise ValueError()
        except ValueError:
            print("Invalid choice.")
            continue
        name = names[idx]
        cfg = devices[name]
        break

    while True:
        print(f"\n{dev_cfg.device_display_name(cfg)}  [{name}]\n")
        print("[1] Identity Validation")
        print("[2] Functional Validation (future)")
        print("[0] Back")
        try:
            raw = input("\nChoice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return []
        if raw == "0" or raw == "":
            return []
        if raw == "1":
            _print_device_config(name, cfg)
            return [identify_fn(name, cfg)]
        if raw == "2":
            if functional_fn is None:
                print("\n  Functional Validation not yet implemented for this "
                      "hardware category.")
                return []
            _print_device_config(name, cfg)
            return functional_fn(name, cfg)
        print("Invalid choice.")


def _print_device_config(name: str, cfg: dict):
    """Print the effective configuration of the device under test."""
    print(f"\n{'-' * 60}")
    print("  Testing Device")
    print(f"{'-' * 60}\n")
    print(f"Device : {dev_cfg.device_display_name(cfg)}")
    print(f"Name : {name}")
    for key, value in cfg.items():
        if key == "name":
            continue
        print(f"{key}: {value}")
    print(f"\n{'-' * 60}")


# =============================================================================
# Result types
# =============================================================================

class Status:
    PASS    = "PASS"
    WARNING = "WARNING"
    FAIL    = "FAIL"


class TestResult:
    def __init__(self, status: str, module: str, device: str,
                 config_ref: str, details: str = ""):
        self.status     = status
        self.module     = module
        self.device     = device
        self.config_ref = config_ref
        self.details    = details

    def print_detail(self):
        tag = {"PASS": "PASS", "WARNING": "WARN", "FAIL": "FAIL"}[self.status]
        print(f"  [{tag}] {self.device}")
        print(f"         Config : {self.config_ref}")
        if self.details:
            for line in self.details.splitlines():
                print(f"         {line}")


def _ok(module, device, ref, detail=""):
    return TestResult(Status.PASS, module, device, ref, detail)

def _warn(module, device, ref, detail):
    return TestResult(Status.WARNING, module, device, ref, detail)

def _fail(module, device, ref, detail):
    return TestResult(Status.FAIL, module, device, ref, detail)


# =============================================================================
# 1. Configuration validator  -- offline, no hardware
# =============================================================================

def test_configuration():
    """
    Validate config/settings.py and config/devices.py.
    Also runs utils/validators.validate_settings() against real config.
    No hardware communication -- safe offline.
    """
    results = []

    # PXI resource strings are validated below, from config/devices.py
    # (SMU_ASSIGNMENTS/DAQ_CONFIG/DMM_CONFIG) -- that is their single source
    # of truth; config/settings.py no longer duplicates them (see the
    # "SMU / DAQ / DMM configs" block further down).

    # -- Relay COM port --------------------------------------------------------
    ref = "config/settings.py -> RELAY_COM_PORT"
    if not Settings.RELAY_COM_PORT:
        results.append(_fail("Configuration", "RELAY_COM_PORT", ref, "Empty"))
    elif not Settings.RELAY_COM_PORT.startswith("COM"):
        results.append(_warn("Configuration", "RELAY_COM_PORT", ref,
                              f"'{Settings.RELAY_COM_PORT}' may not be a valid COM port"))
    else:
        results.append(_ok("Configuration", "RELAY_COM_PORT", ref, Settings.RELAY_COM_PORT))

    # -- Battery channel map --------------------------------------------------
    ref = "config/devices.py -> BATTERY_CHANNELS"
    expected = list(range(1, Settings.BATTERY_POSITIONS + 1))
    actual   = sorted(dev_cfg.BATTERY_CHANNELS.keys())
    if actual != expected:
        results.append(_fail("Configuration", "BATTERY_CHANNELS", ref,
                             f"Expected {expected}, got {actual}"))
    else:
        required = ["relay_address", "daq_voltage_ch", "daq_current_ch",
                    "daq_ntc_ch", "fuse_rating_a"]
        broken = False
        for ch_id, ch in dev_cfg.BATTERY_CHANNELS.items():
            missing = [k for k in required if k not in ch]
            if missing:
                results.append(_fail("Configuration", f"BAT_{ch_id}", ref,
                                     f"Missing keys: {missing}"))
                broken = True
        if not broken:
            results.append(_ok("Configuration", "BATTERY_CHANNELS", ref,
                               f"{len(actual)} channels defined (1-{actual[-1]})"))

    # -- SMU / DAQ / DMM configs (config/devices.py is the single source of
    #    truth for these VISA resource strings -- config/settings.py does
    #    not duplicate them) --------------------------------------------------
    for name, cfg in [("SMU_ASSIGNMENTS", next(iter(dev_cfg.SMU_ASSIGNMENTS.values()), {})),
                      ("DAQ_CONFIG",      dev_cfg.DAQ_CONFIG),
                      ("DMM_CONFIG",      dev_cfg.DMM_CONFIG)]:
        ref = f"config/devices.py -> {name}"
        res = cfg.get("resource")
        if not res:
            results.append(_fail("Configuration", name, ref, "Missing 'resource' key"))
        elif not res.startswith("PXI"):
            results.append(_warn("Configuration", name, ref,
                                 f"'{res}' does not look like a PXI resource string"))
        else:
            results.append(_ok("Configuration", name, ref,
                               f"{res} / {cfg.get('model', '?')}"))

    # -- Value sanity via validate_settings() ---------------------------------
    ref = "utils/validators.validate_settings()"
    try:
        from utils.validators import validate_settings
        validate_settings(Settings)
        results.append(_ok("Configuration", "validate_settings", ref, "All checks passed"))
    except Exception as e:
        results.append(_fail("Configuration", "validate_settings", ref, str(e)))

    # -- Voltage / current cross-checks ---------------------------------------
    ref = "config/settings.py -> limits"
    if Settings.CHARGE_VOLTAGE_V > Settings.BAT_VOLTAGE_MAX:
        results.append(_warn("Configuration", "Charge Voltage", ref,
                             f"CHARGE_VOLTAGE_V ({Settings.CHARGE_VOLTAGE_V}) > MAX ({Settings.BAT_VOLTAGE_MAX})"))
    else:
        results.append(_ok("Configuration", "Charge Voltage", ref,
                           f"{Settings.CHARGE_VOLTAGE_V} V <= {Settings.BAT_VOLTAGE_MAX} V"))

    # DISCHARGE_CUTOFF_V is a discharge TARGET (cycle objective), not the
    # safety floor -- BAT_VOLTAGE_MIN is. It is expected and fine for the
    # target to sit below the global floor; DischargeSequence.run() (and,
    # for group-based test_setpoints, the Safety Monitor Simulator's own
    # _discharge_phase_steps()) clamps the effective cutoff to
    # max(target, floor) so the safety limit always wins regardless. See
    # docs/architecture.md Section 30 "Discharge Cutoff Policy" -- this is
    # informational, not a misconfiguration.
    if Settings.DISCHARGE_CUTOFF_V < Settings.BAT_VOLTAGE_MIN:
        results.append(_ok("Configuration", "Discharge Cutoff", ref,
                           f"Target {Settings.DISCHARGE_CUTOFF_V} V < floor "
                           f"{Settings.BAT_VOLTAGE_MIN} V -- floor takes priority "
                           "(effective cutoff clamped to the floor by design)"))
    else:
        results.append(_ok("Configuration", "Discharge Cutoff", ref,
                           f"{Settings.DISCHARGE_CUTOFF_V} V"))

    return results


# =============================================================================
# 1b. Hardware Discovery -- config-driven connectivity + identification only
# =============================================================================
#
# This is a hardware PRESENCE test, not a measurement test, not a battery
# workflow test, not an accuracy test. For every device found in
# config/devices.py it validates only:
#   - the device exists in config/devices.py                (enumeration)
#   - the device was discovered correctly                    (this loop)
#   - the driver loaded correctly                             (import / factory)
#   - the communication channel opened correctly              (connect)
#   - the instrument responds correctly                       (self-test / login)
#   - instrument identification succeeds                      (identity query)
#
# All devices come from config/devices.py's enumeration dicts -- nothing is
# hardcoded here (no resource strings, no IPs, no COM ports). Adding or
# removing a device there changes what this test covers with no code change.
#
# No outputs are enabled, no voltage/current is sourced, and no channel is
# measured anywhere in this section -- that is deliberately out of scope
# until instrument functionality is implemented.

def _compare_identity(expected_model: str, actual_identity: str) -> str | None:
    """
    Compare the identity string an instrument's own driver reports against
    the model configured in config/devices.py (PXI_SLOTS). Tolerant,
    case-insensitive substring match in either direction, since drivers
    often format the same model slightly differently (e.g. "PXIe-4141" vs
    "NI PXIe-4141") -- this is meant to catch a genuinely wrong/swapped
    card, not to flag harmless formatting differences as a WARNING.

    Returns None if they match (or expected_model is empty -- nothing to
    compare against); otherwise a one-line warning message.
    """
    if not expected_model:
        return None
    exp = expected_model.lower()
    act = actual_identity.lower()
    if exp in act or act in exp:
        return None
    return (
        f"Configured model '{expected_model}' but hardware reports "
        f"'{actual_identity}' -- verify config/devices.py (PXI_SLOTS) "
        f"against the physical rack."
    )


def _identify_smu(name: str, cfg: dict):
    """
    SMU/PSU presence check (the NI SMU is this project's PSU -- there is no
    separate PSU hardware/config; see MENU label "Test SMU (PSU)").

    Uses hardware.smu.SMU -- the SAME production driver class HardwareManager
    constructs -- so discovery and the real battery-test path never diverge.
    connect() + identify() only. Never calls output_enable(),
    set_charge_mode(), set_discharge_mode(), or sources any voltage/current.
    """
    resource = cfg.get("resource", "")
    model    = cfg.get("model", "NI-SMU")
    display  = dev_cfg.device_display_name(cfg)
    ref      = f"config/devices.py -> SMU_ASSIGNMENTS[{name!r}] ({resource} / {model})"

    from hardware.smu import SMU
    smu = SMU(cfg)
    try:
        smu.connect()
        identity = smu.identify()
        mismatch = _compare_identity(model, identity)
        if mismatch:
            return _warn("Hardware Discovery", f"SMU/PSU: {display}", ref,
                        f"Communication established. Identified: {identity}\n{mismatch}")
        return _ok("Hardware Discovery", f"SMU/PSU: {display}", ref,
                   f"Communication established. Identified: {identity}")
    except Exception as e:
        desc = getattr(e, "description", str(e))
        return _fail("Hardware Discovery", f"SMU/PSU: {display}", ref,
                     f"[ERROR] SMU not detected\nReason: {desc}")
    finally:
        try:
            smu.disconnect()
        except Exception:
            pass


def _identify_dmm(name: str, cfg: dict):
    """
    DMM presence check: connect() + identify() only, via hardware.dmm.DMM --
    the same production driver class used everywhere else. Never triggers
    or reads a measurement.
    """
    resource = cfg.get("resource", "")
    model    = cfg.get("model", "NI-4065")
    display  = dev_cfg.device_display_name(cfg)
    ref      = f"config/devices.py -> DMM_CONFIGS[{name!r}] ({resource} / {model})"

    from hardware.dmm import DMM
    dmm = DMM(cfg)
    try:
        dmm.connect()
        identity = dmm.identify()
        mismatch = _compare_identity(model, identity)
        if mismatch:
            return _warn("Hardware Discovery", f"DMM: {display}", ref,
                        f"Communication established. Identified: {identity}\n{mismatch}")
        return _ok("Hardware Discovery", f"DMM: {display}", ref,
                   f"Communication established. Identified: {identity}")
    except Exception as e:
        desc = getattr(e, "description", str(e))
        return _fail("Hardware Discovery", f"DMM: {display}", ref,
                     f"[ERROR] DMM not detected\nReason: {desc}")
    finally:
        try:
            dmm.disconnect()
        except Exception:
            pass


def _identify_daq(name: str, cfg: dict):
    """
    DAQ presence check: connect() + identify() only, via hardware.daq.DAQ --
    the same production driver class HardwareManager constructs. Never
    creates a task, configures a channel, or reads any analog input;
    identify() runs the device's own built-in self-test.
    """
    resource = cfg.get("resource", "")
    model    = cfg.get("model", "NI-6363")
    display  = dev_cfg.device_display_name(cfg)
    ref      = f"config/devices.py -> DAQ_CONFIGS[{name!r}] ({resource} / {model})"

    from hardware.daq import DAQ
    daq = DAQ(cfg)
    try:
        daq.connect()
        identity = daq.identify()
        mismatch = _compare_identity(model, identity)
        if mismatch:
            return _warn("Hardware Discovery", f"DAQ: {display}", ref,
                        f"Communication established. Identified: {identity}\n{mismatch}")
        return _ok("Hardware Discovery", f"DAQ: {display}", ref,
                   f"Communication established. Identified: {identity}  "
                   f"(self-test passed, no channel read performed)")
    except Exception as e:
        return _fail("Hardware Discovery", f"DAQ: {display}", ref,
                     f"[ERROR] DAQ not detected or self-test failed\nReason: {e}")
    finally:
        try:
            daq.disconnect()
        except Exception:
            pass


def _identify_temperature(name: str, cfg: dict):
    """
    Temperature module (PXIe-4353) presence check -- identity/presence ONLY.

    NI-4353 is an NI-DAQmx-family device (universal thermocouple/RTD input
    module), so this deliberately reuses hardware.daq.DAQ rather than
    inventing a separate driver class: DAQ.connect()/identify() are already
    generic NI-DAQmx device enumeration + self-test, nothing 6363-specific.
    No thermocouple/RTD channel is configured or read here -- that would
    require a new driver (see config/devices.py PXI_SLOTS[15]'s
    validation_notes and docs/TODO.md); faking it here would be exactly the
    "COMMAND and assume success" pattern this project's testing philosophy
    exists to avoid.
    """
    resource = cfg.get("resource", "")
    model    = cfg.get("model", "PXIe-4353")
    display  = dev_cfg.device_display_name(cfg)
    ref      = f"config/devices.py -> PXI_SLOTS[{name!r}] ({resource} / {model})"

    from hardware.daq import DAQ
    daq = DAQ(cfg)
    try:
        daq.connect()
        identity = daq.identify()
        mismatch = _compare_identity(model, identity)
        if mismatch:
            return _warn("Hardware Discovery", f"Temperature Module: {display}", ref,
                        f"Communication established. Identified: {identity}\n{mismatch}")
        return _ok("Hardware Discovery", f"Temperature Module: {display}", ref,
                   f"Communication established. Identified: {identity}  "
                   f"(presence/identity only -- no TC/RTD channel read implemented)")
    except Exception as e:
        return _fail("Hardware Discovery", f"Temperature Module: {display}", ref,
                     f"[ERROR] Temperature module not detected\nReason: {e}")
    finally:
        try:
            daq.disconnect()
        except Exception:
            pass


def _identify_relay_eth(name: str, cfg: dict):
    """
    Numato Relay Matrix presence check: TCP connect + Telnet login + the
    driver's own connection-verification "relay readall"
    (hardware/relay_eth.py NumatoRelayMatrix.connect()). This is read-only --
    connect() never writes to any relay, so no channel is energized or
    de-energized by this check. Applies to every device configured under
    NUMATO_RELAY_MATRIX_CONFIGS -- this function is not specific to any one
    device name.
    """
    host    = cfg.get("ip", "")
    port    = cfg.get("port", 23)
    driver  = cfg.get("driver", "RELAY32ETHRL00")
    display = dev_cfg.device_display_name(cfg)
    ref     = f"config/devices.py -> NUMATO_RELAY_MATRIX_CONFIGS[{name!r}] ({driver} / {host}:{port})"

    try:
        from hardware.relay_factory import RelayFactory
        relay = RelayFactory.create(cfg)
    except Exception as e:
        return _fail("Hardware Discovery", f"Numato Relay Matrix: {display}", ref,
                     f"Import / factory error: {e}")

    try:
        relay.connect()
    except Exception as e:
        return _fail("Hardware Discovery", f"Numato Relay Matrix: {display}", ref,
                     f"[ERROR] Relay not detected\nReason: {_classify_relay_error(e)}")

    try:
        relay.disconnect()
    except Exception:
        pass

    return _ok("Hardware Discovery", f"Numato Relay Matrix: {display}", ref,
               f"Communication established. Identified: {driver} at {host}:{port} "
               f"(TCP + Telnet login + readall verification all succeeded)")


def _identify_relay_serial(name: str, cfg: dict):
    """
    Serial relay presence check: RelayFactory.create(cfg) -> SerialRelay --
    the same production driver class -- then connect()/disconnect(). Numato-
    style identity commands do not apply to this diagnostic path (no relay
    protocol is invented here) -- port-open success is the pass criterion.
    """
    port = cfg.get("port", Settings.RELAY_COM_PORT)
    baud = cfg.get("baud_rate", Settings.RELAY_BAUD_RATE)
    ref  = f"config/devices.py -> RELAY_SERIAL_CONFIGS[{name!r}] ({port} / {baud} baud)"

    from hardware.relay_factory import RelayFactory
    relay = RelayFactory.create(cfg)
    try:
        relay.connect()
        return _ok("Hardware Discovery", f"Relay (Serial): {name}", ref,
                   f"Communication channel opened: {port} @ {baud} baud "
                   f"(diagnostic path -- no identity command available)")
    except Exception as e:
        return _fail("Hardware Discovery", f"Relay (Serial): {name}", ref,
                     f"[ERROR] Relay not detected\nReason: {e}")
    finally:
        try:
            relay.disconnect()
        except Exception:
            pass


def _identify_switch(name: str, cfg: dict):
    """
    PXI-resident switch/relay card (PXI_SLOTS[11], category="switch")
    presence check. Reported honestly as N/A -- no niswitch-based driver
    exists in this codebase (see PXI_SLOTS[11]'s validation_notes), so this
    never fakes a real identity query against it.
    """
    resource = cfg.get("resource", "")
    model = cfg.get("model", "")
    display = dev_cfg.device_display_name(cfg)
    note = cfg.get("validation_notes", "No driver class implemented for this category.")
    ref = f"config/devices.py -> PXI_SLOTS[{name!r}] ({resource} / {model})"
    return _warn("Hardware Discovery", f"Switch/Relay (PXI): {display}", ref,
                 f"Not applicable -- no niswitch-based driver exists in this codebase.\n{note}")


def _pxi_slots_by_category(category: str) -> dict:
    """
    PXI_SLOTS entries of the given category, keyed by nickname, in slot-
    number order. config/devices.py::PXI_SLOTS is the single source of
    truth this reads from -- nothing here is hand-duplicated.
    """
    return {
        cfg["nickname"]: cfg
        for slot, cfg in sorted(dev_cfg.PXI_SLOTS.items())
        if cfg["category"] == category
    }


def _print_group_header(label: str):
    print(f"\n{label} Devices")
    print("-" * (len(label) + 8))


def _print_device_line(name: str, cfg: dict):
    """One device's identity block in the grouped discovery report --
    Slot / Model / display name [nickname], matching config/devices.py::
    PXI_SLOTS' own fields exactly (falls back gracefully for non-PXI-slot
    devices, e.g. Numato/serial relay or GPIB, which have no "slot"). The
    display name (config/devices.py::device_display_name()) is what
    identifies the physical hardware at a glance during rack bring-up; the
    nickname in brackets is the internal config/devices.py identifier, kept
    for traceability back to config."""
    slot = cfg.get("slot")
    if slot is not None:
        print(f"Slot {slot}")
    model = cfg.get("model")
    if model:
        print(model)
    print(f"{dev_cfg.device_display_name(cfg)}  [{name}]")


# Category -> (display label, identify function). Config-driven: adding or
# removing a config/devices.py::PXI_SLOTS entry changes exactly what this
# covers -- no other code needs to change, and no resource/model is
# hardcoded here.
_PXI_CATEGORY_TARGETS = [
    ("smu",         "SMU",                _identify_smu),
    ("dmm",         "DMM",                _identify_dmm),
    ("daq",         "DAQ",                _identify_daq),
    ("temperature", "Temperature Module", _identify_temperature),
]

# Non-PXI-slot device groups (Ethernet/serial relay) -- kept separate from
# PXI_SLOTS since they are not chassis-slot devices, shown in the same
# grouped report for one consolidated view rather than a second tool.
_NON_PXI_TARGETS = [
    ("Numato Relay Matrix (Ethernet)", dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, _identify_relay_eth),
    ("Relay (Serial)",   dev_cfg.RELAY_SERIAL_CONFIGS, _identify_relay_serial),
]


def test_hardware_discovery():
    """
    Generic device discovery + connectivity test, grouped by category and
    driven entirely by config/devices.py::PXI_SLOTS (plus the non-PXI-slot
    Numato/serial relay dicts). For every configured device: create the
    driver, connect, identify, compare identity against the configured
    model, report PASS/WARNING/FAIL with full reason.

    This is NOT a measurement test, NOT a battery workflow test, and NOT an
    instrument-accuracy test -- see the module comment above this section.

    Categories with no driver class in this codebase (the PXI-resident
    switch/relay card, and any GPIB instrument) are reported as N/A rather
    than faked -- see config/devices.py's PXI_SLOTS[11]/GPIB_INSTRUMENTS
    validation_notes for why.

    Runs entirely inside _numato_relay_debug_logging() so that any Numato
    Relay Matrix device's full Telnet conversation (RX/TX, prompt detection,
    IAC negotiation) is visible if its connect() fails -- see
    hardware/relay_eth.py's "Authentication debugging" module docstring
    section. Harmless no-op verbosity for the non-relay device types.
    """
    results = []
    with _numato_relay_debug_logging():
        for category, label, identify_fn in _PXI_CATEGORY_TARGETS:
            devices = _pxi_slots_by_category(category)
            _print_group_header(label)
            if not devices:
                results.append(_warn("Hardware Discovery", label,
                                     "config/devices.py -> PXI_SLOTS",
                                     "No devices configured for this category -- skipped"))
                continue
            for name, cfg in devices.items():
                _print_device_line(name, cfg)
                results.append(identify_fn(name, cfg))
                print()

        # PXI-resident switch/relay card -- present in the rack (see
        # PXI_SLOTS[11]), but no niswitch-based driver exists in this
        # codebase. Reported honestly as N/A, never faked as a real check.
        switch_devices = _pxi_slots_by_category("switch")
        if switch_devices:
            _print_group_header("Switch/Relay (PXI)")
            for name, cfg in switch_devices.items():
                _print_device_line(name, cfg)
                print("N/A -- no driver implemented\n")
                results.append(_identify_switch(name, cfg))

        for label, devices, identify_fn in _NON_PXI_TARGETS:
            _print_group_header(label)
            if not devices:
                results.append(_warn("Hardware Discovery", label, "config/devices.py",
                                     "No devices configured for this type -- skipped"))
                continue
            for name, cfg in devices.items():
                _print_device_line(name, cfg)
                results.append(identify_fn(name, cfg))
                print()

        # GPIB -- detected interface, unconfirmed instrument. Reported as
        # N/A rather than attempting a fake identity query against an
        # unknown device.
        if dev_cfg.GPIB_INSTRUMENTS:
            _print_group_header("GPIB")
            for name, cfg in dev_cfg.GPIB_INSTRUMENTS.items():
                print(cfg.get("interface", "?"))
                print(cfg.get("model") or "(model unconfirmed)")
                print(name)
                print("N/A -- instrument unconfirmed\n")
                results.append(_warn(
                    "Hardware Discovery", f"GPIB: {name}",
                    f"config/devices.py -> GPIB_INSTRUMENTS[{name!r}]",
                    cfg.get("validation_notes", "No instrument model confirmed at this address."),
                ))

    return results


# =============================================================================
# 2. SMU / PSU
# =============================================================================

def test_smu():
    """
    Menu entry for the SMU hardware category. Lists every configured SMU
    (config/devices.py::PXI_SLOTS, category="smu"), then routes to either
    Identity Validation (_identify_smu -- interface check + connect() +
    identify() only, never sources anything) or Functional Validation
    (_functional_smu -- a real, laboratory-only DC voltage sourcing check,
    operator physically present at the rack), via the shared
    _run_hardware_category() workflow. See docs/architecture.md, "Identity
    Validation vs Functional Validation".
    """
    devices = _pxi_slots_by_category("smu")
    return _run_hardware_category("SMU", devices, _identify_smu, _functional_smu)


def _functional_smu(name: str, cfg: dict):
    """
    SMU Functional Validation -- laboratory-only, operator physically
    present at the rack with a handheld DMM connected to the SMU output.

    Verifies the SMU can source DC voltage correctly, using a sequence that
    reflects how this SMU is actually used in NIPXI, not a generic bipolar
    power-supply check:

        Charging:    source voltage,  source current
        Discharging: source voltage,  SINK current (current-sink, never a
                     negative source voltage -- see docs/architecture.md
                     Section 12.6 and README.md Section 8.1b)

    This validation therefore exercises a positive voltage point only --
    the same polarity the real charge path (`set_charge_mode()`) will use
    -- never a negative voltage. Discharge's current-sink behavior is not
    covered here (no current-sink validation exists yet; that is separate,
    future work, not part of this bench voltage-sourcing check).

    This is NOT a battery operation: no relay, no battery channel, no
    charge/discharge mode is touched (SMU.set_charge_mode()/
    set_discharge_mode() remain untouched placeholders). See
    hardware/smu.py::SMU.source_dc_voltage_point().

    Sequence: SAFE STATE (output forced off + verified) -> 0 V (baseline)
    -> charge validation voltage -> 0 V (return to baseline) -> output OFF
    (forced + verified again). Every step, and any FAIL or operator
    cancellation (Ctrl+C / blank input at a prompt), always ends with
    SMU.emergency_output_off() -- the operator must never be left with an
    energized output.

    Validation points are derived entirely from EXISTING project
    configuration -- no new hardcoded voltage/current constants, and no
    duplicate configuration:
      - Validation voltage: Settings.CHARGE_VOLTAGE_V -- the already-
        configured real CV-phase charge target for this system, i.e. the
        same voltage setpoint the real charge path is meant to use. This
        is more representative of production behavior than an arbitrary
        bench value, per this validation's purpose.
      - Current limit (compliance): Settings.CHARGE_CURRENT_A -- the
        already-configured real charge current for this system, reused
        as the SMU's output current-limit during this bench check.
      - Voltage source range: Settings.BAT_VOLTAGE_MAX -- the existing
        station-level absolute voltage safety ceiling, reused to bound
        the SMU's source range so it can never be programmed above the
        limit the rest of the safety architecture already enforces.
    """
    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-SMU")
    display    = dev_cfg.device_display_name(cfg)
    config_ref = f"{resource} / {model}"
    results    = []

    validation_v    = Settings.CHARGE_VOLTAGE_V
    current_limit_a = Settings.CHARGE_CURRENT_A
    range_v         = Settings.BAT_VOLTAGE_MAX

    print(f"\nSMU Functional Validation -- {display} ({config_ref})")
    print(f"Validation voltage (Settings.CHARGE_VOLTAGE_V): {validation_v:.3f} V")
    print(f"Current limit (Settings.CHARGE_CURRENT_A): {current_limit_a:.3f} A")
    print("\nConnect handheld DMM to SMU output.")
    try:
        input("Press Enter when ready to begin (Ctrl+C to cancel)... ")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled before sourcing -- output was never enabled.")
        return [_warn("SMU Functional", display, config_ref,
                      "Cancelled by operator before sourcing began")]

    from hardware.smu import SMU
    smu = SMU(cfg)
    try:
        smu.connect()
    except Exception as e:
        return [_fail("SMU Functional", display, config_ref,
                      f"[ERROR] SMU connect failed: {e}")]

    # Start from a safe state -- force output off and verify before sourcing
    # anything, mirroring HardwareManager.connect_all()'s relay open_all().
    if not smu.emergency_output_off("SMU Functional Validation: pre-check safe state"):
        results.append(_fail("SMU Functional", display, config_ref,
                             "[ERROR] Could not verify a safe starting state -- "
                             "output disable/verify failed before sourcing began"))
        try:
            smu.disconnect()
        except Exception:
            pass
        return results

    steps = [
        ("0 V (baseline)", 0.0),
        ("Charge validation voltage", validation_v),
        ("0 V (return to baseline)", 0.0),
    ]
    cancelled = False

    try:
        for step_label, level_v in steps:
            print(f"\nCurrent Step:\n    {step_label}")
            print(f"Expected Voltage:\n    {level_v:.3f} V")
            try:
                reading = smu.source_dc_voltage_point(level_v, current_limit_a, range_v)
            except Exception as e:
                results.append(_fail("SMU Functional", f"{display}: {step_label}", config_ref,
                                     f"[ERROR] {e}"))
                break

            # Configuration readbacks -- NI-DCPower attribute echo (the
            # instrument's stored setpoint, verified internally by
            # hardware/smu.py::SMU._verify_config_readback() before this
            # point was ever reached; these are NOT ADC measurements).
            print("\nConfiguration Readback (verified against commanded values):")
            print(f"    Commanded Voltage:        {reading['commanded_v']:.3f} V")
            print(f"    Readback Voltage Setting: {reading['readback_v']:.6f} V")
            print(f"    Commanded Current Limit:  {reading['commanded_current_limit_a']:.3f} A")
            print(f"    Readback Current Limit:   {reading['readback_current_limit_a']:.6f} A")
            print(f"    Output State Readback:    {'ON' if reading['output_enabled_readback'] else 'OFF'}")

            # Runtime measurements -- real ADC readback of the physical
            # output (session.measure()), NOT compared/asserted against the
            # commanded setpoint -- see hardware/smu.py's module docstring
            # for why (a real load makes measured != commanded by design).
            print("Runtime Measurement (informational; verify against handheld DMM):")
            print(f"    Compliance State:  {'IN COMPLIANCE' if reading['in_compliance'] else 'not in compliance'}")
            print(f"    Measured Voltage:  {reading['measured_v']:.6f} V")
            print(f"    Measured Current:  {reading['measured_i']:.6f} A")

            results.append(_ok("SMU Functional", f"{display}: {step_label}", config_ref,
                               f"Commanded {level_v:.3f} V (readback {reading['readback_v']:.6f} V) -- "
                               f"SMU-measured {reading['measured_v']:.6f} V / "
                               f"{reading['measured_i']:.6f} A (informational; "
                               f"verify against handheld DMM)"))
            try:
                input("Verify reading on handheld DMM, then press Enter to continue "
                      "(Ctrl+C to cancel)... ")
            except (KeyboardInterrupt, EOFError):
                cancelled = True
                print("\nCancelled by operator.")
                break
    finally:
        safe = smu.emergency_output_off("SMU Functional Validation complete/cancelled/failed")
        if safe:
            results.append(_ok("SMU Functional", display, config_ref,
                               "Output disabled and verified OFF -- SMU returned to safe state"))
        else:
            results.append(_fail("SMU Functional", display, config_ref,
                                 "[ERROR] Output disable could not be verified -- "
                                 "physically check the SMU output"))
        try:
            smu.disconnect()
        except Exception:
            pass

    if cancelled:
        results.append(_warn("SMU Functional", name, config_ref,
                             "Cancelled by operator -- remaining validation steps not run"))

    return results


# =============================================================================
# 3. DMM
# =============================================================================

def test_dmm():
    """
    Menu entry for the DMM hardware category. Lists every configured DMM
    (config/devices.py::PXI_SLOTS, category="dmm"), then routes to either
    Identity Validation (_identify_dmm -- interface check + connect() +
    identify(), never a measurement) or Functional Validation
    (_functional_dmm -- a real DC voltage measurement), via the shared
    _run_hardware_category() workflow. See docs/architecture.md, "Identity
    Validation vs Functional Validation".
    """
    devices = _pxi_slots_by_category("dmm")
    return _run_hardware_category("DMM", devices, _identify_dmm, _functional_dmm)


def _functional_dmm(name: str, cfg: dict):
    """
    DMM Functional Validation -- laboratory-only, operator physically
    present at the rack with a known DC voltage source (bench supply,
    calibrator, or other external reference) connected to the DMM input.

    Verifies the DMM can acquire a DC voltage measurement: a REAL
    measurement (DMM.measure_dc_voltage()) -- command (configure + trigger)
    -> readback (measured value) -> verify (finite, within the configured
    range) -> PASS/FAIL. Unlike SMU sourcing, a DMM measurement is passive
    (it only observes), so this never changes hardware state and is safe
    to run unconditionally. Deliberately does NOT repeat the
    interface/identity checks -- those are Identity Validation's job
    (_identify_dmm), run separately.

    First-implementation scope, deliberately minimal: this answers "can the
    DMM successfully perform a voltage measurement?" only. It does NOT
    implement current measurement, calibration validation, accuracy
    certification, or automated metrology limits -- the finite/in-range
    check below is a basic sanity guard (catches a NaN or a wildly
    out-of-range reading), not a claim about measurement accuracy against
    the externally-connected reference. Range comes entirely from
    config/devices.py (PXI_SLOTS -> DMM_CONFIGS[...]["range_v"]) -- no
    hidden constants.
    """
    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-4065")
    range_v    = cfg.get("range_v", 10.0)
    display    = dev_cfg.device_display_name(cfg)
    config_ref = f"{resource} / {model}"
    results    = []

    print(f"\nDMM Functional Validation -- {display} ({config_ref})")
    print("\nConnect a known DC source to the DMM input")
    print("(e.g. bench supply, calibrator, or other external reference).")
    try:
        input("Press Enter when ready to measure (Ctrl+C to cancel)... ")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled before measuring.")
        return [_warn("DMM Functional", display, config_ref,
                      "Cancelled by operator before measurement began")]

    from hardware.dmm import DMM
    dmm = DMM(cfg)
    try:
        dmm.connect()
    except Exception as e:
        desc = getattr(e, "description", str(e))
        results.append(_fail("DMM Functional", display, config_ref,
                             f"[ERROR] DMM not detected or connect failed\n"
                             f"Configuration : {name}\n"
                             f"Interface     : VISA / NI-DMM\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {desc}"))
        return results

    try:
        value = dmm.measure_dc_voltage()
        print(f"\nMeasured Voltage:\n    {value:.6f} V")
        results.append(_ok("DMM Functional", f"{display} DC volts measurement", config_ref,
                           f"Measured {value:.6f} V (within configured range +/-{range_v} V)"))
    except Exception as e:
        results.append(_fail("DMM Functional", f"{display} DC volts measurement", config_ref,
                             f"[ERROR] DMM measurement failed verification\n"
                             f"Configuration : {name}\n"
                             f"Expected range: +/-{range_v} V\n"
                             f"Reason        : {e}"))
    finally:
        try:
            dmm.disconnect()
        except Exception:
            pass

    return results


# =============================================================================
# 4. DAQ
# =============================================================================

def test_daq():
    """
    Menu entry for the DAQ hardware category. Lists every configured DAQ
    (config/devices.py::PXI_SLOTS, category="daq"), then routes to either
    Identity Validation (_identify_daq -- interface check + connect() +
    identify(), never a channel read) or Functional Validation
    (_functional_daq -- a real channel read), via the shared
    _run_hardware_category() workflow. See docs/architecture.md, "Identity
    Validation vs Functional Validation".
    """
    devices = _pxi_slots_by_category("daq")
    return _run_hardware_category("DAQ", devices, _identify_daq, _functional_daq)


def _functional_daq(name: str, cfg: dict):
    """
    Functional Validation for one DAQ: a deep channel read via
    hardware.daq.DAQ.read_channel() -- the same production driver class
    used everywhere else. COMMAND (configure + read the channel) ->
    READBACK (the value) -> VERIFY (finite, within the configured
    +/-voltage_range_v ADC range) -> PASS/FAIL -- a NaN, an out-of-range,
    or a stuck reading is a FAIL, not "the read call didn't throw" (see
    hardware/daq.py::DAQ.read_channel()). Deliberately does NOT repeat the
    interface/identity checks -- those are Identity Validation's job
    (_identify_daq), run separately.

    Assumes the wiring documented in BATTERY_CHANNELS (MAIN_DAQ) -- if
    EXPANSION_DAQ/PRECISION_DAQ is selected instead, the channel string may
    not correspond to a real battery signal on that card; this is a
    pre-existing assumption, not something this refactor introduces.
    """
    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-6363")
    range_v    = cfg.get("voltage_range_v", 5.0)
    config_ref = f"{resource} / {model}"
    test_ch    = dev_cfg.BATTERY_CHANNELS[1]["daq_voltage_ch"]
    results    = []

    from hardware.daq import DAQ
    daq = DAQ(cfg)
    try:
        daq.connect()
    except Exception as e:
        results.append(_fail("DAQ Functional", name, config_ref,
                             f"[ERROR] DAQ not detected or connect failed\n"
                             f"Configuration : {name}\n"
                             f"Interface     : NI-DAQmx\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {e}"))
        return results

    try:
        val = daq.read_channel(test_ch)
        results.append(_ok("DAQ Functional", f"{name} channel read", config_ref,
                           f"Channel {test_ch} read: {val:.4f} V -- verified within "
                           f"configured +/-{range_v} V range"))
    except Exception as e:
        results.append(_fail("DAQ Functional", f"{name} channel read", config_ref,
                             f"[ERROR] DAQ channel read failed\n"
                             f"Configuration : {name}\n"
                             f"Channel       : {test_ch}\n"
                             f"Reason        : {e}"))
    finally:
        try:
            daq.disconnect()
        except Exception:
            pass

    return results


# =============================================================================
# 4b. Temperature Module (PXIe-4353)
# =============================================================================

def test_temperature_module():
    """
    RETIRED as a standalone top-level MENU entry (see docs/architecture.md
    "NTC Sensor Acquisition -- DAQ Architecture" / docs/MILESTONES.md):
    battery temperature monitoring is expected to come entirely through the
    per-position DAQ NTC channel path (BATTERY_CHANNELS[i]["daq_ntc_ch"],
    see test_sensors()'s Test 6), never through this separate PXIe-4353
    module -- no thermocouple/RTD channel driver has ever existed for it
    (see PXI_SLOTS[15]'s validation_notes), and none is planned now that the
    DAQ path covers the same need. The function/identify check are kept
    (not deleted) since the card is still physically present in the rack
    and `test_hardware_discovery()` (MENU item 4) still reports its
    presence/identity via `_identify_temperature()` -- this function is
    simply no longer wired into its own top-level MENU slot. Call directly
    (`test_temperature_module()`) for the old standalone Identity
    Validation workflow if ever needed for one-off bring-up diagnosis.
    """
    devices = _pxi_slots_by_category("temperature")
    return _run_hardware_category("Temperature Module", devices, _identify_temperature)


# =============================================================================
# 5a. Relay -- Serial
# =============================================================================

def test_relay_serial():
    """
    Tests the serial relay driver (hardware/relay_serial.py via RelayFactory).

    Step 1: Verify factory + module interface (RelayBase subclass, all methods present).
    Step 2: Check pyserial is installed and the configured COM port exists.
    Step 3: Attempt to open the port (no relay commands sent -- protocol is still placeholder).

    Returns PASS once the port opens cleanly.
    Returns FAIL if pyserial is missing, the port is absent, or the open fails.

    Steps 1/2 below: show every configured serial relay with a live
    reachability check, then select ONE to run the full test against --
    selecting here never touches any other relay, SMU, DMM, or DAQ.
    """
    name, cfg, results = _discover_and_select(
        "Relay (Serial)", dev_cfg.RELAY_SERIAL_CONFIGS, _identify_relay_serial)
    if cfg is None:
        return results
    _print_device_config(name, cfg)

    port       = cfg.get("port", Settings.RELAY_COM_PORT)
    baud       = cfg.get("baud_rate", Settings.RELAY_BAUD_RATE)
    config_ref = f"config/devices.py -> {name} ({port} / {baud} baud)"

    # Step 1: factory + interface check  -- offline, no hardware ---------------
    try:
        from hardware.relay_factory import RelayFactory
        from hardware.relay import RelayBase
        relay = RelayFactory.create(cfg)
        if not isinstance(relay, RelayBase):
            results.append(_fail("Relay Serial", "Factory", config_ref,
                                 "RelayFactory did not return a RelayBase instance"))
        else:
            required = ["connect", "disconnect", "open", "close", "open_all",
                        "close_all", "query"]
            missing = [m for m in required if not callable(getattr(relay, m, None))]
            if missing:
                results.append(_fail("Relay Serial", "Interface", config_ref,
                                     f"Missing methods: {missing}"))
            else:
                proto_ok = "OPEN {ch}" not in cfg.get("command_open", "")
                if proto_ok:
                    results.append(_ok("Relay Serial", "Driver interface",
                                       config_ref, "RelayFactory -> SerialRelay OK"))
                else:
                    results.append(_warn("Relay Serial", "Driver interface",
                                         config_ref,
                                         "Serial communication works, but protocol commands "
                                         "are not implemented because production hardware is "
                                         "Ethernet."))
    except Exception as e:
        results.append(_fail("Relay Serial", "Factory", config_ref,
                             f"Import / factory error: {e}"))
        return results

    # Step 2 + 3: pyserial + port open ----------------------------------------
    try:
        import serial
        import serial.tools.list_ports
    except ImportError:
        results.append(_fail("Relay Serial", name, config_ref,
                             "[ERROR] Relay not detected\n"
                             f"Port   : {port}\n"
                             "Reason : Library 'pyserial' not installed\n"
                             "Fix    : pip install pyserial"))
        return results

    available = [p.device for p in serial.tools.list_ports.comports()]
    if port not in available:
        results.append(_fail("Relay Serial", name, config_ref,
                             f"[ERROR] Relay not detected\n"
                             f"Port           : {port}\n"
                             f"Available ports: {available if available else 'none'}\n"
                             f"Reason         : {port} not present on this system"))
        return results

    try:
        with serial.Serial(port, baud,
                           timeout=cfg.get("timeout", Settings.RELAY_TIMEOUT_S)) as _:
            results.append(_ok("Relay Serial", name, config_ref,
                               f"Port {port} opened at {baud} baud -- hardware present"))
    except serial.SerialException as e:
        results.append(_fail("Relay Serial", name, config_ref,
                             f"[ERROR] Could not open {port}: {e}"))
    except Exception as e:
        results.append(_fail("Relay Serial", name, config_ref, str(e)))

    return results


# =============================================================================
# 5b. Numato Relay Matrix (Ethernet, RELAY32ETHRL00) -- shared diagnostics
#     helpers. Config-driven / generic: none of these reference a specific
#     device name -- they operate on whatever cfg dict is passed in, so
#     every improvement here automatically applies to every entry under
#     config/devices.py NUMATO_RELAY_MATRIX_CONFIGS, present or future.
# =============================================================================

@contextlib.contextmanager
def _numato_relay_debug_logging():
    """
    Temporarily re-enables hardware/relay_eth.py's logger at DEBUG level for
    the duration of the wrapped block -- test.py silences ALL logging at
    import time (see the top of this file), which is why the driver's
    detailed Telnet transcript (RX/TX, prompt detection, IAC negotiation --
    see hardware/relay_eth.py's "Authentication debugging" module docstring
    section) was never visible from any of the menu items below until now.

    Wrap any code that calls RelayFactory.create(cfg).connect() (or
    equivalent) on a Numato Relay Matrix device in this context manager to
    see the full conversation. Applies uniformly to every device -- there
    is nothing here tied to a specific config/devices.py entry.
    """
    prev_disable_level = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    hw_logger = logging.getLogger("nipxi.hw")
    hw_logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("    [RELAY LOG] %(name)s: %(message)s"))
    hw_logger.addHandler(handler)
    try:
        yield
    finally:
        hw_logger.removeHandler(handler)
        logging.disable(prev_disable_level)


def _ping_host(host: str, timeout_s: float = 1.0):
    """
    ICMP ping (single echo request). Returns (ok, detail).
    Uses the platform ping binary -- no extra dependency required.
    """
    import subprocess
    if not host:
        return False, "No IP configured"
    timeout_ms = max(1, int(timeout_s * 1000))
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 2)
        if proc.returncode == 0:
            return True, f"{host} reachable"
        return False, f"{host} unreachable (no ICMP reply)"
    except Exception as e:
        return False, f"ping failed to execute: {e}"


def _check_web_interface(host: str, port: int = 80, timeout_s: float = 2.0):
    """
    Best-effort HTTP reachability check for the relay's built-in web UI.
    A successful TCP connect + HTTP response (any status code) counts as reachable --
    we're checking the interface exists, not asserting page content.
    """
    import http.client
    if not host:
        return False, "No IP configured"
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
        conn.request("GET", "/")
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return True, f"HTTP connection successful (status {status})"
    except Exception as e:
        return False, f"HTTP connection failed: {e}"


def _classify_relay_error(e: Exception) -> str:
    """
    Map a raw relay exception to one of the standardized, actionable
    diagnostic categories requested for commissioning: network / auth /
    protocol / timeout. NEVER collapses the detail down to a bare label --
    the full exception text (including hardware/relay_eth.py::_login()'s
    expected-vs-received diagnostic) is always appended after the category,
    so "Authentication failed" is never shown on its own. Enable
    _numato_relay_debug_logging() around the call site for the full
    RX/TX Telnet transcript in addition to this summary.
    """
    msg = str(e)
    if "Login rejected" in msg or "Timeout during login" in msg:
        return f"Authentication failed -- {msg}"
    if "invalid response" in msg:
        return f"Relay read returned invalid response -- {msg}"
    if "command timeout" in msg:
        return f"Device reachable but relay command timeout -- {msg}"
    if "not reachable" in msg or "refused" in msg.lower() or "timeout" in msg.lower():
        return f"Device unreachable -- {msg}"
    return msg if msg else "Unknown error"


def test_relay_numato_matrix(name=None, cfg=None):
    """
    Real commissioning test for the Numato Lab 32-Channel Ethernet Relay
    Module, i.e. the Numato Relay Matrix (hardware/relay_eth.py via
    RelayFactory). Applies to whichever device is selected from
    NUMATO_RELAY_MATRIX_CONFIGS -- nothing here is specific to one name.
    This is Functional Validation (it energizes relay 1) -- see
    _functional_relay_numato(), which routes here from the shared
    hardware-category menu; called with no arguments it falls back to its
    own device picker for standalone use.

    Step 1: Verify factory + module interface (RelayBase subclass, all methods present).
    Step 2: Ping the configured IP -- network-layer reachability, before any TCP/Telnet attempt.
    Step 3: Check the relay's web interface (HTTP) -- confirms the unit is alive on the LAN.
    Step 4: Connect (TCP) + authenticate (Telnet login from NUMATO_RELAY_MATRIX_CONFIG) -- reported
            as two distinct steps so auth failures are never confused with network failures.
    Step 5: Relay 1 command protocol -- READ, ON, OFF (each command and its response
            reported independently).
    Step 6: Disconnect (leaving relay 1 open / safe).

    Every step is reported PASS/WARN/FAIL independently -- a failure at any step does
    not stop later steps from being attempted where it is safe to do so. Failure
    reasons are classified (network / authentication / protocol / timeout) via
    _classify_relay_error() so commissioning engineers get an actionable diagnosis
    instead of a bare exception -- and the full Telnet conversation (RX/TX, prompt
    detection, IAC negotiation) is visible via _numato_relay_debug_logging(), which
    wraps this entire test, in case the summary reason isn't enough on its own.
    """
    results = []
    if cfg is None:
        name, cfg, results = _discover_and_select(
            "Numato Relay Matrix", dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, _identify_relay_eth)
        if cfg is None:
            return results
    _print_device_config(name, cfg)

    host       = cfg.get("ip", "")
    port       = cfg.get("port", 23)
    driver     = cfg.get("driver", "RELAY32ETHRL00")
    user       = cfg.get("username", cfg.get("user", ""))
    config_ref = f"config/devices.py -> {name} ({driver} / {host}:{port})"

    with _numato_relay_debug_logging():
        results.extend(_run_relay_numato_matrix_test(cfg, name, host, port, driver, user, config_ref))
    return results


def _run_relay_numato_matrix_test(cfg, name, host, port, driver, user, config_ref):
    results = []

    # Step 1: factory + interface check  -- offline, no hardware ---------------
    try:
        from hardware.relay_factory import RelayFactory
        from hardware.relay import RelayBase
        relay = RelayFactory.create(cfg)
        if not isinstance(relay, RelayBase):
            results.append(_fail("Numato Relay Matrix", "Factory", config_ref,
                                 "RelayFactory did not return a RelayBase instance"))
            return results
        required = ["connect", "disconnect", "open", "close", "open_all",
                    "close_all", "query"]
        missing = [m for m in required if not callable(getattr(relay, m, None))]
        if missing:
            results.append(_fail("Numato Relay Matrix", "Interface", config_ref,
                                 f"Missing methods: {missing}"))
            return results
        results.append(_ok("Numato Relay Matrix", "Driver interface", config_ref,
                           f"RelayFactory -> NumatoRelayMatrix OK  ({driver} / {name})"))
    except Exception as e:
        results.append(_fail("Numato Relay Matrix", "Factory", config_ref,
                             f"Import / factory error: {e}"))
        return results

    # Step 2: ping -- network layer, before attempting TCP/Telnet ---------------
    ping_ok, ping_detail = _ping_host(host)
    if ping_ok:
        results.append(_ok("Numato Relay Matrix", "Ping", config_ref, f"{host} reachable"))
    else:
        results.append(_warn("Numato Relay Matrix", "Ping", config_ref,
                             f"Reason:\n{ping_detail} "
                             "(ICMP may be blocked -- Telnet may still succeed)"))

    # Step 3: web interface reachability -----------------------------------------
    web_ok, web_detail = _check_web_interface(host)
    if web_ok:
        results.append(_ok("Numato Relay Matrix", "Web Interface", config_ref, web_detail))
    else:
        results.append(_warn("Numato Relay Matrix", "Web Interface", config_ref,
                             f"Reason:\n{web_detail}"))

    # Step 4: connection + authentication ----------------------------------------
    # relay.connect() performs the TCP connect *and* the Telnet login in one
    # call -- report them as two lines by classifying the failure reason.
    try:
        relay.connect()
    except Exception as e:
        reason = _classify_relay_error(e)
        if reason.startswith("Authentication failed"):
            results.append(_ok("Numato Relay Matrix", "Ethernet Connection", config_ref,
                               f"TCP connected to {driver} at {host}:{port}"))
            results.append(_fail("Numato Relay Matrix", "Authentication", config_ref,
                                 f"Reason:\n{reason} (user='{user}')"))
        else:
            results.append(_fail("Numato Relay Matrix", "Ethernet Connection", config_ref,
                                 f"Reason:\n{reason}"))
            results.append(_fail("Numato Relay Matrix", "Authentication", config_ref,
                                 "Reason:\nNot attempted -- connection failed"))
        return results

    results.append(_ok("Numato Relay Matrix", "Ethernet Connection", config_ref,
                       f"TCP connected to {driver} at {host}:{port}"))
    results.append(_ok("Numato Relay Matrix", "Authentication", config_ref,
                       f"Telnet login OK (user='{user}')"))

    # Step 5: relay 1 command protocol -- READ, ON, OFF --------------------------
    # Each command is tried independently so one failure doesn't hide the rest.
    test_ch = 1

    try:
        state = relay.read(test_ch)   # "relay read 1"
        results.append(_ok("Numato Relay Matrix", f"Relay {test_ch} READ", config_ref,
                           f"relay read {test_ch} -> {'ON' if state else 'OFF'}"))
    except Exception as e:
        results.append(_fail("Numato Relay Matrix", f"Relay {test_ch} READ", config_ref,
                             f"Reason:\n{_classify_relay_error(e)}"))

    try:
        relay.close(test_ch)          # "relay on 1"
        results.append(_ok("Numato Relay Matrix", f"Relay {test_ch} ON", config_ref,
                           f"relay on {test_ch} sent OK"))
    except Exception as e:
        results.append(_fail("Numato Relay Matrix", f"Relay {test_ch} ON", config_ref,
                             f"Reason:\n{_classify_relay_error(e)}"))

    try:
        relay.open(test_ch)           # "relay off 1"
        results.append(_ok("Numato Relay Matrix", f"Relay {test_ch} OFF", config_ref,
                           f"relay off {test_ch} sent OK"))
    except Exception as e:
        results.append(_fail("Numato Relay Matrix", f"Relay {test_ch} OFF", config_ref,
                             f"Reason:\n{_classify_relay_error(e)}"))

    # Step 6: disconnect ----------------------------------------------------------
    try:
        relay.disconnect()
        results.append(_ok("Numato Relay Matrix", "Disconnect", config_ref,
                           f"Disconnected from {host}:{port}"))
    except Exception as e:
        results.append(_warn("Numato Relay Matrix", "Disconnect", config_ref, str(e)))

    return results


# =============================================================================
# 5c. Relay -- Ethernet full matrix scan (commissioning)
# =============================================================================

def test_relay_matrix_scan(name=None, cfg=None, channel_start=None, channel_end=None, scope_label=None):
    """
    Commissioning test: exercises every configured channel of the Numato
    Relay Matrix module -- ON, READ, OFF -- one connection for the whole scan.
    This is Functional Validation (it energizes every relay channel in turn)
    -- see _functional_relay_numato(), which routes here from the shared
    hardware-category menu; called with no arguments it falls back to its
    own device picker for standalone use.

    `channel_start`/`channel_end` (both optional, 1-based, inclusive) scope
    the scan to a subset of channels -- e.g. one battery group's channel
    range (see _select_relay_scope()/config/devices.py::BATTERY_GROUPS).
    Defaults to the full configured channel population when omitted, same
    as before this parameter existed. `scope_label` (e.g. "Group B" or
    "All Groups") is purely for the printed "Relay validation scope"/
    "Relays under test" banner below -- omitted (None) when called
    standalone (no scope selection happened), in which case no banner is
    printed, matching prior behavior exactly.

    Before scanning, device availability is verified (ping + connect/auth) --
    the scan itself only starts once the device is confirmed reachable and
    authenticated. Runs inside _numato_relay_debug_logging() so the full
    Telnet conversation is visible if connect()/auth fails.

    Channel count comes from config/devices.py NUMATO_RELAY_MATRIX_CONFIG["channel_count"]
    (falls back to "num_channels" for compat -- never hardcoded). A failure on
    any single channel is recorded as FAIL and the scan continues to the
    remaining channels -- it never aborts early.
    """
    if cfg is None:
        name, cfg = _select_device(dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, "Numato Relay Matrix devices")
        if cfg is None:
            return []
    _print_device_config(name, cfg)

    host         = cfg.get("ip", "")
    port         = cfg.get("port", 23)
    driver       = cfg.get("driver", "RELAY32ETHRL00")
    user         = cfg.get("username", cfg.get("user", ""))
    num_channels = cfg.get("channel_count", cfg.get("num_channels", 8))

    ch_start = 1 if channel_start is None else max(1, channel_start)
    ch_end   = num_channels if channel_end is None else min(num_channels, channel_end)
    scope_note = "" if (ch_start == 1 and ch_end == num_channels) else f", channels {ch_start}-{ch_end}"
    config_ref = f"config/devices.py -> {name} ({driver} / {host}:{port}, {num_channels} ch{scope_note})"

    if scope_label is not None:
        # Clear, unmissable confirmation of the applied scope -- printed
        # BEFORE the scan starts, for every scope choice (including "All
        # Groups"), not just non-default ones. See docs/architecture.md
        # "Relay Functional Validation -- Group-Scoped Matrix Scan" for the
        # bug this fixes (a scope selection that silently fell back to
        # scanning every channel).
        print(f"\nINFO Relay validation scope: {scope_label}")
        print(f"INFO Relays under test: {ch_start}-{ch_end}")

    # Safe Cancellation (see docs/architecture.md "Safe Cancellation
    # Architecture"): same pattern as run_main_test() -- Ctrl+C requests a
    # cooperative cancellation checked before each relay channel, rather
    # than raising KeyboardInterrupt mid-scan.
    import signal
    from utils.cancellation import CancellationToken

    token = CancellationToken(owner="test.py:relay_matrix_scan")
    previous_sigint_handler = signal.signal(
        signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
    )
    print("\nPress Ctrl+C to cancel safely.\n")
    try:
        with _numato_relay_debug_logging():
            return _run_relay_matrix_scan(cfg, host, port, driver, user, num_channels, config_ref, token,
                                           channel_start=ch_start, channel_end=ch_end)
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)


def _run_relay_matrix_scan(cfg, host, port, driver, user, num_channels, config_ref, token=None,
                            channel_start=1, channel_end=None):
    channel_end = num_channels if channel_end is None else channel_end
    from utils.cancellation import check_cancellation
    from utils.errors import OperationCancelledError

    results = []
    try:
        from hardware.relay_factory import RelayFactory
        relay = RelayFactory.create(cfg)
    except Exception as e:
        results.append(_fail("Relay Matrix Scan", "Factory", config_ref,
                             f"Import / factory error: {e}"))
        return results

    # Pre-scan device availability check -----------------------------------------
    ping_ok, ping_detail = _ping_host(host)
    if ping_ok:
        results.append(_ok("Relay Matrix Scan", "Device Availability -- Ping",
                           config_ref, f"{host} reachable"))
    else:
        results.append(_warn("Relay Matrix Scan", "Device Availability -- Ping",
                             config_ref,
                             f"Reason:\n{ping_detail} (ICMP may be blocked -- "
                             "Telnet may still succeed)"))

    try:
        relay.connect()
    except Exception as e:
        reason = _classify_relay_error(e)
        results.append(_fail("Relay Matrix Scan", "Device Availability -- Connect + Auth",
                             config_ref, f"Reason:\n{reason}"))
        results.append(_fail("Relay Matrix Scan", "Scan aborted", config_ref,
                             "Reason:\nDevice not available -- channel scan was not started"))
        return results

    results.append(_ok("Relay Matrix Scan", "Device Availability -- Connect + Auth",
                       config_ref, f"Connected and authenticated to {driver} at "
                       f"{host}:{port} (user='{user}')"))

    # Full channel scan -- ON, READ, OFF per channel ------------------------------
    try:
        for ch in range(channel_start, channel_end + 1):
            # Checkpoint: before starting a new channel, never mid-channel
            # (never between relay.close(ch)/relay.read(ch)/relay.open(ch)).
            try:
                check_cancellation(token)
            except OperationCancelledError as e:
                results.append(_warn("Relay Matrix Scan", "Cancelled by operator", config_ref,
                                     f"Reason:\n{e}\nRemaining channels not scanned."))
                try:
                    relay.open_all()
                except Exception:
                    pass
                break

            try:
                relay.close(ch)              # ON
                state = relay.read(ch)       # READ
                relay.open(ch)                # OFF
                if state:
                    results.append(_ok("Relay Matrix Scan", f"Relay {ch}", config_ref,
                                       "ON -> READ -> OFF  OK  (READ reported ON)"))
                else:
                    results.append(_warn("Relay Matrix Scan", f"Relay {ch}", config_ref,
                                         "ON -> READ -> OFF sent, but READ reported OFF "
                                         "-- verify wiring/relay bank"))
            except Exception as e:
                results.append(_fail("Relay Matrix Scan", f"Relay {ch}", config_ref,
                                     f"Reason:\n{_classify_relay_error(e)}"))
            finally:
                try:
                    relay.open(ch)   # leave each channel in the safe state
                except Exception:
                    pass
    finally:
        try:
            relay.disconnect()
        except Exception as e:
            results.append(_warn("Relay Matrix Scan", "Disconnect", config_ref, str(e)))

    return results


# =============================================================================
# 5d. RelayEthernetTest -- native Numato primitives, independent of the
#     public 1-based open()/close() API (see 5e for that layer's self-test)
# =============================================================================

def test_relay_ethernet_test(name=None, cfg=None):
    """
    RelayEthernetTest: validates the native Numato command primitives
    directly -- write(relay_number, state), read_all(), write_all(),
    verify_all() -- using Numato's own 0-based relay numbering, independent
    of the higher-level 1-based open()/close() API that
    test_relay_safety_selftest() exercises. This is Functional Validation
    (it energizes every relay in turn) -- see _functional_relay_numato(),
    which routes here from the shared hardware-category menu; called with
    no arguments it falls back to its own device picker for standalone use.

    Purpose: validate, before relay usage is integrated into higher-level
    battery test workflows --
        - Telnet communication
        - Numato native command handling (on/off/read/readall/writeall)
        - Relay state verification (individual + bulk)
        - Safe relay sequencing
        - Driver architecture
        - Hardware operation

    Relay count comes from configuration (NUMATO_RELAY_MATRIX_CONFIG channel_count,
    itself sourced from Settings.RELAY_COUNT) -- never hardcoded here.

    Sequence per relay_index in range(RELAY_COUNT):
        write_all(OFF) -> read_all() -> verify all OFF
        write(relay_index, ON) -> read_all() -> verify relay_index ON, rest OFF
        write_all(OFF) -> read_all() -> verify all OFF

    Fails immediately on any mismatch -- does not continue to the remaining
    relays once one has failed.
    """
    if cfg is None:
        name, cfg = _select_device(dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, "Numato Relay Matrix devices")
        if cfg is None:
            return []
    _print_device_config(name, cfg)

    host        = cfg.get("ip", "")
    port        = cfg.get("port", 23)
    driver      = cfg.get("driver", "RELAY32ETHRL00")
    relay_count = cfg.get("channel_count", cfg.get("num_channels", Settings.RELAY_COUNT))
    config_ref  = f"config/devices.py -> {name} ({driver} / {host}:{port}, RELAY_COUNT={relay_count})"
    results     = []

    try:
        from hardware.relay_factory import RelayFactory
        relay = RelayFactory.create(cfg)
    except Exception as e:
        return [_fail("RelayEthernetTest", "Factory", config_ref,
                      f"Import / factory error: {e}")]

    # Safe Cancellation (see docs/architecture.md "Safe Cancellation
    # Architecture"): same pattern as test_relay_matrix_scan() -- Ctrl+C
    # requests a cooperative cancellation checked before each relay index,
    # never mid-index (never between write_all(0)/write(relay_index, True)/
    # verify_all()).
    import signal
    from utils.cancellation import CancellationToken, check_cancellation
    from utils.errors import OperationCancelledError

    token = CancellationToken(owner="test.py:relay_ethernet_test")
    previous_sigint_handler = signal.signal(
        signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
    )
    print("\nPress Ctrl+C to cancel safely.\n")

    try:
        with _numato_relay_debug_logging():
            try:
                relay.connect()
            except Exception as e:
                return [_fail("RelayEthernetTest", "Connect + Auth", config_ref,
                              f"Reason:\n{_classify_relay_error(e)}\n"
                              f"Test aborted -- device not available.")]

            results.append(_ok("RelayEthernetTest", "Connect + Auth", config_ref,
                               f"Connected and authenticated to {driver} at {host}:{port}"))

            stopped_early = False
            cancelled = False
            for relay_index in range(relay_count):
                try:
                    check_cancellation(token)
                except OperationCancelledError as e:
                    results.append(_warn("RelayEthernetTest", "Cancelled by operator", config_ref,
                                         f"Reason:\n{e}\nRemaining relays not tested."))
                    try:
                        relay.write_all(0)
                        relay.verify_all(0)
                    except Exception:
                        pass
                    cancelled = True
                    break

                print(f"\n  -- Relay index {relay_index}/{relay_count - 1} (native 0-based) --")
                try:
                    # STEP 1-2 of the Relay Safety Verification Pattern (see
                    # docs/architecture.md): read + log the bank's CURRENT
                    # state before forcing anything off. This test bypasses
                    # close()/open() on purpose (it validates the native
                    # command layer independently of that wrapper), so it
                    # calls the same shared check the wrapper uses
                    # internally, rather than skipping steps 1-2 entirely.
                    relay.check_current_relay_state(context=f"RelayEthernetTest relay {relay_index}")

                    relay.write_all(0)
                    relay.verify_all(0)

                    relay.write(relay_index, True)
                    relay.verify_all(1 << relay_index)

                    relay.write_all(0)
                    relay.verify_all(0)

                    results.append(_ok(
                        "RelayEthernetTest", f"Relay index {relay_index}", config_ref,
                        "read_all (pre-check) -> write_all(OFF) -> verify -> write(ON) -> "
                        "verify -> write_all(OFF) -> verify  PASS"
                    ))
                except Exception as e:
                    results.append(_fail(
                        "RelayEthernetTest", f"Relay index {relay_index}", config_ref,
                        f"Relay Number    : {relay_index}\n"
                        f"Expected State  : see sequence step that failed above\n"
                        f"Actual State    : verification failed (see cause)\n"
                        f"Cause           : {e}"
                    ))
                    stopped_early = True
                    break   # fail immediately -- do not continue to remaining relays

            if stopped_early and not cancelled:
                results.append(_fail("RelayEthernetTest", "Test aborted", config_ref,
                                     "Stopped at first failure -- remaining relays not tested"))

            try:
                relay.disconnect()
            except Exception as e:
                results.append(_warn("RelayEthernetTest", "Disconnect", config_ref, str(e)))
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)

    return results


# =============================================================================
# 5e. Relay -- Ethernet mandatory safety self-test (channels 1-32)
# =============================================================================

def test_relay_safety_selftest(name=None, cfg=None):
    """
    This is Functional Validation (it energizes every relay channel in
    turn) -- see _functional_relay_numato(), which routes here from the
    shared hardware-category menu; called with no arguments it falls back
    to its own device picker for standalone use.

    Validates the mandatory relay safety sequence (hardware/relay_eth.py
    NumatoRelayMatrix.close()/open()) against every configured channel,
    individually, in order:

        For relay N = 1 .. num_channels:
            OFF ALL
            VERIFY OFF                  (relay.close(N) does this internally)
            ON relay N
            VERIFY relay N ON, all others OFF
        Then: OFF ALL / VERIFY OFF

    STOPS IMMEDIATELY on the first failure of any kind -- connection error,
    timeout, readback mismatch, unexpected active relay, parser error, or
    verification failure. It does NOT continue to remaining channels, so a
    partial PASS list plus one FAIL entry means the scan was cut short there
    by design, not that the untested channels are known-good.

    Every relay operation is logged in detail (requested relay, command
    sent, raw readback, decoded mask, decoded active channels, PASS/FAIL)
    via hardware.relay_eth's own logger, which test.py normally silences
    (logging.disable at import time) -- this test temporarily re-enables it
    to stdout for the duration of the run so the audit trail in requirement
    4 is visible, then restores the previous logging state.

    NOTE: the exact byte format of the Numato "relay readall" response has
    been CONFIRMED against the physical unit -- a live 32-channel matrix
    scan (see test_relay_matrix_scan()) passed end-to-end and the decoded
    ACTIVE channel list matched the physically observed relay state at
    every step. If a firmware update ever changes this format,
    hardware/relay_eth.py::_parse_readall_response() is the one place to fix it.
    """
    if cfg is None:
        name, cfg = _select_device(dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, "Numato Relay Matrix devices")
        if cfg is None:
            return []
    _print_device_config(name, cfg)

    host         = cfg.get("ip", "")
    port         = cfg.get("port", 23)
    driver       = cfg.get("driver", "RELAY32ETHRL00")
    num_channels = cfg.get("channel_count", cfg.get("num_channels", 8))
    config_ref   = f"config/devices.py -> {name} ({driver} / {host}:{port}, {num_channels} ch)"
    results      = []

    try:
        from hardware.relay_factory import RelayFactory
        relay = RelayFactory.create(cfg)
    except Exception as e:
        return [_fail("Relay Safety Self-Test", "Factory", config_ref,
                      f"Import / factory error: {e}")]

    # test.py silences all logging at import time, but the detailed per-command
    # audit trail (requested relay / command sent / readback / verification)
    # is the whole point of this test -- _numato_relay_debug_logging()
    # re-enables it (DEBUG level, includes the Telnet login transcript too).
    with _numato_relay_debug_logging():
        try:
            relay.connect()
        except Exception as e:
            return [_fail("Relay Safety Self-Test", "Connect + Auth", config_ref,
                          f"Reason:\n{_classify_relay_error(e)}\n"
                          f"Self-test aborted -- device not available.")]

        results.append(_ok("Relay Safety Self-Test", "Connect + Auth", config_ref,
                           f"Connected and authenticated to {driver} at {host}:{port}"))

        stopped_early = False
        for ch in range(1, num_channels + 1):
            print(f"\n  -- Relay {ch}/{num_channels} --")
            try:
                relay.close(ch)   # OFF ALL -> VERIFY OFF -> ON ch -> VERIFY ch-only-ON
                results.append(_ok("Relay Safety Self-Test", f"Relay {ch}", config_ref,
                                   f"OFF ALL -> VERIFY OFF -> ON {ch} -> VERIFY {ch} only  PASS"))
                relay.open(ch)    # OFF ALL -> VERIFY OFF, restore safe state before next channel
            except Exception as e:
                results.append(_fail(
                    "Relay Safety Self-Test", f"Relay {ch}", config_ref,
                    f"Relay Number    : {ch}\n"
                    f"Expected State  : ONLY relay {ch} ON, all others OFF\n"
                    f"Actual State    : verification failed (see cause)\n"
                    f"Cause           : {e}"
                ))
                stopped_early = True
                break   # STOP IMMEDIATELY -- do not continue to remaining channels

        if stopped_early:
            results.append(_fail("Relay Safety Self-Test", "Self-test aborted", config_ref,
                                 "Stopped at first failure -- remaining channels not tested"))
        else:
            # Final OFF ALL / VERIFY OFF after relay 32 (or the highest configured channel)
            try:
                relay.open_all()
                results.append(_ok("Relay Safety Self-Test", "Final OFF ALL", config_ref,
                                   "All relays forced OFF and verified OFF after full sweep"))
            except Exception as e:
                results.append(_fail("Relay Safety Self-Test", "Final OFF ALL", config_ref,
                                     f"Cause: {e}"))

        try:
            relay.disconnect()
        except Exception as e:
            results.append(_warn("Relay Safety Self-Test", "Disconnect", config_ref, str(e)))

    return results


# =============================================================================
# 5f. Numato Relay Matrix -- hardware-category menu entry (Identity +
#     Functional Validation), reusing the tests above
# =============================================================================

def test_relay_numato():
    """
    Menu entry for the Numato Relay Matrix hardware category. Lists every
    configured Numato Relay Matrix device (config/devices.py::
    NUMATO_RELAY_MATRIX_CONFIGS), then routes to Identity Validation
    (_identify_relay_eth -- TCP connect + Telnet login + readall only, never
    energizes a relay) or Functional Validation (_functional_relay_numato --
    a submenu of the existing relay-energizing tests below), via the shared
    _run_hardware_category() workflow.
    """
    return _run_hardware_category("Numato Relay Matrix (Ethernet)",
                                   dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS,
                                   _identify_relay_eth, _functional_relay_numato)


def _select_relay_scope():
    """
    Scope-selection menu for Relay Functional Validation -- lets a scan be
    restricted to one group's channel range on the CURRENTLY SELECTED relay
    matrix device: Group A = channels 1-8, Group B = 9-16, Group C = 17-24,
    Group D = 25-32 (see config/devices.py::BATTERY_GROUPS' position_start/
    position_end).

    Deliberately NOT gated on BATTERY_GROUPS[group]["enabled"]. That flag
    means "no battery relay matrix has been deployed/wired for this group
    yet for actual battery testing" -- a battery-wiring concern relevant
    only to _select_battery_group()/Monitor Battery. Relay Functional
    Validation tests raw relay hardware on whichever device is already
    selected, completely independent of whether a battery is wired to those
    channels -- channels 9-32 are just as real and safely testable on a
    32-channel Numato matrix as channels 1-8, even before any battery relay
    matrix exists for Group B/C/D. Gating this scope selector on `enabled`
    was a bug: it silently collapsed every Group B/C/D selection back to
    "All Groups" (scanning all 32 channels instead of the requested 8),
    since every group but A currently has `enabled=False`.

    Returns (label, channel_start, channel_end) -- label is "All Groups" or
    "Group <X>" (for the "Relay validation scope: ..." banner the caller
    prints); channel_start/channel_end are 1-based, inclusive, or
    (None, None) for "All Groups" (the full configured population,
    resolved against the actual device's channel count by the caller).
    """
    print("\nRelay Validation Scope")
    print("1. All Groups")
    print("2. Group A")
    print("3. Group B")
    print("4. Group C")
    print("5. Group D")
    choice = input("\nScope: ").strip()
    group_by_choice = {"2": "A", "3": "B", "4": "C", "5": "D"}
    if choice in ("", "1"):
        return "All Groups", None, None
    group = group_by_choice.get(choice)
    grp = dev_cfg.BATTERY_GROUPS.get(group) if group else None
    if grp is None:
        print("Invalid selection -- defaulting to All Groups.")
        return "All Groups", None, None
    return f"Group {group}", grp["position_start"], grp["position_end"]


def _test_relay_matrix_scan_scoped(name=None, cfg=None):
    """Matrix Scan wrapper that prompts for a scope (see _select_relay_scope())
    before running -- the Functional Validation menu entry point."""
    scope_label, channel_start, channel_end = _select_relay_scope()
    return test_relay_matrix_scan(name, cfg, channel_start=channel_start, channel_end=channel_end,
                                   scope_label=scope_label)


def _functional_relay_numato(name: str, cfg: dict):
    """
    Functional Validation submenu for one Numato Relay Matrix device --
    groups the existing, already-implemented relay-energizing tests (each
    closes/opens real relay channels) under one menu entry instead of
    exposing them as separate top-level menu items. Every option here
    changes hardware state -- none of this belongs in Identity Validation.
    """
    options = [
        ("Relay 1 quick check (READ / ON / OFF)",        test_relay_numato_matrix),
        ("Matrix Scan (ON -> READ -> OFF, scoped by group)", _test_relay_matrix_scan_scoped),
        ("RelayEthernetTest (native 0-based primitives)", test_relay_ethernet_test),
        ("Safety Self-Test (1..N, stop on first failure)", test_relay_safety_selftest),
    ]
    print(f"\n{name} -- Functional Validation\n")
    for i, (label, _fn) in enumerate(options, 1):
        print(f"[{i}] {label}")
    print("0. Back")
    try:
        raw = input("\nChoice: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return []
    if raw == "0" or raw == "":
        return []
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(options):
            raise ValueError()
    except ValueError:
        print("Invalid choice.")
        return []
    _, fn = options[idx]
    return fn(name, cfg)


# =============================================================================
# 5g. PXI Relay Matrix -- hardware-category menu entry (Identity Validation
#     only; no niswitch-based driver exists in this codebase)
# =============================================================================

def test_pxi_relay_matrix():
    """
    Menu entry for the PXI-resident switch/relay card (PXI_SLOTS[11],
    category="switch", nickname CHASSIS_RELAY_MATRIX -- physically present
    in the rack, but NOT the active relay driver; see PXI_SLOTS[11]'s
    validation_notes). Routes to Identity Validation (_identify_switch --
    always reports N/A, since no niswitch-based driver exists) via the
    shared _run_hardware_category() workflow. No Functional Validation is
    implemented -- there is nothing to validate functionally without a
    driver.
    """
    devices = _pxi_slots_by_category("switch")
    return _run_hardware_category("PXI Relay Matrix", devices, _identify_switch)


# =============================================================================
# 6. Sensors (NTC temperature)
# =============================================================================

def test_sensors():
    """
    Two parts:

    Part 1 (Tests 1-5, unchanged): exercises hardware/temperature.py's pure
    NTC-thermistor math offline -- 25 degC reference point, out-of-range
    guards, monotonicity, TemperatureSensor class interface. No hardware.

    Part 2 (Test 6, DAQ-based): reads every ENABLED NTC channel through the
    real DAQ (hardware/daq.py::DAQ.read_channel()) and converts each reading
    via ntc_voltage_to_celsius() -- this is the future battery-temperature
    acquisition architecture (see docs/architecture.md "NTC Sensor
    Acquisition -- DAQ Architecture"): temperature monitoring is expected to
    come entirely through this per-position DAQ channel path, NOT through a
    separate Temperature Module (PXIe-4353/TEMP_MODULE, see
    test_temperature_module()'s retirement note).

    Which NTC channels are "enabled" is config-driven, reusing the SAME
    per-position `enabled` flag and `daq_ntc_ch` field config/devices.py::
    BATTERY_CHANNELS already carries (no new/duplicate configuration
    variable introduced) -- this test iterates
    `{i: ch for i, ch in BATTERY_CHANNELS.items() if ch["enabled"]}` rather
    than any hardcoded channel list or count. Disabling a position in
    BATTERY_CHANNELS (e.g. while its wiring is unconfirmed) automatically
    removes it from this scan with no code change here.

    Test 6 requires a real DAQ and is reported per-channel (PASS/FAIL) --
    a missing/unreachable DAQ fails every channel with a clear reason
    rather than raising, so this menu item still completes cleanly on a
    laptop with no rack attached (Part 1 always runs regardless).
    """
    config_ref = "hardware/temperature.py  Beta=3950 K  R25=10 kOhm  Vcc=3.3 V"
    results    = []

    # -- Module import + function test ----------------------------------------
    try:
        from hardware.temperature import (
            ntc_voltage_to_celsius, TemperatureSensor,
            NTC_BETA, NTC_R25_OHM, NTC_VCC
        )
    except ImportError as e:
        return [_fail("Sensors", "NTC", config_ref, f"Import error: {e}")]

    # Test 1: Reference point 25 degC at 1.65 V
    v_ref = NTC_VCC / 2.0   # 1.65 V for 3.3 V supply with matched divider
    t = ntc_voltage_to_celsius(v_ref)
    if t is None:
        results.append(_fail("Sensors", "NTC", config_ref,
                             f"V={v_ref} V returned None unexpectedly"))
    elif abs(t - 25.0) <= 2.0:
        results.append(_ok("Sensors", "NTC", config_ref,
                           f"V={v_ref} V -> {t:.2f} degC (expected ~25 degC)  OK"))
    else:
        results.append(_warn("Sensors", "NTC", config_ref,
                             f"V={v_ref} V -> {t:.2f} degC (expected ~25 degC) "
                             f"-- verify Beta={NTC_BETA} K from datasheet"))

    # Test 2: Out-of-range guard (V=0)
    t_oob = ntc_voltage_to_celsius(0.0)
    if t_oob is None:
        results.append(_ok("Sensors", "NTC guard (V=0)", config_ref,
                           "V=0 V correctly returns None"))
    else:
        results.append(_warn("Sensors", "NTC guard (V=0)", config_ref,
                             f"V=0 V returned {t_oob} instead of None"))

    # Test 3: Out-of-range guard (V=Vcc)
    t_oob2 = ntc_voltage_to_celsius(NTC_VCC)
    if t_oob2 is None:
        results.append(_ok("Sensors", f"NTC guard (V=Vcc)", config_ref,
                           f"V={NTC_VCC} V correctly returns None"))
    else:
        results.append(_warn("Sensors", f"NTC guard (V=Vcc)", config_ref,
                             f"V={NTC_VCC} V returned {t_oob2} instead of None"))

    # Test 4: Monotonicity
    # Divider: higher V -> larger R_NTC -> lower T
    t_lo_v = ntc_voltage_to_celsius(1.0)   # low V -> small R_NTC -> hot (~45 degC)
    t_hi_v = ntc_voltage_to_celsius(2.5)   # high V -> large R_NTC -> cold (~1 degC)
    if t_lo_v is not None and t_hi_v is not None and t_lo_v > t_hi_v:
        results.append(_ok("Sensors", "NTC monotonicity", config_ref,
                           f"V=1.0V -> {t_lo_v:.1f} degC,  V=2.5V -> {t_hi_v:.1f} degC  OK"))
    else:
        results.append(_warn("Sensors", "NTC monotonicity", config_ref,
                             "Monotonicity check failed -- review divider topology"))

    # Test 5: TemperatureSensor class interface
    try:
        sensor = TemperatureSensor(channel=1)
        t_class = sensor.read_celsius(v_ref)
        if t_class is not None and abs(t_class - 25.0) <= 2.0:
            results.append(_ok("Sensors", "TemperatureSensor class", config_ref,
                               f"TemperatureSensor.read_celsius({v_ref}) = {t_class:.2f} degC  OK"))
        else:
            results.append(_warn("Sensors", "TemperatureSensor class", config_ref,
                                 f"read_celsius returned {t_class}"))
    except Exception as e:
        results.append(_fail("Sensors", "TemperatureSensor class", config_ref, str(e)))

    # Test 6: DAQ-based NTC channel scan -- the future battery-temperature
    # acquisition architecture. Iterates every ENABLED BATTERY_CHANNELS
    # entry's daq_ntc_ch -- config-driven, never a hardcoded channel list.
    enabled_channels = {i: ch for i, ch in dev_cfg.BATTERY_CHANNELS.items() if ch.get("enabled")}
    if not enabled_channels:
        results.append(_warn("Sensors", "NTC DAQ scan", config_ref,
                             "No enabled BATTERY_CHANNELS entries -- nothing to scan"))
        return results

    daq_cfg = dev_cfg.DAQ_CONFIG
    daq_ref = f"{daq_cfg.get('resource', '')} / {daq_cfg.get('model', 'NI-6363')}"
    from hardware.daq import DAQ
    daq = DAQ(daq_cfg)
    try:
        daq.connect()
    except Exception as e:
        for i in enabled_channels:
            results.append(_fail("Sensors", f"NTC DAQ scan -- BAT_{i}", daq_ref,
                                 f"[ERROR] DAQ not detected or connect failed\nReason: {e}"))
        return results

    try:
        for i, ch in enabled_channels.items():
            ntc_ch = ch["daq_ntc_ch"]
            try:
                v = daq.read_channel(ntc_ch)
                t_c = ntc_voltage_to_celsius(v)
                if t_c is None:
                    results.append(_warn("Sensors", f"NTC DAQ scan -- BAT_{i}", daq_ref,
                                         f"Channel {ntc_ch}: {v:.4f} V -- out of NTC divider range "
                                         f"(0 < V < {NTC_VCC} V), no valid temperature"))
                else:
                    results.append(_ok("Sensors", f"NTC DAQ scan -- BAT_{i}", daq_ref,
                                       f"Channel {ntc_ch}: {v:.4f} V -> {t_c:.2f} degC"))
            except Exception as e:
                results.append(_fail("Sensors", f"NTC DAQ scan -- BAT_{i}", daq_ref,
                                     f"Channel {ntc_ch} read failed: {e}"))
    finally:
        try:
            daq.disconnect()
        except Exception:
            pass

    return results


# =============================================================================
# 7. Safety Monitor  (real logic, no hardware required)
# =============================================================================
#
# Part 2 of this menu item (below the unit tests) is a workflow-oriented
# WALKTHROUGH SIMULATOR -- the development reference implementation for
# Monitor/Charge/Discharge/Cycle Battery (see docs/architecture.md "Safety
# Monitor Workflow Simulator"). After the operator picks one workflow, it
# steps through the OPERATIONAL SEQUENCE that workflow's real code executes
# (Monitor/Charge/Discharge are implemented in software today -- see
# test_control/monitor_battery_sequence.py, test_control/charge_sequence.py,
# test_control/discharge_sequence.py; Cycle Battery remains genuinely
# unimplemented, this walkthrough is its forward-looking blueprint only)
# -- not just the safety decisions, but every action in order: load config,
# resolve group/position/relay routing, close relay, configure/enable the
# PSU, acquire a measurement, run the REAL SafetyMonitor check, update
# ExecutionFrame, store the measurement, evaluate phase transitions, and so
# on. As with the real workflows, this simulator selects a battery GROUP,
# never a battery type directly -- battery type and test setpoints are
# derived from the selected group via config/devices.py::
# group_test_config(), exactly mirroring test.py::_run_charge_or_discharge()
# (see docs/architecture.md "Architectural Correction: Battery Type Is
# Never Operator Input"). This walkthrough must stay reconciled with the
# real Charge/Discharge implementations as they evolve -- simulator drift
# from the real architecture is not acceptable (see docs/architecture.md
# "Simulator & Reference-Blueprint Reconciliation").
#
# Every step pauses for the operator (Enter to continue) so the sequence
# can be observed one action at a time. NOTHING here touches hardware, a
# relay, an instrument, or a database -- these are development-only
# console messages: no measurements/event_log/run_summary/station_state
# row is ever written by this simulator.

def _render_and_check_step(monitor, workflow_name: str, phase: str, step_num: int, total_steps: int,
                            description: str, voltage_v: float = None, current_a: float = None,
                            temp_c: float = None, run_safety_check: bool = False,
                            relay_switch_check: bool = False, next_action: str = "",
                            note: str = None, pause: bool = True, mode: str = None):
    """
    Render one workflow step (Workflow/Current Phase/Current Step/
    Description, then Voltage/Current/Temperature/Safety Evaluation/
    Decision/Next Action) and, if requested, run the REAL SafetyMonitor
    check(s) against the simulated values. Steps that only describe an
    operational action (load config, resolve routing, update
    ExecutionFrame, ...) pass no voltage/current/temp and neither check
    flag -- they display "N/A" and a CONTINUE decision, exactly like any
    other step, since the walkthrough's point is showing the FULL
    sequence, not only the steps with a safety check.

    Never touches hardware/relay/instrument/database -- console output
    plus test_control/safety_monitor.py's real logic only. Returns
    (safe: bool, reason: str | None).
    """
    print(f"\n{'-' * 60}")
    print(f"Workflow     : {workflow_name}")
    print(f"Current Phase: {phase}")
    print(f"Current Step : {step_num}/{total_steps}")
    print(f"Description  : {description}")
    print(f"{'-' * 60}")

    v_display = "N/A" if voltage_v is None else f"{voltage_v:.3f} V"
    i_display = "N/A" if current_a is None else f"{current_a:.3f} A"
    t_display = "N/A" if temp_c is None else f"{temp_c:.1f} degC"
    print(f"Voltage      : {v_display}")
    print(f"Current      : {i_display}")
    print(f"Temperature  : {t_display}")

    safe, reason = True, None
    if relay_switch_check:
        rs_ok = monitor.is_safe_to_switch_relay(current_a if current_a is not None else 0.0)
        print(f"Safety Evaluation: is_safe_to_switch_relay(I={i_display}) -> {rs_ok}")
        if not rs_ok:
            safe = False
            reason = f"Relay switch blocked -- current {current_a:.3f} A not near zero"
    elif run_safety_check:
        s = monitor.check(voltage_v=voltage_v, current_a=current_a, temp_c=temp_c, mode=mode)
        detail = f"  reason={s.reason}" if not s.safe else ""
        print(f"Safety Evaluation: check(V={voltage_v:.3f}, I={current_a:.3f}, T={temp_c}, mode={mode}) "
              f"-> safe={s.safe}{detail}")
        if not s.safe:
            safe, reason = False, s.reason
    else:
        print("Safety Evaluation: N/A (no SafetyMonitor check performed at this step)")

    if safe:
        print("Decision     : CONTINUE")
    else:
        print(f"Decision     : ABORT -- {reason}")
    print(f"Next Action  : {next_action}")
    if note:
        print(f"Note         : {note}")

    if pause:
        try:
            input("\nPress Enter for next step...")
        except (KeyboardInterrupt, EOFError):
            print()

    return safe, reason


def _run_workflow_walkthrough(monitor, workflow_name: str, steps: list, expect_abort: bool = False):
    """
    Walk `steps` (phase/description/voltage_v/current_a/temp_c/
    run_safety_check/relay_switch_check/next_action/note dicts) in order
    via _render_and_check_step(), stopping immediately on the first unsafe
    result -- the same "stop at first failure, never continue" discipline
    ProtoTestSequence/MonitorBatterySequence/the relay safety self-test all
    already use. `expect_abort=True` marks a scenario that deliberately
    injects an unsafe reading to demonstrate the abort path -- aborting
    there is the CORRECT outcome, not a failure.
    """
    print(f"\n{'=' * 60}")
    print(f"  {workflow_name} -- Workflow Simulation")
    print(f"{'=' * 60}")
    config_ref = "test_control/safety_monitor.py (simulated measurements, no hardware, no database)"
    total = len(steps)

    for i, step in enumerate(steps, 1):
        safe, reason = _render_and_check_step(
            monitor, workflow_name, step["phase"], i, total, step["description"],
            voltage_v=step.get("voltage_v"), current_a=step.get("current_a"), temp_c=step.get("temp_c"),
            run_safety_check=step.get("run_safety_check", False),
            relay_switch_check=step.get("relay_switch_check", False),
            next_action=step.get("next_action", ""), note=step.get("note"),
            mode=step.get("mode"),
        )
        if not safe:
            if expect_abort:
                return _ok("Safety Monitor Simulation", workflow_name, config_ref,
                           f"Correctly aborted at step {i}/{total} ('{step['description']}') -- {reason}")
            return _fail("Safety Monitor Simulation", workflow_name, config_ref,
                         f"Unexpected abort at step {i}/{total} ('{step['description']}') -- {reason}")

    if expect_abort:
        return _fail("Safety Monitor Simulation", workflow_name, config_ref,
                     "Expected an abort during this scenario, but every step passed")
    return _ok("Safety Monitor Simulation", workflow_name, config_ref,
               f"All {total} steps completed -- PASS")


def _monitor_battery_walkthrough_steps():
    """
    Operational sequence mirroring test.py::_run_monitor_battery() +
    test_control/monitor_battery_sequence.py::MonitorBatterySequence.run()
    -- load config through two monitoring samples to operator-requested
    shutdown. This IS the real, already-implemented workflow's shape.
    """
    dev_note = ("Development-only simulation -- NOT written to measurements/"
                "event_log/run_summary/station_state.")
    return [
        {"phase": "INIT", "description": "Load battery configuration",
         "next_action": "Resolve battery group"},
        {"phase": "INIT", "description": "Resolve battery group",
         "next_action": "Resolve battery position"},
        {"phase": "INIT", "description": "Resolve battery position",
         "next_action": "Resolve relay routing"},
        {"phase": "INIT", "description": "Resolve relay routing",
         "next_action": "Close relay 3"},
        {"phase": "RELAY_ROUTING", "description": "Close relay 3",
         "voltage_v": 3.70, "current_a": 0.0, "temp_c": 25.0, "relay_switch_check": True,
         "next_action": "Acquire voltage measurement"},
        {"phase": "MONITORING", "description": "Acquire voltage measurement",
         "voltage_v": 3.71, "current_a": 0.0, "temp_c": 25.2,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "MONITORING", "description": "Run SafetyMonitor checks",
         "voltage_v": 3.71, "current_a": 0.0, "temp_c": 25.2, "run_safety_check": True,
         "next_action": "Update ExecutionFrame"},
        {"phase": "MONITORING", "description": "Update ExecutionFrame",
         "next_action": "Store measurement"},
        {"phase": "MONITORING", "description": "Store measurement", "note": dev_note,
         "next_action": "Repeat monitoring loop"},
        {"phase": "MONITORING", "description": "Repeat monitoring loop",
         "next_action": "Acquire voltage measurement"},
        {"phase": "MONITORING", "description": "Acquire voltage measurement",
         "voltage_v": 3.69, "current_a": 0.0, "temp_c": 25.1,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "MONITORING", "description": "Run SafetyMonitor checks",
         "voltage_v": 3.69, "current_a": 0.0, "temp_c": 25.1, "run_safety_check": True,
         "next_action": "Update ExecutionFrame"},
        {"phase": "MONITORING", "description": "Update ExecutionFrame",
         "next_action": "Store measurement"},
        {"phase": "MONITORING", "description": "Store measurement", "note": dev_note,
         "next_action": "Operator stop (Ctrl+C) or continue loop"},
        {"phase": "SHUTDOWN", "description": "Operator requests stop (Ctrl+C simulated)",
         "next_action": "Open relay 3"},
        {"phase": "SHUTDOWN", "description": "Open relay 3",
         "voltage_v": 3.69, "current_a": 0.0, "temp_c": 25.1, "relay_switch_check": True,
         "next_action": "Monitoring session complete"},
    ]


def _charge_phase_steps(cycle_number: int = None, test_setpoints: dict = None):
    """
    Simulated operational sequence mirroring the real, implemented Charge
    Battery workflow (relay close -> configure/enable PSU -> CC charge ->
    CV taper -> cutoff -> PSU disable -> relay open) -- see
    test_control/charge_sequence.py::ChargeSequence.run().

    `test_setpoints` (a config/devices.py BATTERY_GROUPS[group]
    ["test_setpoints"] entry, resolved from the selected group via
    group_test_config() -- NOT a BATTERY_CONFIGS entry), if given, drives
    the commanded/simulated voltage and current values
    (charge_current_a/charge_voltage_v) -- matching what
    ChargeSequence.run() actually commands. This mirrors the real
    distinction: BATTERY_CONFIGS is a safety limit, never a commanded
    setpoint (see docs/architecture.md "Battery Group Test Configuration
    Architecture") -- this function no longer reads battery_cfg at all,
    exactly like the real ChargeSequence. test_setpoints=None falls back
    to the global Settings.CHARGE_VOLTAGE_V/CHARGE_CURRENT_A constants,
    unchanged fallback behavior. The CV-taper cutoff current
    (CHARGE_CUTOFF_A) has no per-group equivalent and stays a global
    Settings constant -- matching ChargeSequence.run() exactly (a
    deliberate scope boundary, not an oversight). `cycle_number`, if
    given, prefixes each description (for reuse inside the Cycle Battery
    walkthrough below).
    """
    prefix = f"[Cycle {cycle_number}] " if cycle_number else ""
    dev_note = ("Development-only simulation -- NOT written to measurements/"
                "event_log/run_summary/station_state.")
    charge_v = test_setpoints["charge_voltage_v"] if test_setpoints else Settings.CHARGE_VOLTAGE_V
    charge_i = test_setpoints["charge_current_a"] if test_setpoints else Settings.CHARGE_CURRENT_A
    return [
        {"phase": "INIT", "description": prefix + "Load battery configuration",
         "next_action": "Resolve relay routing"},
        {"phase": "INIT", "description": prefix + "Resolve relay routing",
         "next_action": "Close relay"},
        {"phase": "RELAY_ROUTING", "description": prefix + "Close relay",
         "voltage_v": 3.60, "current_a": 0.0, "temp_c": 25.0, "relay_switch_check": True,
         "next_action": "Configure PSU limits"},
        {"phase": "CONFIG",
         "description": prefix + f"Configure PSU limits (V={charge_v:.2f} V, "
                                  f"I_limit={charge_i:.3f} A)",
         "next_action": "Enable PSU output"},
        {"phase": "CC_CHARGE", "description": prefix + "Enable PSU output",
         "voltage_v": 3.70, "current_a": 0.0, "temp_c": 25.0,
         "next_action": "Acquire measurements"},
        {"phase": "CC_CHARGE", "description": prefix + "Acquire measurements",
         "voltage_v": min(3.95, charge_v), "current_a": charge_i, "temp_c": 27.0,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "CC_CHARGE", "description": prefix + "Run SafetyMonitor checks",
         "voltage_v": min(3.95, charge_v), "current_a": charge_i, "temp_c": 27.0,
         "run_safety_check": True, "mode": "charge", "next_action": "Update ExecutionFrame"},
        {"phase": "CC_CHARGE", "description": prefix + "Update ExecutionFrame",
         "next_action": "Store measurement"},
        {"phase": "CC_CHARGE", "description": prefix + "Store measurement", "note": dev_note,
         "next_action": "Evaluate CC/CV transition"},
        {"phase": "CC_CHARGE", "description": prefix + "Evaluate CC/CV transition",
         "next_action": "Continue charge (CV taper)"},
        {"phase": "CV_TAPER", "description": prefix + "Continue charge",
         "voltage_v": charge_v, "current_a": charge_i * 0.3,
         "temp_c": 28.0, "next_action": "Acquire measurements"},
        {"phase": "CV_TAPER", "description": prefix + "Acquire measurements",
         "voltage_v": charge_v, "current_a": Settings.CHARGE_CUTOFF_A, "temp_c": 27.5,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "CV_TAPER", "description": prefix + "Run SafetyMonitor checks",
         "voltage_v": charge_v, "current_a": Settings.CHARGE_CUTOFF_A, "temp_c": 27.5,
         "run_safety_check": True, "mode": "charge", "next_action": "Evaluate cutoff condition"},
        {"phase": "CUTOFF_DETECTED",
         "description": prefix + f"Evaluate cutoff condition (I <= {Settings.CHARGE_CUTOFF_A:.3f} A)",
         "next_action": "Disable PSU output"},
        {"phase": "SHUTDOWN", "description": prefix + "Disable PSU output",
         "voltage_v": charge_v, "current_a": 0.0, "temp_c": 27.0,
         "next_action": "Open relay"},
        {"phase": "SHUTDOWN", "description": prefix + "Open relay",
         "voltage_v": charge_v, "current_a": 0.0, "temp_c": 27.0,
         "relay_switch_check": True, "next_action": "Charge phase complete"},
    ]


def _discharge_phase_steps(cycle_number: int = None, inject_fault: bool = False,
                            battery_cfg: dict = None, test_setpoints: dict = None):
    """
    Simulated operational sequence mirroring the real, implemented
    Discharge Battery workflow (relay close -> configure/enable PSU sink
    -> CC discharge -> cutoff -> PSU disable -> relay open) -- see
    test_control/discharge_sequence.py::DischargeSequence.run().

    `test_setpoints` (a BATTERY_GROUPS[group]["test_setpoints"] entry)
    supplies the commanded discharge current and the discharge TARGET
    (`discharge_cutoff_v` -- a cycle objective, not the safety floor).
    `battery_cfg` (a BATTERY_CONFIGS[...] entry) supplies only the safety
    FLOOR (`voltage_min_v`) and `max_temp_c` -- exactly the same division
    of responsibility DischargeSequence.run() itself uses (battery_cfg
    is never read for a commanded setpoint). The effective cutoff is
    clamped `max(target, floor)` -- the floor always wins, identical to
    DischargeSequence.run() -- see docs/architecture.md "Discharge Cutoff
    Policy". Both arguments default to the global Settings.
    DISCHARGE_CURRENT_A/DISCHARGE_CUTOFF_V/BAT_VOLTAGE_MIN/BAT_TEMP_MAX_C
    constants when not given, unchanged fallback behavior.

    `inject_fault=True` deliberately raises the mid-discharge simulated
    temperature above the active max_temp_c, so the walkthrough aborts at
    that step's "Run SafetyMonitor checks" instead of ever reaching
    cutoff/shutdown -- used by the Cycle Battery walkthrough below to
    demonstrate the fault/abort path explicitly.
    """
    prefix = f"[Cycle {cycle_number}] " if cycle_number else ""
    dev_note = ("Development-only simulation -- NOT written to measurements/"
                "event_log/run_summary/station_state.")
    discharge_i = test_setpoints["discharge_current_a"] if test_setpoints else Settings.DISCHARGE_CURRENT_A
    target_v = test_setpoints["discharge_cutoff_v"] if test_setpoints else Settings.DISCHARGE_CUTOFF_V
    floor_v = battery_cfg["voltage_min_v"] if battery_cfg else Settings.BAT_VOLTAGE_MIN
    cutoff_v = max(target_v, floor_v)  # the floor always wins -- see docstring above
    temp_max = battery_cfg["max_temp_c"] if battery_cfg else Settings.BAT_TEMP_MAX_C
    mid_temp = (temp_max + 5.0) if inject_fault else 27.0
    return [
        {"phase": "INIT", "description": prefix + "Resolve relay routing (discharge)",
         "next_action": "Close relay"},
        {"phase": "RELAY_ROUTING", "description": prefix + "Close relay",
         "voltage_v": 4.10, "current_a": 0.0, "temp_c": 25.0, "relay_switch_check": True,
         "next_action": "Configure PSU limits"},
        {"phase": "CONFIG",
         "description": prefix + f"Configure PSU limits (I_discharge={discharge_i:.3f} A sink)",
         "next_action": "Enable PSU sink"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Enable PSU sink",
         "voltage_v": 4.10, "current_a": 0.0, "temp_c": 25.0, "next_action": "Acquire measurements"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Acquire measurements",
         "voltage_v": 3.80, "current_a": discharge_i, "temp_c": 26.0,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Run SafetyMonitor checks",
         "voltage_v": 3.80, "current_a": discharge_i, "temp_c": 26.0,
         "run_safety_check": True, "mode": "discharge", "next_action": "Update ExecutionFrame"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Update ExecutionFrame",
         "next_action": "Store measurement"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Store measurement", "note": dev_note,
         "next_action": "Continue discharge"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Continue discharge",
         "voltage_v": 3.60, "current_a": discharge_i, "temp_c": mid_temp,
         "next_action": "Acquire measurements"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Acquire measurements",
         "voltage_v": 3.60, "current_a": discharge_i, "temp_c": mid_temp,
         "next_action": "Run SafetyMonitor checks"},
        {"phase": "CC_DISCHARGE", "description": prefix + "Run SafetyMonitor checks",
         "voltage_v": 3.60, "current_a": discharge_i, "temp_c": mid_temp,
         "run_safety_check": True, "mode": "discharge", "next_action": "Evaluate cutoff condition"},
        {"phase": "CUTOFF_DETECTED", "description": prefix + "Evaluate cutoff condition (V <= cutoff)",
         "next_action": "Disable PSU sink"},
        {"phase": "SHUTDOWN", "description": prefix + "Disable PSU sink",
         "voltage_v": cutoff_v, "current_a": 0.0, "temp_c": 26.5, "next_action": "Open relay"},
        {"phase": "SHUTDOWN", "description": prefix + "Open relay",
         "voltage_v": cutoff_v, "current_a": 0.0, "temp_c": 26.5, "relay_switch_check": True,
         "next_action": "Discharge phase complete"},
    ]


def _cycle_battery_walkthrough_steps(battery_cfg: dict = None, test_setpoints: dict = None):
    """
    One full charge phase, a transition, then a discharge phase that
    deliberately injects an overtemperature fault -- demonstrates the
    complete Cycle Battery shape (charge -> transition -> discharge ->
    completion) AND the fault/abort path a real implementation must also
    take, in one continuous walkthrough. `battery_cfg`/`test_setpoints`
    are forwarded to both phases -- see _charge_phase_steps()/
    _discharge_phase_steps().

    Unlike Monitor/Charge/Discharge, CycleSequence itself does not exist
    yet (see docs/TODO.md) -- this walkthrough remains a genuine
    forward-looking blueprint for it, not a mirror of real code, though
    it is built from the same reconciled charge/discharge step generators
    the real ChargeSequence/DischargeSequence are mirrored by.
    """
    steps = _charge_phase_steps(cycle_number=1, test_setpoints=test_setpoints)
    steps.append({"phase": "TRANSITION",
                  "description": "Transition from charge to discharge (cycle_count += 1)",
                  "next_action": "Begin discharge phase"})
    steps.extend(_discharge_phase_steps(cycle_number=1, inject_fault=True,
                                         battery_cfg=battery_cfg, test_setpoints=test_setpoints))
    return steps


def _select_safety_simulation_workflow():
    """
    Workflow-selection menu for the Safety Monitor Simulator. Returns
    "monitor"/"charge"/"discharge"/"cycle", or None if the operator
    cancels/skips.
    """
    print("\nSafety Monitor Workflow Simulator")
    print("1. Monitor Battery")
    print("2. Charge Battery")
    print("3. Discharge Battery")
    print("4. Cycle Battery")
    print("0. Skip")
    try:
        raw = input("\nSelect workflow: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    return {"1": "monitor", "2": "charge", "3": "discharge", "4": "cycle"}.get(raw)


def _select_safety_simulation_group():
    """
    Group-selection menu for the Safety Monitor Simulator -- mirrors the
    real workflows exactly: the operator selects a battery GROUP, never a
    battery type directly (see docs/architecture.md "Architectural
    Correction: Battery Type Is Never Operator Input"). Only lists groups
    that have both a declared battery_type and test_setpoints configured
    (config/devices.py::group_test_config()) -- i.e. groups that could
    actually run a real Charge/Discharge workflow today -- so this
    simulator can never simulate a configuration that couldn't exist in
    practice. Returns the selected group name, or None if the operator
    cancels/skips (falls back to global Settings.BAT_*/CHARGE_*/
    DISCHARGE_* constants, unchanged fallback behavior).
    """
    candidates = [
        name for name in dev_cfg.BATTERY_GROUPS
        if dev_cfg.group_test_config(name)["battery_type"] is not None
        and dev_cfg.group_test_config(name)["test_setpoints"] is not None
    ]
    print("\nSelect a battery group for this simulation (battery type and "
          "test setpoints are derived from the group, exactly as the real "
          "workflows do):")
    for i, name in enumerate(candidates, 1):
        cfg = dev_cfg.group_test_config(name)
        battery_cfg = dev_cfg.BATTERY_CONFIGS[cfg["battery_type"]]
        sp = cfg["test_setpoints"]
        print(f"[{i}] Group {name}  (battery={cfg['battery_type']}, "
              f"capacity={battery_cfg['capacity_ah']:.2f} Ah, "
              f"charge={sp['charge_current_a']:.3f} A, "
              f"discharge={sp['discharge_current_a']:.3f} A)")
    print("[0] Skip -- use global Settings.BAT_*/CHARGE_*/DISCHARGE_* constants")
    try:
        raw = input("\nSelect group: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if raw == "0" or raw == "":
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except ValueError:
        pass
    return None


def _run_selected_workflow_walkthrough(monitor, choice: str, battery_cfg: dict = None,
                                        test_setpoints: dict = None):
    """Dispatch one selected workflow to _run_workflow_walkthrough() with
    its step list. Returns a single TestResult, or None if `choice` is
    invalid (already validated by _select_safety_simulation_workflow()).
    `battery_cfg`/`test_setpoints`, if given (both derived from the same
    selected group -- see _select_safety_simulation_group()), are forwarded
    into the step generators so displayed/enforced values mirror the real
    workflows instead of falling back to global Settings.BAT_*/CHARGE_*/
    DISCHARGE_* constants."""
    if choice == "monitor":
        return _run_workflow_walkthrough(monitor, "Monitor Battery",
                                         _monitor_battery_walkthrough_steps())
    if choice == "charge":
        return _run_workflow_walkthrough(
            monitor, "Charge Battery (simulated -- mirrors the real ChargeSequence)",
            _charge_phase_steps(test_setpoints=test_setpoints))
    if choice == "discharge":
        return _run_workflow_walkthrough(
            monitor, "Discharge Battery (simulated -- mirrors the real DischargeSequence)",
            _discharge_phase_steps(battery_cfg=battery_cfg, test_setpoints=test_setpoints))
    if choice == "cycle":
        return _run_workflow_walkthrough(
            monitor, "Cycle Battery (simulated -- blueprint for the not-yet-implemented "
                     "CycleSequence, injected overtemperature fault)",
            _cycle_battery_walkthrough_steps(battery_cfg=battery_cfg, test_setpoints=test_setpoints),
            expect_abort=True)
    return None


def test_safety_monitor():
    """
    Part 1: exercise test_control/safety_monitor.SafetyMonitor's pure logic
    directly -- overvoltage, undervoltage, overcurrent, overtemperature,
    relay switch guard. Part 2 (below): an interactive, operator-selected
    workflow walkthrough -- see the module comment above this function.
    """
    config_ref = "test_control/safety_monitor.py"
    results    = []

    try:
        from test_control.safety_monitor import SafetyMonitor, SafetyStatus
    except ImportError as e:
        return [_fail("Safety Monitor", "SafetyMonitor", config_ref, f"Import error: {e}")]

    monitor = SafetyMonitor(settings=Settings)

    # Test 1: nominal -- should be safe
    s = monitor.check(voltage_v=3.8, current_a=0.4, temp_c=28.0)
    if s.safe:
        results.append(_ok("Safety Monitor", "nominal", config_ref,
                           "V=3.8 V, I=0.4 A, T=28 degC -> safe  OK"))
    else:
        results.append(_fail("Safety Monitor", "nominal", config_ref,
                             f"Nominal reading marked unsafe: {s.reason}"))

    # Test 2: overvoltage
    s = monitor.check(voltage_v=Settings.BAT_VOLTAGE_MAX + 0.1,
                      current_a=0.0, temp_c=25.0)
    if not s.safe and "Overvoltage" in s.reason:
        results.append(_ok("Safety Monitor", "overvoltage", config_ref,
                           f"Correctly blocked at {Settings.BAT_VOLTAGE_MAX + 0.1:.1f} V"))
    else:
        results.append(_fail("Safety Monitor", "overvoltage", config_ref,
                             "Overvoltage not detected"))

    # Test 3: undervoltage
    s = monitor.check(voltage_v=Settings.BAT_VOLTAGE_MIN - 0.1,
                      current_a=0.0, temp_c=25.0)
    if not s.safe and "Undervoltage" in s.reason:
        results.append(_ok("Safety Monitor", "undervoltage", config_ref,
                           f"Correctly blocked at {Settings.BAT_VOLTAGE_MIN - 0.1:.1f} V"))
    else:
        results.append(_fail("Safety Monitor", "undervoltage", config_ref,
                             "Undervoltage not detected"))

    # Test 4: overcurrent
    s = monitor.check(voltage_v=3.8,
                      current_a=Settings.BAT_CURRENT_MAX + 0.1, temp_c=25.0)
    if not s.safe and "Overcurrent" in s.reason:
        results.append(_ok("Safety Monitor", "overcurrent", config_ref,
                           f"Correctly blocked at {Settings.BAT_CURRENT_MAX + 0.1:.1f} A"))
    else:
        results.append(_fail("Safety Monitor", "overcurrent", config_ref,
                             "Overcurrent not detected"))

    # Test 5: overtemperature
    s = monitor.check(voltage_v=3.8, current_a=0.0,
                      temp_c=Settings.BAT_TEMP_MAX_C + 1.0)
    if not s.safe and "Overtemperature" in s.reason:
        results.append(_ok("Safety Monitor", "overtemperature", config_ref,
                           f"Correctly blocked at {Settings.BAT_TEMP_MAX_C + 1:.0f} degC"))
    else:
        results.append(_fail("Safety Monitor", "overtemperature", config_ref,
                             "Overtemperature not detected"))

    # Test 6: temp_c=None (NTC not yet wired in)
    s = monitor.check(voltage_v=3.8, current_a=0.0, temp_c=None)
    if s.safe:
        results.append(_ok("Safety Monitor", "temp=None", config_ref,
                           "temp_c=None handled gracefully (NTC not connected)"))
    else:
        results.append(_fail("Safety Monitor", "temp=None", config_ref,
                             f"Unexpected UNSAFE for temp_c=None: {s.reason}"))

    # Test 7: relay switch guard
    below = monitor.is_safe_to_switch_relay(Settings.ZERO_CURRENT_THRESHOLD_A * 0.5)
    above = monitor.is_safe_to_switch_relay(Settings.ZERO_CURRENT_THRESHOLD_A * 2.0)
    if below and not above:
        results.append(_ok("Safety Monitor", "relay switch guard", config_ref,
                           f"Threshold {Settings.ZERO_CURRENT_THRESHOLD_A} A enforced correctly"))
    else:
        results.append(_fail("Safety Monitor", "relay switch guard", config_ref,
                             f"below threshold: {below}, above threshold: {above}"))

    # Part 2: operator-selected workflow walkthrough -- see module comment
    # above this function. Skipped entirely if the operator cancels/skips
    # the selection menu (choice is None).
    choice = _select_safety_simulation_workflow()
    if choice is not None:
        group = _select_safety_simulation_group()
        if group is not None:
            cfg = dev_cfg.group_test_config(group)
            battery_cfg = dev_cfg.BATTERY_CONFIGS[cfg["battery_type"]]
            test_setpoints = cfg["test_setpoints"]
        else:
            battery_cfg = None
            test_setpoints = None
        monitor.set_battery_limits(battery_cfg)
        result = _run_selected_workflow_walkthrough(monitor, choice, battery_cfg, test_setpoints)
        if result is not None:
            results.append(result)

    return results


# =============================================================================
# 7b. SQLite foundation (data/sqlite_manager.py)
# =============================================================================

def test_sqlite():
    """
    Minimal SQLite foundation test (data/sqlite_manager.py) -- see
    docs/DATABASE_ROADMAP.md. This is deliberately simple: one table
    (test_records), four functions (create_database/initialize_schema/
    insert_test_record/get_last_record). NOT cycle recovery, NOT battery
    cycling, NOT a repository layer -- those are still on the roadmap.

    Steps (each reported independently):
        1. Create/open database
        2. Verify schema (test_records table exists)
        3. Insert a record
        4. Read it back
        5. Display the last record
        6. Report PASS/FAIL

    Uses a temporary directory -- never touches the real mode-specific
    data_output/<mode>/ database. No hardware required -- this must pass
    on a laptop with no PXI chassis attached (see docs/architecture.md
    Section 9, "System Modes" -- DEVELOPMENT mode's whole point).
    """
    import tempfile
    import shutil

    from data.sqlite_manager import (
        create_database, initialize_schema, insert_test_record, get_last_record,
    )

    config_ref = "data/sqlite_manager.py"
    results = []
    tmp_dir = tempfile.mkdtemp(prefix="nipxi_test_sqlite_")

    try:
        class _TmpSettings(Settings):
            DATA_DIR      = tmp_dir
            DATABASE_FILE = os.path.join(tmp_dir, "test_sqlite_manager.db")

        # Step 1: create/open database
        try:
            conn = create_database(_TmpSettings)
        except Exception as e:
            results.append(_fail("SQLite", "create_database()", config_ref, str(e)))
            return results
        results.append(_ok("SQLite", "create_database()", config_ref,
                           f"Database opened at {_TmpSettings.DATABASE_FILE}"))

        try:
            # Step 2: initialize + verify schema
            try:
                initialize_schema(conn)
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='test_records'"
                ).fetchone()
                if exists is None:
                    results.append(_fail("SQLite", "initialize_schema()", config_ref,
                                         "test_records table not found after initialize_schema()"))
                    return results
                results.append(_ok("SQLite", "initialize_schema()", config_ref,
                                   "test_records table verified present"))
            except Exception as e:
                results.append(_fail("SQLite", "initialize_schema()", config_ref, str(e)))
                return results

            # Step 3: insert a record
            try:
                row_id = insert_test_record(conn, label="laptop_dev_check", value=42.0)
                results.append(_ok("SQLite", "insert_test_record()", config_ref,
                                   f"Inserted row id={row_id}"))
            except Exception as e:
                results.append(_fail("SQLite", "insert_test_record()", config_ref, str(e)))
                return results

            # Step 4 + 5: read back and display the last record
            try:
                last = get_last_record(conn)
            except Exception as e:
                results.append(_fail("SQLite", "get_last_record()", config_ref, str(e)))
                return results

            if last is None:
                results.append(_fail("SQLite", "get_last_record()", config_ref,
                                     "Expected a record, got None"))
                return results

            print(f"    Last record: {last}")
            if (last["id"] != row_id or last["label"] != "laptop_dev_check"
                    or last["value"] != 42.0):
                results.append(_fail("SQLite", "get_last_record()", config_ref,
                                     f"Record mismatch: expected id={row_id} "
                                     f"label='laptop_dev_check' value=42.0, got {last}"))
                return results
            results.append(_ok("SQLite", "get_last_record()", config_ref,
                               f"Last record matches what was inserted: {last}"))

        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# =============================================================================
# 8. Database layer
# =============================================================================

def test_database():
    """
    Exercises data/storage.py DataStorage (SQLite backend).
    Uses a temporary directory -- does not touch data_output/.
    Also verifies StorageBackend interface is implemented.
    """
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    results    = []

    # -- Module import + interface check --------------------------------------
    try:
        from data.storage import DataStorage, StorageBackend
        if not issubclass(DataStorage, StorageBackend):
            results.append(_fail("Database", "StorageBackend interface", config_ref,
                                 "DataStorage does not implement StorageBackend"))
        else:
            results.append(_ok("Database", "StorageBackend interface", config_ref,
                               "DataStorage implements StorageBackend  OK"))
    except ImportError as e:
        return [_fail("Database", "DataStorage", config_ref, f"Import error: {e}")]

    # -- Functional write + read + query cycle --------------------------------
    import tempfile, shutil, sqlite3 as _sqlite3

    tmp_dir = tempfile.mkdtemp(prefix="nipxi_test_")
    try:
        class _TmpSettings(Settings):
            DATA_DIR      = tmp_dir
            DATABASE_FILE = os.path.join(tmp_dir, "test.db")
            CSV_DIR       = os.path.join(tmp_dir, "csv")

        storage = DataStorage(settings=_TmpSettings)
        with storage:
            sample = {
                "elapsed_s": 0.0, "phase": "test",
                "voltage_v": 3.72, "current_a": 0.50, "temp_c": 25.0,
            }
            storage.record(channel=1, sample=sample)
            storage.record(channel=2, sample={**sample, "voltage_v": 3.65})

            # Test query() on the same connection
            rows = storage.query(channel=1)
            if len(rows) == 1 and rows[0]["voltage_v"] == 3.72:
                results.append(_ok("Database", "query()", config_ref,
                                   "query(channel=1) returned 1 correct row  OK"))
            else:
                results.append(_fail("Database", "query()", config_ref,
                                     f"query returned {rows}"))

        # Verify DB after close
        conn = _sqlite3.connect(_TmpSettings.DATABASE_FILE)
        total = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        conn.close()
        if total == 2:
            results.append(_ok("Database", "SQLite persistence", config_ref,
                               "2 records written and verified after close"))
        else:
            results.append(_fail("Database", "SQLite persistence", config_ref,
                                 f"Expected 2 rows, found {total}"))

        # Verify CSV
        csv_files = [f for f in os.listdir(_TmpSettings.CSV_DIR) if f.endswith(".csv")]
        if len(csv_files) == 2:
            results.append(_ok("Database", "CSV output", config_ref,
                               f"2 CSV files created: {csv_files[0]}, {csv_files[1]}"))
        else:
            results.append(_warn("Database", "CSV output", config_ref,
                                 f"Expected 2 CSV files, found {len(csv_files)}"))

    except Exception as e:
        results.append(_fail("Database", "DataStorage", config_ref, str(e)))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# =============================================================================
# 8b. Database Tools -- real-database inspection + regression self-tests
#
# Consolidates the previous "Test SQLite (foundation)"/"Test Database
# Layer" top-level MENU entries into one submenu (see
# docs/architecture.md "Database Tools"). Options 1-5 are read-only
# inspection of the REAL project database (Settings.DATABASE_FILE -- the
# same file Monitor Battery/Proto Test Execution write to), reusing
# DataStorage's existing read methods (get_last_run_summary()/
# get_measurements()/get_recent_events()/get_last_execution_state()) --
# no new read path, no new storage mechanism. Options 6-7 are the original
# test_database()/test_sqlite() temp-directory regression self-tests,
# unchanged, just relocated here instead of their own top-level MENU slots.
# =============================================================================

def _open_real_storage_readonly():
    """
    Open DataStorage against the REAL, mode-specific database
    (Settings.DATABASE_FILE) for read-only inspection. Returns None if the
    file doesn't exist yet (no runs recorded) rather than creating one --
    a "view" option must never create the real database as a side effect
    of merely looking at it. (DataStorage.open()'s additive schema
    migration on an EXISTING file is the same safe, idempotent behavior
    every other real caller already relies on -- see data/storage.py's
    _migrate_add_missing_columns().)
    """
    if not os.path.exists(Settings.DATABASE_FILE):
        print(f"\n  No database found at {Settings.DATABASE_FILE} -- no runs recorded yet.")
        return None
    from data.storage import DataStorage
    storage = DataStorage(settings=Settings)
    storage.open()
    return storage


def _db_view_latest_run():
    """View the most recent run_summary row -- battery config snapshot,
    hardware identity snapshot, voltage summary, everything Milestone II
    traceability records for one run."""
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    storage = _open_real_storage_readonly()
    if storage is None:
        return [_warn("Database Tools", "Latest Run", config_ref,
                      "No database file yet -- no runs recorded")]
    try:
        run = storage.get_last_run_summary()
        if run is None:
            return [_warn("Database Tools", "Latest Run", config_ref,
                          "run_summary is empty -- no runs recorded")]
        print(f"\nLatest Run (run_summary)\n{'-' * 60}")
        for k, v in run.items():
            print(f"  {k:34s}: {v}")
        return [_ok("Database Tools", "Latest Run", config_ref,
                    f"Run #{run.get('id')}  run_id={run.get('run_id')}  "
                    f"test_type={run.get('test_type')}  result={run.get('result')}")]
    finally:
        storage.close()


def _db_view_latest_event_log():
    """View the most recent run's event_log entries -- the full
    traceability narrative (battery selection, hardware identity, phase
    transitions) for that run."""
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    storage = _open_real_storage_readonly()
    if storage is None:
        return [_warn("Database Tools", "Latest Event Log", config_ref,
                      "No database file yet -- no runs recorded")]
    try:
        run = storage.get_last_run_summary()
        if run is None:
            return [_warn("Database Tools", "Latest Event Log", config_ref,
                          "run_summary is empty -- no runs recorded")]
        events = storage.get_recent_events(run_id=run["run_id"], limit=50)
        print(f"\nLatest Event Log -- run_id={run['run_id']}\n{'-' * 60}")
        if not events:
            print("  (no events recorded for this run)")
            return [_warn("Database Tools", "Latest Event Log", config_ref,
                          f"No event_log rows for run_id={run['run_id']}")]
        for e in events:
            print(f"  [{e.get('level')}] {e.get('timestamp')}  {e.get('message')}")
        return [_ok("Database Tools", "Latest Event Log", config_ref,
                    f"{len(events)} event(s) for run_id={run['run_id']}")]
    finally:
        storage.close()


def _db_view_latest_measurements():
    """View the most recent run's measurements rows -- the authoritative
    per-sample historical result store for every test type."""
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    storage = _open_real_storage_readonly()
    if storage is None:
        return [_warn("Database Tools", "Latest Measurements", config_ref,
                      "No database file yet -- no runs recorded")]
    try:
        run = storage.get_last_run_summary()
        if run is None:
            return [_warn("Database Tools", "Latest Measurements", config_ref,
                          "run_summary is empty -- no runs recorded")]
        rows = storage.get_measurements(run_id=run["run_id"])
        print(f"\nLatest Measurements -- run_id={run['run_id']}\n{'-' * 60}")
        if not rows:
            print("  (no measurements recorded for this run)")
            return [_warn("Database Tools", "Latest Measurements", config_ref,
                          f"No measurements rows for run_id={run['run_id']}")]
        for row in rows[-20:]:
            print(f"  ch={row.get('channel')} relay={row.get('relay')} "
                  f"phase={row.get('phase_detail')}  V={row.get('voltage_v')}  "
                  f"I={row.get('current_a')}  T={row.get('temp_c')}")
        return [_ok("Database Tools", "Latest Measurements", config_ref,
                    f"{len(rows)} row(s) for run_id={run['run_id']} "
                    f"(showing up to the last 20)")]
    finally:
        storage.close()


def _db_view_station_state():
    """View the last recorded station_state row -- recovery/current-position
    only, as of Milestone II Phase 3 (read across all run_ids, same as the
    startup "previous execution found" display)."""
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    storage = _open_real_storage_readonly()
    if storage is None:
        return [_warn("Database Tools", "Station State", config_ref,
                      "No database file yet -- no runs recorded")]
    try:
        last = storage.get_last_execution_state()
        print(f"\nStation State -- last recorded execution\n{'-' * 60}")
        if last is None:
            print("  (no station_state rows recorded yet)")
            return [_warn("Database Tools", "Station State", config_ref,
                          "station_state is empty -- no execution recorded")]
        for k, v in last.items():
            print(f"  {k:14s}: {v}")
        return [_ok("Database Tools", "Station State", config_ref,
                    f"relay={last.get('relay')}  state={last.get('state')}  "
                    f"timestamp={last.get('timestamp')}")]
    finally:
        storage.close()


def _db_view_statistics():
    """Row counts per table + file size for the real project database."""
    config_ref = f"data/storage.py -> {Settings.DATABASE_FILE}"
    if not os.path.exists(Settings.DATABASE_FILE):
        return [_warn("Database Tools", "Database Statistics", config_ref,
                      "No database file yet -- no runs recorded")]
    import sqlite3
    conn = sqlite3.connect(Settings.DATABASE_FILE)
    results = []
    try:
        print(f"\nDatabase Statistics -- {Settings.DATABASE_FILE}\n{'-' * 60}")
        for table in ("measurements", "run_summary", "event_log", "station_state"):
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table:14s}: {count} row(s)")
                results.append(_ok("Database Tools", f"{table} row count", config_ref,
                                   f"{count} row(s)"))
            except sqlite3.OperationalError as e:
                print(f"  {table:14s}: table not found")
                results.append(_warn("Database Tools", f"{table} row count", config_ref, str(e)))
        size_kb = os.path.getsize(Settings.DATABASE_FILE) / 1024.0
        print(f"  {'file size':14s}: {size_kb:.1f} KB")
        return results
    finally:
        conn.close()


def test_database_tools():
    """
    Database Tools -- consolidated database inspection + regression menu.
    Replaces the previous separate "Test SQLite (foundation)"/"Test
    Database Layer" top-level MENU entries.
    """
    options = [
        ("View Latest Run (run_summary)",                          _db_view_latest_run),
        ("View Latest Event Log",                                   _db_view_latest_event_log),
        ("View Latest Measurements",                                _db_view_latest_measurements),
        ("View Station State (last execution)",                     _db_view_station_state),
        ("Database Statistics",                                     _db_view_statistics),
        ("Run Storage Layer Self-Test (data/storage.py, temp DB)",   test_database),
        ("Run SQLite Foundation Self-Test (data/sqlite_manager.py, temp DB)", test_sqlite),
    ]
    print("\nDatabase Tools\n")
    for i, (label, _fn) in enumerate(options, 1):
        print(f"[{i}] {label}")
    print("[0] Back")
    try:
        raw = input("\nChoice: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return []
    if raw == "0" or raw == "":
        return []
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(options):
            raise ValueError()
    except ValueError:
        print("Invalid choice.")
        return []
    _, fn = options[idx]
    return fn()


# =============================================================================
# 9. MiniSQL hooks
# =============================================================================

def test_minisql():
    """
    MiniSQL stubs. Extend each hook when the MiniSQL module becomes available.
    The StorageBackend interface in data/storage.py is the integration point.
    """
    config_ref = "MiniSQL - not yet implemented"
    results    = []

    # Hook 0: StorageBackend interface exists (integration point for MiniSQL)
    try:
        from data.storage import StorageBackend
        results.append(_ok("MiniSQL", "StorageBackend interface", config_ref,
                           "data.storage.StorageBackend ABC defined -- "
                           "implement MiniSQLStorage(StorageBackend) when ready"))
    except ImportError as e:
        results.append(_fail("MiniSQL", "StorageBackend interface", config_ref,
                             f"Could not import StorageBackend: {e}"))

    # Hook 1: Library import
    try:
        import minisql  # noqa: F401
        results.append(_ok("MiniSQL", "Library", config_ref, "minisql module found"))
    except ImportError:
        results.append(_warn("MiniSQL", "Library", config_ref,
                             "Module 'minisql' not installed -- skipped"))

    # Hook 2-6: Stubs ready for implementation
    stubs = [
        ("Connection",      "minisql.connect(host, port)"),
        ("Initialization",  "schema initialization"),
        ("Table Creation",  "CREATE TABLE measurements"),
        ("Record Insert",   "INSERT record"),
        ("Record Retrieval","SELECT / verify round-trip"),
    ]
    for name, todo in stubs:
        results.append(_warn("MiniSQL", name, config_ref, f"Stub -- implement: {todo}"))

    return results


# =============================================================================
# 10. Electronic Load (future)
# =============================================================================

def test_electronic_load():
    """
    Placeholder. config/devices.py::GPIB_INSTRUMENTS records that an
    NI-488.2/GPIB0 interface was detected in the rack -- equipment_
    Requirement.md documents an intended "Programmable Electronic Load"
    and "Programmable Power Supply", and this GPIB interface is the most
    likely connection point for one of those, but no specific instrument
    model has been confirmed there yet, and no GPIB driver class exists in
    this codebase. Extend once both are true.
    """
    results = []
    for name, cfg in dev_cfg.GPIB_INSTRUMENTS.items():
        results.append(_warn(
            "Electronic Load", name,
            f"config/devices.py -> GPIB_INSTRUMENTS[{name!r}]",
            cfg.get("validation_notes", "No instrument model confirmed at this address."),
        ))
    if not results:
        results.append(_warn("Electronic Load", "ELOAD_01",
                             "Not yet configured",
                             "No GPIB instrument configured in config/devices.py -- stub only"))
    return results


# =============================================================================
# UI Test -- safe UI development/review environment, independent of
# hardware availability. Replaces the previous "Run All Tests" MENU entry
# (see docs/architecture.md "UI Test").
#
# NO hardware connections, NO real measurements, NO database writes: every
# screen below is built via ExecutionFrame.from_live() with hardcoded,
# clearly-labeled DEMO values, then rendered with the SAME
# render_execution_frame() Proto Test Execution/Monitor Battery use live --
# this previews the real renderer against static data, it is never a
# second UI implementation.
# =============================================================================

def _demo_proto_test_frame():
    from test_control.execution_screen import ExecutionFrame
    return ExecutionFrame.from_live(
        run_number=1, run_id="DEMO-0001", test_type="proto",
        channel=3, relay=3, state="ACTIVE", phase_detail="MEASURED",
        smu_voltage=4.200000, smu_current=0.021500, dmm_voltage=4.199870,
        recent_measurements=[
            {"channel": 1, "relay": 1, "smu_measured_v": 4.200012, "dmm_measured_v": 4.199901},
            {"channel": 2, "relay": 2, "smu_measured_v": 4.199988, "dmm_measured_v": 4.199875},
            {"channel": 3, "relay": 3, "smu_measured_v": 4.200000, "dmm_measured_v": 4.199870},
        ],
        recent_events=[
            {"timestamp": "2026-01-01T12:00:00", "message": "Relay 3 activating (force-all-off -> verify -> activate -> verify)"},
            {"timestamp": "2026-01-01T12:00:05", "message": "Relay 3 activated -- output enabled, sourcing 4.200 V / 0.500 A limit, dwelling 5s"},
            {"timestamp": "2026-01-01T12:00:10", "message": "Measurement acquired -- DMM 4.199870 V"},
        ],
    )


def _demo_monitor_battery_frame():
    from test_control.execution_screen import ExecutionFrame
    return ExecutionFrame.from_live(
        run_number=7, run_id="DEMO-0007", test_type="monitor",
        channel=3, relay=3, state="ACTIVE", phase_detail="MONITORING",
        battery_voltage=3.712000, battery_current=None, battery_temp=None,
        recent_measurements=[
            {"channel": 3, "relay": 3, "voltage_v": 3.705},
            {"channel": 3, "relay": 3, "voltage_v": 3.710},
            {"channel": 3, "relay": 3, "voltage_v": 3.712},
        ],
        recent_events=[
            {"timestamp": "2026-01-01T09:00:00", "message": "Battery selected: HUB"},
            {"timestamp": "2026-01-01T09:00:01", "message": "Relay 3 activated -- monitoring started"},
            {"timestamp": "2026-01-01T09:00:01", "message": "Monitoring source: DMM"},
        ],
    )


def _demo_charge_battery_frame():
    from test_control.execution_screen import ExecutionFrame
    return ExecutionFrame.from_live(
        run_number=12, run_id="DEMO-0012", test_type="charge",
        channel=1, relay=1, state="ACTIVE", phase_detail="CC_CV",
        smu_voltage=4.150000, smu_current=0.050000, dmm_voltage=4.150000,
        battery_voltage=4.150000, battery_current=0.050000, battery_temp=None,
        recent_measurements=[
            {"channel": 1, "relay": 1, "smu_measured_v": 3.900000, "dmm_measured_v": 3.900000},
            {"channel": 1, "relay": 1, "smu_measured_v": 4.150000, "dmm_measured_v": 4.150000},
        ],
        recent_events=[
            {"timestamp": "2026-01-01T13:00:00", "message": "Relay 1 activated -- charging started (0.050 A / 4.200 V CV target)"},
        ],
    )


def _demo_discharge_battery_frame():
    from test_control.execution_screen import ExecutionFrame
    return ExecutionFrame.from_live(
        run_number=13, run_id="DEMO-0013", test_type="discharge",
        channel=1, relay=1, state="ACTIVE", phase_detail="CC_DISCHARGE",
        smu_voltage=3.500000, smu_current=-0.080000, dmm_voltage=3.500000,
        battery_voltage=3.500000, battery_current=-0.080000, battery_temp=None,
        recent_measurements=[
            {"channel": 1, "relay": 1, "smu_measured_v": 3.800000, "dmm_measured_v": 3.800000},
            {"channel": 1, "relay": 1, "smu_measured_v": 3.500000, "dmm_measured_v": 3.500000},
        ],
        recent_events=[
            {"timestamp": "2026-01-01T14:00:00", "message": "Relay 1 activated -- discharging started (0.080 A sink, 4.200 V SMU compliance, 3.000 V EOD cutoff)"},
        ],
    )


def test_ui_preview():
    """
    UI Test -- a safe UI development/review environment. Every option
    below constructs a demo ExecutionFrame (hardcoded sample data, no
    hardware, no database) and renders it through the real
    render_execution_frame() -- the identical renderer Proto Test
    Execution/Monitor Battery/Charge/Discharge Battery use live. Cycle
    Battery and a Historical Results Viewer are reported honestly as "not
    yet implemented" rather than faking a screen for a workflow/viewer
    that doesn't exist yet -- Charge/Discharge Battery graduated out of
    that bucket once ChargeSequence/DischargeSequence were implemented
    (previously this menu lumped all three together as unimplemented,
    which had gone stale).
    """
    from test_control.execution_screen import render_execution_frame

    options = [
        ("Proto Test Execution screen (demo data)",   _demo_proto_test_frame),
        ("Monitor Battery screen (demo data)",         _demo_monitor_battery_frame),
        ("Charge Battery screen (demo data)",          _demo_charge_battery_frame),
        ("Discharge Battery screen (demo data)",       _demo_discharge_battery_frame),
        ("Cycle Battery screen",                       None),
        ("Historical Results Viewer style screens",    None),
    ]
    print("\nUI Test -- static/demo data only. No hardware, no database writes.\n")
    for i, (label, _fn) in enumerate(options, 1):
        print(f"[{i}] {label}")
    print("[0] Back")
    try:
        raw = input("\nChoice: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return []
    if raw == "0" or raw == "":
        return []
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(options):
            raise ValueError()
    except ValueError:
        print("Invalid choice.")
        return []
    label, fn = options[idx]
    if fn is None:
        print(f"\n  {label} -- not yet implemented (no real workflow/viewer exists to preview yet).")
        return []
    frame = fn()
    render_execution_frame(frame)
    return [_ok("UI Test", label, "test_control/execution_screen.py (demo data, no hardware/DB)",
               "Rendered via the real render_execution_frame() against static demo data")]


# =============================================================================
# Summary
# =============================================================================

def print_summary(all_results):
    print()
    print("=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    modules = {}
    for r in all_results:
        modules.setdefault(r.module, []).append(r)
    for module, res in modules.items():
        worst = Status.PASS
        for r in res:
            if r.status == Status.FAIL:
                worst = Status.FAIL
                break
            if r.status == Status.WARNING:
                worst = Status.WARNING
        print(f"  {worst:8s}  {module}")
    total  = len(all_results)
    passed = sum(1 for r in all_results if r.status == Status.PASS)
    warned = sum(1 for r in all_results if r.status == Status.WARNING)
    failed = sum(1 for r in all_results if r.status == Status.FAIL)
    print("-" * 60)
    print(f"  Total {total}  |  PASS {passed}  |  WARNING {warned}  |  FAIL {failed}")
    print("=" * 60)


# =============================================================================
# Pre-flight
# =============================================================================

def test_device_validation():
    """
    Startup device validation (utils/device_validator.py): every configured
    device in config/devices.py can be instantiated, required fields are
    present, no duplicate names/VISA resources/IP addresses/COM ports/relay
    identifiers exist, relay count is consistent (num_channels ==
    channel_count == Settings.RELAY_COUNT), and every relay 'type' is
    registered in RelayFactory.

    Construction only -- no connect() is called, no hardware communication
    is attempted. This runs first in preflight_check(), before the menu (and
    therefore before Hardware Discovery or any other test) is shown.
    """
    from utils.device_validator import validate_devices
    ref = "utils/device_validator.py -> validate_devices(config.devices)"
    errors = validate_devices(dev_cfg)
    if not errors:
        return [_ok("Device Validation", "config/devices.py", ref,
                    "All configured devices passed startup validation")]
    return [_fail("Device Validation", "config/devices.py", ref, e) for e in errors]


def preflight_check():
    """
    Runs before the menu is shown, gating it exactly like test_configuration()
    already did: Settings-level validation (test_configuration) PLUS
    device-level validation (test_device_validation) -- both before any
    hardware communication, and before Hardware Discovery or any other test.
    """
    results  = test_configuration() + test_device_validation()
    failures = [r for r in results if r.status == Status.FAIL]
    warnings = [r for r in results if r.status == Status.WARNING]
    if not failures and not warnings:
        print("  Configuration: OK")
        return results, True
    for r in failures:
        print(f"  [FAIL] {r.device}: {r.details.splitlines()[0]}")
    for r in warnings:
        print(f"  [WARN] {r.device}: {r.details.splitlines()[0]}")
    return results, len(failures) == 0


# =============================================================================
# 0. Main Test -- real commissioning run via HardwareManager / TestExecutor
# =============================================================================

def _hardware_snapshot_fields(smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg):
    """
    Build the run_summary hardware-identity snapshot dict (see
    data/storage.py's run_summary schema and docs/architecture.md "Hardware
    Identity Traceability") from the SAME resolved config/devices.py dicts
    HardwareManager was actually constructed with -- single source of
    truth, no independent re-derivation. Shared by run_proto_test_execution()
    and _run_monitor_battery() so the field-building logic never drifts
    between test types.

    `dmm_name`/`dmm_cfg` may be None (the DMM is optional for some
    workflows) -- every other role is always present, since HardwareManager
    always constructs an SMU/DAQ/relay driver.
    """
    fields = {
        "smu_name": smu_name, "smu_resource": smu_cfg.get("resource"), "smu_model": smu_cfg.get("model"),
        "daq_name": daq_name, "daq_resource": daq_cfg.get("resource"), "daq_model": daq_cfg.get("model"),
        "relay_matrix_name": relay_cfg.get("name"),
        "relay_matrix_model": relay_cfg.get("driver"),
        "relay_matrix_resource": (
            f"{relay_cfg.get('ip', '')}:{relay_cfg.get('port', '')}"
            if relay_cfg.get("type", "").lower() == "ethernet"
            else str(relay_cfg.get("port", ""))
        ),
    }
    if dmm_cfg is not None:
        fields["dmm_name"] = dmm_name
        fields["dmm_resource"] = dmm_cfg.get("resource")
        fields["dmm_model"] = dmm_cfg.get("model")
    return fields


def _select_battery_group():
    """
    Battery group selection -- each group is a distinct relay-matrix
    routing section (see config/devices.py::BATTERY_GROUPS), not a purely
    logical grouping. Only groups with enabled=True can be selected.

    Group is the ONLY thing the operator selects for any battery workflow
    (Monitor Battery, Monitor Battery Scan, Charge Battery, Discharge
    Battery). Battery type, hardware assignment, and test setpoints are
    all engineering-configured per group and derived from it, never
    separately chosen at runtime -- see docs/architecture.md "Battery
    Group Test Configuration Architecture".
    """
    names = list(dev_cfg.BATTERY_GROUPS.keys())
    print("\nSelect Battery Group")
    for name in names:
        grp = dev_cfg.BATTERY_GROUPS[name]
        status = "" if grp["enabled"] else "  (no relay matrix installed yet)"
        print(f"  {name}. Positions {grp['position_start']}-{grp['position_end']}{status}")
    choice = input("\nGroup: ").strip().upper()
    grp = dev_cfg.BATTERY_GROUPS.get(choice)
    if grp is None:
        print("Invalid selection.")
        return None
    if not grp["enabled"]:
        print(f"Group {choice} has no relay matrix installed yet -- cannot select.")
        return None
    return choice


def _select_battery_position(group: str):
    """Position selection is always relative to the selected group (e.g.
    "Group A Position 3"), never a raw global position number."""
    grp = dev_cfg.BATTERY_GROUPS[group]
    size = grp["position_end"] - grp["position_start"] + 1
    choice = input(f"\nPosition within Group {group} (1-{size}): ").strip()
    try:
        pos = int(choice)
    except ValueError:
        print("Invalid selection.")
        return None
    if not (1 <= pos <= size):
        print(f"Position {pos} out of range (1-{size}).")
        return None
    return pos


def _missing_hardware_roles(hw: dict, required_roles=("relay_matrix", "smu", "dmm", "daq")):
    """Return the subset of `required_roles` whose config/devices.py::
    hardware_for_group() cfg resolved to None -- i.e. no device assigned to
    that role for this group. Never silently substitute another device for
    a missing role; the caller must abort before any hardware activation."""
    return [role for role in required_roles if hw[f"{role}_cfg"] is None]


def _confirm_operation(operation: str, battery_type: str, battery_cfg: dict,
                        group: str, positions_label: str, hw: dict,
                        extra_lines=None):
    """
    Single operator confirmation screen shared by every hardware-activating
    workflow (Monitor Battery, Monitor Battery Scan, Charge/Discharge
    Battery, and future Cycle Battery) -- see config/devices.py::
    hardware_for_group() for how `hw` (its return dict) is resolved. No
    workflow builds its own hardware summary/confirmation format; this is
    the one place that does. `extra_lines` is for workflow-specific detail
    (e.g. battery limits) appended below the hardware summary.

    `battery_type`/`battery_cfg` are always derived from the selected
    group's own engineering configuration (config/devices.py::
    BATTERY_GROUPS[group]["battery_type"]) -- never operator input. This
    screen displays them so the operator can see and confirm what will
    run, but the only decision being confirmed here is "proceed with this
    group's configuration," not "which battery."

    Returns True if the operator pressed ENTER (continue), False if the
    operator pressed C (cancel). Cancelling here happens before any relay/
    PSU/measurement action -- callers must not touch hardware until this
    returns True.
    """
    print("\n" + "-" * 60)
    print("Operation Summary")
    print("-" * 60)
    print(f"\nOperation:\n{operation}")
    print(f"\nBattery Type (engineering-configured for this group):\n"
          f"{battery_type}  ({battery_cfg['capacity_ah'] * 1000:.0f} mAh)")
    print(f"\nGroup:\n{group}")
    print(f"\nPositions:\n{positions_label}")
    print(f"\nRelay Matrix:\n{hw['relay_matrix_name'] or '(none assigned)'}")
    print(f"\nSMU:\n{hw['smu_name'] or '(none assigned)'}")
    print(f"\nDMM:\n{hw['dmm_name'] or '(none assigned)'}")
    print(f"\nDAQ:\n{hw['daq_name'] or '(none assigned)'}")
    for line in (extra_lines or []):
        print(line)
    print("\n" + "-" * 60)
    answer = input("Press ENTER to continue, or C to cancel: ").strip().upper()
    return answer != "C"


def _run_monitor_battery():
    """
    Monitor Battery -- read-only battery monitoring, no charging, no
    discharging. Workflow: Select Battery Group -> Select Battery Position
    -> Confirmation Screen -> Configuration Snapshot Logged -> Relay Close
    -> Start Monitoring. Battery type is engineering-configured per group
    (config/devices.py::BATTERY_GROUPS[group]["battery_type"]), never an
    operator choice -- see docs/architecture.md "Battery Group Test
    Configuration Architecture". Uses the same Milestone II infrastructure
    Proto Test Execution already validated (DataStorage: measurements/
    run_summary/event_log/station_state, ExecutionFrame/
    render_execution_frame()) via test_control/monitor_battery_sequence.py::
    MonitorBatterySequence.
    """
    print("MONITOR BATTERY")

    import signal
    from data.storage import DataStorage
    from test_control.hardware_manager import HardwareManager
    from test_control.monitor_battery_sequence import MonitorBatterySequence
    from test_control.safety_monitor import SafetyMonitor
    from utils.cancellation import CancellationToken
    from utils.errors import HardwareInitError, OperationCancelledError

    group = _select_battery_group()
    if group is None:
        return

    position = _select_battery_position(group)
    if position is None:
        return

    # Hardware assignment resolved from config/devices.py::BATTERY_GROUPS via
    # the single centralized resolver -- no positional SMU_ASSIGNMENTS/
    # DAQ_CONFIG/DMM_CONFIG lookup here (see docs/architecture.md "Hardware
    # Resolution Model").
    hw = dev_cfg.hardware_for_group(group)
    missing = _missing_hardware_roles(hw)
    if missing:
        print(f"\n[FAIL] Group {group} has no {', '.join(missing)} assigned -- "
              f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return

    # Battery type is engineering-configured per group, never an operator
    # choice -- see config/devices.py::group_test_config() / docs/
    # architecture.md "Battery Group Test Configuration Architecture".
    battery_type = dev_cfg.group_test_config(group)["battery_type"]
    if battery_type is None:
        print(f"\n[FAIL] Group {group} has no battery_type configured -- "
              f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]

    channel = dev_cfg.resolve_group_position(group, position)
    ch_cfg = dev_cfg.BATTERY_CHANNELS.get(channel)
    if ch_cfg is None:
        print(f"\n[FAIL] No BATTERY_CHANNELS entry for resolved position {channel} -- check config/devices.py.")
        return
    relay_address = ch_cfg["relay_address"]

    positions_label = f"{position} (Group {group} Position {position})"
    extra_lines = [
        f"\nMax Voltage:\n{battery_cfg['voltage_max_v']:.2f} V   "
        f"Min Voltage: {battery_cfg['voltage_min_v']:.2f} V",
        f"\nMax Charge Current:\n{battery_cfg['max_charge_current_a']:.3f} A   "
        f"Max Discharge Current: {battery_cfg['max_discharge_current_a']:.3f} A",
        f"\nMax Temperature:\n{battery_cfg['max_temp_c']:.1f} C",
    ]
    if not _confirm_operation("Monitor Battery", battery_type, battery_cfg, group,
                               positions_label, hw, extra_lines=extra_lines):
        print("\nCancelled -- no relay activated.")
        return

    relay_cfg = hw["relay_matrix_cfg"]
    smu_name, smu_cfg = hw["smu_name"], hw["smu_cfg"]
    # TEMPORARY: voltage source is the DMM, not the DAQ (see
    # test_control/monitor_battery_sequence.py module docstring).
    dmm_name, dmm_cfg = hw["dmm_name"], hw["dmm_cfg"]
    daq_name, daq_cfg = hw["daq_name"], hw["daq_cfg"]
    print("\nSelected Hardware\n")
    print(f"Relay:\n  {dev_cfg.device_display_name(relay_cfg)}  \n  {relay_cfg.get('ip', '')}\n")
    print(f"DMM (temporary voltage source):\n  {dev_cfg.device_display_name(dmm_cfg)}\n  {dmm_cfg.get('resource', '')}\n")

    hw_mgr = HardwareManager(Settings, relay_cfg=relay_cfg, smu_cfg=smu_cfg, daq_cfg=daq_cfg, dmm_cfg=dmm_cfg)
    try:
        hw_mgr.connect_all()
    except HardwareInitError as e:
        print(f"[FAIL] Hardware initialization failed: {e}")
        return

    storage = DataStorage(settings=Settings)
    storage.open()

    try:
        # CRITICAL traceability requirement: every selected-configuration
        # fact is recorded via event_log BEFORE relay activation/monitor
        # start/measurement acquisition -- see docs/architecture.md
        # "Configuration Traceability".
        hardware_snapshot = _hardware_snapshot_fields(
            smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg,
        )
        storage.start_run_summary(
            test_type="monitor",
            battery_type=battery_type,
            battery_voltage_max_v=battery_cfg["voltage_max_v"],
            battery_voltage_min_v=battery_cfg["voltage_min_v"],
            battery_charge_current_limit_a=battery_cfg["max_charge_current_a"],
            battery_discharge_current_limit_a=battery_cfg["max_discharge_current_a"],
            capacity_ah=battery_cfg["capacity_ah"],
            **hardware_snapshot,
        )
        storage.log_event(level="INFO", source="monitor_battery", message="Run started")
        storage.log_event(level="INFO", source="monitor_battery", message="Operation selected: Monitor Battery")
        storage.log_event(level="INFO", source="monitor_battery", message=f"Battery selected: {battery_type}")
        storage.log_event(level="INFO", source="monitor_battery",
                           message=f"Battery capacity: {battery_cfg['capacity_ah'] * 1000:.0f} mAh")
        storage.log_event(level="INFO", source="monitor_battery", message=f"Group selected: {group}")
        storage.log_event(level="INFO", source="monitor_battery",
                           channel=channel, relay=relay_address,
                           message=f"Position selected: {position} (Group {group} Position {position})")
        storage.log_event(level="INFO", source="monitor_battery",
                           message="Configuration snapshot recorded")
        storage.log_event(level="INFO", source="monitor_battery", message="Hardware assignment resolved")
        storage.log_event(level="INFO", source="monitor_battery", message=f"Relay matrix selected: {hw['relay_matrix_name']}")
        storage.log_event(level="INFO", source="monitor_battery", message=f"SMU selected: {smu_name}")
        storage.log_event(level="INFO", source="monitor_battery", message=f"DMM selected: {dmm_name}")
        storage.log_event(level="INFO", source="monitor_battery", message=f"DAQ selected: {daq_name}")
        storage.log_event(level="INFO", source="monitor_battery", message="Operator confirmed execution")
        # Hardware identity traceability -- BEFORE relay activation/monitor
        # start, same requirement as the battery-config snapshot above (see
        # docs/architecture.md "Hardware Identity Traceability").
        for message in dev_cfg.hardware_traceability_messages(hardware_snapshot):
            storage.log_event(level="INFO", source="monitor_battery", message=message)

        token = CancellationToken(owner="test.py:_run_monitor_battery")
        previous_sigint_handler = signal.signal(
            signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
        )
        print("\nPress Ctrl+C to stop monitoring safely.\n")

        try:
            safety = SafetyMonitor(Settings)
            sequence = MonitorBatterySequence(
                smu=hw_mgr.smu, dmm=hw_mgr.dmm, relay=hw_mgr.relay, safety=safety,
                storage=storage, settings=Settings,
            )
            try:
                sequence.run(
                    channel=channel, relay_address=relay_address,
                    token=token,
                )
            except OperationCancelledError:
                print("\nMonitor Battery stopped by operator -- hardware is in a verified safe state.")
            except KeyboardInterrupt:
                print("\nMonitor Battery interrupted by user (Ctrl+C).")
            except Exception as e:
                print(f"\n[FAIL] Monitor Battery aborted: {e}")
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        try:
            storage.close()
        except Exception as e:
            print(f"[WARNING] Storage close failed: {e}")
        try:
            hw_mgr.disconnect_all()
        except Exception as shutdown_err:
            print(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
            print("           Hardware may still be energized -- "
                  "physically disconnect power if this cannot be resolved immediately.")


def _run_monitor_battery_scan():
    """
    Monitor Battery Scan -- intermediate hardware-path validation required
    before Milestone III Charge Battery workflows. Sequentially connects
    every battery position in the selected group through the relay matrix
    and verifies only one battery is visible to the measurement system at
    a time (relay isolation, DMM path, DAQ path). NO charging, NO
    discharging -- the PSU/SMU is never commanded to source or sink
    anything; HardwareManager still connects it (and forces its output off
    at startup, independent of this workflow) purely so the shared safety-
    shutdown path (SafetyMonitor.emergency_stop()/safe_cancel_shutdown())
    has an SMU to confirm OFF, exactly as Monitor Battery already does.

    Workflow: Select Battery Group (= Scan Scope, since only "Single
    Group" is supported today) -> Select Position Range -> Confirmation ->
    Configuration Snapshot -> Hardware Traceability Snapshot -> Sequential
    Relay Scan -> Safe Shutdown. Battery type is engineering-configured
    per group, never an operator choice -- see docs/architecture.md
    "Battery Group Test Configuration Architecture". Reuses the exact same
    Milestone II infrastructure as _run_monitor_battery() (DataStorage,
    HardwareManager, CancellationToken/Ctrl+C handling, ExecutionFrame) via
    test_control/monitor_battery_scan_sequence.py::MonitorBatteryScanSequence.
    """
    print("MONITOR BATTERY SCAN -- relay/DMM/DAQ path validation (no charging)")

    import signal
    from data.storage import DataStorage
    from test_control.hardware_manager import HardwareManager
    from test_control.monitor_battery_scan_sequence import MonitorBatteryScanSequence
    from test_control.safety_monitor import SafetyMonitor
    from utils.cancellation import CancellationToken
    from utils.errors import HardwareInitError, OperationCancelledError

    group = _select_battery_group()
    if group is None:
        return

    grp_cfg = dev_cfg.BATTERY_GROUPS[group]
    size = grp_cfg["position_end"] - grp_cfg["position_start"] + 1
    positions_in_group = list(range(1, size + 1))
    print(f"\nScan Scope: Single Group -- Group {group}, all positions 1-{size}")

    # Hardware assignment resolved from config/devices.py::BATTERY_GROUPS via
    # the single centralized resolver -- see docs/architecture.md "Hardware
    # Resolution Model".
    hw = dev_cfg.hardware_for_group(group)
    missing = _missing_hardware_roles(hw)
    if missing:
        print(f"\n[FAIL] Group {group} has no {', '.join(missing)} assigned -- "
              f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return

    # Battery type is engineering-configured per group, never an operator
    # choice -- see config/devices.py::group_test_config().
    battery_type = dev_cfg.group_test_config(group)["battery_type"]
    if battery_type is None:
        print(f"\n[FAIL] Group {group} has no battery_type configured -- "
              f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]

    positions_label = f"1-{size}"
    extra_lines = ["\nCharging:\nNONE -- PSU/SMU output is never enabled"]
    if not _confirm_operation("Monitor Battery Scan", battery_type, battery_cfg, group,
                               positions_label, hw, extra_lines=extra_lines):
        print("\nCancelled -- no relay activated.")
        return

    relay_cfg = hw["relay_matrix_cfg"]
    dmm_name, dmm_cfg = hw["dmm_name"], hw["dmm_cfg"]
    smu_name, smu_cfg = hw["smu_name"], hw["smu_cfg"]
    daq_name, daq_cfg = hw["daq_name"], hw["daq_cfg"]
    print("\nSelected Hardware\n")
    print(f"Relay:\n  {dev_cfg.device_display_name(relay_cfg)}  \n  {relay_cfg.get('ip', '')}\n")
    print(f"DMM:\n  {dev_cfg.device_display_name(dmm_cfg)}\n  {dmm_cfg.get('resource', '')}\n")
    print(f"DAQ:\n  {dev_cfg.device_display_name(daq_cfg)}\n  {daq_cfg.get('resource', '')}\n")
    print("PSU/SMU: connected for safety-shutdown only -- output never enabled.\n")

    hw_mgr = HardwareManager(Settings, relay_cfg=relay_cfg, smu_cfg=smu_cfg, daq_cfg=daq_cfg, dmm_cfg=dmm_cfg)
    try:
        hw_mgr.connect_all()
    except HardwareInitError as e:
        print(f"[FAIL] Hardware initialization failed: {e}")
        return

    storage = DataStorage(settings=Settings)
    storage.open()

    try:
        # Configuration Snapshot + Hardware Traceability Snapshot -- BEFORE
        # any relay activation, same requirement as _run_monitor_battery().
        hardware_snapshot = _hardware_snapshot_fields(
            smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg,
        )
        storage.start_run_summary(
            test_type="monitor_scan",
            battery_type=battery_type,
            battery_voltage_max_v=battery_cfg["voltage_max_v"],
            battery_voltage_min_v=battery_cfg["voltage_min_v"],
            battery_charge_current_limit_a=battery_cfg["max_charge_current_a"],
            battery_discharge_current_limit_a=battery_cfg["max_discharge_current_a"],
            capacity_ah=battery_cfg["capacity_ah"],
            **hardware_snapshot,
        )
        storage.log_event(level="INFO", source="monitor_battery_scan", message="Run started")
        storage.log_event(level="INFO", source="monitor_battery_scan", message="Operation selected: Monitor Battery Scan")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"Battery selected: {battery_type}")
        storage.log_event(level="INFO", source="monitor_battery_scan",
                           message=f"Battery capacity: {battery_cfg['capacity_ah'] * 1000:.0f} mAh")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"Group selected: {group}")
        storage.log_event(level="INFO", source="monitor_battery_scan",
                           message=f"Scan scope: Single Group -- Group {group}, positions 1-{size}")
        storage.log_event(level="INFO", source="monitor_battery_scan",
                           message="Configuration snapshot recorded")
        storage.log_event(level="INFO", source="monitor_battery_scan", message="Hardware assignment resolved")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"Relay matrix selected: {hw['relay_matrix_name']}")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"SMU selected: {smu_name}")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"DMM selected: {dmm_name}")
        storage.log_event(level="INFO", source="monitor_battery_scan", message=f"DAQ selected: {daq_name}")
        storage.log_event(level="INFO", source="monitor_battery_scan", message="Operator confirmed execution")
        for message in dev_cfg.hardware_traceability_messages(hardware_snapshot):
            storage.log_event(level="INFO", source="monitor_battery_scan", message=message)

        token = CancellationToken(owner="test.py:_run_monitor_battery_scan")
        previous_sigint_handler = signal.signal(
            signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
        )
        print("\nPress Ctrl+C to stop the scan safely.\n")

        try:
            safety = SafetyMonitor(Settings)
            sequence = MonitorBatteryScanSequence(
                smu=hw_mgr.smu, dmm=hw_mgr.dmm, daq=hw_mgr.daq, relay=hw_mgr.relay, safety=safety,
                storage=storage, settings=Settings,
            )
            try:
                sequence.run(
                    battery_type=battery_type, group=group,
                    positions_in_group=positions_in_group, token=token,
                )
                print("\nMonitor Battery Scan complete -- see event log / measurements for results.")
            except OperationCancelledError:
                print("\nMonitor Battery Scan stopped by operator -- hardware is in a verified safe state.")
            except KeyboardInterrupt:
                print("\nMonitor Battery Scan interrupted by user (Ctrl+C).")
            except Exception as e:
                print(f"\n[FAIL] Monitor Battery Scan aborted: {e}")
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        try:
            storage.close()
        except Exception as e:
            print(f"[WARNING] Storage close failed: {e}")
        try:
            hw_mgr.disconnect_all()
        except Exception as shutdown_err:
            print(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
            print("           Hardware may still be energized -- "
                  "physically disconnect power if this cannot be resolved immediately.")


def _run_charge_or_discharge(operation: str, sequence_cls, source: str, limit_line_fn):
    """
    Shared workflow for Charge Battery / Discharge Battery -- both are the
    same skeleton as _run_monitor_battery() (select group -> select
    position -> resolve hardware via hardware_for_group() -> validate
    group test configuration (derives battery type from the group -- see
    docs/architecture.md "Battery Group Test Configuration Architecture")
    -> confirmation screen -> traceability -> ChargeSequence/
    DischargeSequence.run()) with only the sequence class, event-log
    source name, and confirmation-screen setpoint line differing --
    factored here once rather than duplicating the whole workflow twice.
    Built on BatteryOperationSequence (test_control/
    battery_operation_sequence.py) via `sequence_cls` -- never
    TestExecutor/BatteryTestSequence, and never a second workflow
    architecture (see docs/architecture.md Section 35).

    Battery type is NOT operator input here -- it is derived from the
    selected group via validate_group_test_config() below, which reads
    config/devices.py::BATTERY_GROUPS[group]["battery_type"] directly.

    DAQ is intentionally NOT a required hardware role here (unlike Monitor
    Battery Scan, which validates the DAQ path) -- ChargeSequence/
    DischargeSequence use the DMM for voltage and the SMU's own measure()
    for current (docs/architecture.md Section 31 "Telemetry Source
    Strategy"), so this workflow must not be blocked by an unassigned or
    unapproved DAQ.
    """
    print(operation.upper())

    import signal
    from data.storage import DataStorage
    from test_control.hardware_manager import HardwareManager
    from test_control.safety_monitor import SafetyMonitor
    from utils.cancellation import CancellationToken
    from utils.errors import (
        ConfigurationError, GroupConfigurationError, HardwareConfigurationError,
        HardwareInitError, OperationCancelledError,
    )
    from utils.validators import validate_group_test_config

    group = _select_battery_group()
    if group is None:
        return

    position = _select_battery_position(group)
    if position is None:
        return

    hw = dev_cfg.hardware_for_group(group)
    missing = _missing_hardware_roles(hw, required_roles=("relay_matrix", "smu", "dmm"))
    if missing:
        print(f"\n[FAIL] Group {group} has no {', '.join(missing)} assigned -- "
              f"see config/devices.py::BATTERY_GROUPS[{group!r}]. Aborting, no hardware activated.")
        return

    # Battery Group Test Configuration Architecture validation pipeline --
    # Group Configuration -> Battery Limits -> Hardware Capability -- runs
    # BEFORE anything below touches hardware (no HardwareManager
    # constructed yet). Battery type is derived here from the group's own
    # engineering configuration, never from operator input -- see
    # docs/architecture.md "Battery Group Test Configuration Architecture"
    # / utils/validators.py.
    try:
        validated = validate_group_test_config(group)
    except (GroupConfigurationError, ConfigurationError, HardwareConfigurationError) as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}\nAborting, no hardware activated.")
        return
    battery_type = validated["battery_type"]
    battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]
    test_setpoints = validated["test_setpoints"]

    channel = dev_cfg.resolve_group_position(group, position)
    ch_cfg = dev_cfg.BATTERY_CHANNELS.get(channel)
    if ch_cfg is None:
        print(f"\n[FAIL] No BATTERY_CHANNELS entry for resolved position {channel} -- check config/devices.py.")
        return
    relay_address = ch_cfg["relay_address"]

    positions_label = f"{position} (Group {group} Position {position})"
    extra_lines = [
        f"\nMax Voltage:\n{battery_cfg['voltage_max_v']:.2f} V   "
        f"Min Voltage: {battery_cfg['voltage_min_v']:.2f} V",
        limit_line_fn(test_setpoints),
        f"\nMax Temperature:\n{battery_cfg['max_temp_c']:.1f} C",
    ]
    if not _confirm_operation(operation, battery_type, battery_cfg, group,
                               positions_label, hw, extra_lines=extra_lines):
        print("\nCancelled -- no relay activated.")
        return

    relay_cfg = hw["relay_matrix_cfg"]
    smu_name, smu_cfg = hw["smu_name"], hw["smu_cfg"]
    dmm_name, dmm_cfg = hw["dmm_name"], hw["dmm_cfg"]
    daq_name, daq_cfg = hw["daq_name"], hw["daq_cfg"]
    print("\nSelected Hardware\n")
    print(f"Relay:\n  {dev_cfg.device_display_name(relay_cfg)}  \n  {relay_cfg.get('ip', '')}\n")
    print(f"SMU:\n  {dev_cfg.device_display_name(smu_cfg)}\n  {smu_cfg.get('resource', '')}\n")
    print(f"DMM (telemetry source):\n  {dev_cfg.device_display_name(dmm_cfg)}\n  {dmm_cfg.get('resource', '')}\n")

    hw_mgr = HardwareManager(Settings, relay_cfg=relay_cfg, smu_cfg=smu_cfg, daq_cfg=daq_cfg, dmm_cfg=dmm_cfg)
    try:
        hw_mgr.connect_all()
    except HardwareInitError as e:
        print(f"[FAIL] Hardware initialization failed: {e}")
        return

    storage = DataStorage(settings=Settings)
    storage.open()

    try:
        # CRITICAL traceability requirement: every selected-configuration
        # fact is recorded via event_log BEFORE relay activation/PSU
        # output -- same requirement as _run_monitor_battery().
        hardware_snapshot = _hardware_snapshot_fields(
            smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg,
        )
        storage.start_run_summary(
            test_type=source,
            battery_type=battery_type,
            battery_voltage_max_v=battery_cfg["voltage_max_v"],
            battery_voltage_min_v=battery_cfg["voltage_min_v"],
            battery_charge_current_limit_a=battery_cfg["max_charge_current_a"],
            battery_discharge_current_limit_a=battery_cfg["max_discharge_current_a"],
            capacity_ah=battery_cfg["capacity_ah"],
            **hardware_snapshot,
        )
        storage.log_event(level="INFO", source=source, message="Run started")
        storage.log_event(level="INFO", source=source, message=f"Operation selected: {operation}")
        storage.log_event(level="INFO", source=source, message=f"Battery selected: {battery_type}")
        storage.log_event(level="INFO", source=source,
                           message=f"Battery capacity: {battery_cfg['capacity_ah'] * 1000:.0f} mAh")
        storage.log_event(level="INFO", source=source, message=f"Group selected: {group}")
        storage.log_event(level="INFO", source=source,
                           channel=channel, relay=relay_address,
                           message=f"Position selected: {position} (Group {group} Position {position})")
        storage.log_event(level="INFO", source=source, message="Configuration snapshot recorded")
        storage.log_event(level="INFO", source=source, message="Hardware assignment resolved")
        storage.log_event(level="INFO", source=source, message=f"Relay matrix selected: {hw['relay_matrix_name']}")
        storage.log_event(level="INFO", source=source, message=f"SMU selected: {smu_name}")
        storage.log_event(level="INFO", source=source, message=f"DMM selected: {dmm_name}")
        storage.log_event(level="INFO", source=source, message=f"DAQ selected: {daq_name}")
        storage.log_event(level="INFO", source=source, message="Operator confirmed execution")
        for message in dev_cfg.hardware_traceability_messages(hardware_snapshot):
            storage.log_event(level="INFO", source=source, message=message)

        token = CancellationToken(owner=f"test.py:_run_charge_or_discharge:{source}")
        previous_sigint_handler = signal.signal(
            signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
        )
        print(f"\nPress Ctrl+C to stop {operation.lower()} safely.\n")

        try:
            safety = SafetyMonitor(Settings)
            # daq=hw_mgr.daq passed through even though ChargeSequence/
            # DischargeSequence don't read it yet -- see their constructors'
            # comments. Keeps the handle available for a future DAQ
            # integration without requiring test.py to change again.
            sequence = sequence_cls(
                smu=hw_mgr.smu, dmm=hw_mgr.dmm, daq=hw_mgr.daq, relay=hw_mgr.relay,
                safety=safety, storage=storage, settings=Settings,
            )
            try:
                sequence.run(
                    channel=channel, relay_address=relay_address,
                    battery_cfg=battery_cfg, test_setpoints=test_setpoints, token=token,
                )
                print(f"\n{operation} complete.")
            except OperationCancelledError:
                print(f"\n{operation} stopped by operator -- hardware is in a verified safe state.")
            except KeyboardInterrupt:
                print(f"\n{operation} interrupted by user (Ctrl+C).")
            except Exception as e:
                print(f"\n[FAIL] {operation} aborted: {e}")
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        try:
            storage.close()
        except Exception as e:
            print(f"[WARNING] Storage close failed: {e}")
        try:
            hw_mgr.disconnect_all()
        except Exception as shutdown_err:
            print(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
            print("           Hardware may still be energized -- "
                  "physically disconnect power if this cannot be resolved immediately.")


def _run_charge_battery():
    """Charge Battery -- CC-CV charge via ChargeSequence (built on
    BatteryOperationSequence). See test_control/charge_sequence.py and
    docs/architecture.md Sections 33/35/39. `limit_line_fn` receives the
    group's validated test_setpoints (the commanded recipe), not
    battery_cfg (the battery's own limit -- already shown separately by
    _run_charge_or_discharge()'s Max/Min Voltage line)."""
    from test_control.charge_sequence import ChargeSequence
    _run_charge_or_discharge(
        "Charge Battery", ChargeSequence, "charge_battery",
        lambda setpoints: (f"\nCharge Current (commanded):\n{setpoints['charge_current_a']:.3f} A   "
                            f"CV Target: {setpoints['charge_voltage_v']:.2f} V"),
    )


def _run_discharge_battery():
    """Discharge Battery -- CC discharge via DischargeSequence (built on
    BatteryOperationSequence). See test_control/discharge_sequence.py and
    docs/architecture.md Sections 30/33/35/39. `limit_line_fn` receives the
    group's validated test_setpoints, not battery_cfg -- see
    _run_charge_battery()'s docstring for the same rationale."""
    from test_control.discharge_sequence import DischargeSequence
    _run_charge_or_discharge(
        "Discharge Battery", DischargeSequence, "discharge_battery",
        lambda setpoints: (f"\nDischarge Current (commanded):\n{setpoints['discharge_current_a']:.3f} A   "
                            f"Cutoff Target: {setpoints['discharge_cutoff_v']:.2f} V"),
    )


def run_main_test():
    """
    Run Main Test -- battery-centric operator workflow entry point
    (Milestone II Monitor Battery blueprint). Submenu: Monitor Battery,
    Charge Battery, Discharge Battery, and Monitor Battery Scan are
    implemented; Cycle Battery (charge -> rest -> discharge composition)
    remains a placeholder for future work -- see docs/architecture.md
    Section 35 "Revised Roadmap".
    """
    print("RUN MAIN TEST")
    print("\n1. Monitor Battery")
    print("2. Charge Battery")
    print("3. Discharge Battery")
    print("4. Cycle Battery")
    print("5. Monitor Battery Scan (relay/DMM/DAQ path validation, no charging)")
    choice = input("\nSelect mode: ").strip()

    if choice == "1":
        _run_monitor_battery()
    elif choice == "2":
        _run_charge_battery()
    elif choice == "3":
        _run_discharge_battery()
    elif choice == "4":
        print("\nCycle Battery -- not yet implemented.")
    elif choice == "5":
        _run_monitor_battery_scan()
    else:
        print("\nInvalid selection.")


# =============================================================================
# 0b. Proto Test Execution -- Milestone 2: infrastructure validation, no
# battery connected. Exercises the real architecture end-to-end (relay ->
# SMU -> DMM -> SQLite -> recovery display) using test_control/
# proto_test_sequence.py::ProtoTestSequence -- reuses HardwareManager,
# CancellationToken/Ctrl+C handling, and DataStorage exactly as
# run_main_test() above, so this is the same production plumbing, not a
# parallel framework. See docs/architecture.md "Proto Test Execution".
# =============================================================================

def run_proto_test_execution():
    """
    Proto Test Execution: cycles every configured relay, sourcing a bench
    SMU voltage point (fully verified -- see hardware/smu.py) and taking a
    DMM reading on each, persisting station state to SQLite, with NO
    battery connected. Reads and displays (never auto-resumes) the previous
    execution's last known position at startup.
    """
    print("PROTO TEST EXECUTION -- infrastructure validation (no battery connected)")

    import signal
    from data.storage import DataStorage
    from test_control.hardware_manager import HardwareManager
    from test_control.proto_test_sequence import ProtoTestSequence
    from test_control.safety_monitor import SafetyMonitor
    from utils.cancellation import CancellationToken
    from utils.errors import HardwareInitError, OperationCancelledError

    # Named explicitly via Settings.PROTO_TEST_SMU_NAME -- NOT
    # next(iter(SMU_ASSIGNMENTS.items())) (that positional "whichever SMU is
    # listed first" default is what HardwareManager/run_main_test() use for
    # the real battery-test path, and always resolves to PRIMARY_SMU
    # regardless of which physical unit is wired up for this bench
    # workflow). Scoped to this function only -- HardwareManager's own
    # default and main.py are untouched.
    smu_name = Settings.PROTO_TEST_SMU_NAME
    smu_cfg  = dev_cfg.SMU_ASSIGNMENTS[smu_name]
    dmm_name = dev_cfg.find_config_name(dev_cfg.DMM_CONFIGS, dev_cfg.DMM_CONFIG)
    dmm_cfg  = dev_cfg.DMM_CONFIG
    daq_name = dev_cfg.find_config_name(dev_cfg.DAQ_CONFIGS, dev_cfg.DAQ_CONFIG)
    daq_cfg  = dev_cfg.DAQ_CONFIG
    relay_cfg = dev_cfg.NUMATO_RELAY_MATRIX_CONFIG

    print("\nSelected Hardware\n")
    print(f"SMU:\n  {dev_cfg.device_display_name(smu_cfg)}  [{smu_name}]\n  {smu_cfg.get('resource', '')}\n")
    print(f"DMM:\n  {dev_cfg.device_display_name(dmm_cfg)}\n  {dmm_cfg.get('resource', '')}\n")
    print(f"Relay:\n  {dev_cfg.device_display_name(relay_cfg)}\n  {relay_cfg.get('ip', '')}\n")

    # DMM is required for this workflow (unlike run_main_test(), which
    # leaves it optional) -- pass dmm_cfg explicitly so HardwareManager
    # actually constructs and connects it. daq_cfg is now passed explicitly
    # too (previously left to HardwareManager's internal default) so the
    # hardware-identity snapshot below matches, 1:1, the exact cfg dict
    # HardwareManager actually built the DAQ driver from -- same value as
    # before (DAQ_CONFIG), no behavior change.
    hw = HardwareManager(Settings, relay_cfg=relay_cfg, smu_cfg=smu_cfg, daq_cfg=daq_cfg, dmm_cfg=dmm_cfg)

    try:
        hw.connect_all()
    except HardwareInitError as e:
        print(f"[FAIL] Hardware initialization failed: {e}")
        return

    storage = DataStorage(settings=Settings)
    storage.open()

    try:
        last_state = storage.get_last_execution_state()
        print("\nPrevious execution found:\n" if last_state else "\nNo previous execution found.\n")
        if last_state:
            print(f"    Relay:     {last_state['relay']}")
            print(f"    State:     {last_state['state']}")
            print(f"    Timestamp: {last_state['timestamp']}")
        print("\n(Display only -- no automatic resume.)")

        relays  = Settings.ACTIVE_CHANNELS
        dwell_s = Settings.PROTO_TEST_DWELL_S
        print(f"\nRelays to cycle: {relays}")
        print(f"Dwell per relay: {dwell_s:.0f}s")

        token = CancellationToken(owner="test.py:run_proto_test_execution")
        previous_sigint_handler = signal.signal(
            signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
        )
        print("\nPress Ctrl+C to cancel safely.\n")

        try:
            safety   = SafetyMonitor(Settings)
            sequence = ProtoTestSequence(
                smu=hw.smu, dmm=hw.dmm, relay=hw.relay, safety=safety,
                storage=storage, settings=Settings,
            )
            hardware_snapshot = _hardware_snapshot_fields(
                smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg,
            )
            try:
                sequence.run(relays, dwell_s, token=token, hardware_snapshot=hardware_snapshot)
                print("\nProto Test Execution complete -- all relays cycled successfully.")
            except OperationCancelledError:
                print("\nProto Test Execution cancelled by operator -- "
                      "hardware is in a verified safe state.")
            except KeyboardInterrupt:
                # Defensive fallback only -- should not normally fire while
                # the SIGINT handler above is installed.
                print("\nProto Test Execution interrupted by user (Ctrl+C).")
            except Exception as e:
                print(f"\n[FAIL] Proto Test Execution aborted: {e}")
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        try:
            storage.close()
        except Exception as e:
            print(f"[WARNING] Storage close failed: {e}")
        try:
            hw.disconnect_all()
        except Exception as shutdown_err:
            print(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
            print("           Hardware may still be energized -- "
                  "physically disconnect power if this cannot be resolved immediately.")


# =============================================================================
# Menu
# =============================================================================

# MENU structure history (see docs/architecture.md "Menu Restructuring
# Review" and docs/MILESTONES.md for the full rationale behind each
# change below):
#   - "Test Temperature Module" retired as a standalone entry -- battery
#     temperature monitoring comes through the DAQ NTC path (see
#     test_sensors()'s Test 6); test_hardware_discovery() still reports
#     TEMP_MODULE's identity.
#   - "Test Configuration" removed -- test_configuration() already runs
#     automatically in preflight_check() before this menu is ever shown;
#     the function itself is unchanged and still called there.
#   - "Test SQLite (foundation)"/"Test Database Layer" consolidated into
#     one "Database Tools" entry (test_database_tools()) with a submenu
#     that both inspects the REAL project database and still runs both
#     original temp-DB regression self-tests.
#   - "Run All Tests" replaced with "UI Test" (test_ui_preview()) -- a
#     hardware/database-free ExecutionFrame rendering preview. The
#     aggregate-everything behavior this replaced is intentionally not
#     kept elsewhere (it depended on a MENU entry with fn=None, a pattern
#     this restructuring removes -- see _dispatch_menu_choice() below).
MENU = [
    ("Run Main Test",                 run_main_test),
    ("Proto Test Execution (infrastructure validation, no battery)", run_proto_test_execution),
    ("Startup Device Validation (config/devices.py -- no hardware I/O)", test_device_validation),
    ("Hardware Discovery (connectivity + identification, config-driven)", test_hardware_discovery),
    ("Test SMU (PSU)",                test_smu),
    ("Test DMM",                      test_dmm),
    ("Test DAQ",                      test_daq),
    ("Test Numato Relay Matrix (Ethernet)", test_relay_numato),
    ("Test PXI Relay Matrix",         test_pxi_relay_matrix),
    ("Test Sensors (NTC)",            test_sensors),
    ("Test Safety Monitor (workflow simulator)", test_safety_monitor),
    ("Database Tools",                test_database_tools),
    ("UI Test (demo screens -- no hardware, no database)", test_ui_preview),
]


def run_section(label, fn):
    print(f"\n{'-' * 60}")
    print(f"  {label}")
    print(f"{'-' * 60}")
    results = fn()
    for r in results:
        r.print_detail()
    return results


# =============================================================================
# Entry point
# =============================================================================

def _pause_before_main_menu():
    """
    Single, centralized "return to Main Menu" checkpoint -- called after
    EVERY test/validation run (PASS, WARNING, FAIL, exception, or operator
    cancellation all reach this same line, see _dispatch_menu_choice() below).
    Never exits the app and never leaves the operator inside a nested menu:
    once this returns, control is back in main()'s top-level menu loop.
    """
    try:
        input("\nPress Enter to return to the Main Menu... ")
    except (KeyboardInterrupt, EOFError):
        print()


# Full-hardware-run menu entries -- each drives its own real hardware run
# (HardwareManager/CancellationToken/etc.) and returns nothing (unlike every
# other MENU entry, which returns a list[TestResult]). Checked by name here
# (not by category/label) so run_section() below is never called on them --
# doing so would crash on `for r in None`.
_FULL_RUN_ENTRIES = (run_main_test, run_proto_test_execution)


def _dispatch_menu_choice(label: str, fn):
    """
    Run exactly one Main Menu selection, print its summary the same way it
    always has (PASS/FAIL/WARNING reporting unchanged), then unconditionally
    pause at "Press Enter to return to the Main Menu..." before returning --
    regardless of whether the test PASSED, WARNED, FAILED, raised, or was
    cancelled by the operator (Ctrl+C). This is the one place that behavior
    is implemented, so every menu entry gets it identically with no
    duplicated per-entry code.

    The previous "Run All Tests" (fn=None, aggregating every other MENU
    entry) was replaced by "UI Test" as part of the menu restructuring
    review (see docs/architecture.md) -- every MENU entry now returns
    list[TestResult] or drives its own hardware run, so this function no
    longer needs a third fn=None branch.
    """
    try:
        if fn in _FULL_RUN_ENTRIES:
            print(f"\n{'-' * 60}")
            print(f"  {label}")
            print(f"{'-' * 60}")
            fn()
        else:
            results = run_section(label, fn)
            print_summary(results)
    except KeyboardInterrupt:
        print("\nCancelled by operator (Ctrl+C).")
    except Exception as e:
        print(f"\n[ERROR] {label} raised an unexpected exception: {e}")
    _pause_before_main_menu()


def main():
    print()
    print("=" * 60)
    print(f"  {Settings.PROJECT_NAME}  v{Settings.VERSION}")
    print("  Test Framework")
    print("=" * 60)

    print("\n[Pre-flight: Configuration Validation]")
    _config_results, config_ok = preflight_check()

    if not config_ok:
        print("\n  Configuration has FAIL errors.")
        print("  Fix config/ files before running hardware tests.")
        sys.exit(1)

    while True:
        print()
        for i, (label, _) in enumerate(MENU, 1):
            print(f"  {i:2}. {label}")
        print("   0. Exit")
        print()

        try:
            raw = input("Choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return

        if raw == "0":
            return

        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(MENU):
                raise ValueError()
        except ValueError:
            print("Invalid choice.")
            continue

        label, fn = MENU[idx]
        _dispatch_menu_choice(label, fn)


if __name__ == "__main__":
    main()
