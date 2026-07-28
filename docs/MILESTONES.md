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

---

*Record created after Hardware Bring-Up Milestone 1 was confirmed on the
physical PXIe rack, and updated for Milestone 2 (Proto Test Execution)'s
implementation, Milestone II's Monitor Battery implementation, and the
Menu Restructuring Review above. See `docs/TODO.md` for the live
remaining-work checklist and `docs/architecture.md`/`docs/CONFIGURATION.md`
for full technical detail on every item referenced above.*
