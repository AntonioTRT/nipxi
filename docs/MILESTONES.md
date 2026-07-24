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

---

*Record created after Hardware Bring-Up Milestone 1 was confirmed on the
physical PXIe rack, and updated for Milestone 2 (Proto Test Execution)'s
implementation. See `docs/TODO.md` for the live remaining-work checklist
and `docs/architecture.md`/`docs/CONFIGURATION.md` for full technical
detail on every item referenced above.*
