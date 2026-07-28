# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

This file tracks **remaining work only**. Completed architecture/features are
summarized (one line each, not full changelogs) in "Completed (Summary)" at
the bottom, with a pointer to where the real documentation lives
(`docs/architecture.md`, `docs/CONFIGURATION.md`) -- not duplicated here.

---

## Remaining Work

### Hardware drivers / PXI rack

- [ ] `TEMP_MODULE` (PXIe-4353, slot 15) has no real driver, and now has no
  standalone top-level MENU entry either (retired -- see
  `docs/architecture.md` Section 23b; `test_temperature_module()`/
  `_identify_temperature()` still exist and are still covered by Hardware
  Discovery). Battery temperature monitoring is expected to come entirely
  through the DAQ NTC path (`test_sensors()`'s Test 6,
  `BATTERY_CHANNELS[i]["daq_ntc_ch"]`) instead -- if a real TC/RTD-specific
  need for this module is ever identified, revisit whether it's still
  worth a driver at all before building one.
- [ ] Confirm the instrument connected at GPIB0 (`config/devices.py::GPIB_INSTRUMENTS`)
  -- likely the "Programmable Electronic Load" or "Programmable Power Supply"
  from `equipment_Requirement.md`, not yet confirmed. No GPIB driver class
  exists in this codebase.
- [ ] `niswitch`-based `CHASSIS_RELAY_MATRIX` (PXIe-2569, slot 11) driver --
  long-term goal is functional parity with the Numato relay validation
  suite. Architecture reviewed and documented (`docs/architecture.md`
  Section 23d): `test_relay_matrix_scan()`/`test_relay_safety_selftest()`
  already operate purely through `RelayFactory`/`RelayBase` and will work
  against this driver unchanged once it exists; only
  `test_relay_ethernet_test()` (Numato-native 0-based primitives) would
  need a PXI-native equivalent, if one is worth building. Steps: (1)
  `RelayBase`-conforming driver class, (2) a new `RelayFactory.create()`
  branch, (3) a `PXI_RELAY_MATRIX_CONFIGS`-equivalent enumeration dict in
  `config/devices.py`, (4) the two generic tests above then work with zero
  changes.
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
  `is_recovery_enabled()` config hook and a `station_state` table now exist
  (`data/storage.py`, added for Proto Test Execution, Milestone 2 -- see
  docs/architecture.md Section 18), and `get_last_execution_state()` DISPLAYS
  the previous run's last position at startup, but no automatic resume
  logic exists yet -- still display-only, by this milestone's explicit
  scope, not a gap introduced now.
- [ ] `battery_repository.py`/`cycle_repository.py`/`measurement_repository.py`/
  `state_repository.py` (`docs/DATABASE_ROADMAP.md` Section 2) -- planned
  repository split of today's single `DataStorage` class.
- [ ] Wire `hardware/simulated.py` into `HardwareManager`'s lenient connect
  path and `RelayFactory` (`"type": "simulated"`) -- foundations only exist
  today.
- [ ] Charge Battery / Discharge Battery / Cycle Battery -- Run Main Test
  submenu placeholders only, not implemented (see `docs/architecture.md`
  Section 20). Should reuse the same Battery Type/Group/Position selection,
  confirmation screen, and traceability logging Monitor Battery established.
- [MUST] Migrate `MonitorBatterySequence` from its temporary DMM voltage
  source back to the final per-position DAQ architecture
  (`BATTERY_CHANNELS[i]["daq_voltage_ch"]`/`daq_current_ch"]`) once the
  channel/device configuration issue that blocked the original DAQ path is
  resolved and confirmed against real NI-MAX aliases/wiring -- see
  `docs/architecture.md` Section 20a. Until then, Monitor Battery reads one
  shared DMM regardless of selected Group/Position, and `current_a` is
  always `None` (DMM is voltage-only).
- [ ] Physical rack validation of Monitor Battery (DMM voltage path) on
  real Group A hardware -- verified with mocked hardware only so far (see
  `docs/MILESTONES.md` Milestone II).
- [ ] NTC temperature reads in `MonitorBatterySequence` remain `None` --
  same pre-existing gap already carried by `charge_cycle.py`/
  `discharge_cycle.py`.
- [ ] `BATTERY_CONFIGS`' `HUB`/`SB` voltage/current/temperature limits
  marked `# unconfirmed placeholder` (assumed standard Li-ion window and
  0.5C/1C ratios, not from a datasheet) should be confirmed against the
  real datasheet before being relied on for safety enforcement. Only
  `nominal_voltage_v`/`capacity_ah` are currently confirmed.
- [ ] `Settings.ACTIVE_CHANNELS` -> `ACTIVE_POSITIONS` rename, deliberately
  deferred from the `NUM_CHANNELS` -> `BATTERY_POSITIONS` rename (would
  touch `test_control/` files outside that change's scope).

### Configuration

- [MUST] Confirm relay channel numbers match physical wiring on the BLOSS
  Hub PCB, and DAQ channel names (`BATTERY_CHANNELS[i]["daq_voltage_ch"]`
  etc.) match the PCB-to-connector layout -- these still assume a "Dev1"
  NI-MAX alias for `MAIN_DAQ` (`PXI_SLOTS[2]`) that has not been confirmed
  against NI-MAX on the real machine.
- [MUST] `config/settings.py` -- confirm `BAT_VOLTAGE_MAX`/`MIN` against the
  battery datasheet; decide `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN`
  (3.5 V) -- which is correct? Re-surfaced concretely by the new Safety
  Monitor workflow simulator (`test.py::test_safety_monitor()` Part 2, see
  `docs/architecture.md` Section 23e): simulating a discharge to
  `DISCHARGE_CUTOFF_V` directly trips `BAT_VOLTAGE_MIN`'s Undervoltage
  check every time, since 3.0 V < 3.5 V under the currently-enforced logic.
- [MUST] Set `RELAY_COM_PORT` to the real COM port if serial relay diagnostics
  are ever used (diagnostic path only).

### Infrastructure

- [ ] Add a `--dry-run` / `PXI_SIMULATE` mode exercising test logic without
  hardware (builds on `hardware/simulated.py` above).
- [ ] Continue expanding `test.py::test_safety_monitor()`'s Part 2 workflow
  walkthrough (see `docs/architecture.md` Section 23e, now the designated
  development reference implementation for Charge/Discharge/Cycle Battery)
  into a full development/validation harness ahead of deploying against
  real hardware. Candidates: more injected-fault scenarios (overcurrent,
  undervoltage, relay-switch guard mid-workflow), wiring the same
  simulated per-step values through `ExecutionFrame`/`render_execution_frame()`
  for a full mock execution screen (natural pairing with the UI Test menu
  item), or driving the walkthrough from `BATTERY_CONFIGS`' actual per-type
  limits instead of only `Settings.BAT_*`.
- [ ] When Charge/Discharge/Cycle Battery are actually implemented, map
  each real code path back onto `test.py::_charge_phase_steps()`/
  `_discharge_phase_steps()`/`_cycle_battery_walkthrough_steps()` and
  confirm the simulator's step sequence still matches -- update the
  simulator if the real implementation's sequence diverges, so it remains
  an accurate reference rather than a stale blueprint.
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

- **Proto Test Execution (Milestone 2 -- infrastructure validation, no
  battery)** -- `test.py::run_proto_test_execution()` +
  `test_control/proto_test_sequence.py::ProtoTestSequence` exercise the real
  production architecture end-to-end (`HardwareManager`, relay, SMU, DMM,
  SQLite, `CancellationToken`/Ctrl+C, safe shutdown) with no battery
  connected -- cycles every configured relay, sources+verifies a bench SMU
  voltage point (`hardware/smu.py::SMU.source_dc_voltage_point()`, reused
  unchanged, extended with two new backward-compatible optional parameters
  `hold_s`/`during_hold` so a DMM reading happens while output is still
  active), and persists relay/state/SMU/DMM data to a new `station_state`
  table (`data/storage.py`). Displays (never auto-resumes) the previous
  execution's last known position at startup. **Validated end-to-end on the
  physical PXIe rack** (`AUX_SMU_1`/PXI-4130 Slot 7 Ch1 -> `MAIN_DMM`/NI-4065
  Slot 3, no battery/load) -- all 8 relays cycled successfully. A one-time
  first-relay measurement transient was observed and root-caused (no
  settling delay between NI-DCPower `initiate()` and the first `measure()`
  call, only exercised on the session's first commit/initiate cycle) --
  documented, not fixed, per `docs/MILESTONES.md` Milestone 2. See
  `docs/architecture.md` Section 18 and `docs/MILESTONES.md`.
- Also fixed since real-rack validation began: `Settings.PROTO_TEST_SMU_NAME`
  (`config/settings.py`, default `"AUX_SMU_1"`) lets `run_proto_test_execution()`
  target a specific SMU instead of the positional `next(iter(SMU_ASSIGNMENTS...))`
  default (which always resolved to `PRIMARY_SMU`, regardless of which unit
  was physically wired up) -- scoped to this one function only.
  `test_control/proto_test_sequence.py` also gained `print()`-based console
  progress (relay/phase/measurements), since `test.py` never configures a
  logging handler for this workflow.
- **Milestone II data/UI architecture (in progress)** -- Phase 1:
  `measurements` extended into the authoritative historical result store
  for every test type (`test_type`/`relay`/`phase_detail`/SMU-DMM columns,
  additive migration, `data/storage.py::DataStorage.record_measurement()`);
  `station_state` narrowed to execution-recovery-only; new `run_summary`
  (one row per run, `id` = operator-facing Run Number) and `event_log`
  (fine-grained runtime narrative) tables. Phase 2:
  `test_control/execution_screen.py::ExecutionFrame`/`render_execution_frame()`
  -- the one shared runtime UI for Proto Test, future Battery Charge/
  Discharge/cycle execution, the Historical Results Viewer, and
  `UI Preview Test`; both `from_live()`/`from_database()` constructors
  built together to prevent live/replay drift. See `docs/architecture.md`
  Sections 18/18a. `ProtoTestSequence`'s migration to this infrastructure
  (Phase 3) and the Historical Results Viewer/`UI Preview Test` menu
  entries (Phase 4) are still pending.
- **Monitor Battery (Milestone II)** -- Run Main Test replaced with a
  submenu (`1. Monitor Battery`/`2. Charge Battery`/`3. Discharge Battery`/
  `4. Cycle Battery`); only Monitor Battery implemented. Real battery
  catalog (`HUB`/`SB`, replacing the placeholder `GENERIC_LIION_18650` --
  only `nominal_voltage_v`/`capacity_ah` confirmed from spec, remaining
  limit fields still assumed placeholders pending datasheet confirmation),
  explicit operator-controlled battery type
  selection (never inferred from `BATTERY_CHANNELS`), and new
  `BATTERY_GROUPS` relay-routing architecture (Group A/`MATRIX_NUMATO_201`
  enabled today, B/C/D pre-wired for future matrices) with
  `resolve_group_position()`/`group_for_position()` helpers. `NUM_CHANNELS`
  renamed to `BATTERY_POSITIONS` (+ new `GROUP_SIZE`). Confirmation screen
  (Mode/Battery Type/Capacity/Group/Position/limits, `Continue? (Y/N)`)
  gates every relay activation; accepting it writes a `run_summary`
  battery-config snapshot and a mandatory, ordered `event_log` traceability
  sequence *before* the relay closes or any measurement is taken. New
  `test_control/monitor_battery_sequence.py::MonitorBatterySequence`
  mirrors `ProtoTestSequence`'s structure, reusing
  `measurements`/`run_summary`/`event_log`/`station_state`/`ExecutionFrame`
  unchanged -- read-only monitoring, no charging/discharging/SMU sourcing.
  `ExecutionFrame` gained `battery_voltage`/`battery_current`/`battery_temp`
  fields (reusing the original `measurements.voltage_v`/`current_a`/
  `temp_c` columns). Relay Functional Validation's Matrix Scan gained a
  group-scope menu (`All Groups`/Group A-D). Verified with mocked-hardware
  smoke tests (traceability ordering, hardware untouched on declined
  confirmation); physical rack validation still pending. See
  `docs/architecture.md` Sections 19-21 and `docs/MILESTONES.md`
  Milestone II.
- **Fix: Relay Functional Validation group scoping bug** -- `_select_relay_scope()`
  incorrectly gated scope resolution on `BATTERY_GROUPS[group]["enabled"]`,
  silently falling back to "All Groups" (scanning channels 1-32) for every
  Group B/C/D selection instead of the requested 8-channel range. Fixed by
  resolving purely from `position_start`/`position_end` regardless of
  `enabled` (a battery-wiring concern irrelevant to raw relay-hardware
  testing). Also added an explicit "Relay validation scope: ..."/"Relays
  under test: N-M" banner before every scan. See `docs/architecture.md`
  Section 21.
- **Monitor Battery: temporary DMM voltage source** -- the original
  per-position DAQ voltage read failed during real-hardware validation
  (unresolved channel/device configuration); `MonitorBatterySequence` now
  reads voltage from the already-validated DMM (`dmm.measure_dc_voltage()`)
  instead, once per loop iteration -- `current_a`/`temp_c` stay `None`
  since the DMM is voltage-only. Explicitly documented as temporary (module
  docstring TODO + `docs/architecture.md` Section 20a); migration back to
  the final per-position DAQ architecture remains open (see Remaining Work
  above). New `event_log` entry `"Monitoring source: DMM"` per session.
  `run_summary` gained six additive columns (`start_voltage`/`end_voltage`/
  `min_voltage`/`max_voltage`/`average_voltage`/`sample_count`), populated
  at end-of-run from an in-memory running accumulation
  (`monitor_battery_sequence.py::_VoltageStats`) -- no new storage
  mechanism.
- **Hardware identity traceability (Milestone II)** -- battery configuration
  was already captured via `run_summary`/`event_log`, but which physical
  SMU/DMM/DAQ/relay matrix executed a run was not (console-only, lost at
  session end). Extended the same traceability pattern: `run_summary` gains
  twelve additive columns (`smu_name`/`smu_resource`/`smu_model`, `dmm_*`,
  `daq_*`, `relay_matrix_*`), and both `run_proto_test_execution()` and
  `_run_monitor_battery()` log one `event_log` entry per connected
  instrument plus a "Hardware configuration snapshot recorded" entry,
  before the first relay closes. New shared helpers
  `config/devices.py::find_config_name()`/`hardware_traceability_messages()`
  and `test.py::_hardware_snapshot_fields()` avoid duplicating identity
  resolution or message wording between test types.
  `ProtoTestSequence.run()` gained one new optional, backward-compatible
  `hardware_snapshot` parameter; `MonitorBatterySequence` needed no change.
  No new table; verified with mocked smoke tests (traceability precedes
  relay activation for both test types) and real-DB round-trip/migration
  tests. See `docs/architecture.md` Section 22.
- **Menu Restructuring Review** -- full `test.py` execution-tree review
  (`docs/EXECUTION_TREE_REVIEW.md`) followed by nine architectural
  decisions: `test_sensors()` gained a DAQ-based NTC channel scan
  (config-driven via `BATTERY_CHANNELS`' existing `enabled` flag); "Test
  Temperature Module" retired as a standalone MENU entry (DAQ covers
  temperature now; function/Hardware Discovery coverage kept); Numato
  relay timing differences reviewed and confirmed intentional (no code
  change); PXI Relay Matrix future reuse architecture documented (no
  driver exists yet); "Test Safety Monitor" became a workflow-oriented
  simulator (Part 2: step-by-step Monitor/Charge/Discharge/Cycle Battery
  phase simulation against the real `SafetyMonitor` logic, no hardware/DB);
  "Test Configuration" removed (redundant with `preflight_check()`); "Test
  SQLite"/"Test Database Layer" consolidated into a new "Database Tools"
  submenu with 5 new real-database read-only inspection views; "Run All
  Tests" replaced with "UI Test" (hardware/DB-free `ExecutionFrame` demo
  screens via the real `render_execution_frame()`). Top-level MENU count:
  16 -> 13. Verified: `py_compile` clean, every non-hardware-run MENU
  entry smoke-tested with no unhandled exceptions, Database Tools views
  confirmed against the real development database. See
  `docs/architecture.md` Section 23 and `docs/MILESTONES.md`.
- **Safety Monitor Simulator -- full workflow walkthrough** -- follow-up
  enhancement: Part 2 of `test_safety_monitor()` became an interactive,
  operator-selected (`1. Monitor` / `2. Charge` / `3. Discharge` /
  `4. Cycle`), step-by-step operational walkthrough -- not just safety
  decisions, but every action a real workflow executes (load config,
  resolve group/position/relay routing, close relay, configure/enable
  PSU, acquire measurement, run the real `SafetyMonitor` check, update
  `ExecutionFrame`, store measurement, evaluate transitions, ...), pausing
  for Enter between steps. Each step renders Workflow/Current Phase/
  Current Step/Description plus Voltage/Current/Temperature/Safety
  Evaluation/Decision/Next Action. Now designated the development
  reference implementation for Charge/Discharge/Cycle Battery --
  `_monitor_battery_walkthrough_steps()` mirrors the real, already-
  implemented `MonitorBatterySequence.run()` exactly. Step counts:
  Monitor 16, Charge 16, Discharge 14, Cycle 31 (aborts at step 28/31 on
  its injected overtemperature fault). No hardware/relay/instrument/
  database access anywhere in the code path -- confirmed by inspection.
  See `docs/architecture.md` Section 23e and `docs/MILESTONES.md`.
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
