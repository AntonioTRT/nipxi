# NIPXI — Battery Test System

Automated lithium-ion battery charge/discharge testing using a National Instruments PXI rack, the BLOSS Hub PCB, and a relay matrix (serial or Ethernet). Logs all data to SQLite and CSV for downstream analysis in the BLOAST ML pipeline.

This sub-repository is self-contained and independent from the main BLOAST project.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Supported Hardware](#4-supported-hardware)
5. [Prerequisites](#5-prerequisites)
6. [Quick Start](#6-quick-start)
7. [Configuration Reference](#7-configuration-reference)
8. [Testing Framework](#8-testing-framework)
9. [Relay Architecture](#9-relay-architecture)
10. [Database Layer](#10-database-layer)
11. [Safety System](#11-safety-system)
12. [Error Handling](#12-error-handling)
13. [Usage Examples](#13-usage-examples)
14. [Development Workflow](#14-development-workflow)
15. [MiniSQL Integration Path](#15-minisql-integration-path)
16. [Troubleshooting](#16-troubleshooting)
17. [Hardware Abstraction Architecture & Device Onboarding](#17-hardware-abstraction-architecture--device-onboarding)

---

## 1. Project Overview

NIPXI automates capacity and calendar-aging tests on up to 8 Li-ion battery channels simultaneously.

**What it does:**

- Charges each battery to a target SOC using CC-CV (Source Measure Unit)
- Discharges each battery at a constant current (SMU acting as a current sink)
- Measures voltage, current, and temperature per channel at every sample (DAQ + NTC thermistor)
- Enforces real-time safety limits — emergency stop on overvoltage, undervoltage, overcurrent, overtemperature
- Logs all measurements to SQLite and per-channel CSV files
- Generates reports for the BLOAST ML pipeline

**Current status:**  
Configuration, data layer, safety monitor, relay drivers, and test framework are implemented.  
Hardware driver methods (SMU nidcpower calls, DAQ nidaqmx calls) are stubs pending lab commissioning.  
See [docs/TODO.md](docs/TODO.md) for the complete checklist.

---

## 2. Architecture Overview

```
                +--------------------------------------------------+
                |              Host PC / Control System            |
                |                                                  |
                |  main.py  (thin orchestration)                   |
                |    +-- validate_settings()                       |
                |    +-- validate_devices_or_raise()  <- startup   |
                |    |                                   device gate|
                |    +-- HardwareManager  (device lifecycle)       |
                |    +-- TestExecutor     (runs the test sequence) |
                |    +-- ResultManager    (storage + reports)      |
                +-------------------+------------------------------+
                                    |
                     NI-VISA / nidaqmx / nidcpower / nidmm
                                    |
                +-------------------v------------------------------+
                |                PXI Chassis                       |
                |   Slot 2: NI 6363 DAQ  (voltage / current / NTC)|
                |   Slot 3: NI 4065 DMM  (precision verification) |
                |   Slot 4: NI 4140 SMU  (charge / discharge)     |
                |   Slot 5: NI 4130 SMU  (optional second unit)   |
                +---+----------------+-----------+----------------+
                    |                            |
              pyserial / TCP               NI-VISA
                    |
       +------------v-------------+
       |  Relay Matrix            |
       |  Ethernet: Numato 32-ch  |  <- PRODUCTION
       |  Serial:   COM13         |  <- diagnostic only
       |  32 channels, interlocked|
       +------------+-------------+
                    |
       +------------v-----------------------------------------+
       |                  BLOSS Hub PCB (Rev A)               |
       |  8x Li-ion battery connectors (JST / BM8 series)    |
       |  8x 2 A polyfuses                                    |
       |  8x 10k NTC thermistors (voltage divider, 3.3 V)    |
       |  8x Kelvin sense outputs                             |
       +------------------------------------------------------+
```

**config/devices.py is the single source of truth for every device's resource string / address** -- SMU slot, DAQ slot, DMM slot, relay IP, relay COM port. Nothing else duplicates it (see Section 17 for the full pipeline and how HardwareManager/Hardware Discovery both read from it).

**Control flow (per test run):**

```
main.py:
  validate_settings(Settings)          -- fail-fast on bad config values
  validate_devices_or_raise(dev_cfg)   -- fail-fast on bad device config (Section 17)
  HardwareManager(...).connect_all()   -- construct + connect every device
For each active channel:
  1. Read current -- must be < 0.01 A before switching relay
  2. Close relay channel N   (connects battery to SMU)
  3. Charge cycle (CC-CV):
       SMU set_charge_mode -> output_enable -> sample loop:
         DAQ.read_all_batteries() -> safety.check() -> storage.record()
       End when V >= 4.2 V and I <= 0.05 A  (or timeout 2 h)
  4. Disable SMU, wait, verify I ~ 0
  5. Discharge cycle (CC):
       SMU set_discharge_mode -> output_enable -> sample loop:
         DAQ.read_all_batteries() -> safety.check() -> storage.record()
       End when V <= 3.0 V  (or timeout 2 h)
  6. Disable SMU, open relay N
Save final data, generate report
```

---

## 3. Directory Structure

```
nipxi/
|-- main.py                     Entry point. Parses args, validates config, starts test.
|-- test.py                     Interactive test framework (12 test sections).
|-- requirements.txt
|-- .gitignore
|
|-- config/
|   |-- settings.py             Tunable parameters (voltages, currents, timeouts, RELAY_COUNT).
|   |                           Does NOT hold device resource strings/addresses -- see devices.py.
|   +-- devices.py              SINGLE SOURCE OF TRUTH for every device: PXI slots, relay IP/
|                                COM port, channel mapping. See Section 17.
|
|-- hardware/                   One class per physical device. All inherit HardwareBase.
|   |-- base.py                 Abstract base: connect(), disconnect(), context manager.
|   |-- relay.py                RelayBase abstract class (open/close/query interface).
|   |-- relay_serial.py         Serial relay driver (COM13) -- diagnostic only, NOT production.
|   |-- relay_eth.py            PRODUCTION relay driver: Numato 32-ch Ethernet relay via TCP.
|   |                           Enforces mandatory all-off->verify->activate->verify sequence.
|   |-- relay_factory.py        Factory: RelayFactory.create(cfg) -> RelayBase (serial/ethernet).
|   |-- relay_matrix.py         Dead/legacy code -- not imported or wired into RelayFactory.
|   |-- smu.py                  SMU (PSU) driver: real connect()/identify(); charge/discharge/
|   |                           measure still placeholders (see docs/TODO.md).
|   |-- daq.py                  DAQ driver: real connect()/identify() (device enumeration +
|   |                           self-test); channel read still a placeholder.
|   |-- dmm.py                  DMM driver: real connect()/identify().
|   |-- pxi_rack.py             PXI chassis enumeration stub.
|   +-- temperature.py          NTC thermistor voltage-to-Celsius conversion.
|
|-- test_control/               Test sequence logic, no hardware knowledge.
|   |-- hardware_manager.py     Device lifecycle (connect_all/disconnect_all/health_check).
|   |                           Constructs SMU/DAQ/DMM/Relay from config/devices.py.
|   |-- test_executor.py        Runs the test, returns TestRunResult.
|   |-- result_manager.py       Storage + reporting. MiniSQL integration point.
|   |-- battery_test.py         Per-channel charge+discharge sequence.
|   |-- charge_cycle.py         CC-CV charge logic for one channel.
|   |-- discharge_cycle.py      CC discharge logic for one channel.
|   |-- safety_monitor.py       Real-time limit checks + emergency stop.
|   +-- state_machine.py        Optional channel state tracker.
|
|-- data/                       Persistence layer.
|   |-- logger.py               Logging setup (file + console).
|   |-- storage.py              SQLite + CSV writer. StorageBackend ABC for MiniSQL swap.
|   +-- report.py               Report generation (TODO).
|
|-- utils/
|   |-- errors.py               Exception hierarchy (NIPXIError -> RelayError, etc.).
|   |-- validators.py           Settings-level validation (validate_settings()).
|   |-- device_validator.py     Device-level startup validation (validate_devices_or_raise()) --
|   |                           see Section 17. Never touches hardware, construction only.
|   |-- constants.py            Project-wide constants.
|   |-- helpers.py              Utility functions.
|   +-- ethernet_relay_python.py  Numato reference script (read-only, not imported).
|
+-- docs/
    |-- architecture.md         System design and control flow.
    |-- TODO.md                 Prioritized implementation checklist.
    +-- CONFIGURATION.md        Full configuration reference.
```

---

## 4. Supported Hardware

| Role | Model | Interface | Library | Driver |
|------|-------|-----------|---------|--------|
| PXI Chassis | NI PXI-1042 (or compatible) | NI-VISA | — | `hardware/pxi_rack.py` (stub) |
| DAQ | NI 6363 (Slot 2) | NI-DAQmx | `nidaqmx` | `hardware/daq.py::DAQ` (connect/identify real; channel read still TODO) |
| DMM | NI 4065 (Slot 3) | NI-DMM | `nidmm` | `hardware/dmm.py::DMM` (connect/identify real) |
| SMU / PSU (primary) | NI 4140 or 4139 (Slot 4) | NI-DCPower | `nidcpower` | `hardware/smu.py::SMU` (connect/identify real; charge/discharge/measure still TODO) |
| SMU (optional) | NI 4130 (Slot 5) | NI-DCPower | `nidcpower` | same `SMU` class, second `SMU_ASSIGNMENTS` entry |
| Relay (Ethernet) | **Numato Lab 32-Channel Ethernet Relay Module (RELAY32ETHRL00) — PRODUCTION** | TCP/Telnet | stdlib `socket` | `hardware/relay_eth.py::NumatoRelayMatrix` |
| Relay (serial, COM13) | Diagnostic only — NOT the production control path | pyserial | `pyserial` | `hardware/relay_serial.py::SerialRelay` |
| Battery Hub PCB | BLOSS Hub Rev A | — | — | — |

**Production relay hardware (validated):** Numato Lab 32 Channel Ethernet Relay Module, reachable over Ethernet/Telnet — ping, web interface, Telnet login, relay commands, and relay state readback have all been confirmed working. Validated settings:

| Setting | Value |
|---------|-------|
| IP address | `169.254.1.1` |
| Port | `23` |
| Username | `admin` |
| Password | `admin` |

Production architecture path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix -> Numato Relay`.

Serial COM13 (`hardware/relay_serial.py` / `RELAY_CONFIG`) exists only for bench diagnostics and is never selected by `main.py` or `test.py`'s "Run Main Test" — see [Section 9](#9-relay-architecture) and [docs/architecture.md](docs/architecture.md).

**BLOSS Hub PCB specs:**
- 8x Li-ion channels, JST BM8 connectors
- Voltage range: 3.5 V – 4.7 V per cell
- Max current: 1 A per channel (2 A polyfuse per channel)
- NTC thermistor: 10 kOhm at 25 degC, Beta = 3950 K, 3.3 V supply

---

## 5. Prerequisites

**Software:**

```bash
Python 3.10+
pip install nidcpower nidmm nidaqmx pyserial
```

**NI software (Windows only):**

- NI-DAQmx driver  
- NI-DMM driver  
- NI-DCPower driver  
- NI-VISA runtime  

Download from [ni.com/downloads](https://www.ni.com/en/support/downloads.html).

**requirements.txt (active dependencies):**

```
nidaqmx>=0.9.0    # NI 6363 DAQ
nidcpower>=0.9.0  # NI SMU 4140/4139/4130
nidmm>=0.9.0      # NI 4065 DMM
pyserial>=3.5     # serial relay (COM port)
```

Future dependencies (commented out in requirements.txt until the modules are implemented):

| Package | When needed |
|---------|-------------|
| `pyvisa` | `hardware/pxi_rack.py` implementation |
| `minisql` | `data/storage_minisql.py` implementation |
| `matplotlib` | `data/report.py` plotting |
| `pandas` | `data/report.py` table output |

---

## 6. Quick Start

```bash
# 1. Navigate to the nipxi directory
cd nipxi

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Edit configuration (mandatory before first run)
#    config/settings.py  -- voltages/currents/timeouts/RELAY_COUNT only
#    config/devices.py   -- PXI slots, relay IP/COM port, channel wiring
#                           (single source of truth for every device address)

# 5. Run the test framework to verify hardware
python test.py

# 6. Run the application (once hardware drivers are implemented)
python main.py
python main.py --channels 1 2 3   # test only channels 1, 2, 3
python main.py --dry-run           # no hardware, config validation only
```

**First-time checklist:**

- [ ] Set `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` resource strings in `config/devices.py` to match NI-MAX
- [ ] Set `NUMATO_RELAY_MATRIX_CONFIG["ip"]` in `config/devices.py` if using Ethernet relay
- [ ] Fill in `command_open/close/query` in `config/devices.py` RELAY_CONFIG for serial relay (diagnostic only)
- [ ] Confirm `BATTERY_CHANNELS` channel numbers match your physical wiring
- [ ] Run `python test.py` and choose "Test Configuration" (Settings) and "Startup Device Validation" (config/devices.py)
- [ ] Run `python test.py` and choose "Hardware Discovery" to confirm every configured device connects and identifies

---

## 7. Configuration Reference

All configuration is in two files. Edit these before running.

### 7.1 `config/settings.py`

Global parameters for the test system. Class-level attributes on `Settings`.

**Battery limits (Li-ion):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BAT_VOLTAGE_MAX` | `4.7` V | Absolute upper voltage limit per cell |
| `BAT_VOLTAGE_MIN` | `3.5` V | Absolute lower voltage limit (do not discharge below) |
| `BAT_CURRENT_MAX` | `1.0` A | Maximum charge or discharge current |
| `BAT_TEMP_MAX_C` | `45.0` degC | Temperature safety cutoff |
| `BAT_TEMP_MIN_C` | `20.0` degC | Minimum operating temperature |

**Charge parameters (CC-CV):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CHARGE_CURRENT_A` | `0.5` A | Constant-current phase target |
| `CHARGE_VOLTAGE_V` | `4.2` V | Constant-voltage phase target |
| `CHARGE_CUTOFF_A` | `0.05` A | End-of-charge: current taper threshold |
| `CHARGE_TIMEOUT_S` | `7200` s | Maximum charge duration (2 hours) |

**Discharge parameters (CC):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DISCHARGE_CURRENT_A` | `0.5` A | Constant discharge current |
| `DISCHARGE_CUTOFF_V` | `3.0` V | End-of-discharge voltage |
| `DISCHARGE_TIMEOUT_S` | `7200` s | Maximum discharge duration |

> **Note:** `DISCHARGE_CUTOFF_V` (3.0 V) is currently below `BAT_VOLTAGE_MIN` (3.5 V).
> Verify which limit applies to your battery chemistry and update accordingly.

**PXI hardware (simulation mode only -- resource strings live in `config/devices.py`, see 7.2):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PXI_SIMULATE` | `False` | Set `True` for NI simulation mode (no hardware) |

> **Changed:** `PXI_RESOURCE_DAQ`/`PXI_RESOURCE_DMM`/`PXI_RESOURCE_SMU1`/`PXI_RESOURCE_SMU2` used to
> live here and were read directly by `HardwareManager`, duplicating the same values already in
> `config/devices.py`'s `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG`. They have been removed --
> `config/devices.py` is now the only place VISA resource strings are set. Find your actual VISA
> resource strings in **NI-MAX** (Measurement & Automation Explorer): Start -> NI MAX -> Devices
> and Interfaces -> expand PXI Chassis, then edit `config/devices.py`.

**Serial relay:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RELAY_COM_PORT` | `"COM3"` | Serial port of relay matrix controller |
| `RELAY_BAUD_RATE` | `9600` | Baud rate |
| `RELAY_TIMEOUT_S` | `2.0` | Serial read timeout (seconds) |

### 7.2 `config/devices.py`

Physical channel mapping and device assignments. **This file is the single source of truth for every device's resource string / address** -- PXI slot, relay IP, relay COM port. See Section 17 for the full config -> Factory -> HardwareManager pipeline.

**PXI hardware configuration (SMU/PSU, DAQ, DMM):**

```python
SMU_ASSIGNMENTS = {
    "SMU1": {
        "type":     "PXIe",
        "resource": "PXI1Slot4",    # update to match NI-MAX -- SMU/PSU (no separate PSU config)
        "model":    "NI-4140",
        "channels": list(range(1, 9)),
    }
}

DAQ_CONFIG = {
    "type":     "PXIe",
    "resource": "PXI1Slot2",        # update to match NI-MAX
    "model":    "NI-6363",
    "sample_rate_hz": 1.0,
    "voltage_range_v": 5.0,
}

DMM_CONFIG = {
    "type":     "PXIe",
    "resource": "PXI1Slot3",        # update to match NI-MAX
    "model":    "NI-4065",
    "function": "DC_VOLTS",
    "range_v":  10.0,
}
```

`hardware/smu.py::SMU`, `hardware/daq.py::DAQ`, and `hardware/dmm.py::DMM` are each constructed directly from one of these dicts (`SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)`) -- there is exactly one driver class per type, so (unlike relay) no factory/type-dispatch is needed; construction from the config dict already IS the "factory step" for these types.

**Serial relay configuration (diagnostic only):**

```python
RELAY_CONFIG = {
    "type":         "serial",
    "name":         "MAIN_MATRIX",
    "port":         "COM3",         # update to your COM port
    "baud_rate":    9600,
    "timeout":      2.0,
    "num_channels": 8,
    # Replace these with real command strings from your relay controller datasheet:
    "command_open":  "OPEN {ch}\r\n",
    "command_close": "CLOSE {ch}\r\n",
    "command_query": "QUERY {ch}\r\n",
}
```

**Ethernet relay configuration (Numato Lab 32-Channel Ethernet Relay Module -- PRODUCTION):**

```python
NUMATO_RELAY_MATRIX_CONFIG = {
    "type":          "ethernet",
    "driver":        "RELAY32ETHRL00",
    "name":          "MAIN_MATRIX_ETH",
    "ip":            "169.254.1.1",   # validated -- update if your relay's IP differs
    "port":          23,              # default Numato Telnet port
    "username":      "admin",
    "password":      "admin",
    "timeout":       5.0,
    "num_channels":  Settings.RELAY_COUNT,   # single source of truth -- never hardcode 32
    "channel_count": Settings.RELAY_COUNT,
}
```

**Battery channel map:**

Each channel entry maps a logical battery index (1-8) to its physical wiring:

```python
BATTERY_CHANNELS = {
    1: {
        "relay_address":   1,           # relay matrix channel number
        "daq_voltage_ch":  "Dev1/ai0",  # NI 6363 analog input for battery voltage
        "daq_current_ch":  "Dev1/ai8",  # analog input for current (via shunt)
        "daq_ntc_ch":      "Dev1/ai16", # analog input for NTC thermistor voltage
        "fuse_rating_a":   2.0,
    },
    # channels 2-8 follow the same pattern
}
```

Update these channel strings to match your actual PCB connector-to-DAQ wiring.

---

## 8. Testing Framework

`test.py` is the primary commissioning and diagnostic tool. It does not require hardware drivers to be complete — it tests configuration, imports, and interfaces offline, then attempts real hardware connections.

**Launch:**

```bash
python test.py
```

**Menu:**

```
  1. Run Main Test                -- full commissioning run via HardwareManager/TestExecutor
  2. Startup Device Validation    -- config/devices.py only, construction-only, no hardware I/O --
                                     see Section 17.2. Runs automatically before the menu too.
  3. Hardware Discovery           -- config-driven connectivity + identification only, every
                                     device type (SMU/PSU, DMM, DAQ, Relay Eth, Relay Serial) --
                                     see Section 8.1, NOT a measurement/battery/accuracy test
  4. Test SMU (PSU)               -- hardware.smu.SMU connect()/identify() (real, via nidcpower)
  5. Test DMM                     -- hardware.dmm.DMM connect()/identify() (real, via nidmm)
  6. Test DAQ                     -- hardware.daq.DAQ connect()/identify() + deep channel read
  7. Test Relay -- Serial         -- SerialRelay factory + COM port open
  8. Test Relay -- Numato Relay Matrix (Ethernet) -- NumatoRelayMatrix factory + TCP login + open/close/query
  9. Test Relay -- Ethernet Matrix Scan   -- exercises every configured channel in one connection
 10. Test Relay -- RelayEthernetTest      -- native Numato primitives, stop on first failure
 11. Test Relay -- Safety Self-Test       -- public 1-based API, channels 1-32, stop on first failure
 12. Test Electronic Load        -- stub (future)
 13. Test Sensors (NTC)          -- NTC conversion math + TemperatureSensor class
 14. Test Safety Monitor         -- SafetyMonitor logic (all limits + relay guard)
 15. Test Configuration          -- validate_settings() + all Settings values
 16. Test Database Layer         -- DataStorage write/query/CSV (temp dir, no real data)
 17. Test MiniSQL (hooks)        -- StorageBackend interface + stubs
 18. Run All Tests               -- all of the above (except Run Main Test) in sequence
  0. Exit
```

### 8.1 Hardware Discovery (`test_hardware_discovery()`)

A hardware **presence** test, not a measurement test, not a battery-workflow test, and not an instrument-accuracy test. For every device found in `config/devices.py` it validates only:

- the device exists in `config/devices.py` (enumeration)
- the device was discovered correctly
- the driver loaded correctly (import / factory)
- the communication channel opened correctly (connect)
- the instrument responds correctly (self-test / login)
- instrument identification succeeds (identity query)

**Config-driven only** -- it iterates `SMU_ASSIGNMENTS`, `DMM_CONFIGS`, `DAQ_CONFIGS`, `NUMATO_RELAY_MATRIX_CONFIGS`, and `RELAY_SERIAL_CONFIGS` from `config/devices.py`; nothing is hardcoded (no resource strings, IPs, or COM ports), so adding or removing a device there changes coverage automatically with no code change.

**Uses the same production driver classes as HardwareManager -- no duplicated connection logic, no instrument-specific communication code of its own.** Every `_identify_*()` helper in `test.py` does exactly `driver = DriverClass(cfg); driver.connect(); driver.identify(); driver.disconnect()` and nothing else -- it never imports `nidcpower`/`nidmm`/`nidaqmx`/`pyserial` directly. `test_smu()`/`test_dmm()`/`test_daq()` (the deeper per-device menu tests) call the same driver classes for their connect/identify step too, so there is exactly one connect()/identify() implementation per device type, not two:

| Type | Driver class | Connect | Identify | Never does |
|------|--------------|---------|----------|-------------|
| SMU / PSU | `hardware.smu.SMU` | `smu.connect()` (nidcpower session, inside the driver) | `smu.identify()` -> `instrument_model` | `output_enable()`, `set_charge_mode()`, source V/I |
| DMM | `hardware.dmm.DMM` | `dmm.connect()` (nidmm session, inside the driver) | `dmm.identify()` -> `instrument_model` | trigger/read a measurement |
| DAQ | `hardware.daq.DAQ` | `daq.connect()` (NI-DAQmx device enumeration, inside the driver) | `daq.identify()` -> `product_type` + `self_test_device()` | create a task, read any channel |
| Relay (Ethernet) | `hardware.relay_eth.NumatoRelayMatrix` (via `RelayFactory`) | `relay.connect()` (TCP + Telnet login) | the driver's own post-login `relay readall` verification | write to any relay channel (`connect()` is read-only) |
| Relay (Serial) | `hardware.relay_serial.SerialRelay` (via `RelayFactory`) | `relay.connect()` (opens the COM port) | *(no identity command -- no custom protocol is invented; port-open success is the pass criterion)* | send any relay command |

Note: there is no separate PSU hardware/config in this project -- the NI SMU is the PSU (see the "Test SMU (PSU)" menu label), so the SMU/PSU row above covers both.

`RelayEthernetTest` (menu item 10) and Hardware Discovery's Ethernet-relay row both go through `RelayFactory.create(NUMATO_RELAY_MATRIX_CONFIG)`, i.e. the same `NumatoRelayMatrix` instance type -- they never diverge onto a second relay implementation.

**Result format:**

Each test step prints:

```
  [PASS] Device or component name
         Config : config/devices.py -> NUMATO_RELAY_MATRIX_CONFIG (RELAY32ETHRL00 / 169.254.1.1:23)
         Detail : Connected to RELAY32ETHRL00 at 169.254.1.1:23
```

or on failure:

```
  [FAIL] MAIN_MATRIX_ETH
         Config : config/devices.py -> NUMATO_RELAY_MATRIX_CONFIG (RELAY32ETHRL00 / 169.254.1.1:23)
         [ERROR]
         Relay controller not reachable

         Driver:
         RELAY32ETHRL00

         Host:
         169.254.1.1

         Reason:
         Connection timeout
```

**Pre-flight check:**  
`test.py` always runs `test_configuration()` (Settings) AND `test_device_validation()` (config/devices.py) before showing the menu (`preflight_check()`). If any result is FAIL (not just WARNING), the menu is blocked and you must fix `config/settings.py` or `config/devices.py` first -- this is Section 17.2's Startup Validation, gating the menu, not just informational.

**Recommended commissioning sequence** (also the enforced execution order -- see Section 17.4):

```
1. python test.py                          -- Startup Validation + Test Configuration run
                                               automatically before the menu is even shown
2.  -> choose 2 (Startup Device Validation) -- re-run explicitly / inspect in isolation
3.  -> choose 3 (Hardware Discovery)        -- connectivity + identification, every device
4.  -> choose 10 (RelayEthernetTest)        -- native relay primitives, before any relay use
5.  -> choose 13 (Test Safety Monitor)      -- verifies safety logic offline
6.  -> choose 12 (Test Sensors)             -- verifies NTC math
7.  -> choose 16 (Test Database)            -- verifies storage offline
8.  -> choose 4, 5, 6 (SMU/DMM/DAQ)         -- deeper per-device PXI hardware tests
9.  -> choose 18 (Run All Tests)            -- full pass
10. -> choose 1 (Run Main Test)             -- battery test workflow, once everything above passes
```

---

## 9. Relay Architecture

The relay system uses a factory pattern. The rest of the application never imports a concrete relay class — it calls `RelayFactory.create(cfg)` and receives a `RelayBase` object. `config/devices.py` is the single source of truth for device discovery: `NUMATO_RELAY_MATRIX_CONFIG`/`NUMATO_RELAY_MATRIX_CONFIGS`, `DAQ_CONFIG`/`DAQ_CONFIGS`, `SMU_ASSIGNMENTS`, and `DMM_CONFIG`/`DMM_CONFIGS` are all enumerated the same way, so `test.py` and any future device type never need code changes to be discovered — only a new entry in `config/devices.py`.

**Production hardware is the Numato Lab 32-Channel Ethernet Relay Module over Ethernet/Telnet** (`hardware/relay_eth.py::NumatoRelayMatrix`, `NUMATO_RELAY_MATRIX_CONFIG`). The serial COM13 path (`hardware/relay_serial.py::SerialRelay`, `RELAY_CONFIG`) is diagnostic-only and is not the production control path — see [Section 4](#4-supported-hardware).

**Naming:** the driver class and config dicts were previously named `EthernetRelay`/`RELAY_ETH_CONFIG`/`RELAY_ETH_CONFIGS` — generic names that didn't make clear this is specifically Numato hardware, not a vendor-neutral Ethernet relay. They are now `NumatoRelayMatrix`/`NUMATO_RELAY_MATRIX_CONFIG`/`NUMATO_RELAY_MATRIX_CONFIGS`; the old names are kept as backward-compat aliases (`EthernetRelay = NumatoRelayMatrix`, etc. — see `hardware/relay_eth.py` and `config/devices.py`) so nothing that references them by the old name breaks. `"type": "ethernet"` (the `RelayFactory` dispatch key) is unchanged -- it names the transport interface, the same way `"serial"` does, not a vendor.

### 9.1 Unified interface

```python
from config.devices import NUMATO_RELAY_MATRIX_CONFIG       # production: Ethernet
from hardware.relay_factory import RelayFactory

relay = RelayFactory.create(NUMATO_RELAY_MATRIX_CONFIG)

relay.connect()

relay.close(1)          # energize relay channel 1 -- runs the full safety sequence (see 9.4a)
relay.open(1)           # de-energize relay channel 1 (disconnects battery 1)
relay.open_all()        # safe state: all batteries disconnected, verified

state = relay.query(1)  # True = contact closed (battery connected)

relay.disconnect()      # open_all() then close socket / serial port
```

`NumatoRelayMatrix.close_all()` deliberately raises `RelayError` rather than energizing every channel — under the mandatory safety sequence (9.4a), only one relay may ever be active at a time, so "connect all batteries simultaneously" is never a valid operation on the production relay.

### 9.2 Selecting relay type

Change the `"type"` key in your config:

```python
# To use a serial relay:
RELAY_CONFIG = { "type": "serial", "port": "COM3", ... }

# To use an Ethernet relay:
RELAY_CONFIG = { "type": "ethernet", "ip": "192.168.1.50", ... }
```

Or pass either config object to the factory:

```python
# Serial
relay = RelayFactory.create(dev_cfg.RELAY_CONFIG)

# Ethernet
relay = RelayFactory.create(dev_cfg.NUMATO_RELAY_MATRIX_CONFIG)
```

### 9.3 Serial relay (ASCII protocol)

`hardware/relay_serial.py` implements `RelayBase` for relay controllers with a line-oriented text command protocol (the most common type for lab relay boxes).

Commands are read from the config so no code change is needed when the protocol differs between controllers:

```python
RELAY_CONFIG = {
    "type":          "serial",
    "port":          "COM3",
    "baud_rate":     9600,
    "timeout":       2.0,
    "num_channels":  8,
    "command_open":  "OPEN {ch}\r\n",    # {ch} is replaced with the channel number
    "command_close": "CLOSE {ch}\r\n",
    "command_query": "QUERY {ch}\r\n",
}
```

Replace the command strings with your controller's actual protocol.  
The `query` command expects a response containing `"ON"`, `"CLOSED"`, or `"1"` for a closed relay.

### 9.4 Ethernet relay (Numato RELAY32ETHRL00)

`hardware/relay_eth.py` implements `RelayBase` for the Numato RELAY32ETHRL00 32-channel Ethernet relay. Uses a raw TCP socket (not the deprecated `telnetlib`).

**Protocol summary:**

```
1. TCP connect to host:23
2. Wait for "login" -> send username\r\n
3. Wait for "Password: " -> send password\r\n
4. Wait for "successfully" -> login confirmed
5. Wait for ">" -> ready for commands
6. "relay on N\r\n"       -> energize relay N   (wait for ">")
7. "relay off N\r\n"      -> de-energize relay N (wait for ">")
8. "relay read N\r\n"     -> returns "on" or "off" before ">"
9. "relay writeall 00000000\r\n" -> force every relay off in one command
10. "relay readall\r\n"    -> returns a hex bitmask of every relay's state
```

Channel addressing (1-based in the public API, 0-based Numato native addressing):

| API channel | Numato address |
|-------------|---------------|
| 1 | `"0"` |
| 2 | `"1"` |
| ... | ... |
| 10 | `"9"` |
| 11 | `"A"` |
| 12 | `"B"` |
| ... | ... |
| 32 | `"V"` |

**Two API layers.** No custom protocol is invented -- everything is built directly on Numato's own command set ([module docs](https://numato.com/docs/32-channel-ethernet-relay-module/), [readall/writeall reference](https://numato.com/kb/understanding-readallwriteall-commands-for-relay-modules/)):

- **Native primitives** (Numato's own 0-based numbering, relay 0 = first relay): `write(relay_number, state)` ("relay on/off N"), `read_relay(relay_number)` ("relay read N"), `write_all(mask)` ("relay writeall <hex>"), `read_all() -> mask` ("relay readall"), `verify_single(relay_number, expected_state)` (individual verification via `relay read`), `verify_all(expected_mask)` (bulk verification via `relay readall`), `reset()` (native "reset" -- reboots the module; maintenance only, not part of the safety sequence, never called from the battery test path).
- **Public `RelayBase` API** (1-based, matches `BATTERY_CHANNELS`/`ACTIVE_CHANNELS` elsewhere): `open(channel)`, `close(channel)`, `query(channel)`/`read(channel)`, `open_all()`, `close_all()`. `open()`/`close()` are the only methods that ever change relay state, and are implemented entirely on top of the native primitives above.

`RELAY_COUNT` (in `config/settings.py`, default `32`) is the single source of truth for the relay count and flows into `NUMATO_RELAY_MATRIX_CONFIG["channel_count"]` in `config/devices.py` -- it is never hardcoded in the driver or in any relay test.

**Telnet layer guarantees:** every command waits for the `">"` prompt and is checked for an `"invalid"` rejection from the firmware (command acknowledgement validation); every native read/write goes through one automatic reconnect-and-retry if the connection drops or times out (bounded to a single attempt, never a retry loop -- safe because every Numato command is idempotent and every safety-critical write is always independently re-verified by hardware readback afterward); `connect()` itself issues one `relay readall` immediately after login as a connection-verification handshake. A successful command send is never treated as success on its own -- see 9.4a.

### 9.4a Mandatory relay safety sequence (interlock)

`NumatoRelayMatrix.close(channel)` and `NumatoRelayMatrix.open(channel)` are the **only** entry points that ever change relay state, and both route through the same mandatory sequence — the requested relay is never activated directly:

```
1. Turn OFF all relays          write_all(0)        -> "relay writeall 00000000"
2. Read back, verify ALL OFF    verify_all(0)        -> "relay readall"
      -> if any relay is still active: raise RelayStateVerificationError, STOP
3. Turn ON the requested relay  write(n, True)        -> "relay on N"     [close() only]
4. Individual verification      verify_single(n, ON)  -> "relay read N"
      -> if the requested relay did not turn on: raise RelayStateVerificationError, STOP
5. Bulk verification            verify_all(1<<n)      -> "relay readall"
      -> if any OTHER relay is unexpectedly active: raise RelayStateVerificationError, STOP
6. Continue only if both verifications succeeded
```

The governing philosophy is **WRITE -> READ BACK -> VERIFY -> CONTINUE**, never **WRITE -> ASSUME SUCCESS**. `open(channel)`'s target state *is* all-off, so step 2's verification is also its final verification — there is no separate activation step. `close()` verifies twice: once individually (`relay read N`, per the individual-verification requirement) and once against the whole bank (`relay readall`, which alone can catch an unrelated relay unexpectedly energized).

`NumatoRelayMatrix` is the single authority for relay state control on the production path. Developers must not bypass this sequence with raw commands (`_send_raw`, `_send_and_capture`, `writeall`, `relay on/off`) from outside `hardware/relay_eth.py` — every relay state change must go through `open()`/`close()`/`open_all()` (or, for driver-internal/test-harness use only, the native `write()`/`write_all()` primitives, which are themselves always paired with `verify_single()`/`verify_all()`). Any future relay driver (`hardware/relay_<type>.py`, see 9.5) must preserve the same write→read-back→verify guarantee before being used in production.

**Failure policy — no warning-only mode.** Each of the following is a SAFETY FAULT, not a retryable condition:

- `RelayStateVerificationError` (readback mismatch, multiple relays active, unexpected relay state)
- Telnet/TCP timeout (`NIPXITimeoutError`)
- Communication failure / connection loss (`RelayError`)
- Invalid or unparseable relay state transition

Any of these must raise an exception and propagate: `test_control/battery_test.py::BatteryTestSequence.run()` catches `RelayError` (which `RelayStateVerificationError` subclasses) alongside `SafetyViolationError`, calls `SafetyMonitor.emergency_stop()`, and re-raises — it never falls through to another relay command on that channel or continues to the next channel. `test_control/test_executor.py::TestExecutor.run()` catches the same exception at the top level and marks the whole run `aborted`. No component is permitted to log a warning and continue past a relay verification failure.

### 9.4b Relay validation tests

Two `test.py` menu items validate the relay driver at its two API layers, both config-driven (`RELAY_COUNT`, never hardcoded) and both stopping immediately on the first failure:

**"Test Relay -- RelayEthernetTest (native primitives, stop on first failure)"** (`test_relay_ethernet_test()`) exercises the native Numato primitives directly, using Numato's own 0-based relay numbering -- this is the lower-level validation target, meant to be run *before* relay usage is integrated into higher-level battery workflows:

```
For relay_index in range(RELAY_COUNT):
    write_all(0)          -> read_all() -> verify all OFF
    write(relay_index, True) -> read_all() -> verify relay_index ON, all others OFF
    write_all(0)          -> read_all() -> verify all OFF
```

**"Test Relay -- Safety Self-Test (1-32, stop on first failure)"** (`test_relay_safety_selftest()`) exercises the mandatory sequence through the public 1-based API instead:

```
For relay N = 1 .. num_channels:
    OFF ALL
    VERIFY OFF
    ON relay N
    VERIFY relay N ON, all others OFF
Then: OFF ALL / VERIFY OFF
```

Both stop immediately on the first failure of any kind (connection error, timeout, readback mismatch, unexpected active relay, parser error, verification failure) and report Relay Number / Expected State / Actual State / Cause — neither continues to the remaining channels once one has failed. For the duration of the run both also re-enable `hardware/relay_eth.py`'s per-command logging (normally silenced by `test.py`) so every command shows its raw readback, decoded mask, and decoded active-channel list, e.g.:

```
RAW: 00000001  MASK: 0x00000001  ACTIVE: [1]
```

**Confirmed against the physical Numato unit:** a live run of the matrix scan (all 32 channels, ON -> READ -> OFF) and Hardware Discovery both passed end-to-end — login/authentication, the `relay readall` hex-bitmask parsing in `hardware/relay_eth.py::_parse_readall_response()`, and per-channel verification all matched the physically observed relay state. See Section 9.4c for the authentication root cause this run uncovered and fixed.

### 9.4c Authentication debugging (root cause confirmed and fixed)

**Symptom:** the framework reported "Authentication failed" connecting to the Numato Relay Matrix, while a manual Telnet session to the same IP/port/credentials succeeded. Ping, the web UI, and manual Telnet login were all independently confirmed working beforehand, narrowing the problem to the driver's own login sequence.

**Root cause (confirmed by a live run against the physical unit):** the firmware sends a Telnet IAC option-negotiation request ("IAC DO 45", RFC 854) mid-handshake. A real Telnet client (used for the successful manual login) always auto-answers this; the previous implementation had zero IAC handling and never replied — exactly the "manual Telnet works, raw socket doesn't" symptom class.

**Fix:** `hardware/relay_eth.py::NumatoRelayMatrix._handle_iac()` scans every inbound chunk for IAC sequences, strips them from the text stream, and answers with a blanket decline (IAC DONT/WONT). Confirmed in the live transcript: the server proceeded normally immediately after receiving the decline.

**Secondary finding, same investigation:** the real login prompt is "User Name: ", not "login:" -- the previous exact-match implementation (copied from Numato's own reference script) only happened to still work because the word "login" incidentally appears in the banner's instructional sentence. `_login()` now matches case-insensitively against known-plausible prompt words (`_read_until_any()`) and treats the `>` command prompt as the authoritative success signal, both confirmed correct live.

**How to see the conversation yourself:** every login step is logged at DEBUG level -- raw RX chunks, detected prompts, TX sent (username and password in cleartext -- these are lab default credentials; see the caveat in `_login()`'s docstring if credentials are ever changed to something sensitive), IAC negotiation replies, final response, and PASS/FAIL classification. Run any relay test in `test.py` (they all wrap themselves in `_numato_relay_debug_logging()`, which re-enables this output since test.py silences logging by default) and read the `[RELAY LOG]`-prefixed lines. `_classify_relay_error()` was also fixed to never collapse a failure down to a bare "Authentication failed" -- the full underlying diagnostic is always appended after it.

### 9.5 Adding a new relay type

1. Create `hardware/relay_<type>.py` that subclasses `RelayBase`:

```python
from hardware.relay import RelayBase

class ModbusRelay(RelayBase):
    def __init__(self, cfg: dict):
        super().__init__(cfg.get("name", "MODBUS_RELAY"), cfg.get("num_channels", 8))
        # read your config keys here

    def connect(self): ...
    def disconnect(self): ...
    def open(self, channel: int): ...
    def close(self, channel: int): ...
    def query(self, channel: int) -> bool: ...
```

2. Register in `hardware/relay_factory.py`:

```python
_DRIVERS = {
    "serial":   ("hardware.relay_serial", "SerialRelay"),
    "ethernet": ("hardware.relay_eth",    "NumatoRelayMatrix"),
    "modbus":   ("hardware.relay_modbus", "ModbusRelay"),   # <-- add this line
}
```

3. Set `"type": "modbus"` in the config dict.

No other code changes needed.

---

## 10. Database Layer

### 10.1 DataStorage

`data/storage.py` provides `DataStorage`, which writes every measurement sample to:

- **SQLite** (`data_output/nipxi.db`) — one row per sample, queryable by `run_id` and `channel`
- **CSV** (`data_output/csv/<run_id>_ch<N>.csv`) — one file per channel per run

```python
from data.storage import DataStorage
from config.settings import Settings

storage = DataStorage(settings=Settings)

with storage:                              # calls open() and close() automatically
    storage.record(channel=1, sample={
        "elapsed_s": 0.0,
        "phase":     "charge",
        "voltage_v": 3.72,
        "current_a": 0.50,
        "temp_c":    25.0,
    })

    # Query all records for channel 1 in this run
    rows = storage.query(channel=1)
    # rows is a list of dicts: [{"run_id": "20260702_103045", "channel": 1, ...}, ...]
```

Each run gets a unique `run_id` timestamp (`YYYYMMDD_HHMMSS`).

### 10.2 StorageBackend interface

`DataStorage` inherits from `StorageBackend`, an abstract base class that defines the minimum interface any storage backend must implement:

```python
class StorageBackend(ABC):
    def open(self): ...
    def close(self): ...
    def record(self, channel: int, sample: dict): ...
    def query(self, run_id: str = None, channel: int = None) -> list: ...

    # Context manager provided by base class:
    def __enter__(self): self.open(); return self
    def __exit__(self, *_): self.close()
```

### 10.3 Querying data

```python
with DataStorage(Settings) as storage:
    # All records for run "20260702_103045"
    run_rows = storage.query(run_id="20260702_103045")

    # All records for channel 3 across all runs
    ch3_rows = storage.query(channel=3)

    # All records (no filter)
    all_rows = storage.query()
```

Row format (list of dicts):

```python
{
    "run_id":    "20260702_103045",
    "channel":   1,
    "timestamp": "2026-07-02T10:30:45.123456",
    "elapsed_s": 12.5,
    "phase":     "charge",
    "voltage_v": 3.85,
    "current_a": 0.50,
    "temp_c":    27.3,
}
```

---

## 11. Safety System

`test_control/safety_monitor.py` runs on every measurement sample. If any limit is exceeded, it raises `SafetyViolationError`, which triggers an emergency stop.

**Limits (from `config/settings.py`):**

| Condition | Limit |
|-----------|-------|
| Overvoltage | `BAT_VOLTAGE_MAX` = 4.7 V |
| Undervoltage | `BAT_VOLTAGE_MIN` = 3.5 V |
| Overcurrent | `BAT_CURRENT_MAX` = 1.0 A |
| Overtemperature | `BAT_TEMP_MAX_C` = 45.0 degC |
| Relay switch | current must be < `ZERO_CURRENT_THRESHOLD_A` = 0.01 A |
| Relay verification | any `RelayError` (incl. `RelayStateVerificationError`), Telnet/TCP timeout, or comms failure -- see [Section 9.4a](#9.4a-mandatory-relay-safety-sequence) |

**Emergency stop sequence:**

```
1. smu.output_disable()    -- cut SMU output immediately
2. relay.open_all()        -- disconnect all batteries (force-off + verify)
```

**Relay verification failures are safety faults, never warnings.** `BatteryTestSequence.run()` catches `RelayError` alongside `SafetyViolationError`, calls `emergency_stop()`, and re-raises immediately -- it never attempts another relay command on that channel or continues to the next one. `TestExecutor.run()` catches the same exception and marks the whole run `aborted`. No component is permitted to log a warning and continue past a relay verification failure. See [Section 9.4a](#9.4a-mandatory-relay-safety-sequence) for the full sequence.

**Usage example:**

```python
from test_control.safety_monitor import SafetyMonitor
from config.settings import Settings

monitor = SafetyMonitor(settings=Settings)

status = monitor.check(voltage_v=3.8, current_a=0.4, temp_c=28.0)
if not status.safe:
    # status.reason: e.g. "Overvoltage: 4.75 V > 4.7 V"
    monitor.emergency_stop(smu, relay, reason=status.reason)

# Check before switching relay:
if monitor.is_safe_to_switch_relay(current_a=measured_current):
    relay.close(channel)
```

`temp_c` can be `None` when the NTC is not yet wired in — the temperature check is skipped.

---

## 12. Error Handling

All exceptions inherit from `NIPXIError`:

```
NIPXIError
+-- HardwareInitError     -- device failed to initialize
+-- RelayError            -- relay communication failure
|     +-- RelayStateVerificationError  -- readback did not match commanded state
|                                          (always fatal -- see Section 9.4a)
+-- DAQError              -- DAQ read/write failure
+-- SMUError              -- SMU communication or compliance failure
+-- DMMError              -- DMM communication failure
+-- SafetyViolationError  -- safety limit exceeded (triggers e-stop)
+-- NIPXITimeoutError     -- test step exceeded allowed duration
+-- ValidationError       -- config or input validation failed
      +-- DeviceConfigError  -- config/devices.py failed startup validation
                                (see Section 17.2) -- raised before any
                                hardware communication is attempted
```

**Example: catching specific errors:**

```python
from utils.errors import RelayError, NIPXITimeoutError, ValidationError

try:
    relay.connect()
except NIPXITimeoutError as e:
    log.error("Relay timeout: %s", e)
except RelayError as e:
    log.error("Relay error: %s", e)

try:
    validate_settings(Settings)
except ValidationError as e:
    log.error("Bad config: %s", e)
    sys.exit(1)
```

**Configuration validation at startup:**

`main.py` calls `validate_settings(Settings)` before touching any hardware. Failures exit immediately with a descriptive message.

```python
from utils.validators import validate_settings, ValidationError

try:
    validate_settings(Settings)
except ValidationError as e:
    print(f"Configuration error: {e}")
    sys.exit(1)
```

---

## 13. Usage Examples

### 13.1 Run a test

```bash
# Full test on all channels (default: channels 1-8)
python main.py

# Test only channels 1, 2, and 3
python main.py --channels 1 2 3

# Validate configuration without connecting hardware
python main.py --dry-run
```

### 13.2 Run the hardware commissioning framework

```bash
python test.py
# Interactive menu -- choose a subsystem or "Run All Tests"
```

### 13.3 Use HardwareManager directly

```python
from test_control.hardware_manager import HardwareManager
from config.settings import Settings
from config import devices as dev_cfg

# Create the manager (no connection yet)
hw = HardwareManager(Settings, relay_cfg=dev_cfg.NUMATO_RELAY_MATRIX_CONFIG)  # production: Ethernet

# Connect all devices in the correct order
with hw:                          # calls connect_all() on enter, disconnect_all() on exit
    hw.relay.close(1)             # close relay channel 1
    reading = hw.daq.read_all_batteries()
    hw.relay.open(1)

# Check health after connecting
hw.connect_all()
status = hw.health_check()
for device, info in status.items():
    print(device, "ok" if info["ok"] else f"ERROR: {info['detail']}")
hw.disconnect_all()
```

### 13.4 Run a test programmatically

```python
from test_control.hardware_manager import HardwareManager
from test_control.test_executor import TestExecutor
from test_control.result_manager import ResultManager
from config.settings import Settings
from config import devices as dev_cfg

hw         = HardwareManager(Settings, relay_cfg=dev_cfg.RELAY_CONFIG)
result_mgr = ResultManager(settings=Settings)
executor   = TestExecutor(hw=hw, storage=result_mgr.storage, settings=Settings)

hw.connect_all()
try:
    with result_mgr:                          # opens/closes DataStorage
        result = executor.run(channels=[1, 2])

    result_mgr.generate_report(result.run_id)
    print("run_id:", result.run_id)
    print("success:", result.success)
    for r in result.channel_results:
        print(f"  ch{r.channel}: charge={r.charge_completed} discharge={r.discharge_completed}")
finally:
    hw.disconnect_all()
```

### 13.5 Write to storage directly

```python
from data.storage import DataStorage
from config.settings import Settings

with DataStorage(settings=Settings) as storage:
    storage.record(channel=1, sample={
        "elapsed_s": 0.0,
        "phase":     "charge",
        "voltage_v": 3.72,
        "current_a": 0.50,
        "temp_c":    25.0,
    })
    rows = storage.query(channel=1)
    print(rows[0]["voltage_v"])    # 3.72
```

### 13.6 Create a relay from configuration

```python
from hardware.relay_factory import RelayFactory
from config import devices as dev_cfg

# Serial relay
relay = RelayFactory.create(dev_cfg.RELAY_CONFIG)

# Ethernet relay (Numato RELAY32ETHRL00)
relay = RelayFactory.create(dev_cfg.NUMATO_RELAY_MATRIX_CONFIG)

# Both use the same API:
relay.connect()
relay.close(1)          # energize channel 1 (connect battery)
relay.open(1)           # de-energize channel 1 (disconnect battery)
relay.open_all()        # safe state
relay.disconnect()
```

### 13.7 Validate configuration at startup

```python
from utils.validators import validate_settings, ValidationError
from config.settings import Settings
import sys

try:
    validate_settings(Settings)
except ValidationError as e:
    print(f"Configuration error: {e}")
    sys.exit(1)
```

### 13.8 Use ResultManager with MiniSQL

```python
# When MiniSQL is available, inject it -- no other changes needed:
from data.storage_minisql import MiniSQLStorage  # implement this class
from test_control.result_manager import ResultManager
from config.settings import Settings

MINISQL_CFG = {"host": "localhost", "port": 5433}
result_mgr = ResultManager(
    settings=Settings,
    storage_backend=MiniSQLStorage(cfg=MINISQL_CFG),
)

# TestExecutor and BatteryTestSequence are unaffected -- they call storage.record() only.
```

---

## 14. Development Workflow

### 13.1 Implementing a hardware driver

All drivers follow the same pattern. Example: completing `hardware/smu.py`:

```python
# hardware/smu.py
import nidcpower
from hardware.base import HardwareBase
from utils.errors import SMUError

class SMU(HardwareBase):
    def connect(self):
        try:
            self._session = nidcpower.Session(self.resource)
        except nidcpower.Error as e:
            raise SMUError(f"SMU {self.resource} failed to init: {e}") from e
        self.connected = True

    def disconnect(self):
        if self._session is not None:
            self._session.close()
        self.connected = False

    def set_charge_mode(self, current_a: float, voltage_limit_v: float):
        # nidcpower CC-CV configuration here
        ...

    def measure(self) -> dict:
        v = self._session.measure(nidcpower.MeasurementTypes.VOLTAGE)
        i = self._session.measure(nidcpower.MeasurementTypes.CURRENT)
        return {"voltage_v": v, "current_a": i}
```

Steps:
1. Fill in the `TODO` comments in the driver file
2. Run `python test.py` -> "Test SMU" to verify the implementation
3. Check `docs/TODO.md` and mark the item `[DONE]`

### 13.2 Running the validation suite

```bash
# Configuration only (offline, no hardware required)
python test.py   # choose 15 (Test Configuration) or 2 (Startup Device Validation)

# Safety monitor (offline)
python test.py   # choose 14 (Test Safety Monitor)

# Full test pass
python test.py   # choose 18 (Run All Tests)
```

### 13.3 Adding a new test section

1. Write a function `test_<thing>() -> list[TestResult]` in `test.py`
2. Add it to `MENU`:

```python
MENU = [
    ...
    ("Test My New Thing", test_my_new_thing),
    ("Run All Tests",     None),
]
```

The function should return a list of `TestResult` objects using the `_ok()`, `_warn()`, `_fail()` helpers.

### 13.4 Logging

All modules use `logging.getLogger("nipxi.<module>")`. Logging is initialized in `main.py`:

```python
from data.logger import setup as setup_logging
setup_logging(Settings)
```

Log output goes to `logs/nipxi.log` and the console simultaneously.  
Change `LOG_LEVEL` in `config/settings.py` to `"DEBUG"` for verbose hardware tracing.

### 13.5 Adding a new configuration parameter

1. Add the attribute to `Settings` in `config/settings.py` with a comment
2. If it is required to be valid at startup, add a check in `utils/validators.validate_settings()`
3. Reference it as `self.s.NEW_PARAM` inside any class that takes `settings`

---

## 15. MiniSQL Integration Path

The storage layer is designed to be swapped from SQLite to MiniSQL without changing any caller code.

**Current state:**  
`DataStorage(StorageBackend)` writes to SQLite. `StorageBackend` is an ABC that defines the contract.

**To add MiniSQL:**

1. Create `data/storage_minisql.py`:

```python
from data.storage import StorageBackend

class MiniSQLStorage(StorageBackend):
    def __init__(self, cfg):
        import minisql
        self._conn = None
        self._cfg  = cfg

    def open(self):
        self._conn = minisql.connect(
            host=self._cfg["host"],
            port=self._cfg["port"],
        )
        # create table if needed

    def close(self):
        if self._conn:
            self._conn.close()

    def record(self, channel: int, sample: dict):
        self._conn.insert("measurements", {
            "channel": channel, **sample
        })

    def query(self, run_id=None, channel=None) -> list:
        # build SELECT from filters
        ...
        return rows
```

2. In `main.py`, replace the storage instantiation:

```python
# Before:
storage = DataStorage(settings=Settings)

# After:
from data.storage_minisql import MiniSQLStorage
storage = MiniSQLStorage(cfg=MINISQL_CONFIG)
```

No changes needed in `BatteryTestSequence`, `ChargeCycle`, or `DischargeCycle` — they all receive `storage` by injection and call only `storage.record()`.

Test the swap with:

```bash
python test.py   # choose 17 (Test MiniSQL hooks)
```

---

## 16. Troubleshooting

**`ModuleNotFoundError: No module named 'nidaqmx'`**  
Install NI-DAQmx driver and Python bindings: `pip install nidaqmx`

**`ModuleNotFoundError: No module named 'serial'`**  
Install pyserial: `pip install pyserial`

**SMU/DAQ/DMM resource not found**  
Open NI-MAX, expand Devices and Interfaces, note the exact VISA resource string  
(e.g. `"PXI1Slot4"` or `"Dev1"`), and update `config/settings.py`.

**Relay not connecting (serial)**  
- Verify the COM port: Device Manager -> Ports (COM & LPT)
- Verify baud rate matches the relay controller datasheet
- Fill in the real `command_open/close/query` strings from the datasheet

**Relay not connecting (Ethernet)**  
- Ping the relay IP from the host: `ping 192.168.1.50`
- Verify port 23 is open: `telnet 192.168.1.50 23` (Windows: enable Telnet client first)
- Default credentials: `admin` / `admin` (check Numato documentation to change them)

**`ValidationError: DISCHARGE_CUTOFF_V (3.0) < BAT_VOLTAGE_MIN (3.5)`**  
This is a known configuration cross-check warning. Verify which value is correct for  
your battery chemistry: if cells should not be discharged below 3.5 V, raise  
`DISCHARGE_CUTOFF_V` to 3.5 V. If 3.0 V is intentional, update `BAT_VOLTAGE_MIN`.

**Pre-flight blocks with FAIL**  
Run "Test Configuration" or "Startup Device Validation" to see the specific failing parameter -- `preflight_check()` prints the failing item's name before blocking the menu either way.

**Windows console encoding error**  
If you see `UnicodeEncodeError` on Windows, set the console to UTF-8:
```bash
chcp 65001
```
or set the environment variable: `PYTHONUTF8=1`

---

## 17. Hardware Abstraction Architecture & Device Onboarding

### 17.1 The pipeline

```
config/devices.py
     |
     v
Factory                    (RelayFactory for relay; direct construction
     |                       SMU(cfg)/DAQ(cfg)/DMM(cfg) for single-implementation types)
     v
HardwareManager             (device lifecycle: connect_all/disconnect_all/health_check)
     |
     v
Device Drivers               (hardware/smu.py, daq.py, dmm.py, relay_eth.py, relay_serial.py)
     |
     v
Hardware Discovery Test      (test_hardware_discovery() -- connectivity + identification only)
     |
     v
Functional Hardware Tests    (test_smu/test_dmm/test_daq/test_relay_* -- deeper per-device checks)
     |
     v
Battery Test Workflows       (BatteryTestSequence / TestExecutor -- charge/discharge cycling)
```

`config/devices.py` is the single source of truth for every device's resource string / address (PXI slot, relay IP, relay COM port). `config/settings.py` holds tunable *behavior* parameters (voltages, currents, timeouts, `RELAY_COUNT`) and simulation mode (`PXI_SIMULATE`) -- it does not hold device addresses.

**Why relay has a `Factory` class but SMU/DAQ/DMM do not:** the relay role has two real implementations (`SerialRelay`, `NumatoRelayMatrix`) selected dynamically by `cfg["type"]`, so `RelayFactory.create(cfg)` genuinely dispatches. SMU, DAQ, and DMM each have exactly one driver class -- there is nothing to dispatch between, so `SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)` direct construction from a `config/devices.py` dict already fulfills the "config -> Factory -> driver" step without an extra indirection layer. This is a deliberate choice, not a gap: adding a second SMU/DAQ/DMM implementation would be the point at which a real factory becomes justified, exactly like relay.

### 17.2 Startup device validation (`utils/device_validator.py`)

`validate_devices_or_raise(dev_cfg)` runs in `main.py` immediately after `validate_settings()` and before `HardwareManager` is constructed -- and in `test.py`'s `preflight_check()`, before the menu is shown (so it also runs before Hardware Discovery or any other test). It never calls `connect()` -- construction only, no hardware I/O -- and it collects **every** problem before reporting, rather than stopping at the first one:

1. Load `config/devices.py` (the caller already imported it -- this is the function's argument).
2. Verify every configured device can be instantiated (`SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)`/`RelayFactory.create(cfg)` -- construction, not connect).
3. Validate required configuration fields per type (`resource` for SMU/DMM/DAQ; `ip`/`port`/`channel_count` for Ethernet relay; `port` for serial relay).
4. Validate no duplicate device names exist (across every configured device, any type).
5. Validate no duplicate VISA resources exist (SMU/DMM/DAQ share the same PXI bus).
6. Validate no duplicate IP addresses exist (Ethernet relays).
7. Validate no duplicate COM ports exist (serial relays).
8. Validate no duplicate relay identifiers exist (`BATTERY_CHANNELS[...]["relay_address"]`).
9. Validate relay count consistency (`num_channels == channel_count == Settings.RELAY_COUNT`, and every `relay_address` is in range).
10. Validate every relay `"type"` is registered in `RelayFactory` (`RelayFactory.supported_types()`).
11. Report all of the above at once via `DeviceConfigError` -- `main.py` and `test.py` both fail (exit / block the menu) before any hardware communication is attempted.

```python
from config import devices as dev_cfg
from utils.device_validator import validate_devices_or_raise

validate_devices_or_raise(dev_cfg)   # raises DeviceConfigError listing every problem, or returns
```

### 17.3 Adding a new instrument (config/devices.py only)

For a device type that already has a driver class (a second SMU, DAQ, or DMM):

1. Add an entry to `SMU_ASSIGNMENTS` / `DAQ_CONFIGS` / `DMM_CONFIGS` in `config/devices.py` with a unique name, `resource`, and `model`.
2. Nothing else changes -- `test_hardware_discovery()`, `test_device_validation()`, and `preflight_check()` all pick it up automatically (they iterate these dicts, never a hardcoded list).
3. Run `python test.py` -> "Startup Device Validation" then "Hardware Discovery" to confirm it.

For a genuinely new device *type* (e.g. an electronic load), follow the existing pattern:

1. `hardware/<type>.py`: a class inheriting `HardwareBase`, constructed from a `config/devices.py`-shaped dict, implementing `connect()`/`disconnect()`/`identify()` at minimum.
2. `config/devices.py`: a `<TYPE>_CONFIG` dict plus a `<TYPE>_CONFIGS = {name: cfg}` enumeration dict (matching `DMM_CONFIGS`'s shape).
3. `utils/device_validator.py`: add the new enumeration dict to `_build_registry()` and, if it has multiple possible implementations, a factory + a `_check_factory_type()` branch (otherwise direct construction is enough, per 17.1).
4. `test.py`: add `("<Type>", dev_cfg.<TYPE>_CONFIGS, _identify_<type>)` to `_DISCOVERY_TARGETS` -- Hardware Discovery covers it with no other change.
5. Optionally wire it into `HardwareManager` if it participates in the battery test workflow (relay/SMU/DAQ do; DMM is intentionally optional, matching its role as independent verification only).

### 17.4 Test execution order

```
1. Startup Validation         validate_settings() + validate_devices_or_raise()
                               (main.py at startup; test.py's preflight_check() before the menu)
2. Hardware Discovery          test_hardware_discovery() -- connectivity + identification, every type
3. RelayEthernetTest            test_relay_ethernet_test() -- native relay primitives validated
                               before any relay use in a functional test
4. Functional Hardware Tests    test_smu / test_dmm / test_daq / test_relay_* / Safety Self-Test
5. Battery Test Workflows       BatteryTestSequence / TestExecutor (Run Main Test / main.py)
```

Each stage assumes the previous one passed. `test.py`'s menu enforces stage 1 structurally (it gates the whole menu); stages 2-5 are independently selectable menu items so any stage can be re-run in isolation, but running them out of order on unvalidated/unverified hardware is not recommended.

---

## Related Project

This software controls the hardware described in the main BLOAST repository:

- PCB design: `hw/kicad/`
- Battery spec: `hw/kicad/docs/COMPONENT_SPECIFICATIONS.md`
- Project roadmap: `roadmap.md`

---

## Remote Repository

> **TODO:** Set up remote Git repository and update this line.
>
> ```bash
> git remote add origin <YOUR_REMOTE_URL_HERE>
> git push -u origin main
> ```
