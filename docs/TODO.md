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
- [MUST] `SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/
  `output_disable()`*/`measure()` -- still placeholders (`*output_disable()`
  is real; the others are not). Sourcing anything, even a small test
  current, is real instrument functionality with real electrical
  consequences, not a connectivity check -- deliberately deferred.
- [MUST] `DAQ.read_channel()`/`read_all_batteries()`/`verify_zero_current()`
  -- still placeholders; `test_daq()`'s deep channel read uses `nidaqmx`
  directly until these exist.
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
  -- the only one of the three relay-scan-style loops without one.
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
here -- this is an index, not a changelog.

- **PXI rack inventory & `PXI_SLOTS`** -- real rack confirmed via NI-MAX
  (10 PXI-slot devices + GPIB0), single source of truth in
  `config/devices.py::PXI_SLOTS`, `SMU_ASSIGNMENTS`/`DAQ_CONFIG(S)`/
  `DMM_CONFIG(S)` derived from it by category. See `docs/architecture.md`
  Section 14, `docs/CONFIGURATION.md`.
- **Hardware Discovery + device selection workflow** -- grouped by category
  from `PXI_SLOTS`, identity-vs-configured-model comparison, N/A reporting
  for driver-less categories, `_discover_and_select()` reachability-scan
  picker reused across SMU/DMM/DAQ/Temperature Module/relay tests. See
  `docs/architecture.md` Section 8.2/8.2a.
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
