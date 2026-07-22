# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

---

## Safe Cancellation Architecture (see docs/architecture.md Section 13)

- [DONE] `CancellationToken`/`OperationCancelledError`/`StopReason`, checkpoints in
  ChargeCycle/DischargeCycle/BatteryTestSequence/relay matrix scan/RelayEthernetTest,
  SIGINT wiring in main.py/test.py, immediate relay-open-on-fault fix in
  BatteryTestSequence.run(). Verified via mock tests + a critical architecture review
  (docs/architecture.md Section 13.7 "Current Known Risks").
- [ ] Close the `HardwareManager.connect_all()` gap: SIGINT handler/token installed
  and `disconnect_all()` teardown net active before `connect_all()` runs, not after
  (main.py and test.py::run_main_test()) — pre-existing, not introduced by this
  feature, but surfaced by the review.
- [ ] Add the same cancellation checkpoint to `test.py::test_relay_safety_selftest()`
  — currently the only one of the three relay-scan-style loops without one.
- [ ] Fix the adjacent `continue`-after-charge gap in `BatteryTestSequence.run()`
  (skips both `relay.open(ch)` and `emergency_stop()`) before real DAQ acquisition
  replaces the current always-zero-current stub — currently unreachable, will become
  live once DAQ read is implemented.
- [ ] Wire `TIMEOUT` end-to-end (ChargeCycle/DischargeCycle already return `False` on
  timeout; `BatteryTestSequence.run()` still discards it) once per-channel
  `ChannelResult` propagation (see TestExecutor TODO below) is implemented.
- [ ] Persist `stop_reason` to the database/report once state persistence work begins
  — currently only lives on the in-memory `TestRunResult`.

---

## Hardware Drivers

- [DONE] Instrument verification philosophy applied to SMU/DMM/DAQ, mirroring the
  Numato Relay Matrix's command -> readback -> verify -> pass sequence (never
  command-and-assume-success). `SMU.identify()`/`DMM.identify()` now run and
  verify a real instrument self-test (result code + message, raise on
  failure) instead of a bare identity query; `DMM.measure_dc_voltage()` is a
  new real, passive DC voltage measurement (verified finite + within the
  configured range); `test.py::test_daq()`'s deep channel read now verifies
  the readback is finite and within the configured ADC range instead of
  reporting PASS on any value. See `docs/architecture.md` Section 10.

- [DONE] `hardware/smu.py` — `connect()`/`disconnect()`/`identify()` implemented for
  real (opens a real `nidcpower.Session(resource_name=..., options=...)`;
  `identify()` runs a real self-test then returns `session.instrument_model`).
  Constructed from a `config/devices.py` `SMU_ASSIGNMENTS[...]` dict, matching
  the relay drivers' pattern.
  - [MUST] `set_charge_mode()` / `set_discharge_mode()` / `output_enable()` /
    `output_disable()` / `measure()` — still placeholders, deliberately out of
    scope (sourcing anything, even a small test current, is real instrument
    functionality with real electrical consequences, not a connectivity/
    verification check -- see docs/architecture.md Section 10, "what is
    deliberately NOT verified yet")

- [DONE] `hardware/daq.py` — `connect()`/`disconnect()`/`identify()` implemented for
  real (NI-DAQmx device enumeration + `self_test_device()`). Constructed from a
  `config/devices.py` `DAQ_CONFIG`-shaped dict.
  - [MUST] `read_channel()` / `read_all_batteries()` / `verify_zero_current()` —
    still placeholders; `test_daq()`'s "deep channel read" step uses `nidaqmx`
    directly until these are implemented (see `test.py::test_daq()` Step 3,
    which now verifies the reading is finite and in-range before PASS)

- [DONE] `hardware/dmm.py` — created. `connect()`/`disconnect()`/`identify()`
  implemented for real (opens a real `nidmm.Session`; `identify()` runs a
  real self-test). `measure_dc_voltage()` implemented for real (configure +
  read, verified finite and within the configured `range_v`) -- unlike SMU
  sourcing, a DMM measurement is passive and safe to exercise unconditionally.

- [ ] Fill in relay serial command protocol in `config/devices.py RELAY_CONFIG`
  - Only needed if serial is ever promoted beyond bench diagnostics -- production
    is the Numato Ethernet relay (see below), so this is no longer a [MUST]
  - Replace `"OPEN {ch}\r\n"` / `"CLOSE {ch}\r\n"` / `"QUERY {ch}\r\n"`
    with your controller's actual ASCII commands
  - Test with loopback or relay box before connecting batteries
  - Both `relay_serial.py` (new driver) and `relay_matrix.py` (legacy, unused/dead code) read from this config

- [DONE] `NUMATO_RELAY_MATRIX_CONFIG["ip"]` validated: Numato Lab 32-Channel Ethernet Relay
  Module confirmed reachable at `169.254.1.1:23`, Telnet login `admin`/`admin`
  confirmed working, relay commands and state readback confirmed correct.
  Production path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix`.

- [DONE] Mandatory relay safety sequence implemented in `hardware/relay_eth.py`:
  `close()`/`open()` force all relays off, verify, then (for `close()`) activate
  and verify only the requested relay -- raising `RelayStateVerificationError`
  and stopping on any mismatch. See `docs/architecture.md` section 6a.

- [DONE] Relay driver rebuilt around Numato's native command set -- no custom
  protocol. `hardware/relay_eth.py::NumatoRelayMatrix` now exposes two layers:
  native 0-based primitives (`write`, `read_relay`, `write_all`, `read_all`,
  `verify_single`, `verify_all`, `reset`) built directly on
  `relay on/off/read/readall/writeall/reset`, and the existing public 1-based
  API (`open`/`close`/`query`/`open_all`/`close_all`) which is implemented
  entirely on top of the native layer. `close()` now verifies both
  individually (`relay read N`) and in bulk (`relay readall`). Telnet layer
  adds command-rejection detection ("invalid" response) and one bounded
  automatic reconnect-and-retry on comms failure. `RELAY_COUNT` (in
  `config/settings.py`) is the single source of truth for relay count.

- [DONE] `relay readall` hex-bitmask parsing (`hardware/relay_eth.py::_parse_readall_response()`)
  confirmed correct against the physical Numato unit: a live run of
  `test_relay_matrix_scan()` (all 32 channels, ON -> READ -> OFF) and
  `test_hardware_discovery()` both passed end-to-end, decoded ACTIVE channel
  lists matched the physically observed relay state at every step.
  `test_relay_ethernet_test()` (native primitives) and
  `test_relay_safety_selftest()` were not additionally run live in this same
  session (to avoid unnecessary extra relay cycling beyond what was needed
  to confirm the fix) -- both share the identical `connect()`/`_login()`
  code path already proven working, so they are expected to pass too; run
  them for full confirmation when convenient.

- [DONE] Authentication root cause found and fixed: the Numato firmware
  sends a Telnet IAC option-negotiation request ("IAC DO 45") mid-handshake
  that the previous implementation never answered (a real Telnet client
  always does) -- this is why manual Telnet login succeeded while the
  framework reported "Authentication failed". Fixed by
  `hardware/relay_eth.py::NumatoRelayMatrix._handle_iac()` (RFC 854 option
  negotiation, decline-by-default) plus tolerant, case-insensitive prompt
  matching (`_read_until_any()`) since the real login prompt is "User Name: "
  (not "login:"). Confirmed by a live run against the physical unit -- see
  `docs/architecture.md` section 6c.

- [ ] `hardware/pxi_rack.py` — enumerate PXI cards at startup via VISA
  - Use `nidaqmx.system.System.local()` and NI-VISA to confirm expected cards
  - Report missing cards at startup rather than failing silently mid-test

- [ ] `hardware/temperature.py` — verify NTC Beta from the battery pack datasheet
  - Current default: Beta = 3950 K, R25 = 10 kOhm
  - Incorrect Beta shifts the displayed temperature; check at 0 degC, 25 degC, 45 degC

---

## Test Control

- [MUST] `test_control/charge_cycle.py` — wire in real NTC temperature read
  - Replace `t_c = None` with `temperature_sensor.read_celsius(daq.read_ntc(ch))`

- [MUST] `test_control/discharge_cycle.py` — same NTC wire-in as charge_cycle

- [ ] `test_control/battery_test.py` — wire up all hardware objects in `main.py`
  - Instantiate `SMU`, `DAQ`, `RelayFactory.create(cfg)`, `DataStorage`
  - Pass them to `BatteryTestSequence`

- [ ] `test_control/state_machine.py` — review and integrate if state tracking is needed

- [ ] Add rest period between charge and discharge if required by battery spec
  - Typical: 30 min OCV rest at room temperature

---

## Data

- [ ] `data/report.py` — implement test summary report
  - Compute capacity (Ah) per channel per cycle: integrate I over time
  - Generate V/I vs time plot (matplotlib or ASCII table)
  - Export to text or HTML in `data_output/reports/`

---

## Configuration

- [DONE] Removed `PXI_RESOURCE_DAQ`/`PXI_RESOURCE_DMM`/`PXI_RESOURCE_SMU1`/
  `PXI_RESOURCE_SMU2` from `config/settings.py` -- they duplicated the same
  values already in `config/devices.py`'s `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/
  `DMM_CONFIG`, and `HardwareManager` was reading `config/settings.py` instead
  of `config/devices.py`, silently able to diverge from it. Fixed:
  `HardwareManager.__init__()` now defaults `smu_cfg`/`daq_cfg` from
  `config/devices.py` directly. `config/devices.py` is the single source of
  truth for every device's resource string / address.

- [DONE] `BATTERY_CONFIGS` added to `config/devices.py` -- battery type/model
  catalog (chemistry, capacity, voltage/current/temp limits), plus a
  `"battery_type"` field on every `BATTERY_CHANNELS[i]` entry pointing to one.
  Validated at startup (`utils/device_validator.py`). Foundation for the
  future `data/battery_repository.py` (`docs/DATABASE_ROADMAP.md`).
  - [ ] Wire `BATTERY_CONFIGS` into `safety_monitor.py`/`charge_cycle.py`/
    `discharge_cycle.py` so per-battery limits actually apply instead of the
    single global `BAT_VOLTAGE_MAX`/etc. Not done yet -- deliberately deferred,
    same reasoning as the rest of the database roadmap.
  - Note: `BATTERY_CONFIGS` is capabilities/recommended ranges only, never the
    sole operational authority -- see `docs/architecture.md` Section 11
    "Operational Limit Resolution" (planned `LimitResolver`, doc-only).

- [DONE] PMU (=`hardware/smu.py::SMU`) treated as safety-critical: real
  `output_disable()`/`verify_output_disabled()`/`emergency_output_off(reason)`
  (never raises, logs CRITICAL on failure). Wired into
  `charge_cycle.py`/`discharge_cycle.py` (`try/finally` around the sampling
  loop -- closes the prior gap where an unhandled exception mid-loop never
  disabled output), `safety_monitor.py::emergency_stop()`,
  `hardware_manager.py` (startup safety check in both strict/lenient connect
  paths, `disconnect_all()`, and a new `_atexit_smu_shutdown()` alongside the
  existing relay one). See `docs/architecture.md` Section 12 "PMU Safety
  Philosophy". DAQ shutdown behavior deliberately NOT touched -- still
  measurement-only, per explicit instruction.

- [MUST] `config/settings.py`
  - Set `RELAY_COM_PORT` to your COM port (diagnostic serial path only)
  - Confirm `BAT_VOLTAGE_MAX / MIN` against battery datasheet
  - Decide: `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN` (3.5 V) -- which is correct?

- [MUST] `config/devices.py`
  - Confirm relay channel numbers match physical wiring on BLOSS Hub PCB
  - Confirm DAQ channel names match PCB-to-connector layout
  - Set `NUMATO_RELAY_MATRIX_CONFIG["ip"]` to actual relay IP address
  - Set `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` `"resource"` fields to match NI-MAX

- [DONE] `utils/device_validator.py` — startup validation of `config/devices.py`:
  every device instantiable, required fields present, no duplicate names/VISA
  resources/IPs/COM ports/relay identifiers, relay count consistency
  (`num_channels == channel_count == Settings.RELAY_COUNT`), every relay
  `"type"` registered in `RelayFactory`. Wired into `main.py` (right after
  `validate_settings()`, before `HardwareManager`) and `test.py`'s
  `preflight_check()` (before the menu). Construction-only -- never `connect()`.

---

## Infrastructure

- [DONE] `test.py` — modular test framework (Startup Device Validation/Hardware Discovery/
  SMU/DMM/DAQ/Relay Serial/Relay Ethernet/Relay Matrix Scan/RelayEthernetTest/
  Relay Safety Self-Test/E-Load/Sensors/Safety/Config/Database/MiniSQL/Run All)
- [DONE] `test_hardware_discovery()` — config-driven connectivity + identification
  test for every device type (SMU/PSU, DMM, DAQ, Relay Ethernet, Relay Serial).
  Uses the SAME production driver classes as `HardwareManager` (no duplicated
  connection logic, no instrument-specific code of its own, no bypass of the
  hardware abstraction layer) -- see docs/architecture.md Section 8.
- [DONE] Relay driver architecture (`relay.py`, `relay_serial.py`, `relay_eth.py`, `relay_factory.py`)
- [DONE] Mandatory relay safety sequence + audit logging in `relay_eth.py` (all-off ->
  verify -> activate -> verify, `RelayStateVerificationError` on any mismatch)
- [DONE] Confirmed `hardware/relay_matrix.py` (legacy) and `utils/ethernet_relay_python.py`
  (Numato reference script) are dead/reference-only code -- neither is imported or
  wired into `RelayFactory`; `NumatoRelayMatrix` is the single enforcement point for
  Numato relay state changes
- [DONE] Emergency Shutdown Strategy implemented end-to-end (design principle:
  unknown relay state = unsafe state): startup forces+verifies all-off
  (`HardwareManager.connect_all()`, aborts startup on failure), any relay
  verification/communication failure triggers `NumatoRelayMatrix._emergency_all_off()`
  before the exception propagates, `SafetyMonitor.emergency_stop()` and
  `HardwareManager.disconnect_all()` log CRITICAL (not just a warning) if their
  own force-off fails, and an `atexit`-registered backstop in `HardwareManager`
  covers exit paths that bypass the normal `finally:` shutdown. See
  `docs/architecture.md` Section 6d.
- [DONE] `data/storage.py` — StorageBackend ABC, DataStorage (SQLite + CSV), query()
- [DONE] `utils/validators.py` — validate_settings() with proper if/raise
- [DONE] `utils/errors.py` — NIPXITimeoutError, full exception hierarchy
- [DONE] `test_control/safety_monitor.py` — all limits + relay guard
- [DONE] `test_control/hardware_manager.py` — HardwareManager: connect_all/disconnect_all/health_check
- [DONE] `test_control/test_executor.py` — TestExecutor + TestRunResult / ChannelResult
- [DONE] `test_control/result_manager.py` — ResultManager: DataStorage lifecycle + MiniSQL hook
- [DONE] `main.py` — thin orchestration: no business logic, delegates to three managers
- [DONE] Documentation pass (README, architecture.md, CONFIGURATION.md, TODO.md)
- [DONE] System mode architecture (`config/system_mode.py`: `SystemMode` enum,
  `ModePolicy`, `get_mode_policy()`, `is_recovery_enabled()`). `HardwareManager
  .connect_all()` now dispatches to `_connect_all_strict()` (PRODUCTION,
  unchanged) or `_connect_all_lenient()` (DEVELOPMENT/VALIDATION -- missing
  devices warn/error and startup continues; an unverifiable relay is still
  fatal in every mode). Fixes laptop development friction (e.g. `DAQ
  'PXI1Slot2' not found` no longer aborts startup outside PRODUCTION). See
  `docs/architecture.md` Section 9.
- [DONE] Database location is now mode-separated (`data_output/development
  |validation|production/`) -- see `docs/DATABASE_ROADMAP.md`.
- [DONE] `hardware/simulated.py` -- `SimulatedSMU`/`SimulatedDAQ`/
  `SimulatedRelay`/`SimulatedBattery` extension-point stubs. NOT wired into
  `HardwareManager`/`RelayFactory` yet -- foundations only, per the mode
  architecture request.
- [ ] Wire `hardware/simulated.py` into `HardwareManager`'s lenient connect
  path (when `ModePolicy.allow_simulated_devices` and a real device is
  missing) and into `RelayFactory` (`"type": "simulated"`) -- see the
  module's docstring for the intended approach. Not done yet -- foundations
  only were requested.
- [ ] Cycle/state recovery engine (`docs/DATABASE_ROADMAP.md` Section 4) --
  only the `is_recovery_enabled()` configuration hook exists; no recovery
  logic, no `station_state` table, nothing reads the hook yet. Deliberately
  deferred until the mode/database foundations above were in place.
- [DONE] `data/sqlite_manager.py` -- minimal foundation: `create_database()`,
  `initialize_schema()`, `insert_test_record()`, `get_last_record()`, one
  `test_records` table. Verified passing on a laptop with no PXI hardware
  attached via `test.py`'s "Test SQLite (foundation)" menu item (create/open
  -> verify schema -> insert -> read back -> display -> PASS/FAIL). Not
  cycle recovery, not battery cycling, not a full repository layer --
  intentionally simple per `docs/DATABASE_ROADMAP.md` Section 1/2.
- [ ] `battery_repository.py` / `cycle_repository.py` / `measurement_repository.py`
  / `state_repository.py` (`docs/DATABASE_ROADMAP.md` Section 2) -- planned
  repository split of today's single `DataStorage` class, building on
  `sqlite_manager.py`. Not started; today's `StorageBackend` contract is
  deliberately small enough that this can happen later without touching
  `BatteryTestSequence`.
- [ ] Add `--dry-run` mode: `PXI_SIMULATE = True` + relay mock, exercises logic without hardware
- [ ] Create `flowcharts/vi_flowchart.md` (referenced in architecture.md)
- [ ] Set up remote Git repository and update README with URL
- [ ] CI: add basic linting (ruff or flake8) as a pre-commit hook

---

## Optional / Future

- [ ] MiniSQL storage backend: create `data/storage_minisql.py` implementing `StorageBackend`
  - See README section 14 and architecture.md section 7 for the integration path
- [ ] GUI (PyQt or tkinter) for live voltage/current/temperature monitoring
- [ ] Temperature chamber control (if environmental chamber is added)
- [ ] Export results to BLOAST main pipeline format
- [ ] Multi-SMU support: parallel channel testing (requires separate SMU per channel)
- [ ] `data/report.py`: matplotlib plots of V/I/T vs time per channel
