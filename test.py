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


def _print_device_config(name: str, cfg: dict):
    """Print the effective configuration of the device under test."""
    print(f"\n{'-' * 60}")
    print("  Testing Device")
    print(f"{'-' * 60}\n")
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
    expected = list(range(1, Settings.NUM_CHANNELS + 1))
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
    for name, cfg in [("SMU_ASSIGNMENTS", dev_cfg.SMU_ASSIGNMENTS.get("SMU1", {})),
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

    if Settings.DISCHARGE_CUTOFF_V < Settings.BAT_VOLTAGE_MIN:
        results.append(_warn("Configuration", "Discharge Cutoff", ref,
                             f"DISCHARGE_CUTOFF_V ({Settings.DISCHARGE_CUTOFF_V}) < "
                             f"BAT_VOLTAGE_MIN ({Settings.BAT_VOLTAGE_MIN}) -- "
                             "verify battery chemistry limits"))
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
    ref      = f"config/devices.py -> SMU_ASSIGNMENTS[{name!r}] ({resource} / {model})"

    from hardware.smu import SMU
    smu = SMU(cfg)
    try:
        smu.connect()
        identity = smu.identify()
        return _ok("Hardware Discovery", f"SMU/PSU: {name}", ref,
                   f"Communication established. Identified: {identity}")
    except Exception as e:
        desc = getattr(e, "description", str(e))
        return _fail("Hardware Discovery", f"SMU/PSU: {name}", ref,
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
    ref      = f"config/devices.py -> DMM_CONFIGS[{name!r}] ({resource} / {model})"

    from hardware.dmm import DMM
    dmm = DMM(cfg)
    try:
        dmm.connect()
        identity = dmm.identify()
        return _ok("Hardware Discovery", f"DMM: {name}", ref,
                   f"Communication established. Identified: {identity}")
    except Exception as e:
        desc = getattr(e, "description", str(e))
        return _fail("Hardware Discovery", f"DMM: {name}", ref,
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
    ref      = f"config/devices.py -> DAQ_CONFIGS[{name!r}] ({resource} / {model})"

    from hardware.daq import DAQ
    daq = DAQ(cfg)
    try:
        daq.connect()
        identity = daq.identify()
        return _ok("Hardware Discovery", f"DAQ: {name}", ref,
                   f"Communication established. Identified: {identity}  "
                   f"(self-test passed, no channel read performed)")
    except Exception as e:
        return _fail("Hardware Discovery", f"DAQ: {name}", ref,
                     f"[ERROR] DAQ not detected or self-test failed\nReason: {e}")
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
    host   = cfg.get("ip", "")
    port   = cfg.get("port", 23)
    driver = cfg.get("driver", "RELAY32ETHRL00")
    ref    = f"config/devices.py -> NUMATO_RELAY_MATRIX_CONFIGS[{name!r}] ({driver} / {host}:{port})"

    try:
        from hardware.relay_factory import RelayFactory
        relay = RelayFactory.create(cfg)
    except Exception as e:
        return _fail("Hardware Discovery", f"Numato Relay Matrix: {name}", ref,
                     f"Import / factory error: {e}")

    try:
        relay.connect()
    except Exception as e:
        return _fail("Hardware Discovery", f"Numato Relay Matrix: {name}", ref,
                     f"[ERROR] Relay not detected\nReason: {_classify_relay_error(e)}")

    try:
        relay.disconnect()
    except Exception:
        pass

    return _ok("Hardware Discovery", f"Numato Relay Matrix: {name}", ref,
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


# name -> (config dict, identify function). Config-driven: adding/removing a
# device dict entry here changes exactly what gets covered -- no other code
# needs to change, and no instrument/address/port is hardcoded.
_DISCOVERY_TARGETS = [
    ("SMU / PSU",        dev_cfg.SMU_ASSIGNMENTS,      _identify_smu),
    ("DMM",              dev_cfg.DMM_CONFIGS,          _identify_dmm),
    ("DAQ",              dev_cfg.DAQ_CONFIGS,          _identify_daq),
    ("Numato Relay Matrix (Ethernet)", dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, _identify_relay_eth),
    ("Relay (Serial)",   dev_cfg.RELAY_SERIAL_CONFIGS, _identify_relay_serial),
]


def test_hardware_discovery():
    """
    Generic device discovery + connectivity test, driven entirely by
    config/devices.py. For every device of every configured type: create
    the driver, connect, identify, report PASS/FAIL with full reason.

    This is NOT a measurement test, NOT a battery workflow test, and NOT an
    instrument-accuracy test -- see the module comment above this section.

    Runs entirely inside _numato_relay_debug_logging() so that any Numato
    Relay Matrix device's full Telnet conversation (RX/TX, prompt detection,
    IAC negotiation) is visible if its connect() fails -- see
    hardware/relay_eth.py's "Authentication debugging" module docstring
    section. Harmless no-op verbosity for the non-relay device types.
    """
    results = []
    with _numato_relay_debug_logging():
        for label, devices, identify_fn in _DISCOVERY_TARGETS:
            if not devices:
                results.append(_warn("Hardware Discovery", label, "config/devices.py",
                                     "No devices configured for this type -- skipped"))
                continue
            for name, cfg in devices.items():
                results.append(identify_fn(name, cfg))
    return results


# =============================================================================
# 2. SMU / PSU
# =============================================================================

def test_smu():
    """
    Step 1: Import hardware.smu.SMU and verify interface.
    Step 2: connect() + identify() via the SAME production SMU driver class
    used by HardwareManager and Hardware Discovery -- no direct nidcpower
    calls here, so this test can never drift from what production actually
    does. identify() itself is COMMAND (run the instrument's built-in
    self-test) -> READBACK (result code/message) -> VERIFY (code == 0) --
    never a bare "the API call didn't throw" (see hardware/smu.py's module
    docstring, "Verification philosophy"). Real sourcing verification
    (source a current/voltage and measure it back) is intentionally NOT
    done here -- SMU.set_charge_mode()/output_enable()/measure() are still
    placeholders (see docs/TODO.md); testing around a stub would be a fake
    PASS, exactly what this philosophy exists to avoid.
    """
    name, cfg = _select_device(dev_cfg.SMU_ASSIGNMENTS, "SMUs")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-SMU")
    config_ref = f"{resource} / {model}"
    results    = []

    # Step 1: hardware.smu module import + interface ---------------------------
    ref_mod = "hardware/smu.py"
    try:
        from hardware.smu import SMU
        smu = SMU(cfg)
        required_methods = ["connect", "disconnect", "identify", "set_charge_mode",
                            "set_discharge_mode", "output_enable", "output_disable",
                            "measure"]
        missing = [m for m in required_methods if not callable(getattr(smu, m, None))]
        if missing:
            results.append(_fail("SMU", "SMU module", ref_mod,
                                 f"Missing methods: {missing}"))
        else:
            results.append(_ok("SMU", "SMU module", ref_mod,
                               "hardware.smu.SMU interface OK (connect/identify implemented; "
                               "charge/discharge/measure still placeholders)"))
    except Exception as e:
        results.append(_fail("SMU", "SMU module", ref_mod, f"Import error: {e}"))
        return results

    # Step 2: connect() + identify() -- identify() runs and verifies a real
    # instrument self-test internally (command -> readback -> verify), it
    # is not a bare identity string query. See hardware/smu.py.
    try:
        smu.connect()
        model_id = smu.identify()
        results.append(_ok("SMU", name, config_ref,
                           f"Self-test PASSED. Detected: {model_id}"))
    except Exception as e:
        desc = getattr(e, "description", str(e))
        results.append(_fail("SMU", name, config_ref,
                             f"[ERROR] SMU not detected or self-test failed\n"
                             f"Configuration : {name}\n"
                             f"Interface     : VISA / NI-DCPower\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {desc}"))
    finally:
        try:
            smu.disconnect()
        except Exception:
            pass

    return results


# =============================================================================
# 3. DMM
# =============================================================================

def test_dmm():
    """
    Step 1: Import hardware.dmm.DMM and verify interface.
    Step 2: connect() + identify() via the SAME production DMM driver class
    used by Hardware Discovery -- identify() runs and verifies a real
    instrument self-test internally (command -> readback -> verify), not a
    bare identity string query. See hardware/dmm.py's module docstring.
    Step 3: a REAL DC voltage measurement (DMM.measure_dc_voltage()) --
    command (configure + trigger) -> readback (measured value) -> verify
    (finite, within the configured range) -> PASS/FAIL. Unlike SMU sourcing,
    a DMM measurement is passive (it only observes), so this is safe to run
    unconditionally and is real verification, not a stub-backed fake PASS.
    """
    name, cfg = _select_device(dev_cfg.DMM_CONFIGS, "DMMs")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-4065")
    range_v    = cfg.get("range_v", 10.0)
    config_ref = f"{resource} / {model}"
    results    = []

    # Step 1: hardware.dmm module import + interface ---------------------------
    ref_mod = "hardware/dmm.py"
    try:
        from hardware.dmm import DMM
        dmm = DMM(cfg)
        required_methods = ["connect", "disconnect", "identify", "measure_dc_voltage"]
        missing = [m for m in required_methods if not callable(getattr(dmm, m, None))]
        if missing:
            results.append(_fail("DMM", "DMM module", ref_mod,
                                 f"Missing methods: {missing}"))
        else:
            results.append(_ok("DMM", "DMM module", ref_mod,
                               "hardware.dmm.DMM interface OK"))
    except Exception as e:
        results.append(_fail("DMM", "DMM module", ref_mod, f"Import error: {e}"))
        return results

    # Step 2: connect() + identify() (real self-test, see above) --------------
    try:
        dmm.connect()
        model_id = dmm.identify()
        results.append(_ok("DMM", name, config_ref,
                           f"Self-test PASSED. Detected: {model_id}"))
    except Exception as e:
        desc = getattr(e, "description", str(e))
        results.append(_fail("DMM", name, config_ref,
                             f"[ERROR] DMM not detected or self-test failed\n"
                             f"Configuration : {name}\n"
                             f"Interface     : VISA / NI-DMM\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {desc}"))
        try:
            dmm.disconnect()
        except Exception:
            pass
        return results

    # Step 3: real DC voltage measurement + range verification ----------------
    try:
        value = dmm.measure_dc_voltage()
        results.append(_ok("DMM", f"{name} DC volts measurement", config_ref,
                           f"Measured {value:.6f} V (within configured range +/-{range_v} V)"))
    except Exception as e:
        results.append(_fail("DMM", f"{name} DC volts measurement", config_ref,
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
    Step 1: Import hardware.daq.DAQ and verify interface.
    Step 2: connect() + identify() via the SAME production DAQ driver class
    used by HardwareManager and Hardware Discovery -- no direct nidaqmx
    calls for this part.
    Step 3: deep channel read (beyond connectivity/identification -- this
    is the one part of this test that goes past what hardware.daq.DAQ
    currently implements, since read_channel() is still a TODO placeholder;
    it uses nidaqmx directly until that method is implemented). COMMAND
    (configure + read the channel) -> READBACK (the value) -> VERIFY
    (finite, within the configured +/-voltage_range_v ADC range) -> PASS/
    FAIL -- a NaN, an out-of-range, or a stuck reading is a FAIL, not "the
    read call didn't throw."
    """
    name, cfg = _select_device(dev_cfg.DAQ_CONFIGS, "DAQs")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    resource   = cfg.get("resource", "")
    model      = cfg.get("model", "NI-6363")
    config_ref = f"{resource} / {model}"
    test_ch    = dev_cfg.BATTERY_CHANNELS[1]["daq_voltage_ch"]
    results    = []

    # Step 1: hardware.daq module import + interface ---------------------------
    ref_mod = "hardware/daq.py"
    try:
        from hardware.daq import DAQ
        daq = DAQ(cfg)
        required = ["connect", "disconnect", "identify", "read_channel",
                    "read_all_batteries", "verify_zero_current"]
        missing = [m for m in required if not callable(getattr(daq, m, None))]
        if missing:
            results.append(_fail("DAQ", "DAQ module", ref_mod,
                                 f"Missing methods: {missing}"))
        else:
            results.append(_ok("DAQ", "DAQ module", ref_mod,
                               "hardware.daq.DAQ interface OK (connect/identify implemented; "
                               "channel read still a placeholder)"))
    except Exception as e:
        results.append(_fail("DAQ", "DAQ module", ref_mod, f"Import error: {e}"))
        return results

    # Step 2: real connectivity + identification via the DAQ driver class -----
    try:
        daq.connect()
        product_type = daq.identify()
        results.append(_ok("DAQ", name, config_ref, f"Detected: {product_type}"))
    except Exception as e:
        results.append(_fail("DAQ", name, config_ref,
                             f"[ERROR] DAQ not detected\n"
                             f"Configuration : {name}\n"
                             f"Interface     : NI-DAQmx\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {e}"))
        return results

    # Step 3: deep channel read -- goes beyond DAQ.read_channel() (still a
    # TODO placeholder), so this uses nidaqmx directly for now. COMMAND
    # (configure + read the channel) -> READBACK (val) -> VERIFY (finite,
    # within the configured ADC range) -> PASS/FAIL -- a value is only
    # reported PASS once it has actually been checked, never on "the read
    # call didn't throw" alone (a NaN, an out-of-range, or a stuck/floating
    # reading would previously have still shown as PASS).
    try:
        import math
        import nidaqmx
        import nidaqmx.errors
        v_range = cfg.get("voltage_range_v", 5.0)
        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(test_ch,
                                                 min_val=-v_range, max_val=v_range)
            val = task.read()

        if not math.isfinite(val):
            results.append(_fail("DAQ", f"{name} channel read", config_ref,
                                 f"[ERROR] DAQ channel read FAILED verification\n"
                                 f"Configuration : {name}\n"
                                 f"Channel       : {test_ch}\n"
                                 f"Reason        : reading is not a finite number ({val!r})"))
        elif abs(val) > v_range * 1.05:   # allow the ADC's own overrange headroom
            results.append(_fail("DAQ", f"{name} channel read", config_ref,
                                 f"[ERROR] DAQ channel read FAILED verification\n"
                                 f"Configuration : {name}\n"
                                 f"Channel       : {test_ch}\n"
                                 f"Reason        : {val:.4f} V is outside the configured "
                                 f"+/-{v_range} V range"))
        else:
            results.append(_ok("DAQ", f"{name} channel read", config_ref,
                               f"Channel {test_ch} read: {val:.4f} V -- verified within "
                               f"configured +/-{v_range} V range"))
    except nidaqmx.errors.DaqError as e:
        results.append(_fail("DAQ", f"{name} channel read", config_ref,
                             f"[ERROR] DAQ channel read failed\n"
                             f"Configuration : {name}\n"
                             f"Channel       : {test_ch}\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {e}"))
    except Exception as e:
        results.append(_fail("DAQ", f"{name} channel read", config_ref, str(e)))
    finally:
        try:
            daq.disconnect()
        except Exception:
            pass

    return results


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
    """
    name, cfg = _select_device(dev_cfg.RELAY_SERIAL_CONFIGS, "Serial Relays")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    port       = cfg.get("port", Settings.RELAY_COM_PORT)
    baud       = cfg.get("baud_rate", Settings.RELAY_BAUD_RATE)
    config_ref = f"config/devices.py -> {name} ({port} / {baud} baud)"
    results    = []

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


def test_relay_numato_matrix():
    """
    Real commissioning test for the Numato Lab 32-Channel Ethernet Relay
    Module, i.e. the Numato Relay Matrix (hardware/relay_eth.py via
    RelayFactory). Applies to whichever device is selected from
    NUMATO_RELAY_MATRIX_CONFIGS -- nothing here is specific to one name.

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
    name, cfg = _select_device(dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, "Numato Relay Matrix devices")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    host       = cfg.get("ip", "")
    port       = cfg.get("port", 23)
    driver     = cfg.get("driver", "RELAY32ETHRL00")
    user       = cfg.get("username", cfg.get("user", ""))
    config_ref = f"config/devices.py -> {name} ({driver} / {host}:{port})"
    results    = []

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

def test_relay_matrix_scan():
    """
    Commissioning test: exercises every configured channel of the Numato
    Relay Matrix module -- ON, READ, OFF -- one connection for the whole scan.

    Before scanning, device availability is verified (ping + connect/auth) --
    the scan itself only starts once the device is confirmed reachable and
    authenticated. Runs inside _numato_relay_debug_logging() so the full
    Telnet conversation is visible if connect()/auth fails.

    Channel count comes from config/devices.py NUMATO_RELAY_MATRIX_CONFIG["channel_count"]
    (falls back to "num_channels" for compat -- never hardcoded). A failure on
    any single channel is recorded as FAIL and the scan continues to the
    remaining channels -- it never aborts early.
    """
    name, cfg = _select_device(dev_cfg.NUMATO_RELAY_MATRIX_CONFIGS, "Numato Relay Matrix devices")
    if cfg is None:
        return []
    _print_device_config(name, cfg)

    host         = cfg.get("ip", "")
    port         = cfg.get("port", 23)
    driver       = cfg.get("driver", "RELAY32ETHRL00")
    user         = cfg.get("username", cfg.get("user", ""))
    num_channels = cfg.get("channel_count", cfg.get("num_channels", 8))
    config_ref   = f"config/devices.py -> {name} ({driver} / {host}:{port}, {num_channels} ch)"

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
            return _run_relay_matrix_scan(cfg, host, port, driver, user, num_channels, config_ref, token)
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)


def _run_relay_matrix_scan(cfg, host, port, driver, user, num_channels, config_ref, token=None):
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
        for ch in range(1, num_channels + 1):
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

def test_relay_ethernet_test():
    """
    RelayEthernetTest: validates the native Numato command primitives
    directly -- write(relay_number, state), read_all(), write_all(),
    verify_all() -- using Numato's own 0-based relay numbering, independent
    of the higher-level 1-based open()/close() API that
    test_relay_safety_selftest() exercises.

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
                    relay.write_all(0)
                    relay.verify_all(0)

                    relay.write(relay_index, True)
                    relay.verify_all(1 << relay_index)

                    relay.write_all(0)
                    relay.verify_all(0)

                    results.append(_ok(
                        "RelayEthernetTest", f"Relay index {relay_index}", config_ref,
                        "write_all(OFF) -> verify -> write(ON) -> verify -> "
                        "write_all(OFF) -> verify  PASS"
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

def test_relay_safety_selftest():
    """
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
# 6. Sensors (NTC temperature)
# =============================================================================

def test_sensors():
    """
    Exercises hardware.temperature module logic without hardware.
    Tests: 25 degC reference point, out-of-range guard, monotonicity.
    Also tests TemperatureSensor class interface.
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

    return results


# =============================================================================
# 7. Safety Monitor  (real logic, no hardware required)
# =============================================================================

def test_safety_monitor():
    """
    Exercise test_control/safety_monitor.SafetyMonitor logic.
    Tests: overvoltage, undervoltage, overcurrent, overtemperature, relay switch guard.
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
    """Placeholder. Extend when electronic load hardware is added."""
    return [_warn("Electronic Load", "ELOAD_01",
                  "Not yet configured",
                  "No electronic load in config/devices.py -- stub only")]


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

def run_main_test():
    """
    Real commissioning run: loads config/settings.py + config/devices.py,
    builds HardwareManager, and executes the configured test sequence via
    TestExecutor / ResultManager (same path as main.py).
    """
    print("RUNNING MAIN TEST")

    import signal
    from test_control.hardware_manager import HardwareManager
    from test_control.test_executor import TestExecutor
    from test_control.result_manager import ResultManager
    from utils.cancellation import CancellationToken
    from utils.errors import HardwareInitError
    from utils.stop_reason import StopReason

    # Production relay is the Numato Ethernet module -- RELAY_CONFIG (serial)
    # is kept only for diagnostics via menu options 5/6/7.
    smu_name, smu_cfg   = next(iter(dev_cfg.SMU_ASSIGNMENTS.items()))
    dmm_name, dmm_cfg   = next(iter(dev_cfg.DMM_CONFIGS.items()))
    daq_name, daq_cfg   = next(iter(dev_cfg.DAQ_CONFIGS.items()))
    relay_cfg           = dev_cfg.NUMATO_RELAY_MATRIX_CONFIG
    relay_name          = relay_cfg.get("name", "RELAY")

    print("\nSelected Hardware\n")
    print(f"SMU:\n  {smu_name}\n  {smu_cfg.get('resource', '')}\n")
    print(f"DMM:\n  {dmm_name}\n  {dmm_cfg.get('resource', '')}\n")
    print(f"Relay:\n  {relay_name}\n  {relay_cfg.get('ip', '')}\n")
    print(f"DAQ:\n  {daq_name}\n  {daq_cfg.get('resource', '')}\n")

    hw = HardwareManager(Settings, relay_cfg=relay_cfg)

    try:
        hw.connect_all()
    except HardwareInitError as e:
        print(f"[FAIL] Hardware initialization failed: {e}")
        return

    # Safe Cancellation (see docs/architecture.md "Safe Cancellation
    # Architecture"): identical pattern to main.py -- Ctrl+C requests a
    # cooperative, checkpoint-based cancellation instead of raising
    # KeyboardInterrupt while this handler is installed. Restored to
    # Python's default before returning either way.
    token = CancellationToken(owner="test.py:run_main_test")
    previous_sigint_handler = signal.signal(
        signal.SIGINT, lambda signum, frame: token.request_cancel("Ctrl+C")
    )
    print("\nPress Ctrl+C to cancel safely.\n")

    try:
        try:
            result_mgr = ResultManager(settings=Settings)
            executor   = TestExecutor(hw=hw, storage=result_mgr.storage, settings=Settings)

            with result_mgr:
                result = executor.run(token=token)

            result_mgr.generate_report(result.run_id)
            print(result.summary())
            if result.stop_reason == StopReason.CANCELLED:
                print("Test cancelled by operator -- hardware is in a verified safe state.")

        except KeyboardInterrupt:
            # Defensive fallback only -- should not normally fire while the
            # SIGINT handler above is installed.
            print("\nTest interrupted by user (Ctrl+C).")

        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    finally:
        # Always attempted (Python's finally always runs, including on
        # KeyboardInterrupt/any exception above) -- see docs/architecture.md
        # "Emergency Shutdown Strategy". Wrapped defensively so a shutdown
        # failure is reported clearly instead of silently lost.
        try:
            hw.disconnect_all()
        except Exception as shutdown_err:
            print(f"[CRITICAL] Hardware shutdown failed: {shutdown_err}")
            print("           Hardware may still be energized -- "
                  "physically disconnect power if this cannot be resolved immediately.")


# =============================================================================
# Menu
# =============================================================================

MENU = [
    ("Run Main Test",                 run_main_test),
    ("Startup Device Validation (config/devices.py -- no hardware I/O)", test_device_validation),
    ("Hardware Discovery (connectivity + identification, config-driven)", test_hardware_discovery),
    ("Test SMU (PSU)",                test_smu),
    ("Test DMM",                      test_dmm),
    ("Test DAQ",                      test_daq),
    ("Test Relay -- Serial",          test_relay_serial),
    ("Test Relay -- Numato Relay Matrix (Ethernet)", test_relay_numato_matrix),
    ("Test Relay -- Ethernet Matrix Scan", test_relay_matrix_scan),
    ("Test Relay -- RelayEthernetTest (native primitives, stop on first failure)", test_relay_ethernet_test),
    ("Test Relay -- Safety Self-Test (1-32, stop on first failure)", test_relay_safety_selftest),
    ("Test Electronic Load",          test_electronic_load),
    ("Test Sensors (NTC)",            test_sensors),
    ("Test Safety Monitor",           test_safety_monitor),
    ("Test Configuration",            test_configuration),
    ("Test SQLite (foundation)",      test_sqlite),
    ("Test Database Layer",           test_database),
    ("Test MiniSQL (hooks)",          test_minisql),
    ("Run All Tests",                 None),
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

def main():
    print()
    print("=" * 60)
    print(f"  {Settings.PROJECT_NAME}  v{Settings.VERSION}")
    print("  Test Framework")
    print("=" * 60)

    print("\n[Pre-flight: Configuration Validation]")
    config_results, config_ok = preflight_check()

    if not config_ok:
        print("\n  Configuration has FAIL errors.")
        print("  Fix config/ files before running hardware tests.")
        sys.exit(1)

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
        return

    label, fn = MENU[idx]
    if fn is run_main_test:
        print(f"\n{'-' * 60}")
        print(f"  {label}")
        print(f"{'-' * 60}")
        fn()
    elif fn is None:
        all_results = list(config_results)
        for lbl, f in MENU[1:-1]:
            all_results.extend(run_section(lbl, f))
        print_summary(all_results)
    else:
        results = run_section(label, fn)
        print_summary(results)


if __name__ == "__main__":
    main()
