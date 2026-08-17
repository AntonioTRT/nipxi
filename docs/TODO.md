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
  `HardwareManager` still drives only `PRIMARY_SMU`/`MAIN_DAQ`. **Sharpened
  by the Battery Group Assignment review (docs/architecture.md Section 38):**
  `BATTERY_CHANNELS` only covers global positions 1-8 today (hardcoded
  `range(1, 9)`); naively extending its per-position DAQ-channel formula to
  Group B (positions 9-16) would collide with Group A's channels on the
  same shared `MAIN_DAQ` (`BATTERY_GROUPS["B"]["daq"]` is currently
  `"MAIN_DAQ"`, not a dedicated device). When Group B is wired for real:
  enable `EXPANSION_DAQ` (`PXI_SLOTS` slot 17, currently commented out),
  point `BATTERY_GROUPS["B"]["daq"]` at it instead of `MAIN_DAQ`, and
  extend `BATTERY_CHANNELS` for positions 9-16 using per-group-relative
  channel numbering (mirroring Group A's `ai0-7`/`ai8-15`/`ai16-23` shape
  on `EXPANSION_DAQ`, not continuing global numbering onto a shared device).
- [x] `SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/
  `output_disable()`/`measure()` -- **DONE.** All five are now real
  (`output_disable()` was already real). `set_discharge_mode()` is
  implemented as a current-SINK at a positive voltage (negative
  `current_level`, NOT a negative-voltage source, per the requirement in
  docs/architecture.md Section 12.6). Follows the configuration-verification
  contract (Section 12.6b) and the PSU Safety Verification Pattern (Section
  25) via a new shared `_configure_current_source()` helper -- no new safety
  logic invented, both existing helpers (`force_output_off_and_verify()`/
  `_verify_config_readback()`) reused for `DC_CURRENT` instead of
  `DC_VOLTAGE`. See docs/architecture.md Section 36 for full detail and the
  "Completed (Summary)" entry below.
- [x] SMU Functional Validation (no load) -- **DONE.** Validated directly
  against `nidcpower`'s real `simulate=True` mode (the actual NI-DCPower
  driver runtime, not a hand-rolled mock) -- mode configuration, output
  state transitions, readback, safety shutdown, and command verification all
  confirmed through the real, unmodified production code path. One finding
  (default simulated model is unipolar, rejecting negative `current_level`;
  resolved by testing against a simulated bipolar PXIe-4141 instead, which
  matches real production hardware) -- not a code defect. Real current
  flow/CC/CV/EOC/EOD accuracy remain unvalidated (require a real cell or
  load) -- unchanged scope boundary. See docs/architecture.md Section 36.
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
- [x] Wire `BATTERY_CONFIGS` into `safety_monitor.py`/`charge_cycle.py`/
  `discharge_cycle.py` so per-battery limits actually apply instead of the
  single global `BAT_VOLTAGE_MAX`/etc. DONE -- see "Completed (Summary)"
  below and `docs/architecture.md` Section 28.
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
- [x] Charge Battery / Discharge Battery -- **DONE.** `ChargeSequence`
  (`test_control/charge_sequence.py`) / `DischargeSequence`
  (`test_control/discharge_sequence.py`), both built on
  `BatteryOperationSequence`, wired into `run_main_test()`'s menu choices 2/3
  via `test.py::_run_charge_or_discharge()`. Harvested EOC/EOD/PSU-
  sequencing/emergency-shutdown logic from `ChargeCycle`/`DischargeCycle`
  per the harvest plan (`docs/architecture.md` Section 33); did NOT carry
  forward `daq.read_all_batteries()` (uses DMM + `SMU.measure()` instead,
  per the DAQ Strategy decision) or `TestExecutor`/`BatteryTestSequence`
  coupling. `DischargeSequence` applies the Discharge Cutoff Policy (target
  vs. floor clamp) from the start. See docs/architecture.md Section 36.
  Validated with mocked hardware only (happy path + safety-abort path) --
  physical rack validation remains open, see below.
- [ ] **[PRIORITY 8 -- see docs/architecture.md Section 35]** Cycle Battery
  -- still a Run Main Test submenu placeholder. Implement `CycleSequence` as
  a thin composition of `ChargeSequence` -> rest -> `DischargeSequence`
  (both now real, see above) -- not a third independent state machine, and
  not hardwired charge-then-discharge coupling like the legacy
  `BatteryTestSequence` (see docs/architecture.md Section 33's explicit
  warning against reintroducing that coupling here).
- [ ] Wire real NTC temperature into `ChargeSequence`/`DischargeSequence`
  (currently `t_c = None`, same as `ChargeCycle`/`DischargeCycle`/
  `MonitorBatterySequence` -- a live safety-check gap, not a cosmetic one,
  since `SafetyMonitor.check()` silently skips the overtemperature check
  when `temp_c is None`).
- [MUST] Physical rack validation of `ChargeSequence`/`DischargeSequence` on
  real Group A hardware with a real battery -- verified with mocked
  hardware only so far (see docs/architecture.md Sections 36-37). This is
  the first workflow in this project to actually source/sink current into a
  real cell -- treat as new, unvalidated territory (same caution
  `docs/MILESTONES.md` Milestone 1 flagged for this exact step).
- [x] **Confirm PRIMARY_SMU (PXIe-4141) can actually deliver
  BATTERY_CONFIGS' commanded currents before any real hardware test --
  RESOLVED for Group A's current test_setpoints, not for HUB.** Found
  during post-implementation validation (docs/architecture.md Section 37):
  `nidcpower`'s own simulated PXIe-4141 model caps `current_level_range` at
  100 mA. The Battery Group Test Configuration Architecture (Section 39)
  resolved this for Group A by declaring it for SB with a conservative
  recipe (0.05 A charge / 0.08 A discharge) that fits inside PRIMARY_SMU's
  real capability, and by adding a Hardware Capability Validation stage
  (`utils/validators.py::validate_group_test_config()`) that raises
  `HardwareConfigurationError` before any hardware is touched if a future
  edit to Group A's test_setpoints (or a future group) exceeds its
  assigned SMU's `max_current_a`. **HUB still cannot run on Group A** --
  its limits (0.525/1.05 A) exceed PRIMARY_SMU's 0.1 A cap entirely, no
  conservative recipe fixes that; HUB needs `BATTERY_GROUPS["A"]["smu"]`
  reassigned to `HIGH_POWER_SMU` (PXIe-4139, 3.0 A) or `AUX_SMU_1`/`AUX_SMU_2`
  (PXI-4130, 1.0 A -- note HUB's 1.05 A discharge limit is marginally
  *above* even this) -- a real wiring decision, not made here.
- [ ] Reassign a group's SMU (or add a new group) so HUB can actually be
  charge/discharge tested -- `HIGH_POWER_SMU` (PXIe-4139, confirmed 3.0 A
  cap via nidcpower simulation) is the best-fit candidate; `AUX_SMU_1`/
  `AUX_SMU_2` (PXI-4130, confirmed 1.0 A cap) are marginal for HUB's 1.05 A
  discharge limit specifically. See docs/architecture.md Section 39.
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

### Pre-Hardware-Validation MUST-FIX Closure (residual, low priority)

- [x] Reverse polarity protection -- **DONE.** New `ReversePolarityError
  (SafetyViolationError)` (`utils/errors.py`), new
  `Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V` (-0.5 V,
  `config/settings.py`), new `BatteryOperationSequence._check_battery_polarity()`
  called from `ChargeSequence.run()`/`DischargeSequence.run()` with the SMU
  output still disabled, immediately before `set_charge_mode()`/
  `set_discharge_mode()`/`output_enable()`. See docs/architecture.md
  "Pre-Hardware-Validation MUST-FIX Closure" and docs/FAQ.md Section 10.
- [x] Battery-type validation -- **DONE.** `validate_group_test_config()`
  and the two direct-lookup paths (`_run_monitor_battery()`,
  `_run_monitor_battery_scan()`) all now raise a typed
  `ConfigurationError`/`[FAIL]` message instead of a bare `KeyError` for an
  unrecognized `battery_type`. See docs/FAQ.md Section 5.
- [x] `StopReason.TIMEOUT` traceability -- **DONE.**
  `BatteryOperationSequence.run_guarded()` has a dedicated
  `except NIPXITimeoutError` branch recording `StopReason.TIMEOUT` instead
  of the generic `FAILED`. Applies to `ChargeSequence`/`DischargeSequence`
  only (legacy `charge_cycle.py`/`discharge_cycle.py` unaffected, already
  superseded). See docs/FAQ.md Sections 3-4.
- [x] Database startup hardening -- **DONE.** New `test.py::
  _open_storage_guarded()`/`_start_run_summary_guarded()` helpers wrap
  `DataStorage.open()`/`start_run_summary()` with clean `[FAIL]`
  messaging instead of a raw traceback, used by all four real workflow
  entry points (`_run_monitor_battery()`, `_run_monitor_battery_scan()`,
  `_run_charge_or_discharge()`, `run_proto_test_execution()`). See
  docs/FAQ.md Section 7.
- [ ] (Low priority, no hardware risk) Reverse-polarity/damaged-battery/
  disconnected-lead/wiring-fault disambiguation -- `_check_battery_polarity()`
  deliberately does not distinguish these; all raise the same
  `ReversePolarityError`. Intentionally deferred -- the check's job is the
  SMU-enable safety gate, not root-cause diagnosis. See docs/FAQ.md
  Section 10's "Can a damaged battery be mistakenly interpreted as reverse
  polarity?" entry.
- [ ] (Low priority, no hardware risk) Apply the same `battery_type`
  existence guard added to the three real workflow paths to the Safety
  Monitor Simulator's `_select_safety_simulation_group()` and its two
  callers (`test.py:2623`, `test.py:2769`), which still do a bare
  `BATTERY_CONFIGS[cfg["battery_type"]]` lookup. Simulator/demo-only, no
  hardware activation -- deferred, not a blocker.
- [ ] (Low priority, no hardware risk) `BatteryOperationSequence.run_guarded()`'s
  exception branches call `storage.log_event()`/`record_execution_state()`/
  `finish_run_summary()` before `safety.emergency_stop()`/
  `safe_cancel_shutdown()` -- if the database itself is the original
  failure, those storage calls raise again and skip the safety shutdown
  *at that layer* (hardware is still safed via `test.py`'s outer
  `hw_mgr.disconnect_all()` backstop, so this is a stop-reason/
  traceability gap, not a hardware-safety gap). Found during the Monitor
  Battery readiness review -- see docs/architecture.md Section 45,
  docs/FAQ.md Section 13. Not a blocker for Real Hardware Validation.

### Configuration

- [MUST] Confirm relay channel numbers match physical wiring on the BLOSS
  Hub PCB, and DAQ channel names (`BATTERY_CHANNELS[i]["daq_voltage_ch"]`
  etc.) match the PCB-to-connector layout -- these still assume a "Dev1"
  NI-MAX alias for `MAIN_DAQ` (`PXI_SLOTS[2]`) that has not been confirmed
  against NI-MAX on the real machine.
- [MUST] `config/settings.py` -- confirm `BAT_VOLTAGE_MAX`/`MIN` against the
  battery datasheet (still an open confirmation -- unrelated to the item
  below).
- [x] Decide `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN` (3.5 V) --
  **DONE, this review.** These are not conflicting values -- `DISCHARGE_CUTOFF_V`
  is a cycle-objective discharge target, `BAT_VOLTAGE_MIN`/`battery_cfg
  ["voltage_min_v"]` is the absolute safety floor, and the floor always has
  priority. `DischargeCycle.run()` now clamps `cutoff_v = max(target_v,
  floor_v)`; see docs/architecture.md Section 30 "Discharge Cutoff Policy".
  The Safety Monitor Simulator's matching workaround was updated to the same
  clamp (no more `+0.05` margin hack). See "Completed (Summary)" below.
- [MUST] Set `RELAY_COM_PORT` to the real COM port if serial relay diagnostics
  are ever used (diagnostic path only).

### Infrastructure

- [ ] **Official DAQ strategy (documented, docs/architecture.md Section 31):**
  ChargeSequence/DischargeSequence/CycleSequence development must use the
  DMM as its telemetry source (mirroring Monitor Battery), NOT
  `DAQ.read_all_batteries()` -- DAQ mapping/wiring is not approved/finalized
  and Charge/Discharge development must not be blocked waiting on it.
  Migrating Monitor Battery *and* whatever Charge/Discharge/Cycle Sequence
  is built in the interim onto the final per-position DAQ architecture
  remains the long-term goal (see the `[MUST]` Monitor Battery DAQ migration
  item above) -- just not a precondition for starting Charge/Discharge work.
- [ ] (Low priority, not urgent) Migrate `ProtoTestSequence` onto
  `BatteryOperationSequence` -- it currently hand-duplicates the same
  4-exception-type `run_guarded()`-shaped handling and inline
  `ExecutionFrame` construction independently (see docs/architecture.md
  Section 34 "ProtoTestSequence Review Findings"). Not urgent: it is
  validated, real-hardware-tested infrastructure code, not under active
  development, and touching it carries real regression risk for a benefit
  (avoiding a future missed-fix) that hasn't yet materialized. Remember to
  check `ProtoTestSequence` explicitly any time `run_guarded()`/
  `_render_frame()` changes, so the parallel implementation isn't silently
  left behind.
- [ ] Add a `--dry-run` / `PXI_SIMULATE` mode exercising test logic without
  hardware (builds on `hardware/simulated.py` above).
- [ ] Implement PSU/relay cross-validation (docs/architecture.md Section 26)
  -- `SMU.cross_validate_output_state(measured_v, measured_i)` currently
  only raises `NotImplementedError`; wire it into `source_dc_voltage_point()`
  (comparing the SMU's own `measured_v`/`measured_i` against the reported
  `output_enabled` state) and/or `ProtoTestSequence`'s existing DMM reading
  once a real accuracy tolerance/threshold is decided. Not needed for a
  relay equivalent -- see that section's rationale (the relay's own
  `readall` readback already IS a direct physical confirmation, unlike a
  PSU's `output_enabled` attribute).
- [ ] Continue expanding `test.py::test_safety_monitor()`'s Part 2 workflow
  walkthrough (see `docs/architecture.md` Section 23e, the designated
  development reference implementation for Charge/Discharge/Cycle Battery)
  into a full development/validation harness ahead of deploying against
  real hardware. Candidates: more injected-fault scenarios (overcurrent,
  undervoltage, relay-switch guard mid-workflow), wiring the same
  simulated per-step values through `ExecutionFrame`/`render_execution_frame()`
  for a full mock execution screen (natural pairing with the UI Test menu
  item). (Driving the walkthrough from `BATTERY_CONFIGS`' actual per-type
  limits is DONE -- see `docs/architecture.md` Section 28.)
- [x] Map real Charge/Discharge code paths back onto `test.py::
  _charge_phase_steps()`/`_discharge_phase_steps()`/
  `_cycle_battery_walkthrough_steps()` and confirm the simulator's step
  sequence still matches -- **DONE.** Found real drift (simulator still
  derived setpoints from `battery_cfg` limits -- the exact bug already
  fixed in the real sequences -- and still let the operator pick a
  battery type directly, which no longer happens anywhere else). Fixed:
  simulator now takes `test_setpoints`, and `_select_safety_simulation_group()`
  replaces `_select_safety_simulation_battery()`, deriving battery type
  from the selected group exactly like the real workflows. **This item
  must be re-checked any time ChargeSequence/DischargeSequence/a future
  CycleSequence changes** -- simulator drift is not acceptable going
  forward, not just this once. See docs/architecture.md Section 41.
- [ ] Create `flowcharts/vi_flowchart.md` (referenced in `docs/architecture.md`
  but does not exist yet).
- [ ] Set up a remote Git repository and update `README.md` with the URL.
- [ ] CI: add basic linting (ruff or flake8) as a pre-commit hook.

### Production Runtime Architecture (see docs/architecture.md Section 46, Milestone XII)

- [ ] **[MUST before Runtime ships]** Retire or rewrite `main.py`'s legacy
  `TestExecutor`/`BatteryTestSequence`/`ChargeCycle`/`DischargeCycle` path
  -- it is a currently-live second charge/discharge implementation with no
  reverse-polarity check and no Milestone II traceability (`event_log`/
  `run_summary`). A Runtime built on `ChargeSequence`/`DischargeSequence`
  alone becomes a third implementation unless this is resolved. **Sharpened
  by the Section 50 pre-implementation review:** `main.py`'s hardcoded
  relay target (`NUMATO_RELAY_MATRIX_CONFIG` -> `MATRIX_NUMATO_201`) is
  now, under the approved topology, an explicitly disabled group (A1) with
  zero hardware assigned -- it targets exactly the one matrix everyone has
  agreed isn't ready, and `BATTERY_GROUPS[...]["enabled"]` provides zero
  protection against `main.py` running there, since it never reads
  `BATTERY_GROUPS` at all.
- [ ] **[Safety gap, independent of Runtime]** `hardware/daq.py::DAQ.read_all_batteries()`
  is a hardcoded stub (always returns `voltage_v=0.0`/`current_a=0.0` for
  every channel) -- `main.py`'s legacy `ChargeCycle`/`DischargeCycle` path
  is the only real caller, meaning `SafetyMonitor.check()` is evaluated
  against fake zero readings for the entire duration of any charge/
  discharge run through that path, and EOC/EOD by voltage is never
  reachable (a charge cycle runs the full 2-hour `CHARGE_TIMEOUT_S` before
  timing out). Not introduced by the topology work; surfaced by the
  Section 50 "what would happen if executed today" review. Resolved
  automatically once `main.py`'s legacy path is retired (item above) --
  tracked here so it isn't lost as a standalone risk in the meantime.
- [ ] Design and build a resource-checkout/hardware-set-partition layer in
  the future Cycle Controller, derived from `hardware_for_group()` (group
  by shared `relay_matrix`/`smu`/`dmm`/`daq` name) -- required before any
  concurrency claim, given only one `MAIN_DMM`/`MAIN_DAQ` exists today.
- [ ] Design `CycleSequence` (charge -> rest -> discharge) as a composition
  over the existing `ChargeSequence.run()`/`DischargeSequence.run()` calls,
  subclassing `BatteryOperationSequence` like the other four -- not a new
  charge/discharge implementation.
- [x] ~~Fix stale comment in `config/devices.py` (~line 507-509) describing
  Group A as wired to `MATRIX_NUMATO_201`~~ -- **SUPERSEDED (Milestone XV):**
  "Group A" no longer exists under the approved topology; the comment will
  be rewritten as part of the topology implementation itself, not as a
  standalone fix. Coincidentally, A1 (the new name for that same matrix)
  really is on `MATRIX_NUMATO_201`, so this old comment's original claim
  becomes true again for a differently-named group.

### Architecture Standardization (see docs/architecture.md Sections 47-49, Milestones XIII-XV)

- [x] Group-naming semantics + final topology -- **RESOLVED (Milestone XV):**
  `MATRIX_NUMATO_201 -> A1-A4`, `MATRIX_NUMATO_202 -> B1-B4`,
  `MATRIX_NUMATO_203 -> C1-C4` -- groups are hardware ownership sets, not
  workflow/battery-type families. Not a rename of today's A/B/C/D; a new
  topology (today's Group A becomes B1). Active groups: B1 (existing rack
  DMM/SMU/DAQ), C1 (NI USB-6211, NTC-only). A1 disabled (zero hardware
  roles assigned -- see Milestone XV for the full alternatives review);
  A2-A4/B2-B4/C2-C4 disabled placeholders.
- [ ] **[MUST before Group C1's hardware is exercised for real]** Redesign
  position/channel ownership: move `BATTERY_CHANNELS` into each
  `BATTERY_GROUPS[group]["positions"]` sub-dict, scoped to that group's
  own `relay_matrix`. **Corrected structure (Milestone XV):**
  `relay_address` must stay unique across every group sharing one
  `relay_matrix` (B1 owns 1-8, B2 owns 9-16, B3 owns 17-24, B4 owns 25-32
  on `MATRIX_NUMATO_202` -- NOT reset to 1-8 per group), and is only free
  to repeat across *different* matrices (B1 and C1 can both use 1-8).
  Fixes a real, confirmed bug in `utils/device_validator.py`:
  `_check_duplicate_relay_identifiers()` -- rekey by `(relay_matrix,
  relay_address)`, not `relay_address` alone; `_check_battery_groups()` --
  retire entirely (its invariant becomes structurally impossible once
  positions live inside their owning group); `_check_relay_count_consistency()`
  -- loop per group instead of a full matrix x `BATTERY_CHANNELS`
  cross-product. Validate disabled groups too (don't skip), to catch a
  `relay_address` collision between a real group and a disabled sibling
  at config-load time. See docs/architecture.md Section 49 for the exact
  files/functions affected (`config/devices.py`, `test.py`'s group/
  position selection functions, `test_control/monitor_battery_scan_sequence.py`'s
  `DAQ_CHANNEL_0` constant).
- [ ] A1: assign at least one real hardware role (`smu`/`dmm`/`daq`/
  `ntc_daq`) and flip `enabled=True` once that hardware exists -- disabled
  until then, per Milestone XV's decision.
- [ ] `USB_DAQ_DEVICES` needs a second entry, `NTC_DAQ_USB6211` (for C1),
  distinct from the existing `NTC_DAQ_USB6210` (for B1) -- neither
  replaces the other.
- [ ] Add `group_name`/`position_in_group` as additive `run_summary`
  columns (same migration pattern as `battery_type`) -- implement together
  with the position-ownership redesign above, using final `A1`/`B1`/`C1`-
  style names directly. Prerequisite for Group History / Last Test From
  Group / Group Statistics in Database Tools. Populate at
  `start_run_summary()` time; reuse `list_run_summaries()`/
  `get_last_run_summary()`/`run_summary_report.py::render_run_summary()`
  for the three new views.
- [ ] Add a group-centric path to Test SMU/DMM/DAQ/Relay Matrix (Select
  Group -> Resolve Group Hardware -> Run) **alongside**, not replacing,
  the existing per-device picker -- a pure replacement would remove the
  ability to validate hardware not yet assigned to any group
  (`HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2` today).
- [ ] Minor: have NTC Group Scan echo `"Battery selected: {type}"` into
  `event_log`, matching Monitor/Charge/Discharge's narration style (the
  value is already captured structurally in `run_summary.battery_type`;
  this is cosmetic, not a data gap).
- [ ] Minor: `run_summary_report.py::render_run_summary()` has no display
  section for `test_type == "ntc_scan"` -- falls through to "(no summary
  section defined...)". Add an NTC section (reuse the Position/Present/
  Temperature shape `_run_ntc_group_scan()` already prints).

### Temperature Monitoring (see docs/architecture.md Section 51, Milestone XVI)

- [ ] Real-hardware validation of the NTC acquisition path in a live
  Monitor Battery/Charge/Discharge run (not just the standalone NTC Group
  Scan path) once a USB DAQ is physically attached -- confirm
  `classify_ntc_presence()`/`ntc_voltage_to_celsius()` against a real
  divider signal, confirm the overtemperature `SafetyViolationError` path
  actually trips on a real out-of-range reading.
- [ ] Add a separate, non-fatal *warning* temperature threshold (below the
  existing critical `max_temp_c` SafetyMonitor already enforces) --
  needs a design decision on where the threshold value lives
  (`BATTERY_CONFIGS` field vs. global `Settings` constant) and which
  event_log entry announces it. Deliberately not built in this pass.
  See docs/architecture.md Section 51.
- [ ] Add `run_summary`-level temperature aggregation (min/max/avg per
  run), mirroring the still-deferred Charge/Discharge voltage-stat
  enrichment -- a `_TempStats`-style accumulator, same pattern as Monitor
  Battery's existing `_VoltageStats`. Deliberately not built in this pass
  to keep the temperature-acquisition change additive and scoped.
- [ ] Legacy `main.py` temperature integration remains out of scope --
  structurally blocked by `main.py`'s total lack of group awareness
  (Section 50); revisit only as part of `main.py`'s eventual retirement/
  rewrite (Milestone XII).
- [x] **DONE (Milestone XVII):** Group NTC pre-check -- one-time full-group
  NTC snapshot before Monitor Battery/Charge Battery/Discharge Battery,
  gated only on the selected position's own result, sharing
  `_ntc_group_snapshot()` with NTC Group Scan.

### Group NTC Pre-Check follow-ups (see docs/architecture.md Section 52, Milestone XVII)

- [ ] Real-hardware validation of the pre-check itself once NTC hardware
  is available -- confirm a genuine open/shorted target position
  correctly aborts, and a genuine (but readable) absent signal on a
  *different* position in the group correctly does not.
- [ ] Once `group_name`/`position_in_group` lands (Milestone XIII), add a
  "pre-check catch rate" bucket to Group Statistics (how often a group's
  pre-check found a non-PRESENT target position before an operation
  started) -- not built in this pass.

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

- **Monitor Battery Operational Behavior Review -- GO for Real Hardware
  Validation** -- implementation-level readiness review of
  `MonitorBatterySequence` (not an architecture-assumption review):
  confirmed it is intentionally a continuous, no-timeout, no-automatic-
  stop workflow (runs until Ctrl+C or a real fault via its `while True:`
  loop); confirmed every measurement/event is committed to the database
  synchronously, with no buffering, so an 8-hour continuous session is
  supported with no memory-growth or buffering risk; confirmed the relay
  closes once at startup and opens on every exit path (directly via
  `safety.emergency_stop()`/`safe_cancel_shutdown()`, or via
  `HardwareManager.disconnect_all()`'s outer backstop); confirmed DMM
  dependency behavior differs by system mode (PRODUCTION fails closed
  before any hardware activation; DEVELOPMENT/VALIDATION briefly activates
  the relay before the first failed measurement triggers shutdown); and
  confirmed there is no retry/recovery on measurement failure (by design).
  One documented, non-blocking caveat: a mid-run database failure can
  cause `run_guarded()`'s own failure-classification storage calls to
  themselves fail, skipping `safety.emergency_stop()` at that layer --
  hardware is still safed via the outer cleanup path, but the recorded
  stop-reason/traceability for that specific failure mode may be
  incomplete. **Decision: GO.** No software blocker found. See
  docs/architecture.md Section 45 and docs/FAQ.md Section 13.
- **Matrix Scan 5s ON-State Dwell (for physical rack inspection)** -- added
  `Settings.RELAY_MATRIX_SCAN_DWELL_S` (`5.0` s), a new constant used only
  by `test.py::_run_relay_matrix_scan()` ("[2] Matrix Scan (ON -> READ ->
  OFF, scoped by group)"). Each relay now stays ON for 5s after
  activation/read before being turned OFF, giving an operator time to
  observe LEDs/routing/measurements/wiring during real-rack validation.
  Deliberately independent of `Settings.RELAY_SETTLE_TIME_S` (unchanged,
  `2.0` s, still enforced by every `relay.open()`/`close()` call) and does
  not affect any other workflow. See docs/architecture.md Section 21.
- **Single Global Relay Settle/Dead-Time Constant + RelayEthernetTest Fix**
  -- a pre-hardware-validation timing review found relay settle delay was
  inconsistent (0.2s/0s/borrowed-`STABILIZATION_S` across different
  workflows). Fixed by making `Settings.RELAY_SETTLE_TIME_S` (`2.0` s) the
  single relay-timing constant, enforced centrally in
  `hardware/relay.py::RelayBase.open()`/`close()` (never overridable,
  never `0`). A follow-up real-hardware observation then found
  `test.py::test_relay_ethernet_test()` still transitioning immediately --
  root cause: it deliberately bypasses `open()`/`close()` to exercise
  native Numato primitives directly (a pre-existing, documented
  exception), so it never received the new delay. Fixed by exposing
  `RelayBase.settle()` publicly and calling it explicitly from that test
  after each native write. See docs/architecture.md Sections 43-44.
- **Pre-Hardware-Validation MUST-FIX Closure** -- closed the four
  highest-priority gaps a pre-hardware-validation architecture FAQ review
  (`docs/FAQ.md`) identified: reverse polarity protection (new
  `ReversePolarityError(SafetyViolationError)`, pre-output-enable DMM
  sanity check in `ChargeSequence`/`DischargeSequence`), battery-type
  validation (typed `ConfigurationError` instead of a bare `KeyError` for
  an unrecognized `battery_type`, in all three real lookup paths), timeout
  traceability (`StopReason.TIMEOUT` now actually assigned via a dedicated
  `run_guarded()` exception branch), and database startup hardening
  (`_open_storage_guarded()`/`_start_run_summary_guarded()` clean `[FAIL]`
  messaging across all four real workflow entry points). Verified via
  mocked regression tests, not physical hardware. Two narrow, intentional
  residual gaps carried forward (see "Pre-Hardware-Validation MUST-FIX
  Closure (residual, low priority)" above): reverse-polarity/damaged-
  battery disambiguation, and the Safety Monitor Simulator's unguarded
  `battery_type` lookup. See docs/architecture.md "Pre-Hardware-Validation
  MUST-FIX Closure" and docs/MILESTONES.md Milestone IX.
- **Simulator & Reference-Blueprint Reconciliation + Pre-Hardware-Validation
  Readiness** -- final software-focused milestone before Real Hardware
  Validation. Found and fixed real drift: the Safety Monitor Simulator's
  `_charge_phase_steps()`/`_discharge_phase_steps()` still derived commanded
  setpoints from `battery_cfg` limits (the exact conflation bug already
  fixed in the real `ChargeSequence`/`DischargeSequence`) and still let the
  operator pick a battery type directly (`_select_safety_simulation_battery()`),
  which no longer happens anywhere else in the codebase. Both fixed: the
  step generators now take `test_setpoints`; a new
  `_select_safety_simulation_group()` derives battery type from a selected
  group, exactly mirroring the real workflows. Stale "not-yet-implemented"/
  legacy-class docstrings corrected. A second, separately-discovered
  instance of the same drift class was found and fixed in `test_ui_preview()`
  (the "UI Test" menu still called Charge/Discharge "not yet implemented" --
  new demo screens added for both, Cycle correctly remains unimplemented).
  DAQ readiness review found and closed one real interface gap:
  `ChargeSequence`/`DischargeSequence`'s constructors didn't accept a `daq`
  parameter at all even though their own base class supports one -- added
  as an unused, optional placeholder so a future DAQ integration only needs
  to change the two telemetry lines inside each sequence's sampling loop,
  not any constructor or caller. No DAQ dependency introduced. Architecture
  consistency review confirmed Monitor/Monitor Scan/Charge/Discharge/
  Simulator now agree on group/battery/setpoint ownership, validation flow,
  traceability flow, and execution flow. **Software architecture judged
  ready for the Real Hardware Validation milestone** -- remaining blockers
  are all hardware-access tasks (SMU current-capability confirmation,
  relay/DAQ channel confirmation, `BATTERY_CONFIGS` datasheet confirmation),
  not software defects. See docs/architecture.md Section 41.

- **Architectural correction: battery type is never operator input** --
  corrects the prior session's "declaration + cross-check" design (Section
  39), which still let the operator pick a battery type with the group's
  declaration only as a safety net. Battery type is now read exclusively
  from `config/devices.py::BATTERY_GROUPS[group]["battery_type"]` in every
  real workflow (Monitor Battery, Monitor Battery Scan, Charge Battery,
  Discharge Battery) -- no battery-type prompt exists anywhere in them.
  `test.py::_select_battery_type()` deleted (confirmed unused first).
  `utils/validators.py::validate_group_test_config()` signature simplified
  from `(group, battery_type)` to `(group)`, now returning
  `{"battery_type", "test_setpoints"}` together; the mismatch-check logic
  it used to need no longer applies (nothing to mismatch against).
  Operator now selects Group only, for every battery workflow. The Safety
  Monitor Simulator's own battery-type picker
  (`_select_safety_simulation_battery()`) is deliberately unchanged -- a
  dev/exploration tool, not a real execution workflow. See
  docs/architecture.md Section 40.

- **Battery Group Test Configuration Architecture** -- formalized each
  `BATTERY_GROUPS` entry into a complete, self-contained operational test
  definition. Added `"battery_type"` (a declaration the operator's explicit
  selection is cross-checked against -- never an inference shortcut, that
  rule is unchanged) and `"test_setpoints"` (the chosen charge/discharge
  recipe -- distinct from `BATTERY_CONFIGS`' safety limits, which
  `ChargeSequence`/`DischargeSequence` had previously read directly as if
  they were the commanded setpoint). New `config/devices.py::
  group_test_config()` accessor (mirrors `hardware_for_group()`). New
  `PXI_SLOTS[...]["max_current_a"]` on every SMU entry, confirmed via
  `nidcpower` simulation (`PRIMARY_SMU`=0.1A, `HIGH_POWER_SMU`=3.0A,
  `AUX_SMU_1`/`AUX_SMU_2`=1.0A) -- caught and fixed a real propagation gap
  where `SMU_ASSIGNMENTS`'s field-by-field reshape silently dropped the new
  field until its comprehension was updated too. New three-stage validation
  pipeline `utils/validators.py::validate_group_test_config()` (Group
  Configuration -> Battery Limits -> Hardware Capability), three new
  exceptions (`GroupConfigurationError`/`ConfigurationError`/
  `HardwareConfigurationError`, all subclassing the existing
  `ValidationError`), wired into `test.py::_run_charge_or_discharge()`
  before any hardware is touched. Group A declared for SB with a
  conservative recipe fitting inside PRIMARY_SMU's real capability -- HUB
  still cannot run on Group A (see the `[ ]` item above). `ChargeSequence`/
  `DischargeSequence.run()` signatures gained a required `test_setpoints`
  parameter; `battery_cfg` now used only for SafetyMonitor's limits, never
  the commanded value. Verified: all three validation stages independently
  reachable in the correct order; mocked end-to-end smoke tests re-run for
  both sequences with the new parameter. See docs/architecture.md Section 39.

- **Post-implementation validation of ChargeSequence/DischargeSequence --
  two real defects found and fixed** -- a thorough, adversarial review
  (not assuming the prior session's implementation was correct) found: (1)
  neither sequence opened the relay on successful EOC/EOD completion
  (every failure path was already covered via `safety.emergency_stop()`,
  but success was not) -- fixed, both now call `relay.open()` after PMU
  output is confirmed off; (2) `DischargeSequence` configured the SMU's
  compliance `voltage_limit` at the low EOD cutoff (~3.0V) instead of a
  ceiling bounding the real battery voltage range -- confirmed against
  `nidcpower`'s real driver that the default compliance mode is SYMMETRIC
  (+/-voltage_limit), meaning the original code would have put the SMU in
  voltage compliance for virtually the entire discharge, silently
  invalidating real CC discharge tests despite every mocked test passing --
  fixed to use `battery_cfg["voltage_max_v"]` instead. Also found (not a
  code bug, a hardware/config-level risk): `BATTERY_GROUPS["A"]["smu"]`
  (`PRIMARY_SMU`/PXIe-4141) may be physically incapable of the currents
  `BATTERY_CONFIGS` commands for HUB/SB (confirmed via `nidcpower`'s own
  simulated model data: 100 mA max, vs. up to 1.05 A required) -- flagged
  as the top blocker before real hardware use, not silently fixed (see the
  `[MUST]` item above). Architecture review found no duplication, no
  legacy coupling, no `hardware_for_group()` bypass, no battery-type
  inference. See docs/architecture.md Section 37.

- **SMU charge/discharge implementation + ChargeSequence/DischargeSequence**
  -- `hardware/smu.py::SMU.set_charge_mode()`/`set_discharge_mode()`/
  `output_enable()`/`measure()` implemented for real (via a new shared
  `_configure_current_source()` helper reusing `force_output_off_and_verify()`/
  `_verify_config_readback()` unchanged), validated no-load against
  `nidcpower`'s real `simulate=True` driver runtime (not a hand-rolled mock)
  -- configuration/readback/verification, output-state transitions, and
  safety shutdown all confirmed through the actual production code path.
  New `test_control/charge_sequence.py::ChargeSequence` /
  `test_control/discharge_sequence.py::DischargeSequence`, both built on
  `BatteryOperationSequence` (the target execution architecture, unmodified),
  wired into `run_main_test()`'s previously-placeholder menu choices 2/3.
  Harvested EOC/EOD/PSU-sequencing/emergency-shutdown logic from
  `ChargeCycle`/`DischargeCycle` unchanged in shape; did not carry forward
  their `daq.read_all_batteries()` call (DMM + `SMU.measure()` used instead)
  or `TestExecutor`/`BatteryTestSequence` coupling. `DischargeSequence`
  applies the Discharge Cutoff Policy (target vs. floor clamp) from the
  start. Timeout now raises `NIPXITimeoutError` (routed through
  `run_guarded()`'s existing shutdown/persistence handling) instead of the
  legacy cycles' `return False`, which was never actually wired to a safety
  action. Verified with mocked end-to-end smoke tests (happy path to
  EOC/EOD, and a forced-overcurrent safety-abort path confirming
  `safety.emergency_stop()`/`run_summary` finalization). Legacy
  `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence`/`TestExecutor` are
  unchanged and still in place (retirement is a later roadmap step, not
  performed here). See docs/architecture.md Section 36.

- **SMU implementation review (re-verified from source) + Discharge Cutoff
  Policy + DAQ Strategy + SMU Functional Validation milestone + Harvest Plan
  + ProtoTestSequence findings + revised roadmap** -- `hardware/smu.py` was
  re-read in full and its status (only `output_disable()`/`verify_output_
  disabled()`/`emergency_output_off()`/`source_dc_voltage_point()` real;
  `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()`
  still stubs) confirmed unchanged from the prior architecture review, not
  assumed. Fixed a real, previously-undocumented-as-such gap:
  `DischargeCycle.run()` now resolves a discharge TARGET (cycle objective,
  `DISCHARGE_CUTOFF_V`/battery_cfg) separately from the battery's safety
  FLOOR (`BAT_VOLTAGE_MIN`/`battery_cfg["voltage_min_v"]`) and clamps
  `cutoff_v = max(target_v, floor_v)` so the floor always has priority --
  previously the global-Settings-only fallback path could have used a
  target (3.0 V) below the floor (3.5 V) with no defense beyond
  `SafetyMonitor.check()` itself. `config/settings.py`'s
  `DISCHARGE_CUTOFF_V`/`BAT_VOLTAGE_MIN` comments, `test.py::test_
  configuration()`'s cross-check (now informational, not a WARN), and the
  Safety Monitor Simulator's matching fallback (`_discharge_phase_steps()`,
  margin hack removed) all updated to match. Documented three new official
  policies: (1) Discharge Cutoff Policy -- target vs. floor, floor always
  wins, battery type never inferred from group/position/channel/relay; (2)
  DAQ Strategy -- Charge/Discharge/Cycle Sequence development uses the DMM,
  not `DAQ.read_all_batteries()` (unimplemented, unapproved channel mapping),
  and must not be blocked by DAQ work; (3) SMU Functional Validation (no
  load) as a new milestone validating mode configuration/output-state-
  transitions/readback/safety-shutdown/command-verification WITHOUT a
  battery or load, explicitly not validating real current flow/CC/CV/EOC/EOD.
  Also produced a documented KEEP/MIGRATE/REMOVE/RETIRE harvest plan for
  `ChargeCycle`/`DischargeCycle` (no code migrated yet) and re-confirmed
  `ProtoTestSequence` duplicates but does not urgently need migration onto
  `BatteryOperationSequence`. Revised roadmap priority order: review SMU ->
  complete SMU functionality -> SMU Functional Validation (no load) ->
  validate results -> harvest ChargeCycle/DischargeCycle -> ChargeSequence ->
  DischargeSequence -> CycleSequence -> legacy retirement.
  `BatteryOperationSequence` reconfirmed as the target execution
  architecture throughout. See `docs/architecture.md` Sections 29-35.

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
- **Milestone II data/UI architecture** -- Milestone II closed with Phases
  1-2 delivered (see `docs/MILESTONES.md` "Milestone II: Summary"); Phase 3
  (`ProtoTestSequence` migration) and Phase 4 (Historical Results
  Viewer/`UI Preview Test`) remain open work items, tracked below, not a
  Milestone II gap. Phase 1:
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
- **Relay + PSU Safety Verification Pattern** -- implements the compliance
  fix identified by `docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`: relay/PSU
  paths previously went straight to "force off + verify" without ever
  reading/recording the pre-existing state first. New
  `NumatoRelayMatrix.check_current_relay_state()` (Read All -> Verify
  Current Status) is now called at the start of the shared
  `_force_all_off_and_verify()` -- bringing every real relay path
  (`MonitorBatterySequence`, `ProtoTestSequence`, legacy
  `BatteryTestSequence`, `HardwareManager`, `SafetyMonitor`, every Numato
  commissioning test in `test.py`) into full compliance with one
  centralized change; `test_relay_ethernet_test()` (which deliberately
  bypasses the public API) calls the same shared method explicitly so it
  isn't left behind. The identical pattern was extended to PSU control:
  new `SMU.query_output_state()`/`check_current_output_state()`/
  `force_output_off_and_verify()`, with `source_dc_voltage_point()` (the
  one real PSU-output-enabling method today) now calling
  `force_output_off_and_verify()` before any configuration is attempted.
  New `last_known_mask`/`last_known_output_state` attributes record the
  most recently read state on each driver. A `SMU.cross_validate_output_state()`
  stub (raises `NotImplementedError`, never called) marks the future
  DMM/PSU-readback cross-validation extension point without implementing
  it. Verified with mocked-socket (relay) and mocked-NI-DCPower-session
  (SMU) tests confirming the exact 8-step/PSU-equivalent command sequence,
  plus a full non-hardware MENU regression with no failures. See
  `docs/architecture.md` Sections 24-26 and `docs/MILESTONES.md`.
- **Interruptible Wait Mechanism** -- full timing/delay/timeout/polling/
  settling-time analysis (`docs/TIMING_ANALYSIS.md`) found several real
  dwells (`SMU.source_dc_voltage_point()`'s `hold_s`, `ChargeCycle`/
  `DischargeCycle`'s `STABILIZATION_S`) held hardware energized with NO
  cancellation checkpoint inside the wait -- most importantly
  `Settings.PROTO_TEST_DWELL_S`, a temporary 5s value standing in for an
  intended 120s production value (a ~2-minute Ctrl+C blind spot at that
  value). New `utils/cancellation.py::interruptible_sleep(duration_s,
  token=None, poll_interval_s=0.2)` -- a reusable drop-in `time.sleep()`
  replacement that checks cancellation every `poll_interval_s`;
  `token=None` preserves exact prior behavior. Wired into
  `source_dc_voltage_point()` (new `token` parameter, threaded from
  `ProtoTestSequence`), `ChargeCycle`/`DischargeCycle`'s stabilization and
  per-sample sleeps, and `MonitorBatterySequence.run()`'s sample interval.
  A real latent bug was found and fixed while wiring this in:
  `ChargeCycle`/`DischargeCycle`'s `STABILIZATION_S` sleep lived OUTSIDE
  the `try/finally` guarding `emergency_output_off()` -- harmless while
  uninterruptible, a live gap the moment it became cancellable; fixed by
  moving the `try/finally` to start right after `output_enable()`.
  `source_dc_voltage_point()` also gained an explicit
  `except OperationCancelledError: raise` so a mid-hold cancellation is
  never wrapped into a generic `SMUError`. Verified with mocked
  cancellation firing mid-dwell at all three call sites (each cancelled in
  a few hundred ms, not the full configured duration) and a normal
  non-cancelled run confirmed to preserve exact prior timing. See
  `docs/architecture.md` Section 27.
- **BATTERY_CONFIGS -> SafetyMonitor Integration** -- architecture review
  (`docs/SAFETY_MONITOR_BATTERY_LIMITS_REVIEW.md`) found `SafetyMonitor`/
  `ChargeCycle`/`DischargeCycle` battery-type-blind: despite `BATTERY_CONFIGS`
  already holding per-battery (HUB/SB) limits and battery selection already
  existing, safety checks and commanded setpoints only ever read shared
  global `Settings.BAT_*`/`CHARGE_*`/`DISCHARGE_*` constants (SB had up to
  ~12.5x headroom versus its own configured limits, HUB ~1.9x). Fixed with
  an optional, backward-compatible `battery_cfg` parameter: `SafetyMonitor`
  (constructor + `set_battery_limits()`, four private limit resolvers, a new
  `mode`="charge"/"discharge" parameter on `check()`), `ChargeCycle.run()`/
  `DischargeCycle.run()` (resolve commanded PSU current/voltage from
  `battery_cfg`, call `safety.set_battery_limits()`, pass `mode` into every
  check, use the resolved voltage in the EOC/EOD threshold), and
  `BatteryTestSequence.run()` (threaded through for completeness).
  `battery_cfg=None` preserves exact prior global-Settings-only behavior.
  `CHARGE_CUTOFF_A` (no `BATTERY_CONFIGS` equivalent) deliberately stays
  global. Safety Monitor Simulator (`test.py`) gained a battery-type
  selection step so its walkthroughs are also battery-aware. Verified: HUB/
  SB now trip at their own distinct current/voltage limits; all four
  simulator workflows re-run PASS for HUB, SB, and skip. Closes the last
  significant safety-architecture gap before real Charge Battery
  implementation. See `docs/architecture.md` Section 28.
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
  (planned `LimitResolver`, documentation only at the time this was
  written). Superseded in part by the actual `SafetyMonitor`/`ChargeCycle`/
  `DischargeCycle` integration below -- see `docs/architecture.md` Section
  11 (original philosophy) and Section 28 (the implemented integration).
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
