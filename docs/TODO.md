# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

This file tracks **remaining work only**. Completed architecture/features are
summarized (one line each, not full changelogs) in "Completed (Summary)" at
the bottom, with a pointer to where the real documentation lives
(`docs/architecture.md`, `docs/CONFIGURATION.md`) -- not duplicated here.

---

## Remaining Work

### Hardware drivers / PXI rack

- [ ] Wire `TEMP_MODULE` (PXIe-4353, slot 15) into a real driver -- this is
  the most likely real hardware source for the currently-stubbed per-channel
  `t_c` reading in `charge_cycle.py`/`discharge_cycle.py` (`t_c = None`
  today). `test_temperature_module()` today only does presence/identity
  (reusing `hardware.daq.DAQ`) -- no TC/RTD channel read exists. Would need a
  new NI-DAQmx-based `hardware/temperature.py` (or extend `DAQ`).
- [ ] Confirm the instrument connected at GPIB0 (`config/devices.py::GPIB_INSTRUMENTS`)
  -- likely the "Programmable Electronic Load" or "Programmable Power Supply"
  from `equipment_Requirement.md`, not yet confirmed. No GPIB driver class
  exists in this codebase.
- [ ] Decide whether to keep `CHASSIS_RELAY_MATRIX` (PXIe-2569, slot 11)
  unused, or build a `niswitch`-based driver for it as an alternative to the
  Numato Ethernet relay -- currently present in the chassis, reported N/A by
  Hardware Discovery, disabled/unwired.
- [ ] Multi-SMU/multi-DAQ channel assignment: `HIGH_POWER_SMU`/`AUX_SMU_1`/
  `AUX_SMU_2` and `EXPANSION_DAQ`/`PRECISION_DAQ` are configured and
  individually testable but not yet assigned to any battery channel --
  `HardwareManager` still drives only `PRIMARY_SMU`/`MAIN_DAQ`.
- [MUST] `SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`*/
  `output_disable()`*/`measure()` -- still placeholders for the *battery*
  charge/discharge path (`*output_disable()` is real; `output_enable()`/
  `set_charge_mode()`/`set_discharge_mode()`/`measure()` are not). Sourcing
  current into a real battery channel has real electrical consequences well
  beyond a connectivity check -- deliberately deferred. `set_discharge_mode()`
  must be implemented as a current-SINK at a positive voltage when this work
  starts -- NOT a negative-voltage source (see docs/architecture.md Section
  12.6). Note: bench-only DC voltage sourcing for SMU Functional Validation IS
  implemented and real -- `SMU.source_dc_voltage_point()` (see
  docs/architecture.md Section 12.6) -- this is a separate, narrow, positive-
  voltage-only capability with no relay/battery/channel involvement, not the
  battery charge/discharge path above. When this work starts, it MUST follow
  the configuration-verification contract now documented in
  docs/architecture.md Section 12.6b (readback+verify every commanded
  attribute via `_verify_config_readback()`, real `measure()` readback feeding
  `SafetyMonitor.check()`, no limit logic duplicated inside `hardware/smu.py`,
  and never an equality check between measured and commanded voltage/current).
- [MUST] `DAQ.read_all_batteries()`/`verify_zero_current()` -- still
  placeholders (multi-channel synchronized acquisition). `DAQ.read_channel()`
  (single-channel read) IS implemented and real now -- moved out of
  `test.py::_functional_daq()` into `hardware/daq.py`, matching the SMU/DMM/
  Relay architecture (test.py orchestrates, hardware/*.py implements). See
  docs/architecture.md Section 8.2b.
- [ ] `hardware/pxi_rack.py` -- enumerate PXI cards at startup and cross-check
  against `PXI_SLOTS`, reporting a mismatch before any test runs rather than
  failing mid-test on a missing card.
- [ ] Verify NTC Beta/R25 (`hardware/temperature.py` -- the NTC-thermistor
  math module, distinct from the PXIe-4353 temperature module above) against
  the actual battery pack datasheet. Current default: Beta = 3950 K, R25 = 10 kOhm.
- [ ] Fill in relay serial command protocol in `config/devices.py::RELAY_CONFIG`
  -- only needed if serial is ever promoted beyond bench diagnostics;
  production is the Numato Ethernet relay.

### Cancellation architecture

- [ ] Close the `HardwareManager.connect_all()` gap: install the SIGINT
  handler/token and activate the `disconnect_all()` teardown net *before*
  `connect_all()` runs, not after (`main.py` and `test.py::run_main_test()`)
  -- pre-existing, not introduced by the cancellation feature, but surfaced
  by its review. See `docs/architecture.md` Section 13.7/17.
- [ ] Add the same cancellation checkpoint to `test.py::test_relay_safety_selftest()`
  -- the only one of the three relay-scan-style loops without one. Now reached
  from "Test Numato Relay Matrix (Ethernet)" -> Functional Validation, not a
  top-level menu item, but the function and gap are unchanged.
- [ ] Fix the adjacent `continue`-after-charge gap in `BatteryTestSequence.run()`
  (skips both `relay.open(ch)` and `emergency_stop()`) before real DAQ
  acquisition replaces the current always-zero-current stub -- currently
  unreachable, will become live once DAQ read is implemented.
- [ ] Wire `TIMEOUT` end-to-end (`ChargeCycle`/`DischargeCycle` already return
  `False` on timeout; `BatteryTestSequence.run()` still discards it) once
  per-channel `ChannelResult` propagation (see Test Control below) exists.
- [ ] Persist `stop_reason` to the database/report once state persistence
  work begins -- currently only lives on the in-memory `TestRunResult`.

### Test control / data

- [ ] `test_control/charge_cycle.py` / `discharge_cycle.py` -- wire in the
  real NTC (or PXIe-4353) temperature read, replacing `t_c = None`.
- [ ] `TestExecutor._run_sequence()` -- return real per-channel `ChannelResult`
  data (currently marks every requested channel as fully completed once
  `BatteryTestSequence.run()` returns without raising, regardless of what
  actually happened per channel).
- [ ] Add rest period between charge and discharge if required by battery
  spec (typical: 30 min OCV rest at room temperature).
- [ ] `data/report.py` -- implement the test summary report: capacity (Ah)
  per channel per cycle, V/I vs time plot, export to `data_output/reports/`.
- [ ] Wire `BATTERY_CONFIGS` into `safety_monitor.py`/`charge_cycle.py`/
  `discharge_cycle.py` so per-battery limits actually apply instead of the
  single global `BAT_VOLTAGE_MAX`/etc. See `docs/architecture.md` Section 11
  ("Operational Limit Resolution" / planned `LimitResolver`, doc-only today).
- [ ] Cycle/state recovery engine (`docs/DATABASE_ROADMAP.md` Section 4) --
  only the `is_recovery_enabled()` config hook exists; no recovery logic, no
  `station_state` table.
- [ ] `battery_repository.py`/`cycle_repository.py`/`measurement_repository.py`/
  `state_repository.py` (`docs/DATABASE_ROADMAP.md` Section 2) -- planned
  repository split of today's single `DataStorage` class.
- [ ] Wire `hardware/simulated.py` into `HardwareManager`'s lenient connect
  path and `RelayFactory` (`"type": "simulated"`) -- foundations only exist
  today.

### Configuration

- [MUST] Confirm relay channel numbers match physical wiring on the BLOSS
  Hub PCB, and DAQ channel names (`BATTERY_CHANNELS[i]["daq_voltage_ch"]`
  etc.) match the PCB-to-connector layout -- these still assume a "Dev1"
  NI-MAX alias for `MAIN_DAQ` (`PXI_SLOTS[2]`) that has not been confirmed
  against NI-MAX on the real machine.
- [MUST] `config/settings.py` -- confirm `BAT_VOLTAGE_MAX`/`MIN` against the
  battery datasheet; decide `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN`
  (3.5 V) -- which is correct?
- [MUST] Set `RELAY_COM_PORT` to the real COM port if serial relay diagnostics
  are ever used (diagnostic path only).

### Infrastructure

- [ ] Add a `--dry-run` / `PXI_SIMULATE` mode exercising test logic without
  hardware (builds on `hardware/simulated.py` above).
- [ ] Create `flowcharts/vi_flowchart.md` (referenced in `docs/architecture.md`
  but does not exist yet).
- [ ] Set up a remote Git repository and update `README.md` with the URL.
- [ ] CI: add basic linting (ruff or flake8) as a pre-commit hook.

---

## Optional / Future

- [ ] MiniSQL storage backend: `data/storage_minisql.py` implementing
  `StorageBackend` (see `README.md` Section 15, `docs/architecture.md`
  Section 7).
- [ ] GUI (PyQt or tkinter) for live voltage/current/temperature monitoring.
- [ ] Temperature chamber control (if an environmental chamber is added).
- [ ] Export results to the BLOAST main pipeline format.
- [ ] `data/report.py`: matplotlib plots of V/I/T vs time per channel.
- [ ] Emergency Abort (operator-typed `ABORT`, escalates over Safe
  Cancellation) -- designed (`docs/architecture.md` Section 13.8) but
  deliberately not implemented.

---

## Completed (Summary)

Full detail lives in `docs/architecture.md` and `docs/CONFIGURATION.md`, not
here -- this is an index, not a changelog. See `docs/MILESTONES.md` for the
first real-rack hardware bring-up milestone record.

- **SMU configuration verification (post-Milestone-1 hardening)** --
  `source_dc_voltage_point()` now reads back `voltage_level`/`current_limit`/
  `output_enabled` from the NI-DCPower session after `commit()` and verifies
  each against what was just commanded (`SMU._verify_config_readback()`),
  raising the new `SMUStateVerificationError` on mismatch -- the same
  COMMAND->READBACK->VERIFY->fail-on-mismatch philosophy already proven in
  `hardware/relay_eth.py`. The `finally` teardown now also verifies output
  is actually OFF (`verify_output_disabled()`), not command-only. Tolerance
  (`config/settings.py` `SMU_VOLTAGE_READBACK_TOLERANCE_V`/
  `SMU_CURRENT_READBACK_TOLERANCE_A`) is an attribute round-trip bound, not
  a measurement-accuracy figure -- these properties are stored IVI setpoints
  echoed by the driver, not a new ADC measurement. Documents the contract
  the future battery charge/discharge implementation must follow (see the
  `[MUST]` item above and docs/architecture.md Section 12.6b) -- limit
  enforcement stays entirely in `SafetyMonitor`, never duplicated in
  `hardware/smu.py`, and measured values are never equality-checked against
  commanded setpoints.
- **Hardware Bring-Up Milestone 1 (real PXIe rack validation)** -- Hardware
  Discovery/identification, SMU Functional Validation, DMM Functional
  Validation, and both Numato Ethernet Relay Matrix units all confirmed PASS
  against real physical hardware (not simulation). See `docs/MILESTONES.md`.
- **`smu_channel` / `channels_per_card` (config-driven NI-DCPower channel
  selection)** -- root-caused a real rack bring-up failure on the PXI-4130
  (`AUX_SMU_1`/`AUX_SMU_2`, 2-channel cards): an unscoped `nidcpower.Session`
  raised error `-1074118522` ("single channel must be specified") the moment
  any repeated-capability property was set. Fixed by adding `smu_channel`
  (NI-DCPower channel name) and `channels_per_card` (physical channel count)
  to every `PXI_SLOTS` SMU entry; `hardware/smu.py::SMU.connect()` now opens
  its session scoped to exactly the configured channel -- no hardcoded
  channel anywhere in the driver, same code path for single- and
  multi-channel cards. Confirmed on physical hardware: both `AUX_SMU_1` and
  `AUX_SMU_2` are wired to channel `"1"`.
- **Numato Ethernet relay naming/network finalization** -- both Numato units
  renamed to `MATRIX_NUMATO_201`/`MATRIX_NUMATO_202` (named after their
  static IP's last octet), static IPs assigned (`169.254.1.201`/`.202`, DHCP
  disabled), replacing the old factory-default `169.254.1.1` and the interim
  `MAIN_MATRIX_ETH`/`AUX_MATRIX_ETH_1` role-based names (kept as backward-
  compat aliases). See `docs/CONFIGURATION.md`.
- **Operator-facing device display names** -- `config/devices.py::
  device_display_name()` derives a hardware-identifying label (e.g.
  `NI4130-Slot7-Ch1`, `Numato-169.254.1.201`) from each device's own
  model/slot/ip/channel config, shown throughout `test.py`'s menus and test
  output alongside (not replacing) the internal nickname.
- **`test.py` "return to Main Menu" workflow** -- every menu selection (PASS,
  WARNING, FAIL, exception, or operator cancellation) now consistently
  returns to the top-level Main Menu via one centralized dispatch function,
  instead of exiting the app or leaving the operator in a nested menu.

- **PXI rack inventory & `PXI_SLOTS`** -- real rack confirmed via NI-MAX
  (10 PXI-slot devices + GPIB0), single source of truth in
  `config/devices.py::PXI_SLOTS`, `SMU_ASSIGNMENTS`/`DAQ_CONFIG(S)`/
  `DMM_CONFIG(S)` derived from it by category. See `docs/architecture.md`
  Section 14, `docs/CONFIGURATION.md`.
- **Hardware Discovery + device selection workflow** -- grouped by category
  from `PXI_SLOTS`, identity-vs-configured-model comparison, N/A reporting
  for driver-less categories, shared `_run_hardware_category()` select-device
  -> Identity Validation / Functional Validation workflow reused across
  SMU/DMM/DAQ/Temperature Module/Numato Relay Matrix/PXI Relay Matrix
  (Temperature Module and PXI Relay Matrix have no Functional Validation
  implemented yet). See `docs/architecture.md` Section 8.2/8.2a/8.2b.
- **SMU and DMM Functional Validation (laboratory, operator physically
  present)** -- `SMU.source_dc_voltage_point()` (bench-only DC voltage
  sourcing, safe state -> 0 V -> charge validation voltage -> 0 V -> output
  OFF, verified) and the enhanced `_functional_dmm()` (operator instructions
  + "Measured Voltage" display). Positive-voltage only -- an earlier version
  sourced a negative validation point to demonstrate bipolar capability;
  corrected because NIPXI discharge is a current-sink operation, not a
  negative-voltage source, and bipolar capability is only documented for
  `PRIMARY_SMU` (PXIe-4141) in this codebase, not the other three configured
  SMUs. Validation voltage/current/range reuse `Settings.CHARGE_VOLTAGE_V`/
  `Settings.CHARGE_CURRENT_A`/`Settings.BAT_VOLTAGE_MAX`/`DMM_CONFIGS` -- no
  new configuration introduced. See `docs/architecture.md` Section 12.6,
  README.md Section 8.1b.
- **DAQ Functional Validation architecture correction** -- `read_channel()`
  moved from being implemented directly inside `test.py::_functional_daq()`
  (a raw `nidaqmx.Task()` block) into `hardware/daq.py::DAQ.read_channel()`,
  matching the SMU/DMM/Relay pattern (test.py orchestrates only,
  hardware/*.py owns all instrument-specific behavior). No functional or
  reporting change. See `docs/architecture.md` Section 8.2b.
- **Identity vs Functional Validation menu simplification** -- the operator
  menu was narrowed to the current hardware bring-up scope (identification +
  readiness, safe to do remotely over RDP); the Electronic Load stub, MiniSQL
  hooks stub, bench-only serial relay test, and three flat Numato relay
  commissioning menu items were removed from `MENU` (code kept, still
  reachable directly or via each category's Functional Validation submenu).
  See `docs/architecture.md` Section 8.2a/8.2b, README.md Section 8.1a/8.1b.
- **Safe Cancellation Architecture** -- `CancellationToken`/
  `OperationCancelledError`/`StopReason`, checkpoints in charge/discharge
  cycles, `BatteryTestSequence`, relay scan tests, SIGINT wiring in
  `main.py`/`test.py`, immediate relay-open-on-fault fix in
  `BatteryTestSequence.run()`. See `docs/architecture.md` Section 13/16/17.
- **Instrument verification philosophy** -- SMU/DMM/DAQ `identify()` now run
  real self-tests (command -> readback -> verify), never a bare identity
  query; `DMM.measure_dc_voltage()` is a real, verified measurement. See
  `docs/architecture.md` Section 10.
- **PMU (SMU) safety-critical treatment** -- real `output_disable()`/
  `verify_output_disabled()`/`emergency_output_off()`, wired into
  charge/discharge cycles, `safety_monitor.py`, `hardware_manager.py`
  (startup check, shutdown, `atexit`). See `docs/architecture.md` Section 12.
- **Relay architecture** -- Numato Lab 32-Channel Ethernet Relay Module is
  the sole production relay driver, mandatory all-off -> verify ->
  activate -> verify sequence, Telnet IAC/auth root cause found and fixed,
  Emergency Shutdown Strategy implemented end-to-end. See
  `docs/architecture.md` Section 6.
- **System modes** (`DEVELOPMENT`/`VALIDATION`/`PRODUCTION`) -- hardware
  strictness, database location, and (config-only) recovery/simulation hooks
  all driven by one `SYSTEM_MODE` setting. See `docs/architecture.md`
  Section 9.
- **Operational Limit Resolution philosophy** -- `BATTERY_CONFIGS` is
  capabilities/recommended ranges only, never sole operational authority
  (planned `LimitResolver`, documentation only). See `docs/architecture.md`
  Section 11.
- **`data/sqlite_manager.py`** -- minimal foundation (`create_database()`,
  `initialize_schema()`, `insert_test_record()`, `get_last_record()`),
  verified passing without PXI hardware attached.
- **Startup device validation** (`utils/device_validator.py`) -- every
  configured device instantiable, required fields present, no duplicate
  names/resources/IPs/COM ports/relay identifiers, relay count consistency.
- Removed duplicate `PXI_RESOURCE_*` constants from `config/settings.py` --
  `config/devices.py` is the single source of truth for every resource
  string.
- `test.py` modular test framework, `main.py` thin orchestration, core
  hardware drivers (`relay.py`/`relay_serial.py`/`relay_eth.py`/
  `relay_factory.py`/`smu.py`/`daq.py`/`dmm.py`), `data/storage.py`
  (`StorageBackend`/`DataStorage`), `test_control/` (`safety_monitor.py`/
  `hardware_manager.py`/`test_executor.py`/`result_manager.py`).
