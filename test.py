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

    # -- PXI resource strings -------------------------------------------------
    for name, value in [("PXI_RESOURCE_DAQ",  Settings.PXI_RESOURCE_DAQ),
                        ("PXI_RESOURCE_DMM",  Settings.PXI_RESOURCE_DMM),
                        ("PXI_RESOURCE_SMU1", Settings.PXI_RESOURCE_SMU1)]:
        ref = f"config/settings.py -> {name}"
        if not value:
            results.append(_fail("Configuration", name, ref, f"{name} is empty"))
        elif not value.startswith("PXI"):
            results.append(_warn("Configuration", name, ref,
                                 f"'{value}' does not look like a PXI resource string"))
        else:
            results.append(_ok("Configuration", name, ref, value))

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

    # -- SMU / DAQ / DMM configs ----------------------------------------------
    for name, cfg in [("SMU_ASSIGNMENTS", dev_cfg.SMU_ASSIGNMENTS.get("SMU1", {})),
                      ("DAQ_CONFIG",      dev_cfg.DAQ_CONFIG),
                      ("DMM_CONFIG",      dev_cfg.DMM_CONFIG)]:
        ref = f"config/devices.py -> {name}"
        res = cfg.get("resource")
        if not res:
            results.append(_fail("Configuration", name, ref, "Missing 'resource' key"))
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
# 2. SMU / PSU
# =============================================================================

def test_smu():
    """
    Step 1: Import hardware.smu.SMU and verify interface.
    Step 2: Connect to real SMU via nidcpower (if library present).
    """
    cfg        = dev_cfg.SMU_ASSIGNMENTS.get("SMU1", {})
    resource   = cfg.get("resource", Settings.PXI_RESOURCE_SMU1)
    model      = cfg.get("model", "NI-SMU")
    config_ref = f"{resource} / {model}"
    results    = []

    # Step 1: hardware.smu module import + interface ---------------------------
    ref_mod = "hardware/smu.py"
    try:
        from hardware.smu import SMU
        smu = SMU(resource)
        required_methods = ["connect", "disconnect", "set_charge_mode",
                            "set_discharge_mode", "output_enable", "output_disable",
                            "measure"]
        missing = [m for m in required_methods if not callable(getattr(smu, m, None))]
        if missing:
            results.append(_fail("SMU", "SMU module", ref_mod,
                                 f"Missing methods: {missing}"))
        else:
            results.append(_ok("SMU", "SMU module", ref_mod,
                               f"hardware.smu.SMU interface OK (placeholder - nidcpower not wired in yet)"))
    except Exception as e:
        results.append(_fail("SMU", "SMU module", ref_mod, f"Import error: {e}"))

    # Step 2: hardware library connection -------------------------------------
    try:
        import nidcpower
    except ImportError:
        results.append(_fail("SMU", "SMU1", config_ref,
                             "[ERROR] SMU not detected\n"
                             f"Configuration : SMU1\n"
                             f"Interface     : VISA / NI-DCPower\n"
                             f"Expected      : {resource} ({model})\n"
                             "Reason        : Library 'nidcpower' not installed\n"
                             "Fix           : pip install nidcpower"))
        return results

    try:
        session  = nidcpower.Session(resource_name=resource,
                                     simulate=Settings.PXI_SIMULATE)
        model_id = session.instrument_model
        session.close()
        results.append(_ok("SMU", "SMU1", config_ref, f"Detected: {model_id}"))
    except Exception as e:
        desc = getattr(e, "description", str(e))
        results.append(_fail("SMU", "SMU1", config_ref,
                             f"[ERROR] SMU not detected\n"
                             f"Configuration : SMU1\n"
                             f"Interface     : VISA / NI-DCPower\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {desc}"))

    return results


# =============================================================================
# 3. DMM
# =============================================================================

def test_dmm():
    """
    No hardware/dmm.py module yet -- tests nidmm library connection directly.
    Reports missing module as a WARNING (not FAIL) since DMM driver is not yet written.
    """
    resource   = Settings.PXI_RESOURCE_DMM
    model      = dev_cfg.DMM_CONFIG.get("model", "NI-4065")
    config_ref = f"{resource} / {model}"
    results    = []

    # Note: hardware/dmm.py does not exist yet
    results.append(_warn("DMM", "DMM module",
                         "hardware/dmm.py",
                         "No hardware/dmm.py driver exists yet -- "
                         "testing nidmm library directly"))

    try:
        import nidmm
    except ImportError:
        results.append(_fail("DMM", "DMM_01", config_ref,
                             "[ERROR] DMM not detected\n"
                             f"Configuration : DMM_01\n"
                             f"Interface     : VISA / NI-DMM\n"
                             f"Expected      : {resource} ({model})\n"
                             "Reason        : Library 'nidmm' not installed\n"
                             "Fix           : pip install nidmm"))
        return results

    try:
        session  = nidmm.Session(resource_name=resource,
                                 simulate=Settings.PXI_SIMULATE)
        model_id = session.instrument_model
        session.close()
        results.append(_ok("DMM", "DMM_01", config_ref, f"Detected: {model_id}"))
    except Exception as e:
        desc = getattr(e, "description", str(e))
        results.append(_fail("DMM", "DMM_01", config_ref,
                             f"[ERROR] DMM not detected\n"
                             f"Configuration : DMM_01\n"
                             f"Interface     : VISA / NI-DMM\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {desc}"))

    return results


# =============================================================================
# 4. DAQ
# =============================================================================

def test_daq():
    """
    Step 1: Import hardware.daq.DAQ and verify interface.
    Step 2: Connect to real DAQ via nidaqmx (if library present).
    """
    resource   = Settings.PXI_RESOURCE_DAQ
    model      = dev_cfg.DAQ_CONFIG.get("model", "NI-6363")
    config_ref = f"{resource} / {model}"
    test_ch    = dev_cfg.BATTERY_CHANNELS[1]["daq_voltage_ch"]
    results    = []

    # Step 1: hardware.daq module import + interface ---------------------------
    ref_mod = "hardware/daq.py"
    try:
        from hardware.daq import DAQ
        daq = DAQ(resource)
        required = ["connect", "disconnect", "read_channel",
                    "read_all_batteries", "verify_zero_current"]
        missing = [m for m in required if not callable(getattr(daq, m, None))]
        if missing:
            results.append(_fail("DAQ", "DAQ module", ref_mod,
                                 f"Missing methods: {missing}"))
        else:
            results.append(_ok("DAQ", "DAQ module", ref_mod,
                               "hardware.daq.DAQ interface OK (placeholder - nidaqmx not wired in yet)"))
    except Exception as e:
        results.append(_fail("DAQ", "DAQ module", ref_mod, f"Import error: {e}"))

    # Step 2: hardware library connection -------------------------------------
    try:
        import nidaqmx
        import nidaqmx.system
        import nidaqmx.errors
    except ImportError:
        results.append(_fail("DAQ", "DAQ_01", config_ref,
                             "[ERROR] DAQ not detected\n"
                             f"Configuration : DAQ_01\n"
                             f"Interface     : NI-DAQmx\n"
                             f"Expected      : {resource} ({model})\n"
                             "Reason        : Library 'nidaqmx' not installed\n"
                             "Fix           : pip install nidaqmx"))
        return results

    try:
        system      = nidaqmx.system.System.local()
        dev_names   = [d.name for d in system.devices]
        if not dev_names:
            results.append(_fail("DAQ", "DAQ_01", config_ref,
                                 f"[ERROR] DAQ not detected\n"
                                 f"Configuration : DAQ_01\n"
                                 f"Interface     : NI-DAQmx\n"
                                 f"Expected      : {resource} ({model})\n"
                                 "Reason        : No NI-DAQmx devices found on this system"))
            return results
    except Exception as e:
        results.append(_fail("DAQ", "DAQ_01", config_ref,
                             f"NI-DAQmx system query failed: {e}"))
        return results

    try:
        with nidaqmx.Task() as task:
            v_range = dev_cfg.DAQ_CONFIG.get("voltage_range_v", 5.0)
            task.ai_channels.add_ai_voltage_chan(test_ch,
                                                 min_val=-v_range, max_val=v_range)
            val = task.read()
        results.append(_ok("DAQ", "DAQ_01", config_ref,
                           f"Channel {test_ch} read: {val:.4f} V  "
                           f"(devices: {dev_names})"))
    except nidaqmx.errors.DaqError as e:
        results.append(_fail("DAQ", "DAQ_01", config_ref,
                             f"[ERROR] DAQ channel read failed\n"
                             f"Configuration : DAQ_01\n"
                             f"Channel       : {test_ch}\n"
                             f"Expected      : {resource} ({model})\n"
                             f"Reason        : {e}"))
    except Exception as e:
        results.append(_fail("DAQ", "DAQ_01", config_ref, str(e)))

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
    cfg        = dev_cfg.RELAY_CONFIG
    port       = cfg.get("port", Settings.RELAY_COM_PORT)
    baud       = cfg.get("baud_rate", Settings.RELAY_BAUD_RATE)
    config_ref = f"config/devices.py -> RELAY_CONFIG ({port} / {baud} baud)"
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
                                         "SerialRelay interface OK, but command protocol "
                                         "is still placeholder -- fill in RELAY_CONFIG "
                                         "command_open/close/query from your controller datasheet"))
    except Exception as e:
        results.append(_fail("Relay Serial", "Factory", config_ref,
                             f"Import / factory error: {e}"))
        return results

    # Step 2 + 3: pyserial + port open ----------------------------------------
    try:
        import serial
        import serial.tools.list_ports
    except ImportError:
        results.append(_fail("Relay Serial", "RELAY_SERIAL_01", config_ref,
                             "[ERROR] Relay not detected\n"
                             f"Port   : {port}\n"
                             "Reason : Library 'pyserial' not installed\n"
                             "Fix    : pip install pyserial"))
        return results

    available = [p.device for p in serial.tools.list_ports.comports()]
    if port not in available:
        results.append(_fail("Relay Serial", "RELAY_SERIAL_01", config_ref,
                             f"[ERROR] Relay not detected\n"
                             f"Port           : {port}\n"
                             f"Available ports: {available if available else 'none'}\n"
                             f"Reason         : {port} not present on this system"))
        return results

    try:
        with serial.Serial(port, baud,
                           timeout=cfg.get("timeout", Settings.RELAY_TIMEOUT_S)) as _:
            results.append(_ok("Relay Serial", "RELAY_SERIAL_01", config_ref,
                               f"Port {port} opened at {baud} baud -- hardware present"))
    except serial.SerialException as e:
        results.append(_fail("Relay Serial", "RELAY_SERIAL_01", config_ref,
                             f"[ERROR] Could not open {port}: {e}"))
    except Exception as e:
        results.append(_fail("Relay Serial", "RELAY_SERIAL_01", config_ref, str(e)))

    return results


# =============================================================================
# 5b. Relay -- Ethernet (Numato RELAY32ETHRL00)
# =============================================================================

def test_relay_eth():
    """
    Tests the Ethernet relay driver (hardware/relay_eth.py via RelayFactory).

    Step 1: Verify factory + module interface (RelayBase subclass, all methods present).
    Step 2: Attempt TCP connection + Telnet login to the configured relay IP.
    Step 3: If connected, open ch1, close ch1, query ch1, then disconnect.

    Returns PASS if all steps succeed.
    Returns FAIL if the host is unreachable, login fails, or a command errors.
    """
    cfg        = dev_cfg.RELAY_ETH_CONFIG
    host       = cfg.get("ip", "")
    port       = cfg.get("port", 23)
    driver     = cfg.get("driver", "RELAY32ETHRL00")
    name       = cfg.get("name", "ETH_RELAY")
    config_ref = f"config/devices.py -> RELAY_ETH_CONFIG ({driver} / {host}:{port})"
    results    = []

    # Step 1: factory + interface check  -- offline, no hardware ---------------
    try:
        from hardware.relay_factory import RelayFactory
        from hardware.relay import RelayBase
        relay = RelayFactory.create(cfg)
        if not isinstance(relay, RelayBase):
            results.append(_fail("Relay Ethernet", "Factory", config_ref,
                                 "RelayFactory did not return a RelayBase instance"))
            return results
        required = ["connect", "disconnect", "open", "close", "open_all",
                    "close_all", "query"]
        missing = [m for m in required if not callable(getattr(relay, m, None))]
        if missing:
            results.append(_fail("Relay Ethernet", "Interface", config_ref,
                                 f"Missing methods: {missing}"))
            return results
        results.append(_ok("Relay Ethernet", "Driver interface", config_ref,
                           f"RelayFactory -> EthernetRelay OK  ({driver} / {name})"))
    except Exception as e:
        results.append(_fail("Relay Ethernet", "Factory", config_ref,
                             f"Import / factory error: {e}"))
        return results

    # Step 2: connection -------------------------------------------------------
    try:
        relay.connect()
    except Exception as e:
        # Relay unreachable -- format matches the standardized error block
        first_line = str(e).splitlines()[0] if str(e) else "Unknown error"
        results.append(_fail("Relay Ethernet", "RELAY_ETH_01", config_ref,
                             f"[ERROR] Relay controller not reachable\n"
                             f"Driver : {driver}\n"
                             f"Host   : {host}:{port}\n"
                             f"Reason : {first_line}"))
        return results

    results.append(_ok("Relay Ethernet", "Connection", config_ref,
                       f"Connected to {driver} at {host}:{port}"))

    # Step 3: functional relay test  -- open, close, query ch1 ----------------
    test_ch = 1
    try:
        relay.open(test_ch)
        results.append(_ok("Relay Ethernet", f"open(ch{test_ch})", config_ref,
                           f"open({test_ch}) sent OK"))

        relay.close(test_ch)
        results.append(_ok("Relay Ethernet", f"close(ch{test_ch})", config_ref,
                           f"close({test_ch}) sent OK"))

        state = relay.query(test_ch)
        state_str = "closed (energized)" if state else "open (de-energized)"
        results.append(_ok("Relay Ethernet", f"query(ch{test_ch})", config_ref,
                           f"query({test_ch}) -> {state_str}"))

        relay.open(test_ch)   # leave in safe state
        results.append(_ok("Relay Ethernet", "Safe state", config_ref,
                           f"ch{test_ch} returned to open state after test"))
    except Exception as e:
        results.append(_fail("Relay Ethernet", f"Command ch{test_ch}", config_ref,
                             f"Relay command failed: {e}"))
    finally:
        relay.disconnect()

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

def preflight_check():
    results  = test_configuration()
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
# Menu
# =============================================================================

MENU = [
    ("Test SMU (PSU)",                test_smu),
    ("Test DMM",                      test_dmm),
    ("Test DAQ",                      test_daq),
    ("Test Relay -- Serial",          test_relay_serial),
    ("Test Relay -- Ethernet",        test_relay_eth),
    ("Test Electronic Load",          test_electronic_load),
    ("Test Sensors (NTC)",            test_sensors),
    ("Test Safety Monitor",           test_safety_monitor),
    ("Test Configuration",            test_configuration),
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
    if fn is None:
        all_results = list(config_results)
        for lbl, f in MENU[:-1]:
            all_results.extend(run_section(lbl, f))
        print_summary(all_results)
    else:
        results = run_section(label, fn)
        print_summary(results)


if __name__ == "__main__":
    main()
