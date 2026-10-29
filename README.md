# NIPXI — Battery Test System

Automated lithium-ion battery charge/discharge testing using a National Instruments PXI rack, the BLOSS Hub PCB, and relay matrices (Numato Ethernet in production, PXIe-2569 NI-SWITCH implemented). Logs all data to SQLite and CSV for downstream analysis in the BLOAST ML pipeline.

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
18. [System Modes](#18-system-modes)
19. [Instrument Verification Philosophy](#19-instrument-verification-philosophy)
20. [Safe Cancellation Architecture](#20-safe-cancellation-architecture)
21. [State Model](#21-state-model)
22. [Known Limitations](#22-known-limitations)

---

## 1. Project Overview

NIPXI automates capacity and calendar-aging tests on up to 8 Li-ion battery channels simultaneously.

**What it does:**

- Charges each battery to a target SOC using CC-CV (Source Measure Unit)
- Discharges each battery at a constant current (SMU acting as a current sink)
- Measures voltage (DMM), current (SMU) and temperature (NTC thermistor via DAQ) at every sample
- Enforces real-time safety limits — emergency stop on overvoltage, undervoltage, overcurrent, overtemperature
- Supervises every run (loop watchdog, relay heartbeat, free-space check, frozen-measurement detection) and drives the hardware to a verified safe state on any abnormal exit or process termination (Section 11)
- Logs all measurements to SQLite and per-channel CSV files
- Generates reports for the BLOAST ML pipeline

**Current status.** Hardware bring-up is complete for the PXI rack and the Numato matrices; the battery workflows are implemented and being validated on the bench. Full record: [docs/MILESTONES.md](docs/MILESTONES.md); remaining work: [docs/TODO.md](docs/TODO.md).

- **Validated on the physical rack:** Hardware Discovery/identification, SMU and DMM Functional Validation, both Numato Ethernet Relay Matrix units (`MATRIX_NUMATO_201`/`MATRIX_NUMATO_202`), NTC acquisition on `MAIN_DAQ`, Proto Test Execution (relay -> SMU -> DMM -> SQLite -> display, no battery; `AUX_SMU_1` -> `MAIN_DMM`), and short charge runs on a real battery in which constant current was confirmed at 100 mA (Charge status in detail: Section 22).
- **Implemented (Run Main Test):** Monitor Battery, Charge Battery, Discharge Battery, Cycle Battery, Monitor Battery Scan and NTC Group Scan, all built on one shared skeleton (`BatteryOperationSequence`). Monitor Battery, Charge Battery and Monitor Battery Scan are partially validated on batteries (Charge: constant current confirmed at 100 mA; CC to CV transition, CV hold and end-of-charge detection not yet demonstrated on a battery); Discharge Battery and Cycle Battery are implemented but not yet validated on the bench.
- **Implemented, bench validation pending:** the safety shutdown framework (Section 11): Startup Safety Sweep, shutdown ordering, process-termination handling, loop watchdog, relay heartbeat, storage protection and frozen-measurement detection. It was exercised with fake hardware only.
- **PXIe-2569 (NI-SWITCH):** driver implemented (`hardware/relay_niswitch.py`), registered in `RelayFactory`, covered by the relay conformance suite and Relay Range Validation. It has never run against the real card and is not assigned to any battery group.
- **Not implemented:** relay welded/stuck-closed detection (Section 22), `DAQ.read_all_batteries()` multi-channel acquisition, report generation (`data/report.py` placeholder), PXI chassis enumeration (`hardware/pxi_rack.py` stub).

---

## 2. Architecture Overview

```
                +--------------------------------------------------+
                |              Host PC / Control System            |
                |                                                  |
                |  test.py  (operator menus and workflows)         |
                |  main.py  (legacy TestExecutor path)             |
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
                |   PXI Chassis (config/devices.py::PXI_SLOTS)      |
                |   Slot 2:  PXIe-6363  MAIN_DAQ      (active)     |
                |   Slot 3:  PXI-4065   MAIN_DMM      (active)     |
                |   Slot 5:  PXIe-4141  PRIMARY_SMU   (active)     |
                |   Slot 6-8, 17-18: HIGH_POWER_SMU/AUX_SMU_1/2/   |
                |                    EXPANSION_DAQ/PRECISION_DAQ   |
                |                    (present, not yet channel-    |
                |                     assigned -- Section 4)       |
                |   Slot 11: PXIe-2569  CHASSIS_RELAY_MATRIX (drv) |
                |   Slot 15: PXIe-4353  TEMP_MODULE (identity only)|
                +---+----------------+-----------+----------------+
                    |                            |
              pyserial / TCP               NI-VISA
                    |
       +------------v-------------+
       |  Relay Matrix            |
       |  Ethernet: Numato 32-ch  |  <- PRODUCTION
       |  Serial:   COM13         |  <- diagnostic only
       |  NI-SWITCH: PXIe-2569    |  <- implemented, not validated
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

**test.py startup flow:**

```
install_termination_handlers()       -- SIGTERM / Ctrl+Break / console close / logoff / shutdown
Pre-flight: Configuration Validation -- validate_settings() + validate_devices_or_raise(); any FAIL blocks the menu
Pre-flight: Startup Safety Sweep     -- every configured SMU and relay matrix is connected, forced safe, verified
                                        and disconnected; a failed verification blocks startup; an unreachable
                                        device is reported PARTIAL and the menu still opens
Main Menu
```

**Operation run (Monitor / Charge / Discharge / Cycle, `BatteryOperationSequence.run_guarded()`):**

```
select group and position -> open storage (free-space gate) -> HardwareManager.connect_all()
                             (relay open_all() verified) -> pre-checks (battery / NTC presence, polarity)
close relay N (verified: only N closed) -> start SMU output
Per sample:
  1. cancellation checkpoint
  2. periodic checks: watchdog pet, relay heartbeat, free-space check
  3. measure: DMM voltage, SMU current, NTC temperature
  4. frozen-measurement check, then SafetyMonitor.check()
  5. store the sample
End (normal or abnormal): SMU OFF (verified) -> relay OPEN (verified) -> logging and cleanup
Charge ends when the DMM voltage reaches the CV target and the current has tapered to the charge cutoff (implemented; never reached on a real battery);
Discharge ends at the end-of-discharge cutoff.
```

`main.py` is the legacy `TestExecutor` path: it runs validation and the same hardware shutdown nets, but not the Startup Safety Sweep or the per-iteration supervision.

---

## 3. Directory Structure

```
nipxi/
|-- main.py                     Legacy TestExecutor entry point (validation, no startup safety sweep).
|-- test.py                     Operator entry point: menus, validation, diagnostics, all workflows.
|-- requirements.txt
|
|-- config/
|   |-- settings.py             Tunable parameters (voltages, currents, timeouts, safety supervision).
|   |-- devices.py              SINGLE SOURCE OF TRUTH for every device: PXI slots, relay IP/COM
|   |                           port, channel mapping, battery groups. See Section 17.
|   +-- system_mode.py          DEVELOPMENT / VALIDATION / PRODUCTION policy. See Section 18.
|
|-- hardware/                   One class per physical device. All inherit HardwareBase.
|   |-- base.py                 Abstract base: connect(), disconnect(), context manager.
|   |-- relay.py                RelayBase contract (settle delay, fail-closed defaults).
|   |-- relay_factory.py        RelayFactory.create(cfg) -> RelayBase (serial / ethernet / niswitch).
|   |-- relay_eth.py            PRODUCTION relay: Numato 32-ch Ethernet (NumatoRelayMatrix).
|   |-- relay_niswitch.py       NI PXIe-2569 relay matrix (NIPXIe2569RelayMatrix), same safety model.
|   |-- relay_serial.py         Serial relay (COM13) -- diagnostic only.
|   |-- relay_matrix.py         Dead/legacy code -- not imported or wired into RelayFactory.
|   |-- smu.py                  SMU/PSU driver: connect/identify, charge/discharge setpoints, output
|   |                           enable/disable, measure, verified shutdown.
|   |-- dmm.py                  DMM driver (identify, DC voltage measurement).
|   |-- daq.py                  DAQ driver (identify, read_channel; read_all_batteries is a placeholder).
|   |-- temperature.py          NTC thermistor voltage-to-Celsius conversion.
|   |-- sense_router.py         Battery sense-routing abstraction.
|   |-- audit_proxy.py          Automatic raw_hardware_log instrumentation of driver calls.
|   |-- simulated.py            Simulation extension points (foundations only).
|   +-- pxi_rack.py             PXI chassis enumeration stub.
|
|-- test_control/               Workflow logic.
|   |-- hardware_manager.py     Device lifecycle (connect_all/disconnect_all/health_check, is_safe,
|   |                           emergency_shutdown, atexit nets).
|   |-- safety_monitor.py       Per-sample limit checks, emergency_stop(), safe_cancel_shutdown().
|   |-- safety_sweep.py         Startup Safety Sweep.
|   |-- battery_operation_sequence.py  Shared run skeleton: run_guarded(), shutdown ordering,
|   |                           periodic checks (watchdog, heartbeat, storage, frozen signal).
|   |-- monitor_battery_sequence.py / monitor_battery_scan_sequence.py
|   |-- charge_sequence.py / discharge_sequence.py / cycle_sequence.py
|   |-- proto_test_sequence.py  Infrastructure validation, no battery.
|   |-- storage_guard.py        Free-space supervision.   frozen_signal.py  Frozen-signal detector.
|   |-- storage_session.py      Guarded DataStorage lifecycle (start gate).
|   |-- battery_presence_precheck.py / ntc_snapshot.py / battery_diagnostics.py
|   |-- execution_screen.py     Shared runtime UI (ExecutionFrame). run_summary_report.py  Run summary.
|   |-- diagnostic_audit.py     Audit instrumentation for test.py diagnostics.
|   |-- test_executor.py / result_manager.py / battery_test.py / charge_cycle.py / discharge_cycle.py /
|   |   state_machine.py        Legacy main.py path.
|
|-- orchestration/              Multi-group concurrent execution (per-group workers, resource ownership,
|                               fault bus, global emergency stop). See docs/architecture.md.
|
|-- data/                       Persistence layer.
|   |-- storage.py              DataStorage (SQLite) + StorageBackend ABC for the MiniSQL swap.
|   |-- rotation.py             Index database + monthly telemetry database path resolution.
|   |-- raw_hardware_log.py     Hardware audit trail (never raises, disables itself on failure).
|   |-- test_execution_log.py   History of test.py diagnostic executions.
|   |-- sqlite_manager.py       Minimal SQLite foundation.   logger.py  Logging setup.
|   +-- report.py               Report generation placeholder.
|
|-- utils/
|   |-- errors.py               Exception hierarchy (Section 12).
|   |-- cancellation.py         CancellationToken, SIGINT handler, cancel_all_tokens().
|   |-- termination.py          SIGTERM / Ctrl+Break / console close / logoff / shutdown handling.
|   |-- watchdog.py             Loop watchdog.
|   |-- safety_fault.py        Safety-fault persistence and operator acknowledgement.
|   |-- validators.py / device_validator.py   Settings and device startup validation.
|   |-- menu_status.py          Menu/pre-flight status colors.   stop_reason.py  StopReason vocabulary.
|   |-- group_hardware.py / event_format.py / constants.py / helpers.py
|   +-- ethernet_relay_python.py  Numato reference script (read-only, not imported).
|
|-- scripts/                    export_run.py (forensic JSON export of one run), backfill_run_sequence.py.
|-- tests/                      Unit and integration tests (relay conformance suite, safety shutdown,
|                               startup sweep, range validation, ...).
|
+-- docs/
    |-- architecture.md         SOURCE OF TRUTH: system design, safety model (Section 85).
    |-- CONFIGURATION.md        SOURCE OF TRUTH: configuration reference.
    |-- TODO.md                 Remaining work.
    |-- MILESTONES.md           Milestone record.
    |-- FAQ.md                  Design Q&A.
    |-- DATABASE_ROADMAP.md     Future database/recovery architecture.
    |-- TIMING_ANALYSIS.md      Relay/test timing analysis.
    +-- *_REVIEW.md             Historical review documents (not the source of truth).
```

---

## 4. Supported Hardware

Confirmed against the real PXI rack (NI-MAX detection) — `config/devices.py::PXI_SLOTS` is the single source of truth; this table is a hand-maintained snapshot for reference, not generated from code, and not where to edit resource strings. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full per-slot metadata (role, driver family, enabled flag, validation notes), and its "Hardware Replacement Procedure" section (Section 17.3a here) before swapping a card — most changes need `PXI_SLOTS` only, but renaming the DAQ/DMM nickname or changing the DAQ's NI-MAX alias needs one additional edit.

| Role | Model | Slot | Nickname | Interface | Library | Driver |
|------|-------|------|----------|-----------|---------|--------|
| DAQ (primary) | PXIe-6363 | 2 | `MAIN_DAQ` | NI-DAQmx | `nidaqmx` | `hardware/daq.py::DAQ` (connect/identify/read_channel real; multi-channel read_all_batteries still TODO) |
| DMM | PXI-4065 | 3 | `MAIN_DMM` | NI-DMM | `nidmm` | `hardware/dmm.py::DMM` (connect/identify/DC voltage measurement real) |
| SMU / PSU (primary) | PXIe-4141 | 5 | `PRIMARY_SMU` | NI-DCPower | `nidcpower` | `hardware/smu.py::SMU` (connect/identify, charge/discharge setpoints, output enable/disable, measure and verified PMU shutdown are real) — the one SMU `HardwareManager` actually drives |
| SMU (high power, present, not yet wired to a channel) | PXIe-4139 | 6 | `HIGH_POWER_SMU` | NI-DCPower | `nidcpower` | same `SMU` class, second `SMU_ASSIGNMENTS` entry |
| SMU (auxiliary, present, not yet wired to a battery channel) | PXI-4130 | 7 | `AUX_SMU_1` | NI-DCPower | `nidcpower` | same `SMU` class -- NI-DCPower channel `"1"` (confirmed on physical hardware, see `smu_channel` below) |
| SMU (auxiliary, present, not yet wired to a battery channel) | PXI-4130 | 8 | `AUX_SMU_2` | NI-DCPower | `nidcpower` | same `SMU` class -- NI-DCPower channel `"1"` (confirmed on physical hardware, see `smu_channel` below) |
| Relay/switch (100 SPST; driver implemented, not assigned to any group, never run on the real card) | PXIe-2569 | 11 | `CHASSIS_RELAY_MATRIX` | NI-SWITCH | `niswitch` | `hardware/relay_niswitch.py::NIPXIe2569RelayMatrix` (relay type `niswitch`); requires the `niswitch` package, not installed on the dev PC. The Numato Ethernet relay remains the production path |
| Temperature module (present, not yet wired into any driver) | PXIe-4353 + TB-4353/0 | 15 | `TEMP_MODULE` | NI-DAQmx | `nidaqmx` | none yet — likely real source for the currently-stubbed per-channel `t_c` reading in `charge_cycle.py`/`discharge_cycle.py` |
| DAQ (expansion, present, not wired into `HardwareManager`) | PXIe-6368 | 17 | `EXPANSION_DAQ` | NI-DAQmx | `nidaqmx` | same `DAQ` class, second `DAQ_CONFIGS` entry |
| DAQ (precision, present, not wired into `HardwareManager`) | PXIe-6365 | 18 | `PRECISION_DAQ` | NI-DAQmx | `nidaqmx` | same `DAQ` class, third `DAQ_CONFIGS` entry |
| GPIB instrument (unconfirmed) | — | GPIB0 (NI-488.2) | `UNCONFIRMED_GPIB_INSTRUMENT` | GPIB | — | none — likely candidate for the electronic load/power supply in `equipment_Requirement.md`, model not yet confirmed |
| Relay (Ethernet) | **Numato Lab 32-Channel Ethernet Relay Module (RELAY32ETHRL00) — PRODUCTION** | — (Ethernet, not a PXI slot) | `MATRIX_NUMATO_201`, `MATRIX_NUMATO_202` | TCP/Telnet | stdlib `socket` | `hardware/relay_eth.py::NumatoRelayMatrix` |
| Relay (serial, COM13) | Diagnostic only — NOT the production control path | — | `MAIN_MATRIX` | pyserial | `pyserial` | `hardware/relay_serial.py::SerialRelay` |
| Battery Hub PCB | BLOSS Hub Rev A | — | — | — | — | — |

**Production relay hardware (validated):** Numato Lab 32 Channel Ethernet Relay Module, reachable over Ethernet/Telnet — ping, web interface, Telnet login, relay commands, and relay state readback have all been confirmed working. Validated settings:

| Setting | MATRIX_NUMATO_201 | MATRIX_NUMATO_202 |
|---------|-------------------|--------------------|
| IP address | `169.254.1.201` (static, DHCP off) | `169.254.1.202` (static, DHCP off) |
| Port | `23` | `23` |
| Username | `admin` | `admin` |
| Password | `admin` | `admin` |

Production architecture path: `test.py` workflows (and the legacy `main.py`) `-> HardwareManager -> RelayFactory -> NumatoRelayMatrix -> Numato Relay`.

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

# 6. Run the application (legacy TestExecutor path: no startup safety sweep and none of the
#    run supervision in architecture.md Section 85 -- operational workflows run from test.py)
python main.py
python main.py --channels 1 2 3   # test only channels 1, 2, 3
python main.py --dry-run           # no hardware, config validation only
```

**First-time checklist:**

- [ ] Set `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` resource strings in `config/devices.py` to match NI-MAX
- [ ] Set `NUMATO_RELAY_MATRIX_CONFIG["ip"]` in `config/devices.py` if using Ethernet relay
- [ ] Fill in `command_open/close/query` in `config/devices.py` RELAY_CONFIG for serial relay (diagnostic only)
- [ ] Confirm `BATTERY_CHANNELS` channel numbers match your physical wiring
- [ ] Run `python test.py`: Configuration Validation (Settings and config/devices.py) and the Startup Safety Sweep run automatically before the menu; fix any FAIL
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

`PXI_SLOTS` is the single source of truth for every PXI-slot resource string/
model -- confirmed against the real rack (NI-MAX detection). `SMU_ASSIGNMENTS`/
`DAQ_CONFIG(S)`/`DMM_CONFIG(S)` are **derived** from it by `category`; edit
`PXI_SLOTS`, not those dicts, when hardware changes:

```python
PXI_SLOTS = {
    5: {
        "slot": 5, "resource": "PXI1Slot5", "model": "PXIe-4141",
        "nickname": "PRIMARY_SMU", "driver_family": "nidcpower",
        "category": "smu", "role": "Primary SMU -- drives all 8 channels.",
        "enabled": True, "channels": list(range(1, 9)),
        "validation_notes": "...",
    },
    # ... one entry per real slot (MAIN_DAQ, MAIN_DMM, HIGH_POWER_SMU,
    # AUX_SMU_1/2, CHASSIS_RELAY_MATRIX, TEMP_MODULE, EXPANSION_DAQ,
    # PRECISION_DAQ) -- see config/devices.py for the full, current
    # inventory and docs/CONFIGURATION.md for the full table.
}

# Derived (do not hand-edit these -- edit PXI_SLOTS above instead):
SMU_ASSIGNMENTS = { ... }   # every category="smu" slot, by nickname
DAQ_CONFIG      = DAQ_CONFIGS["MAIN_DAQ"]
DMM_CONFIG      = DMM_CONFIGS["MAIN_DMM"]
```

`hardware/smu.py::SMU`, `hardware/daq.py::DAQ`, and `hardware/dmm.py::DMM` are each constructed directly from one of the derived dicts' entries (`SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)`) -- there is exactly one driver class per type, so (unlike relay) no factory/type-dispatch is needed; construction from the config dict already IS the "factory step" for these types. `HardwareManager` still only ever connects ONE SMU (`next(iter(SMU_ASSIGNMENTS.values()))`, currently `PRIMARY_SMU`) and ONE DAQ (`DAQ_CONFIG`) for the active battery test sequence -- the other real SMUs/DAQs in the rack are present and individually testable via `test.py`, but multi-device channel assignment is a future scaling task.

**`smu_channel` -- config-driven NI-DCPower channel selection:** every `PXI_SLOTS` SMU entry also carries `"smu_channel"` (the NI-DCPower channel *name* string opened for that instance, e.g. `"0"`/`"1"`) and `"channels_per_card"` (the card's physical NI-DCPower channel count -- `1` for the single-channel `PRIMARY_SMU`/`HIGH_POWER_SMU`, `2` for the two-channel `AUX_SMU_1`/`AUX_SMU_2` PXI-4130 units). `hardware/smu.py::SMU.connect()` opens its `nidcpower.Session` scoped to exactly that one channel (`channels=self._channel`, read from config, never hardcoded) -- this is what lets the same driver code work for both single- and multi-channel cards, and is what fixed a real rack bring-up failure: an unscoped session on a multi-channel PXI-4130 raises NI-DCPower error `-1074118522` ("the requested function only allows a single channel to be specified") the moment any repeated-capability property (`voltage_level`, `output_enabled`, `measure()`, etc.) is set. Confirmed on physical hardware: `AUX_SMU_1` and `AUX_SMU_2` are both wired to NI-DCPower channel `"1"`.

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

**Ethernet relay configuration (Numato Lab 32-Channel Ethernet Relay Module -- PRODUCTION):** two units, static IPs, DHCP disabled on both (finalized NIPXI network plan). Devices are named after their static IP's last octet (`MATRIX_NUMATO_<octet>`) for easier hardware ID/troubleshooting/rack labeling. `NUMATO_RELAY_MATRIX_CONFIG`, `MAIN_MATRIX_ETH`, and `AUX_MATRIX_ETH_1` are kept as backward-compat aliases (`MAIN_MATRIX_ETH`/`NUMATO_RELAY_MATRIX_CONFIG` -> `MATRIX_NUMATO_201`, `AUX_MATRIX_ETH_1` -> `MATRIX_NUMATO_202`) for any legacy code that still imports the old names.

```python
ETHERNET_DEVICES = {
    # Numato Relay Matrix at 169.254.1.201
    "MATRIX_NUMATO_201": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_201",
        "ip":            "169.254.1.201",
        "port":          23,              # default Numato Telnet port
        "username":      "admin",
        "password":      "admin",
        "timeout":       5.0,
        "num_channels":  Settings.RELAY_COUNT,   # single source of truth -- never hardcode 32
        "channel_count": Settings.RELAY_COUNT,
    },
    # Numato Relay Matrix at 169.254.1.202
    "MATRIX_NUMATO_202": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_202",
        "ip":            "169.254.1.202",
        "port":          23,
        "username":      "admin",
        "password":      "admin",
        "timeout":       5.0,
        "num_channels":  Settings.RELAY_COUNT,
        "channel_count": Settings.RELAY_COUNT,
    },
}
```

**Battery type catalog** (`BATTERY_CONFIGS` -- physical battery specs, independent of which channel a battery occupies; foundation for the future `data/battery_repository.py`, see `docs/DATABASE_ROADMAP.md`; **not yet wired into `safety_monitor.py`/`charge_cycle.py`**, which still use the single global `BAT_VOLTAGE_MAX`/etc. from `config/settings.py` for every channel):

```python
BATTERY_CONFIGS = {
    "GENERIC_LIION_18650": {
        "chemistry":               "Li-ion",
        "form_factor":             "18650",
        "nominal_voltage_v":       3.7,
        "voltage_max_v":           4.2,
        "voltage_min_v":           3.0,
        "capacity_ah":             2.5,
        "max_charge_current_a":    1.25,
        "max_discharge_current_a": 2.5,
        "max_temp_c":              45.0,
    },
}
```

**Battery channel map:**

Each channel entry maps a logical battery index (1-8) to its physical wiring, plus which `BATTERY_CONFIGS` entry is currently installed there:

```python
BATTERY_CHANNELS = {
    1: {
        "relay_address":   1,           # relay matrix channel number
        "daq_voltage_ch":  "Dev1/ai0",  # NI 6363 analog input for battery voltage
        "daq_current_ch":  "Dev1/ai8",  # analog input for current (via shunt)
        "daq_ntc_ch":      "Dev1/ai16", # analog input for NTC thermistor voltage
        "fuse_rating_a":   2.0,
        "battery_type":    "GENERIC_LIION_18650",  # key into BATTERY_CONFIGS
    },
    # channels 2-8 follow the same pattern
}
```

Update these channel strings to match your actual PCB connector-to-DAQ wiring, and `battery_type` whenever a different battery is installed in a channel -- `utils/device_validator.py` validates it references a real `BATTERY_CONFIGS` key at startup.

---

## 8. Testing Framework

`test.py` is the primary commissioning and diagnostic tool. It does not require hardware drivers to be complete — it tests configuration, imports, and interfaces offline, then attempts real hardware connections.

**Launch:**

```bash
python test.py
```

**Startup flow (before the menu is shown):**

1. Termination handlers are installed (Section 11).
2. **Pre-flight Configuration Validation** (`preflight_check()`): `test_configuration()` (Settings) and `test_device_validation()` (`config/devices.py`, construction only, no hardware I/O). Any FAIL blocks the menu; fix `config/settings.py` or `config/devices.py` and restart.
3. **Startup Safety Sweep** (`test_control/safety_sweep.py`): every configured SMU (`SMU_ASSIGNMENTS`) and every relay matrix (`RELAY_MATRIX_CONFIGS`: Numato units and the PXIe-2569) is connected, forced safe (SMU output OFF verified, relays all open verified) and disconnected. A device that connects but cannot be verified safe shows the SAFETY FAULT screen, needs operator acknowledgement and blocks startup in every system mode. A device that cannot be reached is logged and the sweep reports PARTIAL; the menu still opens, in every system mode including PRODUCTION (Section 22).

**Menu:**

```
  1. Run Main Test                -- Monitor Battery / Charge / Discharge / Cycle / Monitor Battery Scan /
                                     NTC Group Scan (submenu)
  2. Proto Test Execution         -- infrastructure validation, no battery
  3. Startup Device Validation    -- config/devices.py only, no hardware I/O (also runs before the menu)
  4. Hardware Discovery           -- connectivity + identification of every configured device (Section 8.1)
  5. Test SMU (PSU)               -- select device -> Identity Validation / Functional Validation
  6. Test DMM                     -- select device -> Identity Validation / Functional Validation
  7. Test DAQ                     -- select device -> Identity Validation / Functional Validation
  8. Test Numato Relay Matrix (Ethernet) -- select device -> Identity / Functional Validation
  9. Test PXI Relay Matrix        -- PXIe-2569: Identity reports N/A (no identity query);
                                     Functional Validation runs Relay Range Validation
 10. Test Sensors (NTC)           -- NTC conversion math + real DAQ NTC scan
 11. Test Safety Monitor          -- SafetyMonitor logic tests + workflow simulator
 12. Database Tools               -- inspect the real database; temp-DB self-tests
 13. Last Test Summary            -- summary of the most recent run
 14. UI Test                      -- demo execution screens, no hardware, no database
  0. Exit
```

Menu colors (status words only) reflect validation status; the classification lives in `test.py::MENU_STATUS`.

Every hardware-category item (5-9) uses the same two-level workflow, described in
Section 8.1a: pick a configured device, then pick **Identity Validation** or
**Functional Validation** for that device only. The configuration test, the Electronic Load/MiniSQL stubs,
the bench-only serial relay and the flat Numato relay commissioning tests are not operator-facing menu
items (the underlying code is unchanged and still reachable from `test.py` -- see Section 8.1b).

### 8.1 Hardware Discovery (`test_hardware_discovery()`)

A hardware **presence** test, not a measurement test, not a battery-workflow test, and not an instrument-accuracy test. For every device configured it validates:

- the device exists in configuration (enumeration)
- the device was discovered correctly
- the driver loaded correctly (import / factory)
- the communication channel opened correctly (connect)
- the instrument responds correctly (self-test / login)
- instrument identification succeeds (identity query)
- **the identity the instrument reports matches the configured model** (new -- a mismatch is reported `WARNING`, not silently accepted)

**Config-driven only, grouped by category from `config/devices.py::PXI_SLOTS`** (`smu`/`dmm`/`daq`/`temperature`), plus the non-PXI-slot Numato/serial relay dicts and `GPIB_INSTRUMENTS`; nothing is hardcoded (no resource strings, IPs, or COM ports), so adding or removing a `PXI_SLOTS` entry changes coverage automatically with no code change. Output is grouped exactly by category:

```
DMM Devices
-----------
Slot 3
PXI-4065
MAIN_DMM

SMU Devices
-----------
Slot 5
PXIe-4141
PRIMARY_SMU

Slot 6
PXIe-4139
HIGH_POWER_SMU
```

Categories with no identity query (the PXIe-2569 `CHASSIS_RELAY_MATRIX`, which has a relay driver but no identity check; any GPIB instrument, which has no driver) are reported **N/A**, never faked as a real check -- see [Section 4](#4-supported-hardware).

**Uses the same production driver classes as HardwareManager -- no duplicated connection logic, no instrument-specific communication code of its own.** Every `_identify_*()` helper in `test.py` does exactly `driver = DriverClass(cfg); driver.connect(); driver.identify(); driver.disconnect()` and nothing else -- it never imports `nidcpower`/`nidmm`/`nidaqmx`/`pyserial` directly. `test_smu()`/`test_dmm()`/`test_daq()`/`test_temperature_module()` (the deeper per-device menu tests) call the same driver classes for their connect/identify step too, so there is exactly one connect()/identify() implementation per device type, not two:

| Type | Driver class | Connect | Identify | Never does |
|------|--------------|---------|----------|-------------|
| SMU / PSU | `hardware.smu.SMU` | `smu.connect()` (nidcpower session, inside the driver) | `smu.identify()` -> `instrument_model` | `output_enable()`, `set_charge_mode()`, source V/I |
| DMM | `hardware.dmm.DMM` | `dmm.connect()` (nidmm session, inside the driver) | `dmm.identify()` -> `instrument_model` | trigger/read a measurement |
| DAQ | `hardware.daq.DAQ` | `daq.connect()` (NI-DAQmx device enumeration, inside the driver) | `daq.identify()` -> `product_type` + `self_test_device()` | create a task, read any channel |
| Temperature Module | `hardware.daq.DAQ` (reused -- PXIe-4353 is NI-DAQmx-family too) | same as DAQ | same as DAQ | configure/read any TC/RTD channel |
| Relay (Ethernet) | `hardware.relay_eth.NumatoRelayMatrix` (via `RelayFactory`) | `relay.connect()` (TCP + Telnet login) | the driver's own post-login `relay readall` verification | write to any relay channel (`connect()` is read-only) |
| Relay (Serial) | `hardware.relay_serial.SerialRelay` (via `RelayFactory`) | `relay.connect()` (opens the COM port) | *(no identity command -- no custom protocol is invented; port-open success is the pass criterion)* | send any relay command |

Note: there is no separate PSU hardware/config in this project -- the NI SMU is the PSU (see the "Test SMU (PSU)" menu label), so the SMU/PSU row above covers both.

`RelayEthernetTest` and Hardware Discovery's Ethernet-relay row both go through `RelayFactory.create(NUMATO_RELAY_MATRIX_CONFIG)`, i.e. the same `NumatoRelayMatrix` instance type -- they never diverge onto a second relay implementation.

### 8.1a Device Selection Workflow -- Identity vs Functional Validation

**The current bring-up goal is hardware identification and readiness, not functional testing.** Every hardware-category menu item (SMU, DMM, DAQ, Temperature Module, Numato Relay Matrix, PXI Relay Matrix) is driven by one shared helper, `test.py::_run_hardware_category()`, so the workflow is identical everywhere and never duplicated per category:

```
1. List every device configured for this category in config/devices.py:

    SMU

    [1] PRIMARY_SMU
    [2] HIGH_POWER_SMU
    [3] AUX_SMU_1
    [4] AUX_SMU_2
    0. Back

    Select device:

2. Second-level menu, for the ONE device just selected:

    PRIMARY_SMU

    [1] Identity Validation
    [2] Functional Validation
    [0] Back

    Choice:
```

Selecting a device only ever touches that one device -- selecting `PRIMARY_SMU` never reads or writes `HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2`, and selecting a DMM never touches any SMU, DAQ, or relay.

**Identity Validation** (`_identify_smu()`/`_identify_dmm()`/`_identify_daq()`/`_identify_temperature()`/`_identify_relay_eth()`/`_identify_switch()` -- the SAME functions Hardware Discovery uses, so this menu path can never drift from what Hardware Discovery reports) opens a driver session, verifies the configured resource exists, verifies communication, reads device identity/model/serial where supported, verifies the detected model matches `config/devices.py`, and confirms the device is ready for the next validation stage. It **never** enables an output, sources voltage/current, closes a relay, or performs any other state-changing action -- see Section 19 (Instrument Verification Philosophy). A PASS here means: *the device is present, correctly identified, reachable, and ready for the next validation stage* -- exactly what's needed to validate hardware bring-up remotely over RDP before going to the lab.

**Functional Validation** is intentionally a separate, later phase (see Section 8.1b) -- it is the only path that ever changes hardware state (SMU sourcing, a DMM/DAQ real measurement, or a Numato relay energizing a channel). Where no functional test exists yet (Temperature Module TC/RTD read), the menu reports "Functional Validation not yet implemented for this hardware category" rather than faking a PASS.

**SMU and DMM Functional Validation are laboratory-only** -- the operator must be physically present at the rack (SMU: connecting a handheld DMM to the SMU output; DMM: connecting a known external DC source to the DMM input). Both print explicit on-screen instructions and pause for the operator before/at each step -- see Section 8.1b.

### 8.1b Functional Validation

Functional Validation is not mixed into Identity Validation. Each category's Functional Validation option runs one focused check:

- **SMU -> Functional Validation**: `_functional_smu()` -- verifies the SMU can source DC voltage correctly, using a sequence that reflects how the SMU is actually used in NIPXI (charging: source voltage + source current; discharging: source voltage + **sink** current -- never a negative source voltage, see the callout below), not a generic bipolar power-supply check. Laboratory-only: the operator connects a handheld DMM to the SMU output and visually confirms each step. Sequence: safe state (output forced off + verified) -> 0 V (baseline) -> charge validation voltage -> 0 V (return to baseline) -> output OFF (forced + verified again), via `hardware/smu.py::SMU.source_dc_voltage_point()`. This is NOT a battery operation -- no relay, no battery channel, no charge/discharge mode (`set_charge_mode()`/`set_discharge_mode()` remain untouched placeholders). Every step, FAIL, or operator cancellation (Ctrl+C / blank input) ends with `SMU.emergency_output_off()` -- the operator is never left with an energized output. Validation voltage/current/range are derived entirely from existing configuration (see "Configuration dependencies" below) -- no new hardcoded voltage/current constants.
- **DMM -> Functional Validation**: `_functional_dmm()` -- verifies the DMM can acquire a DC voltage measurement. Laboratory-only: the operator connects a known external DC source (bench supply, calibrator, etc.) to the DMM input, then the DMM performs a real measurement (`DMM.measure_dc_voltage()`), verified finite and within the configured range, and the "Measured Voltage" is displayed. First-implementation scope only: no current measurement, no calibration validation, no accuracy certification, no automated metrology limits -- purely "can the DMM successfully perform a voltage measurement?"
- **DAQ -> Functional Validation**: `_functional_daq()` -- a real deep channel read via `hardware/daq.py::DAQ.read_channel()`, verified finite and within the configured ADC range.
- **Numato Relay Matrix -> Functional Validation**: `_functional_relay_numato()` -- a submenu of the existing relay-energizing tests (`test_relay_numato_matrix()` relay-1 quick check, `test_relay_matrix_scan()` full channel scan, `test_relay_ethernet_test()` native-primitive test, `test_relay_safety_selftest()` mandatory-sequence self-test), each of which can still be called standalone (no arguments) for scripted/CI use.
- **PXI Relay Matrix (PXIe-2569) -> Functional Validation**: `_functional_switch()` -- runs **Relay Range Validation** through `NIPXIe2569RelayMatrix`, the same validation the Numato matrices have. Identity Validation reports N/A for this card (no identity query exists). If the PXI slot is not enabled as a relay matrix, Functional Validation reports N/A and never touches hardware.
- **Relay Range Validation** (`test_relay_range_validation()`, available for every relay matrix, driver-agnostic): for each relay N in an operator-chosen range -- force all open, verify (independent `closed_channels()` readback), close N, verify N is the only closed relay, dwell `RELAY_RANGE_VALIDATION_DWELL_S` (2 s, the operator watches/listens for relay N), open, verify all open. It stops at the first failure, always ends with a verified `open_all()` (also on Ctrl+C), and logs every driver call to `raw_hardware_log`. It is also the bench check for the PXIe-2569 channel-N to relay-N mapping.
- **Relay conformance suite** (`tests/_relay_conformance.py`, run by `tests/test_relay_conformance.py`; offline, no hardware): the behavioral contract every `RelayBase` driver must satisfy (verified all-open, exclusive select, `close_all()` fails closed, settle after every switch, stuck-relay fault injection), run against a simulated driver, `NumatoRelayMatrix` over an in-memory firmware model and `NIPXIe2569RelayMatrix` over a faked NI-SWITCH session. A new relay driver must pass it before use.

The bench-only serial relay (`test_relay_serial()`) and the GPIB/MiniSQL stubs (`test_electronic_load()`, `test_minisql()`) are no longer in the operator-facing menu (out of scope for the current NIPXI bring-up stage -- see Section 4/15), but their code is unchanged and importable directly from `test.py` if needed later.

**Charging/discharging architecture (why there is no negative-voltage step):** in NIPXI, charging sources voltage and sources current; discharging sources voltage and **sinks** current (the SMU acts as a current sink, not as a negative-voltage source -- see README Section 1's project overview and `hardware/smu.py::set_discharge_mode()`'s docstring, "Configure CC discharge (sink)"). The system never relies on negative-voltage sourcing for discharge. SMU Functional Validation therefore only exercises the polarity the real charge path (`set_charge_mode()`) will actually use.

**Configuration dependencies (SMU Functional Validation) -- no new configuration was introduced:**

| Value | Source | Why reused |
|---|---|---|
| Validation voltage | `Settings.CHARGE_VOLTAGE_V` (4.2 V today) | Already the project's configured real CV-phase charge target -- the same setpoint the real charge path is meant to use, more representative of production behavior than an arbitrary bench value |
| Current limit (compliance) | `Settings.CHARGE_CURRENT_A` (0.5 A today) | Already the configured real charge current for this system |
| Voltage source range | `Settings.BAT_VOLTAGE_MAX` (4.7 V today) | Already the station-level absolute voltage safety ceiling -- bounds the SMU's source range to the same limit the rest of the safety architecture already enforces |

**DMM Functional Validation** reuses `DMM_CONFIGS[...]["range_v"]` (already derived from `PXI_SLOTS`) for its finite/in-range sanity check -- no separate constant.

**Result format:**

Each test step prints:

```
  [PASS] Device or component name
         Config : config/devices.py -> ETHERNET_DEVICES["MATRIX_NUMATO_201"] (RELAY32ETHRL00 / 169.254.1.201:23)
         Detail : Connected to RELAY32ETHRL00 at 169.254.1.201:23
```

or on failure:

```
  [FAIL] MATRIX_NUMATO_201
         Config : config/devices.py -> ETHERNET_DEVICES["MATRIX_NUMATO_201"] (RELAY32ETHRL00 / 169.254.1.201:23)
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
1. python test.py                          -- Configuration Validation and the Startup Safety Sweep
                                               run automatically before the menu is shown
2.  -> choose 3  (Startup Device Validation) -- re-run explicitly / inspect in isolation
3.  -> choose 4  (Hardware Discovery)        -- connectivity + identification, every device
4.  -> choose 5-9 (SMU/DMM/DAQ/Numato Relay/PXI Relay Matrix) -> Identity Validation --
                                                 remote/RDP bring-up: confidence the correct
                                                 devices are present and reachable
5.  -> choose 11 (Test Safety Monitor), 10 (Test Sensors), 12 (Database Tools) -- offline checks
6.  -> in the lab: choose 5, 6, 7, 8 -> Functional Validation (Section 8.1b) -- the first hardware
                                                 state changes (sourcing, measurements, relay energizing)
7.  -> choose 9 (PXI Relay Matrix) -> Functional Validation -- Relay Range Validation on the PXIe-2569
8.  -> choose 2  (Proto Test Execution)      -- infrastructure end-to-end, no battery
9.  -> choose 1  (Run Main Test)             -- Monitor Battery first, then Charge / Discharge;
                                                 Ctrl+C cancels safely (Section 20)
```

(Menu numbers match the current `MENU` list in `test.py` -- verify against `python test.py` if this ever drifts, since menu items are occasionally inserted.)

---

## 9. Relay Architecture

The relay system uses a factory pattern. The rest of the application never imports a concrete relay class — it calls `RelayFactory.create(cfg)` and receives a `RelayBase` object. `config/devices.py` is the single source of truth for device discovery: `NUMATO_RELAY_MATRIX_CONFIG`/`NUMATO_RELAY_MATRIX_CONFIGS`, `DAQ_CONFIG`/`DAQ_CONFIGS`, `SMU_ASSIGNMENTS`, and `DMM_CONFIG`/`DMM_CONFIGS` are all enumerated the same way, so `test.py` and any future device type never need code changes to be discovered — only a new entry in `config/devices.py`.

**Production hardware is the Numato Lab 32-Channel Ethernet Relay Module over Ethernet/Telnet** (`hardware/relay_eth.py::NumatoRelayMatrix`, `NUMATO_RELAY_MATRIX_CONFIG`). The serial COM13 path (`hardware/relay_serial.py::SerialRelay`, `RELAY_CONFIG`) is diagnostic-only and is not the production control path — see [Section 4](#4-supported-hardware).

**Naming:** the driver class and config dicts were previously named `EthernetRelay`/`RELAY_ETH_CONFIG`/`RELAY_ETH_CONFIGS` — generic names that didn't make clear this is specifically Numato hardware, not a vendor-neutral Ethernet relay. They are now `NumatoRelayMatrix`/`NUMATO_RELAY_MATRIX_CONFIG`/`NUMATO_RELAY_MATRIX_CONFIGS`; the old names are kept as backward-compat aliases (`EthernetRelay = NumatoRelayMatrix`, etc. — see `hardware/relay_eth.py` and `config/devices.py`) so nothing that references them by the old name breaks. `"type": "ethernet"` (the `RelayFactory` dispatch key) is unchanged -- it names the transport interface, the same way `"serial"` does, not a vendor.

**PXIe-2569 (NI-SWITCH).** `hardware/relay_niswitch.py::NIPXIe2569RelayMatrix` (relay type `niswitch`, 100 SPST relays, PXI slot 11) implements the same `RelayBase` contract and safety sequence as the Numato driver. It is enabled in `config/devices.py::PXI_SLOTS[11]` and listed in `RELAY_MATRIX_CONFIGS`, but it is not assigned to any `BATTERY_GROUPS` entry, has never run against the real card, and requires the `niswitch` package, which is not installed on the development PC. Every driver must pass the relay conformance suite (`tests/_relay_conformance.py`, run by `tests/test_relay_conformance.py`). Hardware check: Test PXI Relay Matrix -> Functional Validation (Relay Range Validation: for each relay in an operator-chosen range, force open, verify, close, verify only that relay closed, dwell `RELAY_RANGE_VALIDATION_DWELL_S`, open, verify). Details: `docs/architecture.md` Sections 14.4 and 85.8.

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

**SAFETY FAULT now also covers a failed SMU shutdown-verification** (`emergency_output_off()`/`verify_output_disabled()`/`force_output_off_and_verify()` returning `False`) — previously only logged CRITICAL, this is now escalated, persisted (`event_log`/`raw_hardware_log`, `EventType.SAFETY_FAULT_RAISED`/`SAFETY_FAULT_ACKNOWLEDGED`), shown on a console SAFETY FAULT screen the operator must acknowledge, and (for an in-run failure) raised as `SMUStateVerificationError` so it is classified `STATION_FAULT` and aborts Group -> ALL exactly like a relay fault does. Same policy also runs as a Startup Safety Sweep (every configured SMU/relay matrix, before the Main Menu is ever shown) and a Post-Workflow Safety Sweep (`HardwareManager.disconnect_all()`, before returning to the Main Menu). See `docs/architecture.md` Section 82 "Safety Fault Lifecycle" for the full design.

### 9.4b Relay validation tests

Two functions validate the relay driver at its two API layers, both config-driven (`RELAY_COUNT`, never hardcoded) and both stopping immediately on the first failure. Both are Functional Validation (they energize real relay channels), reached from `test.py`'s "Test Numato Relay Matrix (Ethernet)" menu item -> Functional Validation submenu (Section 8.1b) -- not separate top-level menu items:

**RelayEthernetTest (native primitives, stop on first failure)** (`test_relay_ethernet_test()`) exercises the native Numato primitives directly, using Numato's own 0-based relay numbering -- this is the lower-level validation target, meant to be run *before* relay usage is integrated into higher-level battery workflows:

```
For relay_index in range(RELAY_COUNT):
    write_all(0)          -> read_all() -> verify all OFF
    write(relay_index, True) -> read_all() -> verify relay_index ON, all others OFF
    write_all(0)          -> read_all() -> verify all OFF
```

**Safety Self-Test (1-32, stop on first failure)** (`test_relay_safety_selftest()`) exercises the mandatory sequence through the public 1-based API instead:

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

### 9.4d Emergency Shutdown Strategy

**Design principle: an unknown relay state is an unsafe state.** When in doubt, force all relays OFF and verify. FAIL SAFE, never fail-and-leave-energized. This is enforced in layers:

| Layer | What | Where |
|-------|------|-------|
| 1. Startup safe-state enforcement | `relay.open_all()` (force OFF + verify) runs immediately after the relay connects, before `connect_all()` returns. Abort startup (`HardwareInitError`) if it fails. | `HardwareManager.connect_all()` |
| 2. Runtime failure behavior | Any `RelayStateVerificationError`, communication failure surviving the one automatic reconnect, Telnet timeout, readback/parser failure -- `_emergency_all_off()` is attempted BEFORE the exception propagates. | `NumatoRelayMatrix.verify_single()`/`verify_all()`/`_call_with_reconnect()` |
| 3. Emergency stop | On any `SafetyViolationError`/`RelayError` during a battery test, SMU output disabled then `relay.open_all()` called. | `BatteryTestSequence.run()` -> `SafetyMonitor.emergency_stop()` |
| 4. Application exit protection | `disconnect_all()` (disable SMU -> `relay.open_all()` -> disconnect everything) runs in every `finally:` block around the test loop (normal completion, `KeyboardInterrupt`, any exception), plus an independent `atexit`-registered backstop for exit paths that bypass it. | `main.py`/`test.py` `finally:` + `HardwareManager.disconnect_all()` / `_atexit_relay_shutdown()` |

If an emergency shutdown attempt itself fails (most commonly: no working connection left to force anything through), that is logged as **CRITICAL** with explicit "hardware may still be energized -- physically disconnect power" wording, never silently swallowed -- but the original exception that triggered it is still what propagates.

**Guarantees:**

- Program starts with all relays OFF, or does not start.
- Relay changes always go through safety verification (Section 9.4a).
- Any relay failure forces all relays OFF.
- Any safety violation forces all relays OFF.
- Any unhandled exception attempts to force all relays OFF.
- Application exit attempts to force all relays OFF.
- The framework never *intentionally* leaves relays energized after termination.

**Known limitation:** nothing in userspace -- this codebase included -- can catch `SIGKILL` / a hard process kill. That is an OS-level guarantee no software can provide.

See `docs/architecture.md` Section 6d for the full design writeup and a table mapping each guarantee to its enforcing code.

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
    "niswitch": ("hardware.relay_niswitch", "NIPXIe2569RelayMatrix"),
    "modbus":   ("hardware.relay_modbus", "ModbusRelay"),   # <-- add this line
}
```

3. Set `"type": "modbus"` in the config dict.

No other code changes needed.

---

## 10. Database Layer

### 10.1 DataStorage

`data/storage.py` provides `DataStorage` (SQLite, WAL). Database files live under `Settings.DATA_DIR` (`data_output/<mode>/`, one directory per system mode) and are split in two:

- **Index database** (permanent, never rotates): `run_summary`, `station_state`, `run_sequence`.
- **Telemetry database** (`nipxi_<YYYY>_<MM>.db`, monthly rotation, one file per workflow chosen when storage opens): `measurements`, `event_log`, `raw_hardware_log`. A run that straddles a month boundary keeps writing to the file it opened with.

Workflows write through `record_measurement()`, `log_event()`, `record_execution_state()`, `start_run_summary()`/`finish_run_summary()`; every run gets a global sequence number. A failed measurement write raises `StorageFailureError`, which triggers the standard safety shutdown (Section 11); a start-gate free-space check (`STORAGE_MIN_FREE_MB_START`) refuses to start a workflow on a nearly full disk. `raw_hardware_log` (automatic audit of every driver call) never raises and disables itself if it cannot write. The legacy `record()`/`query()` interface below is used by the `main.py` path and also writes per-channel CSV files (`data_output/<mode>/csv/`). Inspect data from the menu with Database Tools / Last Test Summary, or export one run as a self-contained JSON for investigation with `scripts/export_run.py`. Details: `docs/architecture.md` Sections 79, 83, 84.

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

Each legacy-path run gets a unique `run_id` timestamp (`YYYYMMDD_HHMMSS`).

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

> Full runtime safety model, including the open-item register: [docs/architecture.md](docs/architecture.md) Section 85. Remaining gaps: Section 22 below.

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
1. smu.emergency_output_off(reason)  -- SMU output OFF, verified (never raises)
2. relay.open_all()                  -- disconnect all batteries (force-off + verify)
3. logging, run summary, cleanup     -- only after 1 and 2, each step independently guarded
```

The order is a requirement: a broken database or full disk must never delay or skip steps 1 and 2.

**PMU (SMU) is safety-critical.** "PMU" and "SMU" are the same thing in this project
(`hardware/smu.py`). Any error during charge/discharge -- comms failure, timeout,
verification failure, safety violation, or any unhandled exception -- forces:
`PMU Output OFF -> Verify Output OFF -> Raise -> Abort`, never continue. Startup
forces and verifies PMU output OFF before any battery operation is allowed; shutdown
(normal, emergency, or e-stop) does the same. `BATTERY_CONFIGS` describes battery
capability/recommended ranges only -- it is never the sole authority for the current
operating limit, which is always the most conservative value across Battery/PMU/DAQ/
Safety/Test limits. See `docs/architecture.md` Sections 11-12 for the full philosophy,
the worked limit-resolution example, and the planned (documentation-only)
`LimitResolver` concept. DAQ has no equivalent shutdown behavior yet -- it remains
measurement-only for now, by design.

**Relay verification failures are safety faults, never warnings.** `BatteryTestSequence.run()` catches `RelayError` alongside `SafetyViolationError`, calls `emergency_stop()`, and re-raises immediately -- it never attempts another relay command on that channel or continues to the next one. `TestExecutor.run()` catches the same exception and marks the whole run `aborted`. No component is permitted to log a warning and continue past a relay verification failure. See [Section 9.4a](#9.4a-mandatory-relay-safety-sequence) for the full sequence.

**Immediate relay-open-on-fault (any exception, not just relay/safety ones).** `BatteryTestSequence.run()` also has a generic `except Exception` clause (after the specific `OperationCancelledError`/`SafetyViolationError`/`RelayError` ones, so none of those are accidentally caught by it) that calls `emergency_stop()` for genuinely unanticipated failures too (e.g. a `DAQError`) -- before this was added, only `SafetyViolationError`/`RelayError` forced the relay open immediately, and anything else left the relay closed until the outer `HardwareManager.disconnect_all()` eventually ran at process teardown. The PMU never had this gap (`ChargeCycle`/`DischargeCycle`'s own `try/finally` already forced it off on any exception); this closed the equivalent gap for the relay. See [docs/architecture.md Section 13.5](docs/architecture.md).

**Cancellation is not a failure.** Pressing Ctrl+C during `Run Main Test` requests a safe, checkpoint-based cancellation (`CancellationToken`/`OperationCancelledError`) rather than an uncontrolled `KeyboardInterrupt` -- it runs the exact same PMU-off/relay-open safety sequence as above, and is reported as `CANCELLED`, never `FAILED`. See [Section 20](#20-safe-cancellation-architecture).

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

### 11.1 Safety features

**Safe state = SMU output OFF (verified) AND every relay open (verified). An unknown state is treated as unsafe.**

**Implemented** (exercised with fake hardware; bench validation pending):

| Feature | Behavior |
|---|---|
| Startup Safety Sweep | Before the menu: every SMU and relay matrix is connected, forced safe, verified, disconnected. A verification failure blocks startup (SAFETY FAULT screen + operator acknowledgement). |
| Safe shutdown sequencing | Every abnormal exit of a run (`run_guarded()`): 1. SMU OFF verified, 2. relay OPEN verified, 3. logging and cleanup. |
| Emergency shutdown (driver level) | Any relay verification or communication failure forces all relays open before the exception propagates (Numato and PXIe-2569). |
| Storage failure shutdown | A failed measurement write (`StorageFailureError`) or free space below `STORAGE_ABORT_FREE_MB` (100 MB) triggers the standard shutdown. Starting is refused below `STORAGE_MIN_FREE_MB_START` (500 MB); warning below 300 MB. |
| Relay communication loss | Every `RELAY_HEARTBEAT_INTERVAL_S` (10 s) the relay bank is read back; a link error (`RelayCommLostError`) or any state other than "only the active relay closed" triggers the shutdown. |
| Watchdog | If a run loop stops progressing for `LOOP_WATCHDOG_TIMEOUT_S` (60 s), a watchdog thread forces the emergency stop without waiting for the stuck thread. Covers Monitor Battery, Charge and Discharge. |
| Frozen measurement detection | Identical consecutive current or voltage samples (default 60) or NTC samples (default 300) raise `MeasurementFrozenError`. Not applied to voltage in Monitor Battery or the charge CV hold. |
| Signal handling | Ctrl+C: cooperative cancel, per operation. SIGTERM, SIGHUP, Ctrl+Break, console close, logoff and system shutdown (`utils/termination.py`): cancel all operations, wait 8 s (4 s for console close/logoff/shutdown) for hardware to reach a safe state, then force SMU off and relays open. |
| Process-exit nets | `HardwareManager.disconnect_all()` in workflow `finally` blocks, and `atexit` hooks (not run on SIGTERM or console close). |
| Station-level faults | `StorageFailureError` and `WatchdogTimeoutError` abort every remaining position of a Group -> ALL run. |

**Future / planned (not implemented):**

- Relay welded/stuck-closed detection, SMU/DMM cross-validation, isolation checks and independent relay verification (postponed).
- NTC mandatory-fail policy (loss of the NTC reading still does not stop a run).
- Watchdog, heartbeat and frozen-signal coverage for Proto Test, Monitor Battery Scan and the diagnostic loops.
- Explicit timeouts on SMU, DMM and DAQ driver calls.
- Escalation of a second Ctrl+C.

Not coverable from software: `SIGKILL`/Task Manager kill, BSOD, PC power loss, native driver crash.

Configuration: `docs/CONFIGURATION.md`, "Runtime safety supervision".

---

## 12. Error Handling

All exceptions inherit from `NIPXIError`:

```
NIPXIError
+-- HardwareInitError     -- device failed to initialize
+-- RelayError            -- relay communication failure
|     +-- RelayStateVerificationError  -- readback did not match commanded state
|     |                                    (always fatal -- see Section 9.4a)
|     +-- RelayCommLostError           -- heartbeat could not read the relay bank
+-- DAQError              -- DAQ read/write failure
+-- SMUError              -- SMU communication or compliance failure
+-- DMMError              -- DMM communication failure
+-- SafetyViolationError  -- safety limit exceeded (triggers e-stop)
|     +-- DMMMeasurementLostError   -- DMM measurement lost
|     |     +-- MeasurementFrozenError  -- signal stuck at an identical value
|     +-- StorageFailureError       -- measurement write failed or disk space exhausted
|     +-- WatchdogTimeoutError      -- run loop stopped progressing
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
# Interactive menu (Section 8)
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
python test.py   # Configuration Validation runs automatically at startup; choose 3 (Startup Device Validation) to re-run it

# Safety monitor (offline)
python test.py   # choose 11 (Test Safety Monitor)

# UI demo screens (no hardware, no database)
python test.py   # choose 14 (UI Test)
```

### 13.3 Adding a new test section

1. Write a function `test_<thing>() -> list[TestResult]` in `test.py`
2. Add it to `MENU`:

```python
MENU = [
    ...
    ("Test My New Thing", test_my_new_thing),
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

Test the swap with `test_minisql()` in `test.py` -- kept and unchanged, but no longer an operator-facing menu item (it is a stub for future MiniSQL hooks, out of scope for the current hardware bring-up menu -- see Section 8.1b):

```bash
python -c "from test import test_minisql; [r.print_detail() for r in test_minisql()]"
```

---

## 16. Troubleshooting

**`ModuleNotFoundError: No module named 'nidaqmx'`**  
Install NI-DAQmx driver and Python bindings: `pip install nidaqmx`

**`ModuleNotFoundError: No module named 'serial'`**  
Install pyserial: `pip install pyserial`

**SMU/DAQ/DMM resource not found**  
Open NI-MAX, expand Devices and Interfaces, note the exact VISA resource string  
(e.g. `"PXI1Slot5"`), and update the matching entry in `config/devices.py::PXI_SLOTS` --
not `config/settings.py`, which does not hold resource strings, and not
`SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` directly, since those are derived
from `PXI_SLOTS` (see [Section 4](#4-supported-hardware)).

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
Run "Startup Device Validation" (menu item 3) to see the specific failing parameter -- `preflight_check()` prints the failing item's name before blocking the menu either way.

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
Device Drivers               (hardware/smu.py, daq.py, dmm.py, relay_eth.py, relay_niswitch.py, relay_serial.py)
     |
     v
Hardware Discovery Test      (test_hardware_discovery() -- connectivity + identification only)
     |
     v
Identity Validation          (test_smu/test_dmm/test_daq/test_temperature_module/
                               test_relay_numato/test_pxi_relay_matrix -> select device ->
                               Identity Validation -- remote/RDP bring-up, never changes
                               hardware state)
     |
     v
Functional Validation        (same menu items -> Functional Validation -- DMM measurement,
                               DAQ channel read, Numato relay energizing; lab-only, requires
                               physical access)
     |
     v
Battery Test Workflows       (BatteryTestSequence / TestExecutor -- charge/discharge cycling)
```

`config/devices.py` is the single source of truth for every device's resource string / address (PXI slot, relay IP, relay COM port). `config/settings.py` holds tunable *behavior* parameters (voltages, currents, timeouts, `RELAY_COUNT`) and simulation mode (`PXI_SIMULATE`) -- it does not hold device addresses.

**Why relay has a `Factory` class but SMU/DAQ/DMM do not:** the relay role has three real implementations (`SerialRelay`, `NumatoRelayMatrix`, `NIPXIe2569RelayMatrix`) selected dynamically by `cfg["type"]`, so `RelayFactory.create(cfg)` genuinely dispatches. SMU, DAQ, and DMM each have exactly one driver class -- there is nothing to dispatch between, so `SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)` direct construction from a `config/devices.py` dict already fulfills the "config -> Factory -> driver" step without an extra indirection layer. This is a deliberate choice, not a gap: adding a second SMU/DAQ/DMM implementation would be the point at which a real factory becomes justified, exactly like relay.

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
4. `test.py`: add `(category, "<Label>", _identify_<type>)` to `_PXI_CATEGORY_TARGETS` (PXI-slot devices) or a `(label, cfg_dict, _identify_<type>)` tuple to `_NON_PXI_TARGETS` (Ethernet/serial-style devices) -- Hardware Discovery covers it with no other change. Add a `test_<type>()` menu entry calling the shared `_run_hardware_category()` helper (Section 8.1a) to expose it as its own hardware category in the menu.
5. Optionally wire it into `HardwareManager` if it participates in the battery test workflow (relay/SMU/DAQ do; DMM is intentionally optional, matching its role as independent verification only).

### 17.3a Replacing an existing PXI card

Full procedure and known limitations: `docs/CONFIGURATION.md` "Hardware Replacement Procedure". Summary:

| Change | Requirement |
|---|---|
| Model/resource change only, same `nickname` (any category) | Edit `PXI_SLOTS` only |
| SMU `nickname` change (e.g. `PRIMARY_SMU` -> a new name) | Edit `PXI_SLOTS` only -- `HardwareManager` resolves the active SMU by slot order, never by name |
| DAQ or DMM `nickname` change away from `MAIN_DAQ`/`MAIN_DMM` | Also update `DAQ_CONFIG`/`DMM_CONFIG`'s lookup key in `config/devices.py` -- forgetting this raises `KeyError` at `import config.devices` time, before any test runs |
| DAQ card replaced with a different NI-MAX device alias | Also reconfirm and update the `"Dev1"` literals in `BATTERY_CHANNELS` (`config/devices.py`) -- the NI-MAX alias is not derivable from a chassis slot string in software |

`utils/constants.py`'s `CARD_*` block is a comment-only, unread duplicate of `PXI_SLOTS` data -- updating it is optional (nothing breaks if it's not), but it will silently drift out of sync with the real inventory after a swap if skipped.

### 17.4 Test execution order

```
1. Startup Validation         validate_settings() + validate_devices_or_raise()
                               (main.py at startup; test.py's preflight_check() before the menu),
                               then the Startup Safety Sweep (test.py)
2. Hardware Discovery          test_hardware_discovery() -- connectivity + identification, every type
3. Identity Validation          test_smu / test_dmm / test_daq / test_relay_numato /
                               test_pxi_relay_matrix -> Identity Validation --
                               remote/RDP bring-up stage, never changes hardware state
4. Functional Validation        same menu items -> Functional Validation -- SMU sourcing, DMM
                               measurement, DAQ channel read, Numato relay tests, Relay Range
                               Validation (Numato and PXIe-2569) -- lab-only, requires physical access
5. Battery Test Workflows       Run Main Test (Monitor / Charge / Discharge / Cycle) and the legacy main.py
```

Each stage assumes the previous one passed. `test.py`'s menu enforces stage 1 structurally (it gates the whole menu); stages 2-5 are independently selectable menu items so any stage can be re-run in isolation, but running them out of order on unvalidated/unverified hardware is not recommended. Stages 1-3 are the current bring-up focus (hardware identification and readiness, safe to do remotely over RDP); stage 4 requires physical lab access since it changes hardware state.

---

## 18. System Modes

**Problem this solves:** `HardwareManager` used to try to initialize every production device unconditionally, so laptop development without the PXI chassis attached failed on things like `DAQ 'PXI1Slot2' not found` even for work that had nothing to do with the DAQ. `config/system_mode.py` fixes this with three formal modes.

| Mode | Purpose | Missing-hardware behavior |
|------|---------|---------------------------|
| `DEVELOPMENT` (default) | Laptop/software work, UI, database, architecture, simulation | Logged as a **warning**; startup continues |
| `VALIDATION` | Hardware integration, driver validation | Logged as an **error** ("test failure"); framework still launches |
| `PRODUCTION` | Real battery cycling | **Aborts startup** (`HardwareInitError`) -- unchanged from before this feature |

Set in `config/settings.py`: `SYSTEM_MODE = "DEVELOPMENT"`. Validated at startup like any other Settings value.

**Not relaxed by mode, ever:** if the relay driver connects but its startup force-off/verify can't be confirmed, that's still fatal in every mode -- missing hardware is tolerated, hardware in an unverifiable state never is (see Section 9.4d, "Emergency Shutdown Strategy").

**Also mode-driven:** database location (`data_output/development|validation|production/nipxi*.db` -- see `docs/DATABASE_ROADMAP.md`), a recovery-enabled config hook (no recovery engine exists yet), and simulation extension points (`hardware/simulated.py` -- foundations only, not wired into `HardwareManager`/`RelayFactory` yet).

Full design writeup: `docs/architecture.md` Section 9. Future database/recovery architecture: `docs/DATABASE_ROADMAP.md`.

---

## 19. Instrument Verification Philosophy

**Never COMMAND and assume success. Always COMMAND -> READ BACK -> VERIFY -> PASS.** A test that merely calls an API and reports PASS because nothing raised gives false confidence -- exactly the "just execute an API call and report PASS" pattern this section exists to rule out. This is the Numato Relay Matrix's mandatory safety sequence (Section 9.4a: never activate a relay without forcing and verifying a baseline first) applied to every other instrument driver.

| Device | Command | Readback | Verify |
|--------|---------|----------|--------|
| Relay | `relay on/off <n>` | `relay read <n>` + `relay readall` | Commanded state matches, all others unaffected (unchanged -- this is what the others now mirror) |
| SMU | Instrument built-in self-test | Self-test result code + message | Code indicates success, else `SMUError` (`hardware/smu.py::SMU.identify()`) |
| SMU | Configure + enable a DC voltage output point | `query_in_compliance()` + the SMU's own voltage measurement | Not in current-limit compliance, else `SMUError`; output always disabled afterward regardless (`hardware/smu.py::SMU.source_dc_voltage_point()`) -- the measured value itself is reported informationally, verified by the operator's handheld DMM, not asserted against a tolerance (none is configured) |
| DMM | Instrument built-in self-test | Self-test result code + message | Code indicates success, else `DMMError` (`hardware/dmm.py::DMM.identify()`) |
| DMM | Configure + trigger a DC volts measurement | The measured value | Finite and within the configured range, +5% overrange margin (`DMM.measure_dc_voltage()`) |
| DAQ | Instrument built-in self-test | `self_test_device()` (raises on failure) | No exception raised (`hardware/daq.py::DAQ.identify()`) |
| DAQ | Configure + read one analog channel | The read value | Finite and within the configured `voltage_range_v`, +5% overrange margin (`test.py::_functional_daq()`) |

**What is deliberately NOT verified:** electrical truth independent of the instrument's own report. Relay readback (`relay readall` on the Numato, `get_relay_position` on the PXIe-2569) reflects the commanded state, not necessarily the physical contact, and `verify_output_disabled()` trusts the NI-DCPower driver's `output_enabled` property. A welded relay contact therefore passes every current check (Section 22). SMU battery sourcing (`set_charge_mode`/`set_discharge_mode`/`output_enable`/`measure`) is implemented and has been exercised on a real battery: current was programmed and measured steady at 100 mA, and voltage-compliance clamping was observed at higher setpoints. Charge termination (EOC) has not been reached on a real battery (Section 22). `source_dc_voltage_point()` (SMU Functional Validation, Section 8.1b) remains separate: a single bench DC voltage point, verified by the operator's handheld DMM.

Full design writeup, including why Hardware Discovery needed no code changes to inherit this: `docs/architecture.md` Section 10.

---

## 20. Safe Cancellation Architecture

Lets an operator stop a running test safely via Ctrl+C, without an uncontrolled `KeyboardInterrupt` landing on an arbitrary line mid-operation. Only Safe Cancellation is implemented -- a separate, faster "Emergency Abort" (operator types `ABORT`) was designed but deliberately not built; see `docs/architecture.md` Section 13.8.

**Flow:**

```
Ctrl+C -> SIGINT handler (main.py/test.py) -> token.request_cancel("Ctrl+C")
       -> next safe checkpoint -> OperationCancelledError raised
       -> PMU output OFF, verified (ChargeCycle/DischargeCycle's own try/finally)
       -> Relay OPEN ALL, verified (SafetyMonitor.safe_cancel_shutdown())
       -> TestExecutor absorbs it: stop_reason = CANCELLED (never FAILED)
       -> main.py: sys.exit(3) -- distinct from success (0) and failure (1/2)
```

**Components:** `utils/cancellation.py::CancellationToken` (idempotent `request_cancel()`/`check()`; live tokens are tracked so `cancel_all_tokens()` can cancel every operation on SIGTERM/console close), `utils/errors.py::OperationCancelledError`, `utils/stop_reason.py::StopReason`. Ctrl+C is translated to a cooperative flag by a `signal.signal(SIGINT, ...)` handler, checked at defined checkpoints, never an asynchronous interrupt; the first press cancels, later presses are ignored. SIGTERM, Ctrl+Break, console close, logoff and system shutdown are handled by `utils/termination.py` (cancel all operations, wait 4-8 s for hardware to reach a safe state, then force SMU off and relays open). A watchdog thread (`utils/watchdog.py`) forces the emergency stop if a run loop stalls for 60 s. Not covered: hard kill, BSOD, power loss. See `docs/architecture.md` Section 85.

**Checkpoints exist only between atomic hardware operations** -- top of the `ChargeCycle`/`DischargeCycle` sampling loop, before each channel in `BatteryTestSequence`, before each channel in the relay matrix scan/RelayEthernetTest -- never inside a relay activate/verify sequence or a PMU verify sequence, since interrupting mid-sequence would leave hardware state less certain, not safer.

Full design, the complete flow diagram, checkpoint table, and known risks (SMU/DMM timeout gaps, the `HardwareManager.connect_all()` SIGINT-installation-order gap, `test_relay_safety_selftest()`'s missing checkpoint): `docs/architecture.md` Section 13, 16, 17.

## 21. State Model

Every test run (and each channel within one) ends with a `stop_reason` (`utils/stop_reason.py::StopReason`), kept independent from "how much completed" -- a run can be `CANCELLED` after 2 of 8 channels passed.

| State | Meaning |
|---|---|
| `COMPLETED` | Ran to natural completion |
| `FAILED` | An unexpected error (`RelayError`, `HardwareInitError`, or any other unanticipated exception) |
| `SAFETY_VIOLATION` | `SafetyMonitor` detected a limit breach -- correct, intentional behavior, not a defect |
| `TIMEOUT` | Defined, **not yet wired end-to-end** -- a charge/discharge cycle hitting its deadline is currently discarded by `BatteryTestSequence.run()` rather than surfaced |
| `CANCELLED` | Operator requested a graceful stop (Ctrl+C) -- never reported as `FAILED` |

Full model, including the currently-unreachable `"PARTIAL"` summary branch and why `stop_reason` doesn't yet reach the database/report: `docs/architecture.md` Section 16.

---

## 22. Known Limitations

Remaining gaps after the safety shutdown phase. The full register (S-1 to S-18, with assumptions) is in `docs/architecture.md` Section 85.9.

- **Relay welded/stuck-closed detection is not implemented.** Relay and SMU verification is readback of the commanded state; a welded contact is not detected. SMU/DMM cross-validation, isolation checks and independent relay verification are postponed.
- **Charge Battery is only partly validated.** Status per stage, from stored runs (`run_summary`/`measurements`) and the code:
  - *CC:* implemented (`SMU.set_charge_mode()` programs `current_level` and the voltage limit with readback verification; `output_enable()` starts it). Confirmed on a real battery at 100 mA only (current measured steady at 0.100 A for ~3 min). At 500 mA and above the SMU hit its voltage limit instead, so the commanded constant current was not reached.
  - *CV:* implemented as the SMU's voltage-limit (compliance) clamp, not as a software control loop. Clamping was observed on real runs (SMU terminal voltage pinned at the limit while current fell), but a full CV taper was not.
  - *CC to CV transition:* not demonstrated. There is no distinct phase marker (everything is labeled `CC_CV`) and no transition detection.
  - *CV hold:* there is no explicit hold stage; the SMU clamp holds the voltage.
  - *End-of-charge:* not reached on any real battery. The only `PASS` recorded was a run with no battery connected (voltage driven to the limit), which later detection logic now rejects. EOC requires DMM voltage at or above the CV target while current is at or below `CHARGE_CUTOFF_A`; with series wiring resistance the DMM sees less than the SMU terminal limit, so this may be unreachable until the current is near zero (needs bench confirmation).
- **NTC mandatory-fail policy is pending.** A missing or failing NTC reading is still tolerated indefinitely; only a frozen NTC value is caught.
- **Watchdog, heartbeat and frozen-signal coverage:** Proto Test Execution and Monitor Battery Scan are not yet covered (Monitor Battery, Charge and Discharge are). Diagnostic loops in `test.py` and the legacy `main.py` path are not covered either.
- **Frozen-measurement thresholds are uncalibrated.** The defaults are starting values and need field calibration against `raw_hardware_log` from real runs; a very stable NTC or current may trigger false positives (`FROZEN_DETECTION_ENABLED` is the kill switch).
- **PXIe-2569 hardware validation is still required.** Unconfirmed assumptions: logical channel N is relay N of the card's own relay list (check with Relay Range Validation), and `get_relay_position` may report only the commanded position. The `niswitch` package is not installed on the development PC.
- **Safety framework not bench-validated.** Watchdog, heartbeat, termination handling and storage protection were tested with fake hardware only. Console-close, Ctrl+Break and SIGTERM behavior and relay behavior on a TCP drop are unverified on the rack.
- **Startup Safety Sweep does not block on an unreachable device**, in any system mode including PRODUCTION; it reports PARTIAL and the menu opens.
- **No explicit timeouts** on NI-DCPower, NI-DMM and NI-DAQmx calls; the watchdog (60 s) is the only bound on a hung call.
- **A second Ctrl+C is ignored** while an operation is cancelling; there is no escalation.
- **`logging.disable(logging.CRITICAL)`** in `test.py` suppresses log output while running under `test.py`, and `raw_hardware_log` disables itself silently if it cannot write.
- **Not coverable from software:** `SIGKILL`, BSOD, PC power loss, native driver crash. Whether the Numato keeps or drops its state on loss of link and whether the 2569 relays are latching are unverified bench questions.

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
