# NIPXI test.py -- Execution Tree Review

> **Superseded snapshot notice:** the `##Antonio` annotations added to this
> document were reviewed and acted on -- see `docs/architecture.md` Section
> 23 ("Menu Restructuring Review") and `docs/MILESTONES.md` for what was
> implemented. The tree/menu numbering below (16 top-level items) reflects
> `test.py` **before** that restructuring; the current MENU has 13 items
> (Test Temperature Module retired, Test Configuration removed, Test SQLite/
> Test Database Layer merged into Database Tools, Run All Tests replaced
> with UI Test, Test Sensors/Test Safety Monitor extended). This document
> was intentionally left as the historical record of that review rather
> than regenerated -- see `docs/architecture.md` Section 23j for the
> current menu list.

Analysis-only document. No code was modified to produce this review. Generated
by reading `test.py` end-to-end (all ~3070 lines, every `def`, the `MENU`
list, `_FULL_RUN_ENTRIES`, `_dispatch_menu_choice()`, `main()`) and following
every call chain into `test_control/`, `hardware/`, `data/`, `config/`,
`utils/` only as far as needed to confirm hardware/database/safety behavior.

Legend for **Status** classification used throughout:
- **Production** -- real hardware path intended for actual battery testing.
- **Validation** -- real hardware path, but infrastructure/commissioning/
  bench-check only (no battery connected, or a diagnostic/self-test).
- **Development** -- offline logic, no hardware I/O, safe on a laptop.
- **Legacy/Dead** -- defined but unreachable from any menu path today.

---

## 1. Full ASCII Execution Tree

```
Main Menu (test.py::main())
│
├── [Pre-flight, runs BEFORE the menu is shown] preflight_check()
│   ├── test_configuration()            -- same function as menu item 13
│   └── test_device_validation()        -- same function as menu item 3
│       (FAIL here -> sys.exit(1) before the menu ever appears)
│
├── 1. Run Main Test                                  run_main_test()
│   ├── 1. Monitor Battery                            _run_monitor_battery()
│   │   ├── Battery Type Selection                    _select_battery_type()
│   │   ├── Battery Group Selection                   _select_battery_group()
│   │   ├── Battery Position Selection                _select_battery_position()
│   │   ├── Confirmation Screen                       _confirm_monitor_battery()
│   │   ├── HardwareManager.connect_all()             (SMU + DAQ + DMM + Relay)
│   │   ├── Hardware/Battery Snapshot + Traceability
│   │   │   ├── _hardware_snapshot_fields()           -> run_summary (start_run_summary **fields)
│   │   │   ├── event_log: Run started / Mode / Battery selected / capacity /
│   │   │   │             Group selected / Position selected / snapshot recorded
│   │   │   └── event_log: SMU/DMM/DAQ/Relay matrix "in use" + snapshot recorded
│   │   │                  (dev_cfg.hardware_traceability_messages())
│   │   └── MonitorBatterySequence.run()              test_control/monitor_battery_sequence.py
│   │       ├── Relay Close                           relay.close(relay_address)
│   │       ├── Monitoring Loop (until Ctrl+C)
│   │       │   ├── dmm.measure_dc_voltage()           TEMPORARY voltage source (see docs/architecture.md 20a)
│   │       │   ├── record_measurement()               -> measurements (current_a/temp_c always None)
│   │       │   └── ExecutionFrame.from_live() / render_execution_frame()
│   │       └── On exit (Ctrl+C / fault)
│   │           ├── record_execution_state()          -> station_state
│   │           ├── finish_run_summary(voltage stats) -> run_summary
│   │           └── safety.safe_cancel_shutdown() / emergency_stop()
│   ├── 2. Charge Battery                              -- NOT IMPLEMENTED (print only)
│   ├── 3. Discharge Battery                           -- NOT IMPLEMENTED (print only)
│   └── 4. Cycle Battery                                -- NOT IMPLEMENTED (print only)
│
├── 2. Proto Test Execution                           run_proto_test_execution()
│   ├── Resolve SMU (Settings.PROTO_TEST_SMU_NAME) / DMM / DAQ / Relay cfg
│   ├── HardwareManager.connect_all()                 (SMU + DAQ + DMM + Relay -- DMM required here)
│   ├── storage.get_last_execution_state()             -> station_state (display only, no auto-resume)
│   └── ProtoTestSequence.run()                        test_control/proto_test_sequence.py
│       ├── start_run_summary(test_type="proto", hardware_snapshot)  -> run_summary
│       ├── event_log: SMU/DMM/DAQ/Relay matrix "in use" + snapshot recorded
│       └── Per relay N in Settings.ACTIVE_CHANNELS (1-8):
│           ├── event_log: "Relay N activating"
│           ├── relay.close(N)
│           ├── smu.source_dc_voltage_point(CHARGE_VOLTAGE_V, CHARGE_CURRENT_A, hold_s, during_hold=DMM read)
│           ├── record_measurement()                  -> measurements (test_type="proto", full SMU/DMM columns)
│           ├── record_execution_state()               -> station_state (ACTIVE)
│           ├── ExecutionFrame.from_live() / render_execution_frame()
│           ├── relay.open(N)  (on success)            + event_log "Relay N deactivated"
│           └── on failure/cancel: record_execution_state(FAILED/CANCELLED/SAFETY_VIOLATION),
│               finish_run_summary(), safety.emergency_stop()/safe_cancel_shutdown()
│       └── On full completion: record_execution_state(COMPLETED), finish_run_summary(PASS), event_log complete
│
├── 3. Startup Device Validation                       test_device_validation()
│   └── utils/device_validator.py::validate_devices(dev_cfg)   -- construction-only, no hardware I/O
│
├── 4. Hardware Discovery                              test_hardware_discovery()
│   ├── SMU        (PXI_SLOTS category="smu")          _identify_smu()          per configured device
│   ├── DMM        (PXI_SLOTS category="dmm")          _identify_dmm()          per configured device
│   ├── DAQ        (PXI_SLOTS category="daq")          _identify_daq()          per configured device
│   ├── Temperature Module (category="temperature")    _identify_temperature()  per configured device
│   ├── Switch/Relay (PXI, category="switch")          _identify_switch()       -- always N/A, no driver
│   ├── Numato Relay Matrix (Ethernet)                  _identify_relay_eth()    per configured device
│   ├── Relay (Serial)                                  _identify_relay_serial() -- RELAY_SERIAL_CONFIGS is {} today
│   └── GPIB                                            always N/A -- no instrument confirmed
│
├── 5. Test SMU (PSU)                                  test_smu() -> _run_hardware_category()
│   ├── Identity Validation                             _identify_smu()  ##Antonio: per configured device
│   └── Functional Validation                           _functional_smu()   -- REAL voltage sourcing, operator-in-loop
│
├── 6. Test DMM                                         test_dmm() -> _run_hardware_category()
│   ├── Identity Validation                             _identify_dmm() ##Antonio: per configured device
│   └── Functional Validation                           _functional_dmm()   -- REAL DC voltage measurement
│
├── 7. Test DAQ                                         test_daq() -> _run_hardware_category()
│   ├── Identity Validation                             _identify_daq() ##Antonio: per configured device
│   └── Functional Validation                           _functional_daq()   -- REAL single-channel read
│
├── 8. Test Temperature Module                          test_temperature_module() -> _run_hardware_category()##Antonio: this needs to be ignore becasue temperature is motnitore by daq
│   ├── Identity Validation                             _identify_temperature() ##Antonio: this needs to be ignore
│   └── Functional Validation                           -- NOT IMPLEMENTED ("Functional Validation not yet implemented")##Antonio: this needs to be ignore
│
├── 9. Test Numato Relay Matrix (Ethernet)              test_relay_numato() -> _run_hardware_category() ##Antonio: this system works but why the waiting time betewn  switching all the test is diferent all test
│   ├── Identity Validation                             _identify_relay_eth()
│   └── Functional Validation                           _functional_relay_numato()  -- 4-option submenu
│       ├── 1. Relay 1 quick check (READ/ON/OFF)        test_relay_numato_matrix() -> _run_relay_numato_matrix_test()
│       ├── 2. Matrix Scan (scoped by group)             _test_relay_matrix_scan_scoped() 
│       │       └── _select_relay_scope()  (All Groups / Group A / B / C / D)
│       │           └── test_relay_matrix_scan() -> _run_relay_matrix_scan()
│       ├── 3. RelayEthernetTest (native 0-based)        test_relay_ethernet_test()
│       └── 4. Safety Self-Test (1..N, stop on 1st fail) test_relay_safety_selftest()
│
├── 10. Test PXI Relay Matrix                           test_pxi_relay_matrix() -> _run_hardware_category() ##Antonio: per configured device, I dont have it yet but I need the same logic that we have with  numato relays but for nipxi
│   ├── Identity Validation                             _identify_switch()  -- always N/A, no niswitch driver ##Antonio: per configured device, I dont have it yet but I need the same logic that we have with  numato relays but for nipxi
│   └── Functional Validation                           -- NOT IMPLEMENTED (nothing to validate, no driver) ##Antonio: per configured device, I dont have it yet but I need the same logic that we have with  numato relays but for nipxi
│
├── 11. Test Sensors (NTC)                              test_sensors()## Antonio: his test will use the DAQ to read all enabled NTC channels. A configuration variable/group is required to define which NTC channels are enabled should be scanned.
│
├── 12. Test Safety Monitor                             test_safety_monitor() -- ##antonio: iu need to this to be just the logic stemp by step (simulating that equipment is working but using same logc of all test monitor, cicle discharge charge)
│
├── 13. Test Configuration                              test_configuration()  -- offline, same fn as pre-flight ##Antonio(this needs to be erased)
│
├── 14. Test SQLite (foundation)                        test_sqlite()        -- temp DB, data/sqlite_manager.py, this to be for reading the last input, and maybe se the last raw logs ,
│
├── 15. Test Database Layer                             test_database()      ##Antonio" this needs to be a subtest oTest SQLite 
│
├── 16. Run All Tests                                   fn=None in MENU ## Antonio: this option should nbe renamed as ui, and display the UI using static/demo information only, without connecting to hardware or loading real test data.
│
│   └── _dispatch_menu_choice(): runs config_results + every MENU[1:-1] entry
│       EXCEPT items whose fn is in _FULL_RUN_ENTRIES (Run Main Test, Proto
│       Test Execution are SKIPPED here -- they return None, not
│       list[TestResult], and would crash `for r in None`)
│
└── 0. Exit


Orphaned / unreachable from any menu path (defined in test.py, never called):
├── test_relay_serial()      -- COM-port serial relay diagnostic (RELAY_SERIAL_CONFIGS == {} today)
├── test_minisql()           -- MiniSQL integration stub (all WARN placeholders)
└── test_electronic_load()   -- GPIB electronic-load placeholder (all WARN placeholders)
```

---

## 2. Leaf Node Detail

Grouped where multiple leaves share an identical pattern (e.g. the four
Identity Validation checks) to avoid repeating identical text four times;
every leaf named in the tree above is covered below.

### Run Main Test -> Monitor Battery
```
Purpose:  Real, read-only battery voltage monitoring for one battery
          position (no charging, no discharging).
Hardware: Relay Matrix (MATRIX_NUMATO_201), DMM (temporary voltage source),
          SMU (constructed/connected but never sources -- only used as the
          safety-shutdown argument), DAQ (constructed/connected but unused
          by this mode today).
Database: measurements, run_summary, event_log, station_state.
Status:   Production (implemented, physically validated with mocked
          hardware; DMM-as-voltage-source is an explicitly temporary
          substitute for the still-unresolved DAQ channel wiring --
          see docs/architecture.md Section 20a).
```

### Run Main Test -> Charge Battery / Discharge Battery / Cycle Battery
```
Purpose:  Reserved menu entries for future CC-CV charge, CC discharge, and
          multi-cycle workflows.
Hardware: None (placeholder).
Database: None (placeholder).
Status:   Not implemented -- explicit `print("... not yet implemented")`,
          no logic behind them. Future work.
```

### Proto Test Execution
```
Purpose:  End-to-end infrastructure validation -- relay -> SMU -> DMM ->
          SQLite -> recovery display -- with NO battery connected. Proves
          the Milestone II plumbing works, not that a battery passes.
Hardware: SMU (Settings.PROTO_TEST_SMU_NAME, default AUX_SMU_1), DMM
          (MAIN_DMM, required for this path), DAQ (MAIN_DAQ, connected by
          HardwareManager but not read by this sequence), Relay Matrix
          (MATRIX_NUMATO_201).
Database: measurements, run_summary, event_log, station_state.
Status:   Validation (physically validated on the rack per
          docs/MILESTONES.md Milestone 2 -- all 8 relays cycled
          successfully, no battery/load).
```

### Startup Device Validation
```
Purpose:  Construction-only sanity check of every config/devices.py entry
          (required fields present, no duplicate names/resources/IPs/COM
          ports, relay count consistent, every relay "type" registered in
          RelayFactory). Runs automatically before the menu even appears.
Hardware: None -- no connect() call, no hardware I/O at all.
Database: None.
Status:   Validation (offline, safe on a laptop with no rack attached).
```

### Hardware Discovery -> SMU / DMM / DAQ / Temperature Module
```
Purpose:  Presence + identity check only: connect() + identify(), compare
          reported model string against config/devices.py. Never sources
          voltage/current, never triggers a measurement, never reads a
          channel.
Hardware: Whichever PXI-slot device is configured for that category
          (SMU_ASSIGNMENTS / DAQ_CONFIGS / DMM_CONFIGS / PXI_SLOTS
          category="temperature").
Database: None.
Status:   Validation (identity-only, real hardware, no side effects).
```

### Hardware Discovery -> Switch/Relay (PXI)
```
Purpose:  Report the PXI-resident switch/relay card's presence honestly as
          N/A -- no niswitch-based driver exists in this codebase.
Hardware: PXI_SLOTS[11] (CHASSIS_RELAY_MATRIX) -- reported, never queried.
Database: None.
Status:   Validation (deliberately non-functional -- documents a gap
          rather than faking a check).
```

### Hardware Discovery -> Numato Relay Matrix (Ethernet)
```
Purpose:  TCP connect + Telnet login + the driver's own "relay readall"
          connection-verification. Read-only -- never energizes a relay.
Hardware: Every device under NUMATO_RELAY_MATRIX_CONFIGS (today:
          MATRIX_NUMATO_201, MATRIX_NUMATO_202).
Database: None.
Status:   Validation.
```

### Hardware Discovery -> Relay (Serial)
```
Purpose:  Presence check for a COM-port serial relay (diagnostic path,
          NOT the production relay -- see RELAY_COUNT/NUMATO_RELAY_MATRIX_
          CONFIG commentary in config/devices.py).
Hardware: RELAY_SERIAL_CONFIGS -- currently `{}` (empty), since RELAY_CONFIG
          (MAIN_MATRIX, COM13) was intentionally commented out during
          hardware cleanup. This branch always reports "no devices
          configured -- skipped" today.
Database: None.
Status:   Legacy -- config intentionally emptied; the code path is alive
          but has nothing to exercise until/unless a serial relay is
          reintroduced.
```

### Hardware Discovery -> GPIB
```
Purpose:  Report a detected GPIB0 interface, unconfirmed instrument.
Hardware: dev_cfg.GPIB_INSTRUMENTS -- interface only, no driver.
Database: None.
Status:   Validation (honest N/A, not implemented further).
```

### Test SMU (PSU) -> Identity Validation
```
Purpose:  connect() + identify() only (same _identify_smu() Hardware
          Discovery uses -- cannot drift from it).
Hardware: The selected PXI SMU.
Database: None.
Status:   Validation.
```

### Test SMU (PSU) -> Functional Validation
```
Purpose:  Laboratory-only DC voltage-sourcing check (operator physically
          present with a handheld DMM). Sequence: safe state -> 0V -> the
          real CHARGE_VOLTAGE_V setpoint -> 0V -> safe state. Verifies
          command/readback/measured values at each step.
Hardware: The selected PXI SMU (hardware/smu.py::SMU).
Database: None.
Safety:   emergency_output_off() called at start, after every step, and in
          the `finally` block -- output is never left energized.
Status:   Validation (bench check, not a battery operation -- no relay,
          no battery channel, no charge/discharge mode touched).
```

### Test DMM -> Identity Validation
```
Purpose:  connect() + identify() only.
Hardware: The selected PXI DMM.
Database: None.
Status:   Validation.
```

### Test DMM -> Functional Validation
```
Purpose:  Laboratory-only real DC voltage measurement against an
          externally-connected known reference. Finite/in-range sanity
          check only -- not an accuracy/calibration certification.
Hardware: The selected PXI DMM (hardware/dmm.py::DMM).
Database: None.
Status:   Validation.
```

### Test DAQ -> Identity Validation
```
Purpose:  connect() + identify() only (device self-test, no channel read).
Hardware: The selected PXI DAQ.
Database: None.
Status:   Validation.
```

### Test DAQ -> Functional Validation
```
Purpose:  Real single-channel read via DAQ.read_channel() against
          BATTERY_CHANNELS[1]'s configured voltage channel. Command ->
          readback -> verify finite/in-range.
Hardware: The selected PXI DAQ (hardware/daq.py::DAQ). Assumes MAIN_DAQ's
          wiring -- if EXPANSION_DAQ/PRECISION_DAQ is selected instead, the
          channel string may not map to a real signal on that card
          (pre-existing assumption).
Database: None.
Status:   Validation.
```

### Test Temperature Module -> Identity Validation
```
Purpose:  Presence/identity only, reusing hardware.daq.DAQ (NI-4353 is an
          NI-DAQmx-family device) -- no TC/RTD channel read exists.
Hardware: PXIe-4353 (TEMP_MODULE).
Database: None.
Status:   Validation.
```

### Test Temperature Module -> Functional Validation
```
Purpose:  N/A.
Hardware: None -- no channel-read driver exists yet.
Database: None.
Status:   Not implemented (menu reports "not yet implemented"; tracked in
          docs/TODO.md as the likely future home for real per-channel
          temp_c acquisition, currently stubbed None everywhere else).
```

### Test Numato Relay Matrix -> Identity Validation
```
Purpose:  TCP connect + Telnet login + readall verification, read-only.
Hardware: The selected Numato Relay Matrix (MATRIX_NUMATO_201/202).
Database: None.
Status:   Validation.
```

### Test Numato Relay Matrix -> Functional Validation -> Relay 1 quick check
```
Purpose:  6-step commissioning check on relay 1 only: interface, ping, web
          UI, connect+auth (reported as 2 steps), READ/ON/OFF protocol,
          disconnect.
Hardware: The selected Numato Relay Matrix -- relay 1 only.
Database: None.
Relay:    Yes -- relay 1 is energized (ON) then de-energized (OFF).
Status:   Validation.
```

### Test Numato Relay Matrix -> Functional Validation -> Matrix Scan (scoped by group)
```
Purpose:  ON->READ->OFF exercise across a scoped channel range: All Groups
          (1-32), Group A (1-8), Group B (9-16), Group C (17-24), or
          Group D (25-32) -- see _select_relay_scope(). Scope is a pure
          channel-number restriction on the currently selected device,
          independent of BATTERY_GROUPS[group]["enabled"] (fixed bug --
          see docs/architecture.md Section 21).
Hardware: The selected Numato Relay Matrix -- every channel in the chosen
          range.
Database: None.
Relay:    Yes -- every channel in the scoped range is energized then
          de-energized in turn.
Logging:  Prints "INFO Relay validation scope: <label>" / "INFO Relays
          under test: <start>-<end>" before scanning, whenever a scope was
          selected.
Status:   Validation.
```

### Test Numato Relay Matrix -> Functional Validation -> RelayEthernetTest (native 0-based)
```
Purpose:  Validates the native Numato command primitives directly
          (write/read_all/write_all/verify_all), independent of the
          higher-level 1-based open()/close() API.
Hardware: The selected Numato Relay Matrix -- all configured channels,
          0-based native addressing.
Database: None.
Relay:    Yes -- every relay index energized/de-energized; fails and stops
          immediately on the first mismatch.
Status:   Validation.
```

### Test Numato Relay Matrix -> Functional Validation -> Safety Self-Test (1..N)
```
Purpose:  Validates the mandatory safety sequence (force-all-off -> verify
          -> activate N -> verify N-only) against every channel 1..N,
          individually, stopping immediately on the first failure of any
          kind.
Hardware: The selected Numato Relay Matrix -- all configured channels,
          1-based public API.
Database: None.
Relay:    Yes.
Logging:  Temporarily re-enables hardware/relay_eth.py's logger (DEBUG,
          full per-command audit trail) for the duration of the run.
Status:   Validation.
```

### Test PXI Relay Matrix -> Identity Validation
```
Purpose:  Reports N/A -- no niswitch-based driver exists for this
          PXI-resident switch/relay card.
Hardware: PXI_SLOTS[11] (CHASSIS_RELAY_MATRIX) -- reported only, never
          communicated with.
Database: None.
Status:   Validation (deliberate non-functional placeholder).
```

### Test PXI Relay Matrix -> Functional Validation
```
Purpose:  N/A -- nothing to validate without a driver.
Hardware: None.
Database: None.
Status:   Not implemented.
```

### Test Sensors (NTC)
```
Purpose:  Exercises hardware/temperature.py's NTC-thermistor math
          (voltage-to-Celsius conversion, out-of-range guards,
          monotonicity, TemperatureSensor class interface) -- pure
          arithmetic, no hardware I/O.
Hardware: None.
Database: None.
Status:   Development.
```

### Test Safety Monitor
```
Purpose:  Exercises test_control/safety_monitor.py::SafetyMonitor's pure
          logic -- overvoltage/undervoltage/overcurrent/overtemperature/
          relay-switch-guard/temp_c=None handling.
Hardware: None.
Database: None.
Safety:   This IS the safety-logic test itself (not a hardware check).
Status:   Development.
```

### Test Configuration
```
Purpose:  Offline validation of config/settings.py + config/devices.py --
          relay COM port format, BATTERY_CHANNELS completeness, SMU/DAQ/DMM
          resource-string sanity, utils/validators.validate_settings(),
          voltage/current cross-checks. Same function preflight_check()
          calls automatically at startup.
Hardware: None.
Database: None.
Status:   Development.
```

### Test SQLite (foundation)
```
Purpose:  Minimal data/sqlite_manager.py foundation check (create_database
          / initialize_schema / insert_test_record / get_last_record) --
          one throwaway table (test_records), in a temp directory.
Hardware: None.
Database: A temporary SQLite file only -- never data_output/<mode>/'s real
          database. Does NOT touch measurements/run_summary/event_log/
          station_state.
Status:   Development.
```

### Test Database Layer
```
Purpose:  Exercises data/storage.py::DataStorage (StorageBackend interface
          check + record()/query() round-trip + CSV output verification),
          in a temp directory.
Hardware: None.
Database: A temporary `measurements` table (via record()/query()) -- the
          original, pre-Milestone-II write path, not record_measurement()/
          run_summary/event_log/station_state. Never touches
          data_output/<mode>/'s real database.
Status:   Development.
```

### Run All Tests
```
Purpose:  Aggregates every MENU entry except "Run Main Test"/"Proto Test
          Execution" (which return None, not list[TestResult]) into one
          combined pass/warn/fail summary, reusing config_results from
          preflight_check() so Configuration/Device Validation aren't run
          twice.
Hardware: Union of everything above -- includes real hardware I/O (SMU/
          DMM/DAQ/relay Identity+Functional Validation) if the operator
          lets it run unattended; Functional Validation steps that need
          `input()` will block waiting for an operator.
Database: None directly (delegates to the individual tests above).
Status:   Validation (aggregator, not a distinct code path).
```

### Orphaned: test_relay_serial()
```
Purpose:  Full RelayFactory + interface check for a COM-port serial relay,
          plus a real port-open attempt (protocol commands not
          implemented -- production hardware is Ethernet).
Hardware: RELAY_SERIAL_CONFIGS -- currently `{}` (empty).
Database: None.
Status:   Legacy/Dead -- not reachable from MENU, not reachable from
          _functional_relay_numato()'s submenu, not called by
          test_hardware_discovery() (which calls _identify_relay_serial()
          directly instead, bypassing this function entirely). Only
          reachable via a direct Python call, e.g. `python -c
          "import test; test.test_relay_serial()"`.
```

### Orphaned: test_minisql()
```
Purpose:  Placeholder hooks for a future MiniSQL StorageBackend
          implementation -- all WARN-level stubs.
Hardware: None.
Database: None (checks StorageBackend ABC exists; does not touch any DB).
Status:   Legacy/Dead -- not in MENU, not called anywhere else in test.py.
```

### Orphaned: test_electronic_load()
```
Purpose:  Placeholder reporting any configured GPIB_INSTRUMENTS entry as
          "instrument unconfirmed" -- future Programmable Electronic Load/
          Power Supply integration point.
Hardware: dev_cfg.GPIB_INSTRUMENTS (reported only, no driver).
Database: None.
Status:   Legacy/Dead -- not in MENU, not called anywhere else in test.py.
          (test_hardware_discovery() independently reports GPIB_INSTRUMENTS
          inline, without calling this function.)
```

---

## 3. Architecture Observations

### A. Good / consistent / reusable patterns

- **`_run_hardware_category()`** is the single shared two-level menu
  (device picker -> Identity/Functional Validation) reused identically by
  SMU, DMM, DAQ, Temperature Module, Numato Relay Matrix, and PXI Relay
  Matrix (6 of the 16 top-level menu items). Adding a 7th hardware
  category requires zero new menu-plumbing code.
- **Identity vs. Functional Validation** is a strictly maintained
  distinction everywhere: every `_identify_*()` function only ever calls
  `connect()`/`identify()`/`disconnect()` -- never sources, never energizes,
  never reads a channel. Every `_functional_*()` function is the one place
  real hardware state changes, and is clearly documented as such. This
  discipline is applied with zero exceptions across all 6 categories.
- **Config-driven enumeration** -- nothing in the Hardware Discovery /
  category-menu code hardcodes a resource string, IP, or device count;
  everything is derived from `config/devices.py` (`PXI_SLOTS`,
  `SMU_ASSIGNMENTS`, `NUMATO_RELAY_MATRIX_CONFIGS`, etc.). Adding hardware
  is a config-only change in the common case.
- **`ProtoTestSequence`/`MonitorBatterySequence`** deliberately mirror each
  other's structure (constructor shape, exception handling, storage/event
  calls) rather than inventing a second framework -- a genuinely reusable
  "sequence" pattern, documented as such in both modules' docstrings.
- **Traceability pattern reuse** -- the battery-configuration snapshot
  (`run_summary` + `event_log`, before relay activation) was extended
  verbatim to hardware identity (`_hardware_snapshot_fields()`,
  `dev_cfg.hardware_traceability_messages()`) rather than inventing a
  second mechanism; both `run_proto_test_execution()` and
  `_run_monitor_battery()` use the exact same helpers.
- **`_dispatch_menu_choice()`** centralizes "pause before returning to Main
  Menu" and exception handling for every menu entry in one place -- no
  per-entry duplicated try/except/pause code.
- **`TestResult`/`_ok()`/`_warn()`/`_fail()`** give every one of the ~15
  menu entries the exact same PASS/WARNING/FAIL reporting shape, which is
  what makes `print_summary()`/"Run All Tests" possible with zero
  per-category special-casing.
- **Safety-shutdown consistency** -- `safety.emergency_stop()`/
  `safety.safe_cancel_shutdown()` are called on every failure/cancellation
  exit path in both `ProtoTestSequence` and `MonitorBatterySequence`,
  including `MonitorBatterySequence` (which never sources through the SMU
  at all) -- one shared safety entry point for every mode, not a
  mode-specific shortcut.

### B. Potential issues

- **Three fully dead functions**: `test_relay_serial()`, `test_minisql()`,
  `test_electronic_load()` are defined but never called from `MENU`, from
  `_FULL_RUN_ENTRIES`, from `_functional_relay_numato()`'s submenu, or from
  any other function in the file. They are only reachable via a direct
  Python call, never from the running application. (`test_hardware_
  discovery()` covers the same ground for serial relay and GPIB via
  `_identify_relay_serial()`/inline `GPIB_INSTRUMENTS` reporting, without
  calling these functions.)
- **`RELAY_SERIAL_CONFIGS = {}`** -- the serial relay path (Hardware
  Discovery's "Relay (Serial)" group, and the orphaned `test_relay_serial()`)
  is permanently a no-op today since this dict was intentionally emptied
  during hardware cleanup (production relay is Ethernet/Numato). The code
  path is harmless (prints "no devices configured -- skipped") but is
  effectively legacy scaffolding for hardware that is no longer part of
  the active pipeline.
- **Charge/Discharge/Cycle Battery are unimplemented submenu entries**,
  not missing top-level menu items -- consistent with the explicit
  Milestone II scope, but worth flagging since the top-level `MENU` list
  gives no visual indication that 3 of "Run Main Test"'s 4 real workflows
  don't exist yet; an operator only discovers this after selecting
  "Run Main Test".
- **`_functional_daq()`'s hardcoded assumption**: it always reads
  `BATTERY_CHANNELS[1]["daq_voltage_ch"]` regardless of which DAQ device
  (`MAIN_DAQ`/`EXPANSION_DAQ`/`PRECISION_DAQ`) was selected -- if a
  non-MAIN_DAQ card is chosen, the channel string may not correspond to
  any real signal on that card. Documented in the function's own docstring
  as a pre-existing assumption, not something recently introduced, but
  still a real correctness gap for that specific selection.
- **Two independent "which SMU/DAQ default" resolution styles** exist side
  by side: `HardwareManager`'s own internal default
  (`next(iter(SMU_ASSIGNMENTS.values()))` / `DAQ_CONFIG`), and each of
  `run_proto_test_execution()`/`_run_monitor_battery()` now separately
  re-deriving the same values via `next(iter(...))`/`find_config_name()`
  so the hardware-identity snapshot matches exactly. This is deliberate
  (documented in `docs/architecture.md` Section 22) but is still two
  places that must stay in sync if `HardwareManager`'s defaults ever
  change.
- **`test_temperature_module()`'s Functional Validation and
  `test_pxi_relay_matrix()`'s Functional Validation** are both permanent
  "not yet implemented" placeholders (no driver exists for either) --
  correctly reported as such rather than faked, but they occupy full
  top-level `MENU` slots (8 and 10) that currently only ever do Identity
  Validation.
- **`test_configuration()` and `test_device_validation()` are each
  reachable two ways**: automatically via `preflight_check()` at startup,
  and manually via MENU items 13 and 3. Not a bug (the manual path is
  useful for re-checking config after an edit without restarting), but
  worth being aware of when reading `main()` -- the same result set can
  appear twice in one session.
- **DMM-as-Monitor-Battery-voltage-source is a single shared instrument**
  regardless of which Group/Position was selected -- `_run_monitor_battery()`
  always constructs `dev_cfg.DMM_CONFIG` (`MAIN_DMM`), so the relay is
  switched to the correct battery position but the actual voltage reading
  comes from one shared DMM, not a per-position channel. Explicitly
  documented as temporary (Section 20a), not a hidden gap.

### C. Recommendations

- **Keep**: `_run_hardware_category()`, the Identity/Functional Validation
  split, config-driven enumeration (`_pxi_slots_by_category()`,
  `NUMATO_RELAY_MATRIX_CONFIGS`, etc.), the `ProtoTestSequence`/
  `MonitorBatterySequence` structural mirroring, the hardware/battery
  traceability pattern, and `_dispatch_menu_choice()`'s centralized
  pause/exception handling. These are the load-bearing, consistently-
  applied patterns and should be the template for Charge/Discharge/Cycle
  Battery when they're built.
- **Refactor candidates** (not urgent, no functional bugs):
  - `_functional_daq()` could accept/derive the correct
    `BATTERY_CHANNELS` entry per selected DAQ device instead of always
    reading channel 1's MAIN_DAQ mapping, once EXPANSION_DAQ/PRECISION_DAQ
    are ever actually exercised through this menu path.
  - Consider whether `HardwareManager` should expose the resolved
    `smu_cfg`/`daq_cfg`/`dmm_cfg`/`relay_cfg` it was actually constructed
    with (e.g. a read-only property), so `run_proto_test_execution()`/
    `_run_monitor_battery()` no longer need to independently re-derive the
    "which SMU/DAQ is this" answer a second time just for the traceability
    snapshot.
- **Remove candidates**:
  - `test_relay_serial()`, `test_minisql()`, `test_electronic_load()` --
    genuinely unreachable from the running application. Either wire them
    into a menu path (if still wanted) or remove them; keeping unreachable
    code silently increases the surface a future reader has to reconcile
    against the actual menu tree.
  - `RELAY_SERIAL_CONFIGS`'s "Relay (Serial)" reporting in Hardware
    Discovery could be dropped (or clearly labeled "retired") if serial
    relay hardware is permanently out of scope, rather than always
    printing an empty-group message every run.
- **Future work** (already tracked in `docs/TODO.md`, cross-referenced
  here for completeness):
  - Charge Battery / Discharge Battery / Cycle Battery implementations,
    reusing the exact Battery Type -> Group -> Position -> Confirm ->
    Traceability -> Sequence pattern Monitor Battery established.
  - Migrate Monitor Battery from the temporary DMM voltage source to a
    real per-position DAQ channel read once `BATTERY_CHANNELS`' NI-MAX
    channel mapping is confirmed.
  - A real Temperature Module driver (TC/RTD channel read) to replace both
    the permanent "not yet implemented" Functional Validation placeholder
    and the `temp_c = None` stub carried by Monitor Battery/charge/
    discharge cycle code.
  - A `niswitch`-based PXI Relay Matrix driver, or a decision to formally
    retire that hardware category from the active pipeline (it is
    physically present but has never had a driver).
