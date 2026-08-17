# NIPXI Milestones

Project milestone records -- captured when a phase of hardware bring-up or
architecture work is validated and formally closed out. This is a milestone
log, not a changelog (`docs/TODO.md`'s "Completed (Summary)" indexes
individual completed items; `git log` has the full commit history).

---

## Milestone 1: Hardware Bring-Up Milestone 1

**Status:** ACHIEVED
**Scope:** First real PXIe rack validation -- hardware communication
confirmed working end-to-end against physical hardware, not simulation.

### Objectives achieved

- Establish real, verified communication between the test framework
  (`test.py`) and every category of physical hardware in the PXIe rack.
- Root-cause and fix the first real-hardware-only defect surfaced by actual
  rack testing (the PXI-4130 multi-channel session issue -- see below).
- Finalize the Numato Ethernet relay network plan (static IPs, DHCP
  disabled, hardware-identifying naming).
- Improve operator usability for rack bring-up work (menu navigation,
  device naming).

### Hardware successfully validated (real rack, PASS)

| Hardware | Validation performed | Result |
|---|---|---|
| Numato Ethernet Relay Matrix #1 (`MATRIX_NUMATO_201`, 169.254.1.201) | TCP connect + Telnet login + readall verification | PASS |
| Numato Ethernet Relay Matrix #2 (`MATRIX_NUMATO_202`, 169.254.1.202) | TCP connect + Telnet login + readall verification | PASS |
| SMU / PSU (`PRIMARY_SMU`, `AUX_SMU_1`, `AUX_SMU_2`) | Identity Validation (self-test) + Functional Validation (bench DC voltage sourcing + SMU-side readback, `source_dc_voltage_point()`) | PASS |
| DMM (`MAIN_DMM`) | Identity Validation + Functional Validation (real `measure_dc_voltage()` against an external reference) | PASS |
| Hardware Discovery / identification | Grouped connectivity + identity check across every configured PXI-slot and Ethernet device | PASS |

Battery charge/discharge *sourcing* (`SMU.set_charge_mode()`/
`output_enable()`/`measure()`, `DAQ.read_all_batteries()`) remains out of
scope for this milestone -- deliberately still a placeholder (see
`docs/TODO.md`), not attempted or claimed as validated here.

### Major issues discovered

- **PXI-4130 multi-channel session ambiguity.** `AUX_SMU_1`/`AUX_SMU_2`
  (2-channel PXI-4130 cards) passed Identity Validation but failed
  Functional Validation with NI-DCPower error `-1074118522` ("the requested
  function only allows a single channel to be specified"). Root cause:
  `SMU.connect()` opened `nidcpower.Session(resource_name=...)` with no
  channel specified, which implicitly opens *all* channels on a
  multi-channel card as one ambiguous session -- any channel-repeated-
  capability property (`voltage_level`, `output_enabled`, `measure()`)
  then fails. Single-channel cards (`PRIMARY_SMU`/`HIGH_POWER_SMU`) never
  hit this, since a bare resource string resolves to exactly one channel on
  those cards.
- **Unconfirmed physical channel wiring.** Once the above was fixed for
  `AUX_SMU_1` (channel `"1"`), rack testing showed `AUX_SMU_2` activating
  channel `"0"` instead -- traced to an explicit placeholder config value
  (`"0"`) pending hardware confirmation, not a code defect. Confirmed via
  rack testing that `AUX_SMU_2` is also wired to channel `"1"`.

### Major issues resolved

- Added config-driven `smu_channel` (NI-DCPower channel name) and
  `channels_per_card` (physical channel count) fields to every SMU entry in
  `config/devices.py::PXI_SLOTS`. `SMU.connect()` now opens its
  `nidcpower.Session` scoped to exactly the configured channel -- no
  hardcoded channel anywhere in `hardware/smu.py`, same driver code path for
  single- and multi-channel cards. See `docs/architecture.md` Section 12.6a.
- `AUX_SMU_1` and `AUX_SMU_2` both corrected to `smu_channel: "1"`, matching
  confirmed physical wiring.
- Numato Ethernet relay IPs finalized as static (`169.254.1.201`/`.202`,
  DHCP disabled), replacing the shared factory-default `169.254.1.1` on both
  units.

### Architectural decisions made

- **Configuration-driven channel selection over hardcoding.** The channel
  fix was deliberately implemented as new `PXI_SLOTS` fields
  (`smu_channel`/`channels_per_card`) rather than a hardcoded channel in
  `hardware/smu.py`, so future PXI-4130 (or other multi-channel) units can
  be wired to any channel without a code change -- config is the single
  source of truth, matching this project's existing pattern for every other
  hardware field.
  - `AUX_SMU_1` -> `"smu_channel": "1"`
  - `AUX_SMU_2` -> `"smu_channel": "1"`
- **Hardware-identifying operator display names, config-derived.**
  `config/devices.py::device_display_name()` builds a display label (e.g.
  `NI4130-Slot7-Ch1`, `Numato-169.254.1.201`) directly from each device's own
  `model`/`slot`/`ip`/`smu_channel` fields -- no hardcoded string per device,
  and no change to any internal identifier (nicknames, dict keys, `resource`
  strings) used elsewhere in the codebase. Shown alongside (not replacing)
  the internal nickname throughout `test.py`.
- **Numato naming convention: name by static IP, not role.** Replaced the
  interim role-based names `MAIN_MATRIX_ETH`/`AUX_MATRIX_ETH_1` with
  `MATRIX_NUMATO_201`/`MATRIX_NUMATO_202` (named after each unit's static IP
  octet) for faster hardware ID during rack bring-up/troubleshooting/
  maintenance. Old names kept as backward-compat aliases.

### Operator usability improvements

- **Centralized "return to Main Menu" workflow** (`test.py`) -- every menu
  selection (PASS, WARNING, FAIL, exception, or Ctrl+C cancellation) now
  consistently returns to the top-level Main Menu through one dispatch
  function, instead of exiting the app or leaving the operator stuck in a
  nested menu.
- **Hardware-identifying device names** shown throughout Hardware Discovery,
  device-selection menus, and test output (see naming decision above) --
  faster to correlate against the physical rack during bring-up.

### Remaining work (unchanged scope, tracked in `docs/TODO.md`)

- Battery charge/discharge sourcing path (`SMU.set_charge_mode()`/
  `output_enable()`/`measure()`, `set_discharge_mode()` as current-sink).
- `DAQ.read_all_batteries()`/`verify_zero_current()` multi-channel
  synchronized acquisition.
- Temperature module (`TEMP_MODULE`, PXIe-4353) real TC/RTD channel read.
- Multi-SMU/multi-DAQ battery channel assignment (`HIGH_POWER_SMU`/
  `AUX_SMU_1`/`AUX_SMU_2`, `EXPANSION_DAQ`/`PRECISION_DAQ` still unassigned).
- `AUX_SMU_2`'s `smu_channel` was unconfirmed at the *start* of this
  milestone and is now confirmed `"1"` -- no other SMU channel assignments
  remain unconfirmed as of this milestone.
- GPIB0 instrument identification, PXI-resident switch/relay card
  (`CHASSIS_RELAY_MATRIX`) driver decision.

### Risks

- Battery charge/discharge sourcing is still unimplemented -- this
  milestone validates connectivity/bench-level hardware control only, not
  the safety-critical current-sourcing path into a real battery channel.
  Treat that as new, unvalidated territory when it begins.
- `AUX_SMU_2`'s channel was silently wrong (defaulted to `"0"`) until this
  rack validation caught it -- a reminder that config placeholders for
  not-yet-physically-confirmed values should be tested against real
  hardware before being relied on, not just accepted because they don't
  raise an error.
- No automated regression test exists yet for the `smu_channel` fix (no
  hardware-in-the-loop CI) -- a future change to `hardware/smu.py::connect()`
  could silently reintroduce the multi-channel ambiguity without a real rack
  test catching it.

### Recommended next milestone

**Milestone 2: Battery Charge/Discharge Sourcing Bring-Up** -- implement and
validate `SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/
`measure()` for a single battery channel end-to-end (one relay-selected
channel, one SMU, real current sourced/sunk, real DAQ readback), before
scaling to all 8 channels or additional SMUs. This is the natural next step
now that every individual piece of hardware (relay, SMU, DMM, discovery) is
confirmed reachable and controllable in isolation.

---

## Milestone 2: Proto Test Execution

**Status:** ACHIEVED -- validated end-to-end on the physical PXIe rack (see
"Physical rack validation" below). Unit-verified with mocked hardware first,
then confirmed against real hardware.
**Scope:** exercise the real production architecture end-to-end -- relay,
SMU, DMM, SQLite persistence, state display, safe shutdown, Ctrl+C
cancellation -- through the same code path a future battery test will use,
with **no battery connected**. Infrastructure validation, not battery
validation. Inserted ahead of Milestone 1's originally recommended next
step (battery charge/discharge sourcing, see below) as a deliberate,
lower-risk step: prove every layer works together before sourcing current
into a real battery channel.

### Objectives achieved

- Cycle every configured relay (force-all-off -> verify -> activate ->
  verify, via the unchanged `hardware/relay_eth.py::NumatoRelayMatrix`),
  source and fully verify a bench SMU voltage point on each
  (`hardware/smu.py::SMU.source_dc_voltage_point()`, reused unchanged from
  SMU Verification Hardening), and take a DMM reading while output is
  genuinely still active.
- Persist relay number, timestamp, SMU commanded/readback/measured values,
  and the DMM reading to a new `station_state` SQLite table
  (`data/storage.py::DataStorage`).
- Display (never auto-resume) the previous execution's last known position
  at startup, reading `station_state` across run_ids.
- Preserve every existing safety/verification guarantee from Milestones 1
  and the SMU Verification Hardening work: relay verification, SMU
  configuration verification, safe-state teardown on every exit path
  (success, failure, safety violation, operator cancellation), and the
  centralized "return to Main Menu" operator workflow.

### Architectural decisions made

- **New file:** `test_control/proto_test_sequence.py::ProtoTestSequence` --
  justified as the natural extension of this project's existing one-class-
  per-sequence-file pattern (`battery_test.py`/`charge_cycle.py`/
  `discharge_cycle.py` are already separate files), not a parallel
  framework. Mirrors `BatteryTestSequence`'s constructor shape and
  exception-handling structure exactly.
- **Two new optional parameters on `SMU.source_dc_voltage_point()`**
  (`hold_s`, `during_hold`), both defaulting to no-ops -- zero behavior
  change for the existing SMU Functional Validation caller. Chosen over
  duplicating the ~40 lines of already-audited configure/verify logic into
  a second method, and over teaching `hardware/smu.py` anything about the
  DMM (the `during_hold` callback is opaque to the driver).
- **New `station_state` table, not a new database or storage class.**
  Added directly to the existing `DataStorage` (two new concrete methods,
  not part of the `StorageBackend` abstract interface, since station/
  execution position is a different concern from a per-sample measurement).
  This is the `station_state` table `docs/DATABASE_ROADMAP.md`/
  `docs/TODO.md` already anticipated for the future recovery engine --
  built now, scoped to Proto Test Execution's narrower "display, don't
  resume" need.
- **No new entry-point clutter in `main.py`.** The entire Proto Test
  Execution workflow lives in `test.py::run_proto_test_execution()` and
  `test_control/proto_test_sequence.py` -- `main.py` was not modified.
- **No battery-limit logic added anywhere.** `SafetyMonitor` remains the
  sole owner of limit/abort decisions, per the SMU Verification Hardening
  separation-of-responsibilities decision -- `ProtoTestSequence` only calls
  `safety.emergency_stop()`/`safety.safe_cancel_shutdown()`.

### Physical rack validation

**Hardware used:**
- SMU: `AUX_SMU_1` -- PXI-4130, Slot 7, channel `"1"` (selected via
  `Settings.PROTO_TEST_SMU_NAME`, added specifically so this workflow could
  target this unit instead of `HardwareManager`'s positional
  `next(iter(SMU_ASSIGNMENTS...))` default, which always resolves to
  `PRIMARY_SMU`/Slot 5 -- see the fix below).
- DMM: `MAIN_DMM` -- NI-4065, Slot 3.
- Wiring: SMU Output+ -> DMM Voltage Input, SMU Output- -> DMM COM (direct,
  fixed, permanent connection for this validation stage).
- Relay: Numato Ethernet Relay Matrix, `MATRIX_NUMATO_201`.
- No battery, no external load connected.
- `PROTO_TEST_DWELL_S` temporarily set to `5` (from the `120` production
  default) for a fast validation pass.

**Results:** all 8 relays cycled successfully; run completed with a
`COMPLETED` final `station_state` row. Relays 2-8 showed SMU-measured and
DMM-measured voltage in close agreement (~4.199 V on both instruments,
e.g. Relay 8: SMU 4.199566 V / DMM 4.199649 V). Relay 1 showed a one-time
discrepancy: SMU measured 3.535174 V while the DMM (read moments later in
the same hold window) measured 4.199639 V.

**First-relay startup transient -- interpretation:** root-caused via code
inspection (`hardware/smu.py::source_dc_voltage_point()`) to the complete
absence of any settling delay between `session.initiate()` (where active
sourcing to the commanded setpoint begins) and the SMU's own
`session.measure(VOLTAGE)` call, taken immediately afterward with zero
delay. This gap exists on every relay, but Relay 1 is the only iteration
where the underlying NI-DCPower session executes its first-ever
`commit()`/`initiate()` cycle (the session is opened once at startup and
reused for all 8 relays; `HardwareManager`'s startup safety check only ever
calls `output_disable()`, never a real sourcing configuration) -- a "cold"
first activation plausibly carries settling/warm-up overhead (internal
range-relay actuation, first-use calibration) that "warm," already-used
activations on Relays 2-8 don't repeat. The DMM's reading, taken slightly
later in the same hold window (after two additional NI-DCPower round-trips
have already elapsed), landed after the transient had already settled --
consistent with every observation: the mismatch occurred once, self-
corrected within the same cycle, and never recurred.

**Classification:** expected startup transient / instrument behavior, not a
relay-timing issue (relay timing is identical across all 8 relays and does
not explain a discrepancy isolated to the first one) and not a verification
bug (the SMU's configuration readback correctly confirmed 4.2 V was
accepted on Relay 1 too -- only the physical measurement lagged, and
measured values are deliberately never asserted equal to commanded values
by this project's existing verification philosophy).

**Validation status: PASSED.** Decision (see the timing/reliability review
that accompanied this validation pass): document, do not fix before Battery
Integration -- a real fix is better designed once Battery Integration's
actual load/settling dynamics are known, and the anomaly did not affect
safety, relay verification, or SMU configuration verification, all of which
worked exactly as designed.

**Fix applied following this validation:** `Settings.PROTO_TEST_SMU_NAME`
(`config/settings.py`) added, defaulting to `AUX_SMU_1`, and
`test.py::run_proto_test_execution()` updated to resolve the SMU by that
name instead of the positional `next(iter(...))` default --
`HardwareManager`'s own default and `main.py`'s real battery-test path are
untouched. Console progress output (`test_control/proto_test_sequence.py`)
was also added -- relay number, phase, and SMU/DMM measurements are now
printed live per relay, since `test.py` never configures a logging handler
for this workflow and the multi-minute per-relay dwell was otherwise silent
on screen.

### Remaining work

- Two additional physical rack validation runs planned, to confirm Relays
  2-8's behavior repeats and to check whether the Relay 1 transient recurs
  (predicted: yes, since the NI-DCPower session is reopened fresh each run)
  or does not (which would indicate an instrument-level effect not yet
  accounted for).
- Restore `PROTO_TEST_DWELL_S` to `120` (or the intended production value)
  once the two additional validation runs are complete.
- Automatic resume from a previous execution position -- deliberately out
  of scope for this milestone (display only).
- The originally-recommended Milestone 2 (battery charge/discharge sourcing
  bring-up, see below) is now effectively Milestone 3.

### Risks

- The first-relay startup transient (documented above) has not been
  eliminated, only explained and deliberately deferred -- if Battery
  Integration's first real charge/discharge cycle also happens to be the
  session's first `commit()`/`initiate()`, the same class of transient
  could appear in real battery data, not just a bench measurement.
- No explicit settling-time verification exists anywhere between
  `session.initiate()` and the first measurement taken afterward, for any
  caller of `source_dc_voltage_point()` -- relies entirely on the
  configured `hold_s` dwell (when present) to provide incidental margin.

### Recommended next milestone

**Milestone 3: Battery Charge/Discharge Sourcing Bring-Up** (carried over
from Milestone 1's recommendation, renumbered) -- implement and validate
`SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()`
for a single battery channel end-to-end, before scaling to all 8 channels
or additional SMUs.

## Milestone II: Monitor Battery

### Objectives achieved

- Run Main Test's single legacy action replaced with a submenu: `1. Monitor
  Battery` / `2. Charge Battery` / `3. Discharge Battery` / `4. Cycle
  Battery`. Only Monitor Battery is implemented; the other three are
  explicit placeholders.
- `config/devices.py::BATTERY_CONFIGS` now catalogs the two real battery
  types (`HUB` -- 1050 mAh, `SB` -- 160 mAh), replacing the placeholder
  `GENERIC_LIION_18650` entry. Only `nominal_voltage_v`/`capacity_ah` are
  confirmed from the spec -- voltage/current/temperature limits remain
  assumed placeholders (marked `# unconfirmed placeholder` inline) pending
  datasheet confirmation.
- Battery type selection is explicit and operator-controlled
  (`test.py::_select_battery_type()`) -- `BATTERY_CHANNELS` was stripped of
  its `battery_type` field and is now physical wiring information only.
- New `config/devices.py::BATTERY_GROUPS` -- battery groups are a relay
  routing architecture (one relay matrix per group of `GROUP_SIZE` (8)
  positions), not a purely logical grouping. Group A (positions 1-8,
  `MATRIX_NUMATO_201`) is the only enabled group today; B/C/D are pre-wired
  for future relay matrices. Operator workflow is always Battery Type ->
  Battery Group -> Battery Position, with position always shown relative to
  its group ("Group A Position 3").
- `Settings.NUM_CHANNELS` renamed to `Settings.BATTERY_POSITIONS`, with a
  new `Settings.GROUP_SIZE` constant -- both call sites
  (`test.py`/`utils/validators.py`) updated.
- Confirmation screen (Mode/Battery Type/Capacity/Group/Position/Max-Min
  Voltage/Max Charge-Discharge Current/Max Temperature, `Continue? (Y/N)`)
  gates every relay activation -- declining touches no hardware at all.
- Mandatory configuration traceability: accepting the confirmation screen
  writes a `run_summary` battery-config snapshot and a fixed sequence of
  seven `event_log` entries (Run started / Mode selected / Battery selected
  / Battery capacity / Group selected / Position selected / Configuration
  snapshot recorded) **before** the relay closes or any measurement is
  taken -- verified in order by a mocked smoke test.
- New `test_control/monitor_battery_sequence.py::MonitorBatterySequence`,
  mirroring `ProtoTestSequence`'s structure, using the same Milestone II
  infrastructure (`measurements`/`run_summary`/`event_log`/`station_state`,
  `ExecutionFrame`/`render_execution_frame()`) -- no new storage design.
  Read-only: no charging, no discharging, no SMU sourcing.
- `ExecutionFrame` extended with `battery_voltage`/`battery_current`/
  `battery_temp` fields (reusing the original `measurements.voltage_v`/
  `current_a`/`temp_c` columns), kept distinct from `smu_voltage`/
  `smu_current`/`dmm_voltage` since Monitor Battery observes a different
  signal (a plain reading of the battery itself, no SMU sourcing).
- Relay Functional Validation's Matrix Scan gained a scope-selection menu
  (`1. All Groups` / `2. Group A` / `3. Group B` / `4. Group C` / `5. Group
  D`) via new optional `channel_start`/`channel_end` parameters on
  `test_relay_matrix_scan()`/`_run_relay_matrix_scan()` -- future-proof for
  additional relay matrices.

### Update: temporary DMM voltage source (post real-hardware validation)

The original DAQ-per-channel voltage read (`hardware/daq.py::DAQ.read_channel()`
against `BATTERY_CHANNELS[i]["daq_voltage_ch"]`) failed during real-hardware
validation due to channel/device configuration issues not yet resolved.
`MonitorBatterySequence` was changed to read voltage from the DMM instead
(`hardware/dmm.py::DMM.measure_dc_voltage()`, the same call already validated
by Proto Test Execution) -- already-working hardware, sufficient for basic
voltage-only monitoring at this development phase. `current_a`/`temp_c` are
`None` for every Monitor Battery sample as a result (the DMM is voltage-only).
This is explicitly documented as temporary (see `docs/architecture.md`
Section 20a) -- Charge/Discharge/Cycle Battery and a future Monitor Battery
revision must migrate to the final per-position DAQ architecture once channel
mapping/wiring is confirmed. `run_summary` gained six new columns
(`start_voltage`/`end_voltage`/`min_voltage`/`max_voltage`/`average_voltage`/
`sample_count`, additive migration) populated at end-of-run; an `event_log`
entry ("Monitoring source: DMM") records the acquisition source for every
session.

### Update: hardware identity traceability

Review found that while battery configuration (type/capacity/group/position/
mode) was correctly persisted via the traceability pattern, hardware identity
(which physical SMU/DMM/DAQ/relay matrix executed the run) was not -- it only
ever appeared in console output, lost once the session ended. Extended the
same traceability pattern (durable `run_summary` snapshot + `event_log`
entries before hardware activation) to cover it:

- `run_summary` gains twelve additive columns: `smu_name`/`smu_resource`/
  `smu_model`, `dmm_name`/`dmm_resource`/`dmm_model`, `daq_name`/
  `daq_resource`/`daq_model`, `relay_matrix_name`/`relay_matrix_resource`/
  `relay_matrix_model` -- `name` is the `config/devices.py` dict key,
  `resource` is the VISA resource string (PXI) or `ip:port` (Ethernet relay
  matrix), `model` is the real instrument model or relay driver identifier.
- Both `run_proto_test_execution()` and `_run_monitor_battery()` now log one
  `event_log` entry per connected instrument ("SMU in use: ...", "DMM in
  use: ...", "DAQ in use: ...", "Relay matrix in use: ...") plus a
  "Hardware configuration snapshot recorded" entry, all before the first
  relay closes -- verified by mocked smoke tests to precede relay
  activation for both test types.
- New shared helpers: `config/devices.py::find_config_name()` (reverse
  dict-key lookup by identity, avoids hardcoding a second copy of a
  config key like `"MAIN_DMM"`) and `hardware_traceability_messages()`
  (shared message-building, used identically by both test types);
  `test.py::_hardware_snapshot_fields()` (shared `run_summary` field
  builder).
- `ProtoTestSequence.run()` gained one new, optional, backward-compatible
  parameter (`hardware_snapshot: dict = None`) -- `None` reproduces prior
  behavior exactly. `MonitorBatterySequence` needed no change -- its
  `start_run_summary()` call already lives in `test.py` itself.
- No new table. No behavior change to what hardware actually connects --
  `smu_cfg`/`daq_cfg` are now passed explicitly into `HardwareManager(...)`
  in both workflows (previously left to its internal defaults) purely so
  the snapshot matches, 1:1, the exact cfg dict a driver was built from.

See `docs/architecture.md` Section 22 for full rationale.

### Architectural decisions made

- Battery type is never inferred from wiring config (`BATTERY_CHANNELS`) --
  always an explicit operator choice, so the same physical position can be
  reused across different battery models without a config edit.
- Battery Groups model physical relay-matrix boundaries, not just a display
  grouping -- this is the scaling path for every future relay expansion
  (each new group of 8 positions gets its own matrix and `BATTERY_GROUPS`
  entry).
- Cancellation (Ctrl+C) is Monitor Battery's expected, normal end -- there
  is no bounded "success" exit the way Proto Test's fixed relay cycle has
  one; `run_summary.result = "STOPPED_BY_OPERATOR"` on that path, not a
  failure result.
- `MonitorBatterySequence` still takes an `smu` reference purely for
  `safety.emergency_stop()`/`safety.safe_cancel_shutdown()` (both require
  one) even though this mode never sources through it -- one shared
  safety-shutdown entry point for every mode, not a Monitor-specific
  relay-only path.
- The previous `run_main_test()` body (`TestExecutor`/`ResultManager`, the
  same path `main.py` uses) was retired from this menu entry in favor of
  the new submenu; `main.py`'s own production path is untouched.

### Verification

- `python -m py_compile` clean on every touched file
  (`config/devices.py`, `utils/device_validator.py`, `config/settings.py`,
  `utils/validators.py`, `test_control/execution_screen.py`,
  `test_control/monitor_battery_sequence.py`, `test.py`).
- `utils/device_validator.py::validate_devices()` returns zero errors
  against the updated `BATTERY_CONFIGS`/`BATTERY_CHANNELS`/`BATTERY_GROUPS`.
- Mocked-hardware smoke test (`HardwareManager`/`DataStorage` mocked,
  `input()` scripted through the full Battery Type -> Group -> Position ->
  Confirm flow, `DMM.measure_dc_voltage()` mocked, `MonitorBatterySequence`
  run for real): confirms the seven traceability `event_log` calls plus
  "Monitoring source: DMM" all precede/accompany relay/monitoring start,
  that `MonitorBatterySequence` receives the correct channel/relay
  arguments, and that `finish_run_summary()` receives correct
  start/end/min/max/average voltage and sample_count values.
- A second mocked run confirms that declining the confirmation screen
  (`N`) never constructs `HardwareManager` -- no hardware is touched.

### Remaining work

- Charge Battery, Discharge Battery, Cycle Battery -- menu placeholders
  only, not implemented.
- Migrate Monitor Battery from the temporary DMM voltage source to the
  final per-position DAQ architecture once `BATTERY_CHANNELS`'
  `daq_voltage_ch`/`daq_current_ch`/`daq_ntc_ch` channel mapping is
  confirmed against real NI-MAX aliases/wiring (see `docs/architecture.md`
  Section 20a).
- `current_a`/NTC temperature reads in Monitor Battery remain `None` --
  the former because the temporary DMM source is voltage-only, the latter
  the same pre-existing gap already carried by `charge_cycle.py`/
  `discharge_cycle.py`.
- Physical rack validation of Monitor Battery (DMM voltage path) on real
  Group A hardware (this milestone's changes have been verified with
  mocked hardware only).
- `BATTERY_CONFIGS` voltage/current/temperature limits marked
  `# unconfirmed placeholder` should be confirmed against the real BLOSS
  Hub datasheet before being relied on for safety enforcement.
- `Settings.ACTIVE_CHANNELS` -> `ACTIVE_POSITIONS` rename, deliberately
  deferred (would touch `test_control/` files outside this change's scope).

### Recommended next milestone

**Milestone III: Charge Battery** -- implement the first real
current-sourcing battery mode using the same Battery Type/Group/Position
selection, confirmation screen, and traceability logging Monitor Battery
established, adding CC-CV charge control on top.

## Milestone II: Menu Restructuring Review

Following a full execution-tree review of `test.py`
(`docs/EXECUTION_TREE_REVIEW.md`), nine annotated architecture questions
were addressed. Full rationale for each is in `docs/architecture.md`
Section 23; summary here:

- **Test Sensors (NTC)** gained a real DAQ-based NTC channel scan (Test 6),
  iterating every `BATTERY_CHANNELS` position marked `enabled` and reading
  its `daq_ntc_ch` -- config-driven, no new configuration variable, no
  hardcoded channel list. Pure-math Tests 1-5 unchanged.
- **Test Temperature Module** retired as a standalone top-level MENU
  entry (temperature monitoring now comes through the DAQ NTC path above)
  -- the function and its Identity Validation check are kept, and Hardware
  Discovery still reports `TEMP_MODULE`'s presence.
- **Numato relay timing** reviewed: no hardcoded dwell/sleep exists
  anywhere in the four relay Functional Validation tests; the differing
  wall-clock speed between them is an intentional consequence of which
  API layer each one exercises (public safety-wrapped `close()`/`open()`
  vs. native `write()`/`verify_all()` primitives) -- not standardized,
  since doing so would weaken or defeat each test's distinct purpose.
- **PXI Relay Matrix** future reuse architecture documented (no PXI
  hardware/driver exists yet, so nothing implemented): `test_relay_matrix_
  scan()`/`test_relay_safety_selftest()` already operate purely through
  `RelayFactory`/`RelayBase` and will work against a future `niswitch`
  driver unchanged once one exists; only the native-primitives test
  (`test_relay_ethernet_test()`) is Numato-protocol-specific.
- **Test Safety Monitor** became a workflow-oriented simulator: the
  original 7 logic unit tests are unchanged, plus a new step-by-step
  simulation of Monitor/Charge/Discharge/Cycle Battery's phase shape
  (relay close -> phases -> relay open) using the REAL `SafetyMonitor`
  logic against simulated measurements -- no hardware, no database writes.
  Cycle Battery's simulation deliberately injects an overtemperature fault
  to demonstrate the abort path. Surfaced a pre-existing, already-tracked
  Settings inconsistency (`DISCHARGE_CUTOFF_V` below `BAT_VOLTAGE_MIN`)
  while building the Discharge simulation.
- **Test Configuration** removed from the top-level MENU (redundant with
  `preflight_check()`, which already runs it automatically at startup) --
  the function itself is unchanged.
- **Database Tools** (new, replaces "Test SQLite (foundation)"/"Test
  Database Layer"): a submenu with 5 new read-only real-database
  inspection views (Latest Run/Event Log/Measurements/Station State/
  Statistics) plus the two original temp-DB regression self-tests,
  relocated unchanged.
- **Run All Tests** replaced with **UI Test**: a hardware-and-database-free
  preview of Proto Test/Monitor Battery `ExecutionFrame` screens via
  hardcoded demo data and the real `render_execution_frame()`; Charge/
  Discharge/Cycle and a Historical Results Viewer are honestly reported as
  "not yet implemented" rather than faked. The `fn=None`
  aggregate-everything dispatch branch was removed alongside it (no
  MENU entry uses that pattern anymore).

Final top-level MENU count: 13 (was 16) -- see `docs/architecture.md`
Section 23j for the full before/after list.

Verified: `py_compile` clean; every non-hardware-run MENU entry executed
via a mocked smoke test with no unhandled exceptions; `Database Tools`'
inspection views confirmed against the real development database;
`preflight_check()`/`validate_devices()` unaffected.

## Milestone II: Safety Monitor Simulator -- Full Workflow Walkthrough

Follow-up enhancement to the Menu Restructuring Review above: the Safety
Monitor Simulator (Part 2 of `test_safety_monitor()`) became an
interactive, step-by-step **operational walkthrough** -- not just safety
decisions, but the full sequence a real workflow executes. The operator
now selects one workflow (Monitor/Charge/Discharge/Cycle Battery) from a
menu, then the simulator walks every action in order (load configuration,
resolve group/position/relay routing, close relay, configure/enable PSU,
acquire measurement, run the REAL `SafetyMonitor` check, update
`ExecutionFrame`, store measurement, evaluate phase transitions, ...),
pausing for Enter between each step. Each step displays Workflow/Current
Phase/Current Step/Description, then Voltage/Current/Temperature/Safety
Evaluation/Decision/Next Action.

This is now designated the **development reference implementation** for
Charge/Discharge/Cycle Battery -- future developers should be able to map
the displayed step sequence directly onto production code (see
`docs/architecture.md` Section 23e). `_monitor_battery_walkthrough_steps()`
mirrors the already-implemented `MonitorBatterySequence.run()` exactly, so
it doubles as a worked example of simulator-step-to-real-code
correspondence. Step counts: Monitor Battery 16, Charge Battery 16,
Discharge Battery 14, Cycle Battery 31 (charge phase + transition +
discharge phase, aborting at step 28/31 on the deliberately-injected
overtemperature fault).

Verified: `py_compile` clean; each of the four workflows run end-to-end
via a mocked smoke test (Monitor/Charge/Discharge complete all steps
PASS; Cycle correctly aborts and reports PASS for "correctly aborted");
console output format confirmed against the requested display spec;
confirmed by code inspection that no step anywhere imports or calls
`HardwareManager`/`DataStorage`/`RelayFactory`/any `hardware/*.py` driver.

## Milestone II: Relay + PSU Safety Verification Pattern

Implements the compliance improvement identified by
`docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`, and extends the same philosophy
to PSU/SMU output control. See `docs/architecture.md` Sections 24-26 for
full detail; summary here.

**Root cause:** every real relay path converged on one shared function
(`NumatoRelayMatrix._force_all_off_and_verify()`), which forced the relay
bank off and verified it -- but never first read and recorded the bank's
*pre-existing* state. A pre-existing unsafe state (a relay left active
from an earlier fault) was silently corrected, never diagnosed. The exact
same shape of gap existed in `SMU.source_dc_voltage_point()` -- the one
real PSU-output-enabling method in the codebase -- which configured and
enabled output on every call without first confirming a safe baseline.

**Relay fix (`hardware/relay_eth.py`):** new `check_current_relay_state()`
(Read All -> Verify Current Status, steps 1-2) is now called at the start
of `_force_all_off_and_verify()` (which already did Force Off -> Verify
Off -> Action -> Verify Action, steps 3-8) -- bringing every real relay
path (`MonitorBatterySequence`, `ProtoTestSequence`, legacy
`BatteryTestSequence`, `HardwareManager` startup/shutdown, `SafetyMonitor`
shutdown, and every Numato commissioning test in `test.py` that uses the
public `open()`/`close()`/`open_all()` API) into full compliance with a
single, centralized change. `test.py::test_relay_ethernet_test()` (the
one path that deliberately bypasses the public API to test native
primitives) now calls the same shared `check_current_relay_state()`
explicitly, so it is not left behind. New `NumatoRelayMatrix.last_known_mask`
attribute records the most recently read state.

**PSU fix (`hardware/smu.py`):** new `query_output_state()` (pure
readback), `check_current_output_state()` (steps 1-2, mirrors the relay
method), and `force_output_off_and_verify()` (steps 1-2 + 3-4-5 combined)
-- `source_dc_voltage_point()` now calls `force_output_off_and_verify()`
as its first action, before any configuration is attempted, raising
`SMUError` immediately if a safe baseline can't be verified. New
`SMU.last_known_output_state` attribute records the most recently queried
state. `emergency_output_off()` (the existing shutdown reflex) already
implemented the agreed Disable -> Query -> Verify Off shutdown pattern
exactly, unchanged.

**Future cross-validation, prepared not implemented:** new
`SMU.cross_validate_output_state(measured_v, measured_i)` stub (raises
`NotImplementedError`, never called) marks where a future comparison of
PSU-reported state against an independent measurement (DMM, or the SMU's
own ADC readback) belongs -- documented rationale for why this extension
point was added to the PSU side and not the relay side in
`docs/architecture.md` Section 26.

**Compliance status:** every real, production/validation-reachable relay
path -- previously Partially Compliant (steps 3-8 only) -- is now Fully
Compliant with the agreed 6-stage pattern. The one real PSU-output path
(`source_dc_voltage_point()`, and therefore SMU Functional Validation and
Proto Test Execution) now implements the full agreed pattern too. The
three non-production relay abstractions already flagged as unreachable
(`SerialRelay`, `SimulatedRelay`, `RelayMatrix`) were intentionally left
unmodified -- out of scope for this centralized fix.

Verified: `py_compile` clean on all touched files; a mocked-socket test
confirms `close(1)` against a bank with relay 3 already active issues,
in order, `relay readall` (finds/logs the unexpected state) ->
`relay writeall 00` -> `relay readall` (verify off) -> `relay on 0` ->
`relay read 0` -> `relay readall` (verify action) -- the complete 8-step
sequence, command-by-command; a mocked NI-DCPower session test confirms
`source_dc_voltage_point()` detects a PSU already reporting ON, forces
off + verifies, then configures/enables/verifies-ON as before, with
`last_known_output_state` tracked correctly throughout; `cross_validate_
output_state()` confirmed to raise `NotImplementedError`; full
non-hardware MENU smoke test (all 13 top-level entries) and the Monitor
Battery/Safety Monitor Simulator mocked smoke tests re-run with no
regressions.

## Milestone II: Timing/Delay/Settling Analysis + Interruptible Wait Mechanism

Full timing/delay/timeout/polling/settling-time analysis performed across
every hardware category (`docs/TIMING_ANALYSIS.md`), ahead of real
Charge/Discharge Battery implementation. Top finding: several real dwells
held hardware energized for their full configured duration with **no
cancellation checkpoint inside the wait itself** -- most importantly
`SMU.source_dc_voltage_point()`'s `hold_s` (used by `ProtoTestSequence`
via `Settings.PROTO_TEST_DWELL_S`, a temporary 5s value standing in for an
intended 120s production value -- at 120s, an uncancellable dwell would be
a ~2-minute Ctrl+C blind spot) and `ChargeCycle`/`DischargeCycle`'s
pre-loop `STABILIZATION_S` (5.0s).

**Fix:** new `utils/cancellation.py::interruptible_sleep(duration_s, token=None,
poll_interval_s=0.2)` -- a reusable drop-in replacement for `time.sleep()`
that checks for cancellation every `poll_interval_s` (default 0.2s)
instead of blocking uninterrupted. `token=None` preserves the exact prior
`time.sleep()` behavior (zero change for callers that don't pass one);
cancelled-mid-wait bounds worst-case Ctrl+C latency to ~`poll_interval_s`
instead of the full duration; normal (non-cancelled) total wait time is
unchanged.

Wired into every real dwell identified by the review: `SMU.
source_dc_voltage_point()`'s `hold_s` (new `token` parameter, threaded
through from `ProtoTestSequence.run()`), `ChargeCycle`/`DischargeCycle`'s
`STABILIZATION_S` and per-sample `dt` sleeps, and `MonitorBatterySequence.
run()`'s `sample_interval_s`.

**A real latent bug was found and fixed** while wiring this in:
`ChargeCycle`/`DischargeCycle`'s `STABILIZATION_S` sleep was located
OUTSIDE the `try/finally` guarding `smu.emergency_output_off()` --
harmless while the sleep was uninterruptible, but a live gap the moment
it became cancellable (a cancellation during stabilization would have
skipped the PMU shutdown entirely). Fixed by moving the `try/finally` to
start immediately after `output_enable()`, covering the stabilization
wait and the sampling loop both. `SMU.source_dc_voltage_point()`'s
exception handling also gained an explicit `except OperationCancelledError:
raise` (mirroring its existing `SMUStateVerificationError` clause) so a
mid-hold cancellation is never silently wrapped into a generic `SMUError`.

Verified: `interruptible_sleep()` unit-tested in isolation (no-token /
never-cancelled / cancelled-mid-wait, all three behaviors confirmed);
`source_dc_voltage_point(hold_s=10.0)` cancelled after ~0.2s with output
still confirmed OFF; `ChargeCycle.run()` cancelled 0.1s into a 5.0s
`STABILIZATION_S` window with `emergency_output_off()` confirmed called;
`MonitorBatterySequence.run()` cancelled ~0.3s into a 2.0s
`sample_interval_s` window with safe-cancel shutdown confirmed; a normal
(non-cancelled) `hold_s=0.6` run confirmed to still take the full ~0.6s;
full non-hardware MENU regression (13 entries) re-run with no failures.
See `docs/architecture.md` Section 27.

---

## Milestone II: BATTERY_CONFIGS -> SafetyMonitor Integration

Architecture review (`docs/SAFETY_MONITOR_BATTERY_LIMITS_REVIEW.md`) found
that `SafetyMonitor`/`ChargeCycle`/`DischargeCycle` were battery-type-blind:
despite `config/devices.py::BATTERY_CONFIGS` already holding per-battery
(HUB/SB) voltage/current/temperature limits and battery selection already
existing in the UI, safety checks and commanded charge/discharge setpoints
still only read the shared global `Settings.BAT_*`/`CHARGE_*`/`DISCHARGE_*`
constants -- quantified as up to ~12.5x headroom for SB and ~1.9x for HUB
versus their own configured limits.

**Fix:** `SafetyMonitor` gained an optional `battery_cfg` (constructor and
`set_battery_limits()`), four private limit resolvers, and a `mode`
("charge"/"discharge") parameter on `check()` to pick the matching current
limit -- `battery_cfg=None` (default) preserves the exact prior
global-Settings-only behavior. `ChargeCycle.run()`/`DischargeCycle.run()`
gained an optional `battery_cfg` parameter: when given, it resolves the
commanded PSU current/voltage from `BATTERY_CONFIGS` instead of global
`Settings`, calls `safety.set_battery_limits(battery_cfg)`, and passes the
matching `mode` into every safety check. `BatteryTestSequence` threads
`battery_cfg` through for completeness. The Safety Monitor Simulator
(`test.py`) gained a battery-type selection step so its displayed/enforced
limits also come from the selected `BATTERY_CONFIGS` entry.

Verified: HUB and SB now trip `SafetyMonitor.check()` at their own distinct
current limits (previously indistinguishable under the shared global
ceiling); `battery_cfg=None` reproduces prior behavior exactly; all four
Safety Monitor Simulator workflows (Monitor/Charge/Discharge/Cycle) re-run
PASS for HUB, SB, and "skip battery selection". See `docs/architecture.md`
Section 28. This closes the last significant safety-architecture gap
identified before real Charge Battery implementation.

---

## Milestone II: Summary

**Status:** COMPLETE

Milestone II covers every "Milestone II: ..." entry above -- Monitor
Battery, the Menu Restructuring Review, the Safety Monitor Simulator, the
Relay + PSU Safety Verification Pattern, the Timing/Delay/Settling
Analysis + Interruptible Wait Mechanism, and the BATTERY_CONFIGS ->
SafetyMonitor Integration. This entry is the closing summary; each linked
section above still holds the full technical detail.

### Delivered

- Database architecture (`data/storage.py`, `data/sqlite_manager.py`,
  `station_state` table -- see `docs/DATABASE_ROADMAP.md`, Milestone 2 entry
  above)
- ExecutionFrame architecture (`docs/architecture.md` Section 18a)
- Proto Test migration (Milestone 2 entry above)
- Monitor Battery implementation (`docs/architecture.md` Section 20)
- Hardware traceability (`docs/architecture.md` Section 22)
- Battery traceability (`docs/architecture.md` Section 19)
- Relay safety architecture (`docs/architecture.md` Section 21, 24)
- PSU safety architecture (`docs/architecture.md` Section 25, 26)
- Safety Monitor Simulator (`docs/architecture.md` Section 23e)
- BATTERY_CONFIGS integration with SafetyMonitor (`docs/architecture.md`
  Section 28)

### Validated

- Real hardware (Hardware Discovery, SMU/DMM Functional Validation, both
  Numato relay matrices, Proto Test Execution -- Milestone 1/Milestone 2
  above)
- SQLite persistence (`test_sqlite`, `station_state`/`test_records`)
- Recovery architecture (`station_state` last-known-position display; full
  cycle/state recovery engine remains on `docs/TODO.md`, not yet built)
- Execution UI (`ExecutionFrame`/`render_execution_frame()`)
- Safety flows (Relay + PSU Safety Verification Pattern, Interruptible Wait
  Mechanism, BATTERY_CONFIGS-aware `SafetyMonitor`, all exercised via the
  Safety Monitor Simulator's four workflow walkthroughs)

### Not yet done (carried into Milestone III, not a Milestone II gap)

- Real Charge/Discharge/Cycle Battery current sourcing -- `SMU.set_charge_mode()`/
  `set_discharge_mode()`/`output_enable()`/`measure()` beyond bench DC
  voltage sourcing, and `DAQ.read_all_batteries()` multi-channel acquisition,
  remain placeholders (see `docs/TODO.md`).
- NTC temperature read (`t_c = None` in `ChargeCycle`/`DischargeCycle`/
  `MonitorBatterySequence`).
- Physical rack validation of Monitor Battery on real Group A hardware
  (verified with mocked hardware only so far).

---

## Milestone III: SMU Review, Discharge Cutoff Policy, and Roadmap Correction

**Status:** ACHIEVED

**Scope:** before starting the Charge/Discharge Workflows milestone below, the
current SMU implementation was re-verified directly from source (not assumed
from the prior architecture review) to confirm what is and isn't real, and
several architecture decisions were formalized and one live gap fixed.

### Findings

- `hardware/smu.py` re-confirmed: only `output_disable()`/
  `verify_output_disabled()`/`emergency_output_off()`/`source_dc_voltage_point()`
  are real; `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/
  `measure()` remain stubs (log-only or fixed-zero return), unchanged since
  the prior review. This confirms real SMU sourcing capability, not
  ChargeSequence orchestration, is the correct next step. See
  `docs/architecture.md` Section 29.
- **Fix:** `DischargeCycle.run()` previously resolved its EOD cutoff voltage
  without regard to the active safety floor. Now resolves a discharge TARGET
  (`DISCHARGE_CUTOFF_V`/`battery_cfg["voltage_min_v"]`, a cycle objective)
  separately from the battery's safety FLOOR (`BAT_VOLTAGE_MIN`/
  `battery_cfg["voltage_min_v"]`) and clamps `cutoff_v = max(target_v,
  floor_v)` -- the floor always has priority. This resolves the previously
  open `docs/TODO.md` item asking "which is correct, `DISCHARGE_CUTOFF_V` or
  `BAT_VOLTAGE_MIN`?" -- the answer is that they are not in conflict; they
  answer different questions. See `docs/architecture.md` Section 30.

### Architectural decisions made

- **Discharge Cutoff Policy** -- documented as a formal policy (Section 30):
  discharge target vs. absolute safety floor, floor always wins, battery
  type always explicit (never inferred from group/position/channel/relay).
- **DAQ Strategy** -- Charge/Discharge/Cycle Sequence development uses the
  DMM as its telemetry source (mirroring Monitor Battery), not
  `DAQ.read_all_batteries()` (unimplemented, channel mapping not approved).
  This work must not be blocked by DAQ mapping work. See
  `docs/architecture.md` Section 31.
- **SMU Functional Validation (no load) milestone added** -- validates mode
  configuration, output state transitions, readback, safety shutdown, and
  command verification without a battery or load, once the missing SMU
  methods are implemented, and explicitly before Charge/Discharge Sequence
  is built on top of them. See `docs/architecture.md` Section 32.
- **ChargeCycle/DischargeCycle harvest plan documented** (KEEP/MIGRATE/
  REMOVE/RETIRE), no code migrated yet -- see `docs/architecture.md`
  Section 33.
- **ProtoTestSequence reviewed** -- confirmed it duplicates
  `BatteryOperationSequence.run_guarded()`'s pattern independently;
  migration recommended but not urgent, no refactor performed. See
  `docs/architecture.md` Section 34.
- **Roadmap reordered** (Section 35): review SMU -> complete SMU
  functionality -> SMU Functional Validation (no load) -> validate results
  -> harvest ChargeCycle/DischargeCycle -> ChargeSequence -> DischargeSequence
  -> CycleSequence -> legacy retirement. `BatteryOperationSequence` remains
  the target execution architecture throughout.

### Verified

`python -m py_compile` clean on every touched file (`config/settings.py`,
`test_control/discharge_cycle.py`, `test.py`). No behavior change for any
caller using a real, currently-configured `battery_cfg` (HUB/SB: target and
floor are numerically identical today, `cutoff_v` unchanged). The
global-Settings-only fallback path's effective cutoff changed from 3.0 V to
3.5 V (the more conservative value) -- a deliberate, documented safety
correction, not a regression.

### Recommended next milestone (achieved -- see below)

**Milestone IV: SMU Functional Validation (no load)** -- implement the
missing `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/
`measure()` methods for real, then validate them without a battery or load,
per `docs/architecture.md` Section 32, before harvesting ChargeCycle/
DischargeCycle logic into a real ChargeSequence.

---

## Milestone IV: SMU Implementation + ChargeSequence/DischargeSequence

**Status:** ACHIEVED (no-load/mocked-hardware validation only -- physical
rack validation with a real battery remains open, tracked in `docs/TODO.md`)

**Scope:** implemented the four remaining SMU stub methods, performed no-load
SMU Functional Validation, then (no major blocker found) implemented
`ChargeSequence`/`DischargeSequence` on `BatteryOperationSequence` and wired
them into the live MENU.

### Objectives achieved

- `hardware/smu.py::SMU.set_charge_mode()`/`set_discharge_mode()`/
  `output_enable()`/`measure()` implemented for real via a new shared
  `_configure_current_source()` helper, reusing `force_output_off_and_verify()`
  (Section 25)/`_verify_config_readback()` (Section 12.6b) unchanged.
  `set_discharge_mode()` configures a genuine current SINK (negative
  `current_level`) at a positive voltage, never a negative-voltage source.
- SMU Functional Validation (no load) performed against `nidcpower`'s real
  `simulate=True` driver runtime -- the actual NI-DCPower driver is
  installed in this environment, so validation exercised the real,
  unmodified production code path rather than a hand-rolled mock.
  Configuration/readback/verification, output-state transitions, and safety
  shutdown all confirmed. One finding (default simulated model is unipolar,
  rejecting negative current -- resolved by testing against a simulated
  bipolar PXIe-4141, matching real production hardware) -- a test-harness
  artifact, not a code defect.
- New `test_control/charge_sequence.py::ChargeSequence` /
  `test_control/discharge_sequence.py::DischargeSequence`, both on
  `BatteryOperationSequence`, wired into `run_main_test()`'s Charge/
  Discharge Battery menu entries (previously "not yet implemented").
- `DischargeSequence` applies the Discharge Cutoff Policy (Milestone III,
  Section 30) from the start -- target/floor clamp, floor always wins.
- Verified with mocked end-to-end smoke tests: happy path to EOC/EOD with
  correct `run_summary` finalization, and a forced-overcurrent safety-abort
  path confirming `SafetyViolationError` -> `safety.emergency_stop()` ->
  `run_summary` finalized `SAFETY_VIOLATION`/`FAIL`.

### Architectural decisions made

- `BatteryOperationSequence` preserved unmodified as the target execution
  architecture -- `ChargeSequence`/`DischargeSequence` are subclasses,
  exactly mirroring `MonitorBatterySequence`, not a new/parallel workflow
  shape.
- Telemetry: DMM for voltage, the SMU's own `measure()` for current -- no
  DAQ dependency introduced, per the documented DAQ Strategy (Section 31).
- Legacy `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence`/`TestExecutor`
  left unchanged and in place -- not retired this milestone (Section 35
  roadmap step 9, later).

### Remaining work

- NTC temperature still not wired into either sequence (`t_c = None`).
- `BATTERY_CONFIGS` limits remain unconfirmed placeholders.
- No physical rack validation with a real battery yet -- mocked hardware
  only. This will be the project's first real current-sourcing/-sinking
  test into an actual cell; treat as new, unvalidated territory.
- `CycleSequence` (charge -> rest -> discharge composition) not yet
  implemented.

### Recommended next milestone (superseded -- see below)

**Milestone V: Physical rack validation of ChargeSequence/DischargeSequence**
-- validate a single real charge and discharge cycle on one relay-selected
channel with a real battery (or electronic load), before scaling to all 8
channels or implementing CycleSequence.

---

## Milestone V: Post-Implementation Validation Review

**Status:** ACHIEVED

**Scope:** thorough, adversarial validation and architecture review of
Milestone IV's `ChargeSequence`/`DischargeSequence`, performed before
adding any new functionality, per the explicit instruction not to assume
the implementation was correct.

### Findings

Two real defects found and fixed (full detail: `docs/architecture.md`
Section 37):

1. Neither sequence opened the relay on successful EOC/EOD completion --
   fixed.
2. `DischargeSequence` configured the SMU's compliance `voltage_limit` at
   the EOD cutoff instead of a ceiling bounding the real battery voltage
   range -- confirmed against `nidcpower`'s real driver that this would
   have put the SMU in voltage compliance for virtually the entire
   discharge (default compliance mode is symmetric, +/-voltage_limit) --
   fixed to use `battery_cfg["voltage_max_v"]`.

One hardware/config-level risk surfaced, not silently fixed: Group A's
assigned SMU (`PRIMARY_SMU`/PXIe-4141) may be physically incapable of the
currents `BATTERY_CONFIGS` commands (confirmed via `nidcpower`'s own
simulated model data -- 100 mA max vs. up to 1.05 A required for HUB
discharge). This requires a real datasheet/wiring confirmation, not a code
change, and is now the top blocker before real hardware use (see
`docs/TODO.md`).

Architecture review (Phase 1) found no duplication, no legacy
(`TestExecutor`/`BatteryTestSequence`) coupling, no `hardware_for_group()`
bypass, and no battery-type inference -- the implementation is
architecturally sound; the two bugs were logic/hardware-semantics defects
within an otherwise correct structure.

### Recommended next milestone

**Milestone VI: confirm PRIMARY_SMU's real current capability** against
the actual installed PXIe-4141 datasheet, and reassign
`BATTERY_GROUPS["A"]["smu"]` if insufficient, before attempting physical
rack validation of `ChargeSequence`/`DischargeSequence`. Once resolved,
proceed to Battery Group Assignment Architecture review (see
`docs/architecture.md` Section 38).

---

## Milestone III (superseded numbering -- see above): Battery Charge/Discharge Workflows

**Status:** STARTING (objectives below superseded by the reordered roadmap in
`docs/architecture.md` Section 35 -- SMU Functional Validation now comes
before Charge/Discharge Sequence implementation, not concurrently with it)

**Scope:** Implement and validate the first real current-sourcing battery
modes -- Charge Battery, Discharge Battery, and Cycle Battery -- reusing
the Battery Type/Group/Position selection, confirmation screen, and
traceability logging Monitor Battery established (Milestone II), and
built on the safety foundation Milestone II closed out: the Relay + PSU
Safety Verification Pattern, the Interruptible Wait Mechanism, and the
now-battery-aware `SafetyMonitor`/`ChargeCycle`/`DischargeCycle`. The
Safety Monitor Simulator (`docs/architecture.md` Section 23e) is the
designated development-reference blueprint for this work -- its simulated
step sequence should be matched (and updated if it diverges) as each real
workflow is implemented, per the `docs/TODO.md` item tracking this.

### Planned objectives

- `hardware/smu.py::SMU.set_charge_mode()`/`set_discharge_mode()`/
  `output_enable()`/`measure()` implemented for real current sourcing/
  sinking on a single battery channel (see `docs/TODO.md`'s `[MUST]` item
  for the full contract: configuration-verification readback, PSU Safety
  Verification Pattern, `interruptible_sleep()` for any real dwell,
  `try/finally` covering that dwell).
- `hardware/daq.py::DAQ.read_all_batteries()` real multi-channel
  synchronized acquisition, replacing the current stub.
- Charge Battery / Discharge Battery / Cycle Battery wired into the live
  MENU submenu (currently explicit placeholders per the Menu Restructuring
  Review), using `ChargeCycle`/`DischargeCycle` with `battery_cfg` passed
  through end to end.
- NTC (or PXIe-4353) temperature read wired into `ChargeCycle`/
  `DischargeCycle`/`MonitorBatterySequence`, replacing `t_c = None`.
- Physical rack validation of a single real charge/discharge cycle before
  scaling to all 8 channels.

---

## Milestone VI: Battery Group Test Configuration Architecture

**Status:** ACHIEVED

**Scope:** formalized `BATTERY_GROUPS` into a complete, self-contained
operational test definition per group -- battery type, hardware
assignment, and test configuration in one place, without a parallel
config system -- and added the validation pipeline this makes possible.

### Objectives achieved

- `BATTERY_GROUPS[group]` gained `"battery_type"` (a declaration the
  operator's explicit selection is cross-checked against, never an
  inference shortcut) and `"test_setpoints"` (the chosen charge/discharge
  recipe, kept explicitly distinct from `BATTERY_CONFIGS`' safety limits).
- New `config/devices.py::group_test_config()` accessor and
  `PXI_SLOTS[...]["max_current_a"]` on every SMU entry (confirmed via
  `nidcpower` simulation: `PRIMARY_SMU`=0.1A, `HIGH_POWER_SMU`=3.0A,
  `AUX_SMU_1`/`AUX_SMU_2`=1.0A).
- New three-stage validation pipeline (`utils/validators.py::
  validate_group_test_config()`: Group Configuration -> Battery Limits ->
  Hardware Capability) and three new exceptions, wired into
  `test.py::_run_charge_or_discharge()` before any hardware is touched.
- `ChargeSequence`/`DischargeSequence.run()` now take `test_setpoints` as
  the commanded value; `battery_cfg` is used only for `SafetyMonitor`.
- A real gap was found and fixed while implementing Stage 3: `PXI_SLOTS`
  fields don't automatically propagate to `SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/
  `DMM_CONFIGS` (each reshapes its source dict field-by-field) -- caught by
  testing the validator itself, not assumed correct.

### Architectural decisions made

- Battery type remains always explicit at selection time -- the group's
  declaration is a consistency guard, never a substitute.
- `BATTERY_CONFIGS` remains untouched -- battery safety limits only, never
  a source of commanded setpoints, now enforced by validation as well as
  by convention.
- Group A declared for SB (not HUB) with a conservative recipe that fits
  inside `PRIMARY_SMU`'s real 0.1A capability -- HUB still requires
  reassigning a group's SMU to a higher-current card (not done here).
- No new file, no parallel config system -- `config/devices.py` remains
  the sole source of truth; everything added is additive fields plus one
  new pure accessor and one new validator function.

### Remaining work

- HUB cannot yet be charge/discharge tested on any group -- needs a group
  reassigned to `HIGH_POWER_SMU` or `AUX_SMU_1`/`AUX_SMU_2`.
- Groups B/C/D still need real hardware, `battery_type`, and
  `test_setpoints` before they're usable (data-only work once wired).
- Physical rack validation of the whole pipeline remains open (mocked
  hardware only so far).

### Recommended next milestone

**Milestone VII: physical rack validation** of `ChargeSequence`/
`DischargeSequence` against Group A + a real SB battery, now that the full
validation pipeline (group -> battery limits -> hardware capability) gates
execution -- the first real hardware run should exercise a configuration
this pipeline has already confirmed valid, not an unvalidated one.

---

## Milestone VII: Architectural Correction -- Battery Type Is Never Operator Input

**Status:** ACHIEVED

**Scope:** corrected Milestone VI's group architecture -- battery type is
no longer operator input in any real workflow, only engineering
configuration read from the selected group.

### Objectives achieved

- `test.py::_select_battery_type()` removed entirely (confirmed unused
  first). Monitor Battery, Monitor Battery Scan, Charge Battery, and
  Discharge Battery all now prompt for Group (and, where applicable,
  Position) only -- no battery-type prompt anywhere in real execution.
- `utils/validators.py::validate_group_test_config()` simplified from
  `(group, battery_type)` to `(group)` -- battery type is read directly
  from `BATTERY_GROUPS[group]["battery_type"]`, no cross-check against
  operator input remains (there is no operator input to check against).
- Confirmation screens updated to label battery type as
  "engineering-configured for this group", clarifying what's actually
  being confirmed.
- `_select_safety_simulation_battery()` (Safety Monitor Simulator)
  deliberately left unchanged -- a dev/exploration tool, not a real
  workflow.

### Architectural decisions made

- Battery type has exactly one source (`BATTERY_GROUPS[group]
  ["battery_type"]`) with no runtime input path that could diverge from
  it -- stronger than the prior "declaration + cross-check" design, which
  still had two paths (operator input, group declaration) kept in sync by
  validation.
- `BATTERY_CONFIGS` remains untouched -- battery characteristics/limits
  only.
- Operator responsibility is now exactly: select Group.

### Verification

Scripted smoke tests (mocked `input()`, declining before any hardware
touch) confirm all three real workflows prompt for Group/Position only;
`validate_group_test_config('A')` returns the derived battery type with no
parameter beyond `group`; Group B and an unknown group both still raise
`GroupConfigurationError` at the same point as before. `py_compile` clean;
no remaining reference to `_select_battery_type` in `test.py`. See
docs/architecture.md Section 40.

### Recommended next milestone

**Milestone VIII: physical rack validation**, unchanged from Milestone
VI's recommendation -- validate `ChargeSequence`/`DischargeSequence`
against Group A + a real SB battery.

---

## Milestone VIII: Simulator & Reference-Blueprint Reconciliation + Pre-Hardware-Validation Readiness

**Status:** ACHIEVED

**Scope:** the final software-focused milestone before Real Hardware
Validation. Reconciled the Safety Monitor Simulator with the real,
current implementation, swept the rest of `test.py` for the same class of
drift, reviewed DAQ readiness without introducing a DAQ dependency, and
made a formal go/no-go decision on entering hardware validation.

### Objectives achieved

- **Simulator setpoint-source drift fixed.** `_charge_phase_steps()`/
  `_discharge_phase_steps()` had continued deriving simulated commanded
  values from `battery_cfg` limits -- the exact conflation bug already
  found and fixed in the real `ChargeSequence`/`DischargeSequence` two
  milestones ago. The simulator had never been updated to reflect that
  fix. Now takes `test_setpoints` instead, matching the real sequences
  exactly.
- **Simulator battery-type-selection drift fixed.** `_select_safety_simulation_battery()`
  (direct battery-type picker) replaced with `_select_safety_simulation_group()`
  (group picker, battery type derived from it) -- the operator-input model
  no real workflow uses anymore.
- **Stale status claims corrected** -- "not-yet-implemented" language and
  legacy `ChargeCycle`/`DischargeCycle` references removed from simulator
  docstrings and the module-level comment introducing it.
- **A second instance of the same drift class found and fixed**, outside
  the simulator: `test_ui_preview()`'s "UI Test" menu still called
  Charge/Discharge Battery "not yet implemented." New demo screens added
  for both (mirroring the existing Monitor Battery demo-screen pattern);
  Cycle Battery correctly remains "not yet implemented" (it genuinely is).
- **DAQ readiness gap closed, without a DAQ dependency.** `ChargeSequence`/
  `DischargeSequence`'s constructors didn't accept a `daq` handle at all,
  even though their own base class (`BatteryOperationSequence`) already
  supports one. Added as an unused, optional parameter -- a future DAQ
  integration will only need to change two telemetry lines per sequence,
  not any constructor or caller.
- **Architecture consistency review** confirmed Monitor Battery, Monitor
  Battery Scan, `ChargeSequence`, `DischargeSequence`, and the now-
  reconciled Simulator all agree on group ownership, battery ownership,
  setpoint ownership, validation flow, traceability flow, and execution
  flow.

### Verification

Scripted smoke tests (mocked `input()`) confirm the Charge/Discharge/Cycle
Battery walkthroughs now display Group A's real `test_setpoints` (not
SB's `BATTERY_CONFIGS` limits), the Cycle walkthrough still correctly
aborts at its injected overtemperature fault, and the "Skip" fallback
still exercises the global `Settings.*` constants unchanged. UI Test's new
Charge/Discharge demo screens render via the real `render_execution_frame()`.
Full four-workflow regression (Monitor/Monitor Scan/Charge/Discharge,
declining before hardware touch) re-run clean after the `daq=` constructor
change. `py_compile` clean; no remaining reference to
`_select_safety_simulation_battery`, `ChargeCycle.run(battery_cfg`, or
`DischargeCycle.run(battery_cfg` anywhere in the codebase.

### Milestone readiness decision

**GO.** The software architecture is judged ready to leave the
implementation phase and enter Real Hardware Validation. No further
software-only blocker was found. Remaining blockers are all hardware-
access tasks: confirm `PRIMARY_SMU`'s real current rating, confirm relay/
DAQ channel numbers against real wiring, confirm `BATTERY_CONFIGS` against
the real datasheet, then run the physical validation itself.

### Recommended next milestone

**Milestone IX: Pre-Hardware-Validation MUST-FIX Closure** (below), a
software-documentation-only gate check performed in response to a
pre-hardware-validation architecture FAQ review, followed by **Milestone X:
Real Hardware Validation** -- validate `ChargeSequence`/`DischargeSequence`
against Group A + a real SB battery (the only fully software-validated
configuration today) on one relay-selected channel, before scaling to more
channels, attempting HUB, or starting `CycleSequence`.

---

## Milestone IX: Pre-Hardware-Validation MUST-FIX Closure

**Status:** ACHIEVED

**Scope:** a pre-hardware-validation architecture FAQ review
(`docs/FAQ.md`, inspecting the codebase question-by-question) flagged
several RED (not handled) and YELLOW (partially handled) findings ahead of
Milestone X. This milestone closed the four highest-priority ones,
documentation-and-code-only, verified by mocked regression tests -- no
physical hardware access performed.

### Objectives achieved

- **Reverse Polarity Protection.** New `Settings.
  REVERSE_POLARITY_VOLTAGE_THRESHOLD_V` (-0.5 V) and
  `ReversePolarityError(SafetyViolationError)`; new
  `BatteryOperationSequence._check_battery_polarity()`, called by both
  `ChargeSequence.run()`/`DischargeSequence.run()` with a DMM reading taken
  while the SMU output is still disabled, strictly before
  `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`. Subclasses
  `SafetyViolationError` so it flows through the existing
  `run_guarded()`/`emergency_stop()` shutdown path unchanged -- no new
  shutdown logic. Previously the single highest-priority gap identified by
  the FAQ review, given batteries were about to be connected for real.
- **Battery-Type Validation.** `validate_group_test_config()`'s Stage 2,
  plus `test.py::_run_monitor_battery()`/`_run_monitor_battery_scan()`'s
  direct lookups, now all raise a typed `ConfigurationError`/`[FAIL]`
  message instead of a bare, uncaught `KeyError` for an unrecognized
  `battery_type`.
- **Timeout Traceability.** `BatteryOperationSequence.run_guarded()` gained
  a dedicated `except NIPXITimeoutError` branch recording
  `StopReason.TIMEOUT` (previously fell through to the generic
  `except Exception` branch and was recorded as `StopReason.FAILED`).
  Applies to `ChargeSequence`/`DischargeSequence` only; the legacy
  `charge_cycle.py`/`discharge_cycle.py` path is unaffected (already
  superseded).
- **Database Startup Hardening.** New `test.py::
  _open_storage_guarded()`/`_start_run_summary_guarded()` helpers wrap
  `DataStorage.open()`/`start_run_summary()` with clean, operator-facing
  `[FAIL]` messaging instead of a raw traceback, used by all four real
  workflow entry points. Diagnostic detail unchanged -- still logged via
  `DataStorage`'s own `self.log.error(...)` calls; only what the operator
  sees at the terminal changed.

### Architectural decisions made

- `ReversePolarityError` deliberately answers "is it safe to enable the
  SMU," not "what is wrong with the battery" -- disambiguating reversed
  vs. disconnected vs. damaged vs. wiring-fault was explicitly ruled out
  of scope for this milestone, carried forward as a residual, low-priority
  YELLOW item.
- The Safety Monitor Simulator's own unguarded `battery_type` lookup
  (`_select_safety_simulation_group()` and its two callers) was
  deliberately left unfixed -- simulator/demo-only code, no hardware
  activation, no safety consequence, lower priority than the three real
  workflow paths that were fixed.
- Per-write database calls made DURING a test
  (`record_measurement()`/`record_execution_state()`/`log_event()`) were
  deliberately left unwrapped this milestone -- startup-time failures
  (`open()`/`start_run_summary()`) were the priority; a mid-test failure
  is still caught by `run_guarded()`'s generic exception handler and still
  triggers a full safety shutdown, only the clean-`[FAIL]`-messaging
  benefit is missing for that specific failure mode.

### Verification

All four closures verified by mocked regression test (not physical
hardware): reverse polarity -- a -3.5 V DMM reading raises
`ReversePolarityError` before `smu.set_charge_mode()`/`output_enable()`
are ever called; a plausible positive reading proceeds normally to a
completed charge. Battery-type validation -- monkeypatching an unknown
`battery_type` raises `ConfigurationError`, not `KeyError`. Timeout
traceability -- a `ChargeSequence` run with `CHARGE_TIMEOUT_S=0.0` records
`StopReason.TIMEOUT` in both `record_execution_state`/`finish_run_summary`
mock call args. Database hardening -- `_open_storage_guarded()` returns
`None` and disconnects hardware on a simulated `sqlite3.OperationalError`;
`_start_run_summary_guarded()` returns `False` on a simulated
`sqlite3.Error`, `True` on success.

### Milestone readiness decision

**GO for Milestone X: Real Hardware Validation.** No software blocker
remains -- all four MUST-FIX items are closed. Remaining blockers are all
hardware-access tasks, unchanged from Milestone VIII: confirm
`PRIMARY_SMU`'s real current rating, confirm relay/DAQ channel numbers
against real wiring, confirm `BATTERY_CONFIGS` against the real datasheet,
confirm the SMU/relay power-loss fail-safe behavior, confirm the real
electrical signature of a reversed-polarity connection against the new
-0.5 V threshold, then run the physical validation itself. Three RED items
are explicitly, deliberately deferred and are NOT blockers for this gate,
per explicit instruction that DAQ/NTC/`CycleSequence`/runtime power-loss-
and-incomplete-run recovery work is out of scope: power-loss/incomplete-run
recovery, reverse-polarity/damaged-battery disambiguation, and the
simulator's unguarded `battery_type` lookup. See docs/architecture.md
Section 42 for full detail and docs/FAQ.md for the underlying review.

### Recommended next milestone

**Milestone X: Real Hardware Validation** -- unchanged from Milestone
VIII's recommendation -- validate `ChargeSequence`/`DischargeSequence`
against Group A + a real SB battery on one relay-selected channel, before
scaling to more channels, attempting HUB, or starting `CycleSequence`.

---

## Milestone X: Relay Settle-Time Consolidation + Real-Hardware Timing Fix

**Status:** ACHIEVED

**Scope:** two related fixes ahead of/during Real Hardware Validation.
First, a pre-hardware timing review found relay settling delay was
inconsistent across workflows -- `0.2s` in `MonitorBatteryScanSequence`,
`0s` in `MonitorBatterySequence` and the `test.py` relay validation/
hardware-validation scans, and the unrelated `STABILIZATION_S` (`5.0s`,
an SMU electrical-settling value, not a relay one) borrowed by
`ChargeSequence`/`DischargeSequence`. Second, real-hardware use of `[3]
RelayEthernetTest (native 0-based primitives)` on the physical 8-relay
Numato board showed relay transitions occurring immediately, not
respecting the expected ~2s gap -- a real behavioral defect, not a
misconfiguration.

### Objectives achieved

- **Single global relay timing constant.** `Settings.RELAY_SETTLE_TIME_S`
  (`config/settings.py`) is now `2.0` s and the only relay-settling/
  dead-time constant in the codebase. Enforced structurally in
  `hardware/relay.py::RelayBase.open()`/`close()`, which are now concrete
  (no longer abstract/overridable) and call the renamed driver-specific
  `_open_impl()`/`_close_impl()` then unconditionally block via the new
  `RelayBase.settle()`, which raises `ValidationError` if the constant is
  ever configured `<= 0`. `NumatoRelayMatrix`, `SerialRelay`, and
  `SimulatedRelay` were all updated to the new `_open_impl`/`_close_impl`
  naming. Duplicate/inconsistent per-call-site delays were removed
  (`MonitorBatteryScanSequence`'s own `settle_s` parameter/sleeps;
  `ChargeSequence`/`DischargeSequence`'s pre-enable `STABILIZATION_S`
  sleep, which was actually serving the relay-settle role under a
  different constant's name).
- **RelayEthernetTest root cause found and fixed.** `test.py::
  test_relay_ethernet_test()` deliberately bypasses `RelayBase.open()`/
  `close()` (an intentional, pre-existing, documented exception -- see
  `docs/architecture.md` Section 24 -- to validate the native Numato
  command layer independently). Because Section 43's fix lived entirely
  inside `open()`/`close()`, this test received no settle delay at all,
  which is exactly the immediately-transitioning behavior observed on
  real hardware. Fixed by making `RelayBase.settle()` public and calling
  it explicitly after each of the test's three native write operations
  per relay index (plus its cancellation-triggered force-off), reusing
  the same constant and the same guard -- no second implementation.
- **Full relay-path audit performed** (`docs/architecture.md` Section
  44): every relay-state-changing call site in the repo was checked
  against the global constant; `test_relay_ethernet_test()` was the only
  bypass found. Read/write/verify sequencing (Section 24's pattern) was
  independently re-confirmed intact everywhere, including on the fixed
  path -- the defect was specifically the missing delay, not a missing
  verification step.

### Architectural decisions made

- The settle delay is enforced structurally (inside `RelayBase`), not by
  convention at each call site, specifically so that no future workflow
  can reintroduce a `0s`/inconsistent relay transition by omission.
- `RelayBase.settle()` is deliberately public (not `_settle()`) precisely
  because at least one legitimate, documented code path
  (`test_relay_ethernet_test()`) must operate below the `open()`/`close()`
  wrapper by design -- the fix had to accommodate that exception rather
  than force every relay interaction through the wrapper.
- `Settings.STABILIZATION_S` (SMU output electrical settling) and
  `Settings.PROTO_TEST_DWELL_S` (per-relay measurement dwell) remain
  intentionally separate from `RELAY_SETTLE_TIME_S` -- the requirement
  was one constant per concern (relay switching), not collapsing all
  hardware timing into a single number.

### Verification

All touched modules byte-compile cleanly (`hardware/relay.py`,
`hardware/relay_eth.py`, `hardware/relay_serial.py`,
`hardware/simulated.py`, `config/settings.py`, `test.py`,
`test_control/monitor_battery_scan_sequence.py`,
`test_control/charge_sequence.py`, `test_control/discharge_sequence.py`).
A live smoke test against `SimulatedRelay` confirmed `close()` now blocks
for the full configured `2.0` s before returning. Confirming `2.0` s
against the Numato board's actual mechanical settling time on the
physical rack remains an open real-hardware task (see
`docs/TIMING_ANALYSIS.md` Recommendations) -- this milestone fixes
software consistency and the RelayEthernetTest defect, not the
hardware-measured value itself.

### Recommended next milestone

Re-run `[3] RelayEthernetTest` on the physical rack to confirm the ~2s
gap is now observed between relay operations, then continue Milestone
IX's recommended Real Hardware Validation scope (`ChargeSequence`/
`DischargeSequence` against Group A + a real SB battery).

---

## Milestone XI: Monitor Battery Operational Behavior Review -- GO for Real Hardware Validation

**Status:** ACHIEVED

**Scope:** Monitor Battery (`test_control/monitor_battery_sequence.py::MonitorBatterySequence`)
is the first workflow scheduled for Real Hardware Validation. This
milestone is an implementation-level readiness review -- performed
against the actual code, not architectural assumptions -- covering
startup, DMM dependency behavior, long-duration operation, database
persistence, relay behavior, cancellation/shutdown, retry behavior, and
failure-mode traceability. Documentation-only session: no implementation
code was changed to produce or act on this review.

### Objectives achieved

- **Confirmed intended lifecycle.** Monitor Battery's `while True:` loop
  has no timeout, no automatic stop condition, and no normal-completion
  path by design -- it runs until operator cancellation (Ctrl+C) or a
  real fault, exactly as the module's own docstring states.
- **Confirmed long-duration safety.** An example 8-hour continuous
  session was analyzed specifically: no memory-growth risk (bounded
  in-memory state, four floats), no buffering risk (every measurement
  committed to the database synchronously, immediately, every iteration).
  **Supported.**
- **Confirmed relay behavior.** Closes once at startup, stays closed for
  the full session, opens on every exit path -- either directly via
  `safety.emergency_stop()`/`safe_cancel_shutdown()`, or via
  `HardwareManager.disconnect_all()`'s outer backstop.
- **Confirmed DMM dependency behavior is mode-dependent.** PRODUCTION
  fails closed (`HardwareInitError`, no hardware activation).
  DEVELOPMENT/VALIDATION briefly activates the relay before the first
  failed measurement triggers the standard emergency-shutdown path.
- **Confirmed no retry/recovery exists** -- by design, consistent with
  the project's safety-first philosophy of never masking a real fault.
- **Found and documented one non-blocking caveat:** a mid-run database
  failure can cause `run_guarded()`'s own failure-classification storage
  calls to themselves fail, skipping `safety.emergency_stop()` at that
  layer specifically. Hardware is still safed via the outer `test.py`
  cleanup path (`hw_mgr.disconnect_all()`'s independently-guarded relay
  `open_all()`), so this is a traceability/stop-reason gap, not a
  hardware-safety gap.

### Architectural decisions made

- No code changes were made as part of this review -- the caveat found
  is tracked as an optional, low-priority follow-up (see docs/TODO.md),
  not treated as a blocker, since hardware safety is preserved either way
  via the existing outer-layer backstop.

### Verification

Every finding is cited against the actual source (`test_control/
monitor_battery_sequence.py`, `test_control/battery_operation_sequence.py`,
`test_control/hardware_manager.py`, `hardware/dmm.py`, `data/storage.py`,
`test.py`, `utils/errors.py`, `utils/cancellation.py`) -- see
docs/architecture.md Section 45 for full evidence and docs/FAQ.md Section
13 for the Q&A-form record. No mocked or physical-hardware test was run
as part of this documentation-only session; the review is a static
implementation trace, not a dynamic test.

### Milestone readiness decision

**GO for Real Hardware Validation using Monitor Battery.** No software
blocker identified.

### Recommended next milestone

Proceed with Real Hardware Validation using Monitor Battery per this
GO decision. Track the `run_guarded()` storage-ordering hardening
(docs/TODO.md) as an optional low-priority follow-up, not a gate.

---

## Milestone XII: Production Runtime Architecture Review -- Reuse, Concurrency, and Failure-Policy Assessment

**Status:** ACHIEVED (design review only -- no implementation performed)

**Scope:** With Monitor Battery and Monitor Battery Scan validated on real
hardware and ChargeSequence/DischargeSequence real-hardware validation
still the next milestone, this review assessed whether a future
production runtime (start every enabled `BATTERY_GROUPS` group, run
indefinitely, one worker per hardware set) can be built entirely on the
existing architecture. Documentation-only session: no implementation code
was changed to produce or act on this review.

### Objectives achieved

- **Confirmed shared architecture** across `MonitorBatterySequence`/
  `MonitorBatteryScanSequence`/`ChargeSequence`/`DischargeSequence` --
  one base class (`BatteryOperationSequence`), one shutdown path, one
  safety class, one database path, one traceability path, one
  cancellation path.
- **Found a real reuse gap:** `main.py` (the actual production entry
  point) still runs a second, live charge/discharge implementation
  (`TestExecutor`/`BatteryTestSequence`/`ChargeCycle`/`DischargeCycle`)
  with no reverse-polarity protection and no Milestone II traceability.
  A Runtime built on `ChargeSequence`/`DischargeSequence` alone would
  become a third implementation unless this path is retired/replaced as
  part of the same effort.
- **Confirmed `BATTERY_GROUPS` already models hardware ownership**
  sufficiently to derive a hardware-set partition (`hardware_for_group()`,
  shared-resource-name grouping) -- no topology file or duplicate
  ownership model is needed.
- **Found a real hardware-inventory constraint:** only one `MAIN_DMM`/
  `MAIN_DAQ` exists today, shared by every configured group, capping true
  N-way concurrency below the 3-hardware-set example until additional
  instruments are assigned or a resource-checkout layer serializes shared
  access -- consistent with an existing, already-documented `docs/TODO.md`
  note about Group B sharing `MAIN_DAQ`.
- **Recommended a failure policy:** isolate the affected hardware set by
  default; escalate only to hardware sets that depend on a resource that
  is itself the failure (shared DMM/DAQ, or the shared SQLite file) --
  derived from the same ownership model used for scheduling.
- **Reaffirmed the Section 45 database-failure caveat** in this new
  context: the Runtime's Cycle Controller must reproduce `test.py`'s
  outer `finally: hw_mgr.disconnect_all()` backstop per worker, or that
  mitigation is silently lost.

### Architectural decisions made

- No code changes were made as part of this review. Retiring/replacing
  `main.py`'s legacy path, building the resource-checkout layer, and
  designing `CycleSequence` (composition over the existing Charge/
  Discharge sequences, not a new implementation) are named as required
  parts of the Runtime design effort, not deferred silently.

### Verification

Every finding is cited against the actual source (`main.py`,
`test_control/test_executor.py`, `test_control/battery_test.py`,
`test_control/charge_cycle.py`, `test_control/battery_operation_sequence.py`,
`config/devices.py`, `test_control/hardware_manager.py`, `data/storage.py`,
`docs/TODO.md`) -- see docs/architecture.md Section 46 for full evidence
and docs/FAQ.md Section 14 for the Q&A-form record. No mocked or
physical-hardware test was run; this is a static implementation trace.

### Milestone readiness decision

**GO for starting Production Runtime Architecture design**, conditional on
the design explicitly addressing: `main.py` retirement/replacement, the
resource-checkout/hardware-set-partition layer, `CycleSequence`'s design,
and treating concurrency validation as gated on a second real hardware set
existing. No finding blocks starting the design itself.

### Recommended next milestone

Begin Production Runtime Architecture design under the conditions above,
in parallel with (not instead of) the still-pending ChargeSequence/
DischargeSequence real-hardware validation milestone.

---

## Milestone XIII: Architecture Standardization Review -- Group Naming, Enabled Groups, Group-Centric Workflows, Database, Runtime Prep

**Status:** ACHIEVED (design/documentation review only -- no implementation performed)

**Scope:** With ChargeSequence/DischargeSequence implemented, Generic Run
Summary/Last Test Summary/NTC Group Scan/NTC Presence Detection
implemented, and CycleSequence/Runtime Architecture designed (Milestone
XII), this review standardized the operator workflow around
`BATTERY_GROUPS` ahead of Runtime implementation. Documentation-only
session: no implementation code was changed to produce or act on this
review.

### Objectives achieved

- **Confirmed the group naming migration (A -> A1/A2/A3/A4) is
  code-neutral** -- `group` is used everywhere as an opaque string key, no
  code assumes single-letter format. Identified one open, operator-only
  decision on naming semantics (1:1 rename vs. family reorganization) that
  must be resolved before the rename is executed.
- **Confirmed `enabled`/disabled groups are already fully implemented** --
  not a new field, already the sole ownership model, already enforced at
  the one shared battery-workflow gate. Flagged one nuance that must be
  preserved: `_select_relay_scope()`'s deliberate, previously-fixed
  bypass of `enabled` for raw hardware-validation scoping.
- **Confirmed operator workflow standardization already matches the
  proposed shape** across all six battery workflows -- no conflicts found.
- **Found a real risk in the hardware-test standardization proposal:**
  switching Test SMU/DMM/DAQ/Relay Matrix to pure group-resolution would
  remove the ability to validate hardware not yet assigned to any group
  (`HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2` today). Recommended an
  additive group-centric path instead of a replacement.
- **Documented the exact group/hardware ownership resolution chain**
  (Group -> battery type -> relay matrix/SMU/DMM/DAQ/NTC DAQ -> position
  mapping), with exact files/functions.
- **Identified the blocking schema gap for group-centric database
  reporting:** `run_summary` has no `group` column today (only free-text
  `event_log` narration). Recommended an additive `group_name`/
  `position_in_group` migration -- the same pattern already used for
  `battery_type`/hardware-identity columns -- as the prerequisite for
  Group History/Last Test From Group/Group Statistics.
- **Reaffirmed the Milestone XII `main.py` retirement prerequisite** --
  unaffected by, and not resolved by, this standardization batch.

### Architectural decisions made

- No code changes were made as part of this review. The naming-semantics
  decision, the `group_name`/`position_in_group` schema addition, and the
  additive group-centric hardware-test path are named as concrete
  follow-up implementation items, not deferred silently.

### Verification

Every finding is cited against the actual source (`config/devices.py`,
`test.py`, `utils/validators.py`, `data/storage.py`,
`test_control/run_summary_report.py`) -- see docs/architecture.md Section
47 for full evidence and docs/FAQ.md Section 15 for the Q&A-form record.
No mocked or physical-hardware test was run; this is a static
implementation trace.

### Milestone readiness decision

**GO for this standardization work.** The naming migration's open
1:1-vs-family decision requires operator confirmation before that rename
is executed; the `group_name`/`position_in_group` schema addition and the
additive hardware-test path are the concrete follow-up implementation
items. No finding blocks proceeding toward Runtime implementation --
`main.py` retirement (Milestone XII) remains the standing prerequisite,
unchanged by this review.

### Recommended next milestone

Resolve the group-naming semantics decision with the operator, then
implement (in any order): the naming migration itself, the
`group_name`/`position_in_group` schema addition and its three
group-centric reporting features, and the additive group-centric
hardware-test path -- in parallel with the still-pending
ChargeSequence/DischargeSequence real-hardware validation and the
Milestone XII `main.py` retirement work.

---

*Record created after Hardware Bring-Up Milestone 1 was confirmed on the
physical PXIe rack, and updated for Milestone 2 (Proto Test Execution)'s
implementation, Milestone II's Monitor Battery implementation, and the
Menu Restructuring Review above. See `docs/TODO.md` for the live
remaining-work checklist and `docs/architecture.md`/`docs/CONFIGURATION.md`
for full technical detail on every item referenced above.*

---

## Milestone XIV: Group Hardware Ownership Model -- Multi-Matrix Topology (B1-B4/C1-C4) and Position/Channel Storage Fix

**Status:** ACHIEVED (design/documentation review only -- no implementation performed)

**Scope:** Resolved Milestone XIII's open group-naming question with a
concrete real-hardware topology: groups are hardware ownership sets
(`<matrix letter><partition number>`, e.g. `B1..B4` on `MATRIX_NUMATO_202`,
`C1..C4` on `MATRIX_NUMATO_203`), never workflow/battery-type/test-type
families. Documentation-only session: no implementation code was changed.

### Objectives achieved

- **Confirmed the naming scheme itself requires no code change** -- `group`
  is used everywhere as an opaque string key, unchanged from Milestone
  XIII's finding.
- **Found a real, latent bug the naming question was masking:**
  `BATTERY_CHANNELS`'s flat, global position/relay-address model, and
  `utils/device_validator.py`'s startup checks built on top of it, assume
  exactly one physical relay matrix. Confirmed by direct code inspection
  that `_check_duplicate_relay_identifiers()` would report a false
  duplicate-relay collision the moment a second matrix (Group C1) is
  populated with its own, legitimately separate `relay_address` values.
- **Recommended the fix:** move position/channel ownership into each
  `BATTERY_GROUPS` entry (a `"positions"` sub-dict scoped to that group's
  own `relay_matrix`), eliminating the shared global namespace entirely.
- **Confirmed operator-facing behavior (positions 1-8 within a group) is
  already correct and unaffected** -- only the internal storage location
  changes.
- **Confirmed convergence with Milestone XIII's pending database
  migration:** `channel` and the planned `position_in_group` column are
  the same underlying fix, best implemented together, using the final
  `B1`/`C1`-style names directly.
- **Confirmed Runtime should schedule groups, not relay matrices** --
  strengthening, not changing, Milestone XII's shared-resource
  concurrency model (B1-B4 share one hardware set; C1-C4 share a
  different one).

### Architectural decisions made

- No code changes were made as part of this review. The position-ownership
  redesign, the matrix-scoping fix to `utils/device_validator.py`, and the
  Milestone XIII database migration are recommended to be implemented
  together, using the final group names directly.

### Verification

Every finding is cited against the actual source (`config/devices.py`,
`test.py`, `test_control/monitor_battery_scan_sequence.py`,
`utils/device_validator.py`) -- see docs/architecture.md Section 48 for
full evidence and docs/FAQ.md Section 16 for the Q&A-form record. No
mocked or physical-hardware test was run; this is a static implementation
trace.

### Milestone readiness decision

**GO for adopting this group-ownership model and naming scheme.** The
naming was never the blocker; the position/channel storage redesign is
required regardless of naming, because the current model has a latent
validator bug that triggers the moment Group C1's hardware is populated.

### Recommended next milestone

Implement, together: the position-ownership redesign
(`BATTERY_GROUPS[group]["positions"]`), the matrix-scoping fix to
`utils/device_validator.py`, and the Milestone XIII `group_name`/
`position_in_group` database migration -- using `B1`/`C1`-style names
directly -- before Group C1's hardware is exercised for real. In parallel
with the still-pending ChargeSequence/DischargeSequence real-hardware
validation and the Milestone XII `main.py` retirement work.

---

## Milestone XV: Final Group Topology, Position Ownership Structure, and Validator Redesign Plan

**Status:** ACHIEVED (design/documentation review only -- no implementation performed)

**Scope:** Locked the final, production group topology begun in Milestone
XIV: `MATRIX_NUMATO_201 -> A1-A4`, `MATRIX_NUMATO_202 -> B1-B4`,
`MATRIX_NUMATO_203 -> C1-C4`. Resolved A1's open enabled/hardware-assignment
question. Corrected the `positions` structure sketched in Milestone XIV
(relay_address must stay unique per matrix, not reset per group). Produced
the exact `utils/device_validator.py` redesign plan. Documentation-only
session: no implementation code was changed.

### Objectives achieved

- **Locked the final topology and active groups:** B1 (existing rack
  DMM/SMU/DAQ) and C1 (NI USB-6211, NTC-only) enabled; A1 and all other
  eight groups (A2-A4/B2-B4/C2-C4) disabled placeholders with reserved,
  non-overlapping position/`relay_address` ranges on their owning matrix.
- **Resolved A1's enabled state:** disabled, zero hardware roles assigned.
  Reviewed and rejected two alternatives (full hardware assignment --
  requires unconfirmed second-instrument hardware or an undesirable
  cross-matrix resource coupling with B1; "relay-only" enabled -- doesn't
  actually work given current code, since every real battery workflow
  except NTC Group Scan requires the full `smu`+`dmm`+`daq` role set by
  default). Confirmed disabling A1 costs nothing for relay hardware
  bring-up (`_select_relay_scope()` already bypasses `enabled`).
- **Corrected a real error in the Milestone XIV `positions` sketch:**
  `relay_address` must remain unique across every group sharing one
  physical `relay_matrix` (B1 owns 1-8, B2 owns 9-16, etc. on the same
  32-channel matrix), not reset to 1-8 per group -- caught and fixed
  before any implementation.
- **Produced the exact validator redesign:** `_check_duplicate_relay_identifiers()`
  keyed by `(relay_matrix, relay_address)`; `_check_battery_groups()`
  retired entirely (its invariant becomes structurally impossible under
  the new model); `_check_relay_count_consistency()` restructured to loop
  per group instead of a full cross-product.
- **Confirmed menu behavior requires no change** -- `_select_battery_group()`
  already handles disabled placeholders correctly, exercised and confirmed
  for today's B/C/D; twelve groups behave identically to today's four.

### Architectural decisions made

- No code changes were made as part of this review. The `positions`
  restructure, the three validator changes, and the Milestone XIII
  database migration are recommended to be implemented together, in that
  order, using these final names.

### Verification

Every finding is cited against the actual source (`config/devices.py`,
`test.py`, `utils/device_validator.py`) -- see docs/architecture.md
Section 49 for full evidence and docs/FAQ.md Section 17 for the Q&A-form
record. No mocked or physical-hardware test was run; this is a static
implementation trace.

### Milestone readiness decision

**GO for this final topology and structure.** A1 resolved (disabled until
hardware is assigned); B1/C1 confirmed as designed. No finding blocks
proceeding to implementation.

### Recommended next milestone

Implement, together and in this order: (1) the `positions` restructure
(`BATTERY_GROUPS[group]["positions"]`, final topology, A1 disabled), (2)
the three `utils/device_validator.py` changes, (3) the Milestone XIII
`group_name`/`position_in_group` database migration -- using `A1`/`B1`/
`C1`-style names directly throughout. In parallel with the still-pending
ChargeSequence/DischargeSequence real-hardware validation and the
Milestone XII `main.py` retirement work.

---

## Milestone XVI: Temperature Monitoring as a First-Class Feature -- Dual DAQ Ownership (Option A) Implemented

**Status:** ACHIEVED (implemented and verified this session -- not design-review-only)

**Scope:** Implemented real NTC temperature acquisition for Monitor
Battery, Charge Battery, and Discharge Battery, resolving the dual-DAQ
ownership question (a group's general `"daq"` role vs. its `"ntc_daq"`
role, possibly a different physical instrument) raised in the prior
review. Decision: Option A -- `HardwareManager` owns a fifth device role.

### Objectives achieved

- **`HardwareManager` extended with an `ntc_daq` slot** -- backward
  compatible (`main.py`'s call site unaffected), with identity-based
  dedup so a group whose `"ntc_daq"` falls back to `"daq"` never opens a
  second connection to the same physical instrument. Verified
  programmatically for both the shared-instance and distinct-instance
  cases.
- **New `SafetyMonitor.check_temperature()`** -- a small, additive
  temperature-only check for Monitor Battery specifically, since it has
  no real `current_a` to pass into the existing `check()` and must not
  silently start enforcing voltage/current limits it never enforced
  before. `check()` itself is unchanged.
- **`ChargeSequence`/`DischargeSequence`** now feed a real, classified
  temperature reading into the *existing* `check()` call -- `temp_c` was
  always a checked parameter, just always `None` until now. No change to
  `check()`'s own logic.
- **`MonitorBatterySequence`** gained a `daq` constructor parameter and
  `ntc_channel`/`battery_cfg` run() parameters -- an overtemperature
  reading now raises `SafetyViolationError`, routed through the existing
  `run_guarded()` safety-shutdown path, for the first time in this
  sequence's history.
- **Per-position acquisition model confirmed correct and implemented as
  designed** -- reads only the active position's NTC channel, once per
  sampling-loop iteration; does not continuously scan the whole group
  (that remains NTC Group Scan's distinct job).
- **Fault/absent NTC readings are throttled to one `event_log` entry per
  state transition**, not per sample -- avoids event_log spam over a
  multi-hour charge/discharge run.
- **Confirmed the USB-to-PXI DAQ migration remains configuration-only**
  after this change -- no code added anywhere reads a hardcoded device
  identity; `ntc_daq_cfg`/`ntc_channel` flow purely through parameters and
  config.

### Architectural decisions made

- Option A (HardwareManager ownership) adopted over the alternative
  (per-sequence bolt-on DAQ connections) reviewed previously -- single
  connection lifecycle, single shutdown lifecycle, single error-handling
  path, as required.
- `run_summary`-level temperature aggregation (min/max/avg per run) and a
  separate, non-fatal warning threshold (below the existing critical
  `max_temp_c`) are explicitly deferred, tracked in `docs/TODO.md` --
  keeps this implementation additive and scoped, mirroring the
  already-standing deferral of Charge/Discharge's own voltage-stat
  enrichment.
- Legacy `main.py` integration remains explicitly out of scope --
  structurally blocked by its lack of group awareness (Section 50), not
  attempted here.

### Verification

Full `py_compile` check across all six modified files
(`test_control/hardware_manager.py`, `safety_monitor.py`,
`monitor_battery_sequence.py`, `charge_sequence.py`,
`discharge_sequence.py`, `test.py`). `HardwareManager`'s dual-DAQ
construction verified programmatically for both the shared-instance
(`ntc_daq is daq == True`) and distinct-instance
(`ntc_daq is daq == False`) cases. `SafetyMonitor.check_temperature()`
unit-verified (`None`/safe/unsafe, with and without `battery_cfg`).
`_run_monitor_battery()` and `_run_charge_battery()` dry-run end to end
with no real hardware attached -- both fail cleanly at the expected
real-network boundary (Numato relay matrix unreachable from this dev
machine), with no exception from any new code path. No physical hardware
was available for this session; real-hardware validation of the NTC
acquisition path itself remains a follow-up once a USB DAQ is attached.

### Milestone readiness decision

**Implemented and verified to the extent possible without physical NTC
hardware attached.** No blocker found during the pre-implementation
review; the one architectural decision it surfaced (dual-DAQ ownership)
was resolved by the operator as Option A and implemented accordingly.

### Recommended next milestone

Validate the NTC acquisition path against real USB DAQ hardware once
available (confirm `classify_ntc_presence()`/`ntc_voltage_to_celsius()`
against a real divider signal in a live Monitor Battery/Charge/Discharge
run, not just the standalone NTC Group Scan path). In parallel: the
`group_name`/`position_in_group` database migration (Milestone XIII), the
position-ownership/validator redesign (Milestone XV), and the standing
`main.py` retirement and ChargeSequence/DischargeSequence real-hardware
validation work (Milestone XII).

---

*Record created after Hardware Bring-Up Milestone 1 was confirmed on the
physical PXIe rack, and updated for Milestone 2 (Proto Test Execution)'s
implementation, Milestone II's Monitor Battery implementation, and the
Menu Restructuring Review above. See `docs/TODO.md` for the live
remaining-work checklist and `docs/architecture.md`/`docs/CONFIGURATION.md`
for full technical detail on every item referenced above.*
