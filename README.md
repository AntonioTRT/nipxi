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
                |    +-- HardwareManager  (device lifecycle)       |
                |    +-- TestExecutor     (runs the test sequence) |
                |    +-- ResultManager    (storage + reports)      |
                +-------------------+------------------------------+
                                    |
                     NI-VISA / nidaqmx / nidcpower
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
       |  Serial: NI 2569 / COM   |
       |  Ethernet: RELAY32ETHRL00|
       |  8 channels, multiplexed |
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

**Control flow (per test run):**

```
Initialize hardware
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
|   |-- settings.py             All tunable parameters (voltages, currents, paths, ports).
|   +-- devices.py              Channel mapping, PXI slot assignments, relay config.
|
|-- hardware/                   One class per physical device. All inherit HardwareBase.
|   |-- base.py                 Abstract base: connect(), disconnect(), context manager.
|   |-- relay.py                RelayBase abstract class (open/close/query interface).
|   |-- relay_serial.py         Serial relay driver (ASCII text protocol over COM port).
|   |-- relay_eth.py            Ethernet relay driver (Numato RELAY32ETHRL00 via TCP).
|   |-- relay_factory.py        Factory: RelayFactory.create(cfg) -> RelayBase.
|   |-- relay_matrix.py         Legacy relay driver (kept for backward compat).
|   |-- smu.py                  SMU driver stub (nidcpower -- TODO: implement).
|   |-- daq.py                  DAQ driver stub (nidaqmx -- TODO: implement).
|   |-- pxi_rack.py             PXI chassis enumeration stub.
|   +-- temperature.py          NTC thermistor voltage-to-Celsius conversion.
|
|-- test_control/               Test sequence logic, no hardware knowledge.
|   |-- hardware_manager.py     Device lifecycle (connect_all/disconnect_all/health_check).
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
|   |-- validators.py           Config and input validation functions.
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

| Role | Model | Interface | Library |
|------|-------|-----------|---------|
| PXI Chassis | NI PXI-1042 (or compatible) | NI-VISA | — |
| DAQ | NI 6363 (Slot 2) | NI-DAQmx | `nidaqmx` |
| DMM | NI 4065 (Slot 3) | NI-DMM | `nidmm` |
| SMU (primary) | NI 4140 or 4139 (Slot 4) | NI-DCPower | `nidcpower` |
| SMU (optional) | NI 4130 (Slot 5) | NI-DCPower | `nidcpower` |
| Relay (serial) | Any ASCII-command relay via COM | pyserial | `pyserial` |
| Relay (Ethernet) | Numato RELAY32ETHRL00 | TCP/Telnet | stdlib `socket` |
| Battery Hub PCB | BLOSS Hub Rev A | — | — |

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
#    config/settings.py  -- set RELAY_COM_PORT, PXI_RESOURCE_* to match your hardware
#    config/devices.py   -- set relay command strings, confirm channel wiring

# 5. Run the test framework to verify hardware
python test.py

# 6. Run the application (once hardware drivers are implemented)
python main.py
python main.py --channels 1 2 3   # test only channels 1, 2, 3
python main.py --dry-run           # no hardware, config validation only
```

**First-time checklist:**

- [ ] Set `RELAY_COM_PORT` in `config/settings.py` to your COM port (e.g. `"COM4"`)
- [ ] Set `PXI_RESOURCE_DAQ`, `PXI_RESOURCE_DMM`, `PXI_RESOURCE_SMU1` to match NI-MAX
- [ ] Set `RELAY_ETH_CONFIG["ip"]` in `config/devices.py` if using Ethernet relay
- [ ] Fill in `command_open/close/query` in `config/devices.py` RELAY_CONFIG for serial relay
- [ ] Confirm `BATTERY_CHANNELS` channel numbers match your physical wiring
- [ ] Run `python test.py` and choose "Test Configuration" to validate settings

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

**PXI hardware:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PXI_RESOURCE_DAQ` | `"PXI1Slot2"` | NI 6363 VISA resource string |
| `PXI_RESOURCE_DMM` | `"PXI1Slot3"` | NI 4065 VISA resource string |
| `PXI_RESOURCE_SMU1` | `"PXI1Slot4"` | NI SMU VISA resource string |
| `PXI_SIMULATE` | `False` | Set `True` for NI simulation mode (no hardware) |

> Find your actual VISA resource strings in **NI-MAX** (Measurement & Automation Explorer):
> Start -> NI MAX -> Devices and Interfaces -> expand PXI Chassis.

**Serial relay:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RELAY_COM_PORT` | `"COM3"` | Serial port of relay matrix controller |
| `RELAY_BAUD_RATE` | `9600` | Baud rate |
| `RELAY_TIMEOUT_S` | `2.0` | Serial read timeout (seconds) |

### 7.2 `config/devices.py`

Physical channel mapping and device assignments.

**Serial relay configuration:**

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

**Ethernet relay configuration (Numato RELAY32ETHRL00):**

```python
RELAY_ETH_CONFIG = {
    "type":         "ethernet",
    "driver":       "RELAY32ETHRL00",
    "name":         "MAIN_MATRIX_ETH",
    "ip":           "192.168.1.50",  # update to your relay IP address
    "port":         23,              # default Numato Telnet port
    "user":         "admin",
    "password":     "admin",
    "timeout":      5.0,
    "num_channels": 8,
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

**Menu (12 sections):**

```
  1. Test SMU (PSU)              -- nidcpower session + hardware.smu.SMU interface
  2. Test DMM                    -- nidmm session
  3. Test DAQ                    -- nidaqmx device + channel read
  4. Test Relay -- Serial        -- SerialRelay factory + COM port open
  5. Test Relay -- Ethernet      -- EthernetRelay factory + TCP login + open/close/query
  6. Test Electronic Load        -- stub (future)
  7. Test Sensors (NTC)          -- NTC conversion math + TemperatureSensor class
  8. Test Safety Monitor         -- SafetyMonitor logic (all limits + relay guard)
  9. Test Configuration          -- validate_settings() + all config values
 10. Test Database Layer         -- DataStorage write/query/CSV (temp dir, no real data)
 11. Test MiniSQL (hooks)        -- StorageBackend interface + stubs
 12. Run All Tests               -- all of the above in sequence
  0. Exit
```

**Result format:**

Each test step prints:

```
  [PASS] Device or component name
         Config : config/devices.py -> RELAY_ETH_CONFIG (RELAY32ETHRL00 / 192.168.1.50:23)
         Detail : Connected to RELAY32ETHRL00 at 192.168.1.50:23
```

or on failure:

```
  [FAIL] RELAY_ETH_01
         Config : config/devices.py -> RELAY_ETH_CONFIG (RELAY32ETHRL00 / 192.168.1.50:23)
         [ERROR]
         Relay controller not reachable

         Driver:
         RELAY32ETHRL00

         Host:
         192.168.1.50

         Reason:
         Connection timeout
```

**Pre-flight check:**  
`test.py` always runs `test_configuration()` before showing the menu. If any configuration value is FAIL (not just WARNING), the menu is blocked and you must fix `config/settings.py` or `config/devices.py` first.

**Recommended commissioning sequence:**

```
1. python test.py  -> choose 9 (Test Configuration)  -- verify all config passes
2.                 -> choose 8 (Test Safety Monitor)  -- verifies safety logic offline
3.                 -> choose 7 (Test Sensors)         -- verifies NTC math
4.                 -> choose 10 (Test Database)       -- verifies storage offline
5.                 -> choose 4 or 5 (Relay)           -- first hardware test
6.                 -> choose 1, 2, 3 (SMU/DMM/DAQ)   -- PXI hardware tests
7.                 -> choose 12 (Run All Tests)        -- full pass
```

---

## 9. Relay Architecture

The relay system uses a factory pattern. The rest of the application never imports a concrete relay class — it calls `RelayFactory.create(cfg)` and receives a `RelayBase` object.

### 9.1 Unified interface

```python
from config.devices import RELAY_CONFIG          # or RELAY_ETH_CONFIG
from hardware.relay_factory import RelayFactory

relay = RelayFactory.create(RELAY_CONFIG)

relay.connect()

relay.close(1)          # energize relay channel 1 (connects battery 1 to SMU)
relay.open(1)           # de-energize relay channel 1 (disconnects battery 1)
relay.open_all()        # safe state: all batteries disconnected
relay.close_all()       # connect all batteries simultaneously

state = relay.query(1)  # True = contact closed (battery connected)

relay.disconnect()      # open_all() then close socket / serial port
```

The same four lines work whether `cfg["type"]` is `"serial"` or `"ethernet"`.

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
relay = RelayFactory.create(dev_cfg.RELAY_ETH_CONFIG)
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
6. "relay on N\r\n"   -> close relay N  (wait for ">")
7. "relay off N\r\n"  -> open relay N   (wait for ">")
8. "relay read N\r\n" -> returns "on" or "off" before ">"
```

Channel addressing (1-based in API, 0-based Numato addressing):

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
    "ethernet": ("hardware.relay_eth",    "EthernetRelay"),
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

**Emergency stop sequence:**

```
1. smu.output_disable()    -- cut SMU output immediately
2. relay_matrix.open_all() -- disconnect all batteries
```

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
+-- DAQError              -- DAQ read/write failure
+-- SMUError              -- SMU communication or compliance failure
+-- SafetyViolationError  -- safety limit exceeded (triggers e-stop)
+-- NIPXITimeoutError     -- test step exceeded allowed duration
+-- ValidationError       -- config or input validation failed
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
hw = HardwareManager(Settings, relay_cfg=dev_cfg.RELAY_CONFIG)

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
relay = RelayFactory.create(dev_cfg.RELAY_ETH_CONFIG)

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
python test.py   # choose 9

# Safety monitor (offline)
python test.py   # choose 8

# Full test pass
python test.py   # choose 12
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
python test.py   # choose 11 (Test MiniSQL hooks)
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
Run "Test Configuration" (option 9) to see the specific failing parameter.

**Windows console encoding error**  
If you see `UnicodeEncodeError` on Windows, set the console to UTF-8:
```bash
chcp 65001
```
or set the environment variable: `PYTHONUTF8=1`

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
