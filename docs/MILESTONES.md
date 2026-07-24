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

**Status:** IMPLEMENTED -- unit-verified with mocked hardware; awaiting an
actual PXIe rack run to close out (unlike Milestone 1's record above, this
entry does NOT claim real-hardware validation).
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

### Remaining work

- Real PXIe rack execution of this workflow -- not yet run against physical
  hardware in this session.
- Automatic resume from a previous execution position -- deliberately out
  of scope for this milestone (display only).
- The originally-recommended Milestone 2 (battery charge/discharge sourcing
  bring-up, see below) is now effectively Milestone 3.

### Risks

- Unverified against real NI-DCPower/DMM/relay timing -- the `hold_s`
  dwell and `during_hold` DMM-read-while-active pattern is new code,
  exercised only against mocked hardware so far.
- `dwell_s` defaults to 120s per relay across up to 8 relays (~16 minutes
  minimum for a full cycle) -- worth confirming this is an acceptable
  bench-test duration before an unattended rack run.

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
