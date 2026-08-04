# Database Roadmap

**Status: planning document, partially superseded by as-built schema.** Nothing in
this file is implemented yet except the mode-separated database *location* and the
minimal `data/sqlite_manager.py` foundation (Section 1), which are real today.
Sections 3 (`station_state`) and 5.3 (`run_summary`)'s proposals were subsequently
built, in a shape close to but not identical to what's sketched below -- see
`docs/architecture.md` Sections 18/18a/20/22/28 and `docs/CONFIGURATION.md`'s
"Data storage" section for the current, as-built `station_state`/`run_summary`/
`event_log`/`measurements` schema, which is authoritative over this document for
anything already built. Everything else here — the repository classes, the new
data categories, and cycle/state recovery — remains future architecture, written
down now so it gets designed deliberately instead of improvised later. See
`docs/architecture.md` "System Modes" for how `SYSTEM_MODE` feeds into this.

**Note (SMU/Discharge/Roadmap review, see `docs/architecture.md` Sections 29-35):**
this review's changes (SMU implementation status, Discharge Cutoff Policy, DAQ
telemetry strategy, SMU Functional Validation milestone, ChargeCycle/DischargeCycle
harvest plan, revised roadmap) made **no changes to database schema or to this
document's build order** -- the repository-split/recovery-engine roadmap below is
unaffected and remains open work, independent of when Charge/Discharge Sequence
ships.

---

## 1. What exists today (implemented)

- `data/storage.py::StorageBackend` (ABC) / `DataStorage` (SQLite + CSV) -- the current,
  working persistence layer. `BatteryTestSequence`/`ChargeCycle`/`DischargeCycle` write
  to it via `record(channel, sample)` only; they have no knowledge of SQLite vs any
  future backend (see README.md Section 15, "MiniSQL Integration Path").
- **`data/sqlite_manager.py`** -- the first, deliberately minimal piece of Section 2's
  planned repository layer. Four functions only: `create_database(settings)`,
  `initialize_schema(conn)`, `insert_test_record(conn, label, value)`,
  `get_last_record(conn)`, operating on one table (`test_records`). Exists to prove the
  mode-separated database location actually works end to end -- on a laptop with no PXI
  hardware attached -- before `sqlite_manager.py` grows a real schema and the
  repositories below are built on top of it. Exercised by `test.py`'s "Test SQLite
  (foundation)" menu item (create/open -> verify schema -> insert -> read back -> display
  -> PASS/FAIL). This is NOT `battery_repository.py`/`cycle_repository.py`/etc. --
  those are still just planned (Section 2) -- and it is NOT used by
  `BatteryTestSequence`/`DataStorage` today; the two SQLite paths are independent
  until the repository split actually happens.
- **Mode-separated database location** (`config/settings.py`, driven by `SYSTEM_MODE` --
  see `config/system_mode.py`):

  | Mode | Directory | Database file |
  |------|-----------|----------------|
  | DEVELOPMENT | `data_output/development/` | `nipxi_dev.db` |
  | VALIDATION | `data_output/validation/` | `nipxi_validation.db` |
  | PRODUCTION | `data_output/production/` | `nipxi.db` |

  This guarantees DEVELOPMENT experiments can never collide with VALIDATION or
  PRODUCTION data, without any code outside `config/settings.py` needing to know
  about modes at all -- `Settings.DATABASE_FILE`/`DATA_DIR`/`CSV_DIR`/`REPORT_DIR`
  already point at the right place.

  **Deviation from an earlier sketch of this layout:** the original idea was a
  `data/dev/nipxi_dev.db`-style path. This project already has a `data/` **package**
  directory (`data/storage.py`, `data/logger.py`, `data/report.py`) -- reusing `data/`
  as the runtime output root too would put generated database files inside the same
  directory as source code, which is confusing and risks colluding with the package
  namespace. The existing `data_output/` root (already treated as generated/gitignored
  output) is used instead, with the mode as a subdirectory.

## 2. Planned repository layer (mostly NOT implemented)

The current `DataStorage` class does everything (schema, writes, CSV export) itself.
The plan is to split it into focused repository classes once real usage patterns (and
the recovery engine, Section 4) make the right boundaries clear -- doing this split
speculatively now, before there's a second consumer of the data, would be premature
structure with no payoff yet. `sqlite_manager.py` (Section 1) is the one exception --
a deliberately tiny seed of this table, not the full connection-lifecycle/migration
responsibility described below yet.

| File | Responsibility |
|------|-----------------|
| `data/sqlite_manager.py` | Connection lifecycle, schema migrations, one shared `sqlite3.Connection` per run. Everything else below depends on this instead of opening its own connection. |
| `data/battery_repository.py` | Battery identity/metadata -- which physical battery (serial number, chemistry, capacity rating) occupied which channel on which run. Not tracked at all today. |
| `data/cycle_repository.py` | Charge/discharge cycle records -- start/end time, phase, outcome (completed/aborted/timeout), summary stats (capacity Ah, peak temp). One step up from today's per-sample rows. |
| `data/measurement_repository.py` | The high-frequency per-sample rows `DataStorage.record()` already writes today (voltage/current/temp per elapsed second) -- this is today's `measurements` table, formalized as its own repository once `sqlite_manager.py` exists to share a connection with the others. |
| `data/state_repository.py` | Current known station/channel state -- see `station_state` below. This is the piece cycle/state recovery (Section 4) depends on. |

None of these exist yet. `StorageBackend`'s current `open()/close()/record()/query()`
contract is intentionally small enough that introducing them later is a
`main.py`/`result_manager.py` wiring change, not a rewrite of `BatteryTestSequence`.

## 3. Planned data categories (NOT implemented)

Today, `DataStorage` writes one thing: per-sample measurement rows (the existing
`measurements` table documented in `docs/architecture.md` Section 5). The plan adds
four more categories, each mapping to one of the repositories above:

| Category | Purpose | Repository |
|----------|---------|-------------|
| `cycle_samples` | High-speed charge/discharge data used for plotting V/I/T curves. This is today's `measurements` table, renamed/reframed once the other categories exist alongside it so "cycle_samples" (curve data) is clearly distinct from the other four (all more like event/metadata records). | `measurement_repository.py` |
| `station_state` | The current known state of the whole station (which channel is mid-charge/mid-discharge/idle, at what elapsed time, at what point in the CC-CV sequence) -- written frequently enough that a crash or power loss loses at most a few seconds of progress. This is the data cycle/state recovery (Section 4) reads on startup. | `state_repository.py` |
| `event_log` | High-level events -- test started/completed, channel started/aborted, safety violation raised, emergency stop triggered, relay commissioning result. Coarser-grained than `cycle_samples`, meant for a human skimming "what happened during this run" without wading through per-second data. | `cycle_repository.py` (or a dedicated `event_repository.py` if it grows large enough to warrant one) |
| `raw_logs` | Timestamped raw log lines (today: `data/logger.py` writes to `logs/nipxi.log` as a flat file, outside SQLite entirely). Moving these into the database would make them queryable alongside `event_log`/`errors` for post-run analysis, at the cost of losing the "tail -f a text file" workflow -- worth deciding deliberately, not by default. | not yet assigned |
| `errors` | Fault history -- every `NIPXIError` subclass raised during a run (which one, when, on which channel, with what message), independent of whether it aborted the run. Distinct from `event_log` in that this is specifically the exception trail, queryable by error type across runs (e.g. "how often has `RelayStateVerificationError` fired this month"). | not yet assigned -- likely `state_repository.py` or its own module |

## 4. Cycle/state recovery (NOT implemented -- explicitly deferred)

The motivating problem: if the application crashes or the host machine loses power
mid-cycle, today nothing remembers which channels were mid-charge/mid-discharge, so a
restart has no way to resume (or even report) an interrupted cycle safely. This is
real, valuable future work -- but it was explicitly deferred in favor of the mode
architecture in this document, because building a recovery engine before there's a
stable, mode-separated place for it to read/write from would mean redoing it.

**What exists today in preparation:** only the configuration hook --
`config.system_mode.is_recovery_enabled(settings)`, backed by
`ModePolicy.recovery_enabled` (DEVELOPMENT: off, VALIDATION: off by default /
overridable, PRODUCTION: on) and `Settings.RECOVERY_ENABLED_OVERRIDE`. No code reads
this flag yet -- there is nothing to enable. It exists so the eventual recovery engine
has a single, already-agreed-upon place to check "should I even try", rather than
inventing that decision under time pressure alongside the recovery logic itself.

**Sketch of what recovery would need, once built** (not designed in detail -- this is
scope-awareness, not a spec):

1. `state_repository.py` writes a `station_state` row frequently enough during
   `ChargeCycle`/`DischargeCycle` that a crash loses only seconds of progress.
2. On startup, if `is_recovery_enabled(settings)` and `station_state` shows an
   in-progress cycle from a previous run, decide: resume it, mark it aborted, or
   require operator confirmation before doing either (safety-critical -- resuming a
   charge cycle blind, without re-verifying the physical relay/battery state first,
   is exactly the kind of "unknown state = unsafe state" situation
   `docs/architecture.md`'s Emergency Shutdown Strategy exists to prevent -- recovery
   must re-run the same startup safety + relay verification path, never skip it).
3. `event_log` records the recovery decision made, for auditability.

---

## Summary: what to actually build, in order

1. *(this document)* -- planning only, done.
2. `data/sqlite_manager.py` -- **minimal foundation DONE** (`create_database`/
   `initialize_schema`/`insert_test_record`/`get_last_record`, one `test_records`
   table, verified passing on a laptop with no PXI hardware via `test.py`'s
   "Test SQLite (foundation)"). Growing this into real connection-lifecycle/schema-
   migration management for the repositories below is still ahead of it.
3. **`station_state` -- DONE** (Proto Test Execution, Milestone 2). Built directly
   into `DataStorage` rather than a separate `state_repository.py` (that split is
   still deferred, per Section 2 -- premature before a second consumer exists).
   `DataStorage.record_execution_state()`/`get_last_execution_state()` are the real,
   working implementation of what this section originally sketched.
4. Recovery decision logic, gated by `is_recovery_enabled()`, built on top of (3).
   Still not implemented -- `station_state` is currently display-only at startup
   (`test.py::run_proto_test_execution()` shows the last row, never resumes).
5. `battery_repository.py`, `cycle_repository.py`, `event_log`, `errors`, `raw_logs` --
   valuable, but none of them block (3)/(4), so they can come in any order after.

---

## 5. Long-term data strategy review (rotation, telemetry/state/events/config, run summary)

Review performed before the first physical Proto Test Execution rack run, prompted by
a request to plan for years of operation without redesigning the architecture. Nothing
in this section is implemented -- it refines Sections 1-4 above with concrete schema
shapes and a revised recommendation on database rotation.

### 5.1 Database rotation

**Recommendation: rotate, but split scope by data category rather than rotating everything.**

The motivating goals (organization, backups, archival, server migration, a readable
narrative of project evolution -- e.g. `2026_07.db` = first Proto Test Execution,
`2026_08.db` = first battery integration) are legitimate and the monthly period fits
this project's actual rhythm (each month tends to be a distinct milestone). The
complication is that **not all data categories benefit from rotation the same way**:

- `measurements`/`cycle_samples` (high-volume, per-second telemetry) -- rotates
  cleanly. This is the data that actually grows large over years, and a month's
  worth of telemetry is a natural, self-contained unit (matches the "2026_07.db =
  first Proto Test Execution runs" narrative exactly).
- `station_state` (or its future `state_repository.py` form) and a future
  `run_summary` table (Section 5.5) are **low-volume, need-fast-answer** data:
  "what did the station last do" and "list all historical runs" are exactly the
  queries you don't want to become a multi-file scan. Splitting these across
  monthly files means `get_last_execution_state()` (today, `data/storage.py:265`)
  would need a fallback to the previous month's file at every month boundary, and
  "browse all historical runs" would need to open every monthly file that ever
  existed.

**Refined recommendation:** rotate only the telemetry table(s) monthly (or
quarterly/yearly -- see the trade-off below); keep `station_state` and the future
`run_summary` in one small, non-rotating "index" database that grows slowly (one row
per relay-visit or per run, not per second) and never needs cross-file logic. This
keeps the part of the goal that's actually about data volume (telemetry) rotating,
while keeping the part that's about fast historical lookup (state/run index) simple
and always-current. This is a refinement of, not a departure from, the original
monthly-rotation idea -- it answers "impact on state recovery" cleanly instead of
requiring a fallback-to-last-month hack.

**Advantages:** matches the requested organizational/backup/archival goals; monthly
period aligns with this project's actual milestone cadence; splitting rotation scope
by category avoids the worst cross-file query problems.

**Disadvantages:** a long-duration run that spans a month boundary (the user's own
"2026_09.db -> long-duration validation runs" example) would have its telemetry split
across two files -- worth deciding upfront whether that's acceptable (it likely is,
since the run's own row in `run_summary`/`station_state` -- the non-rotating index --
still records it as one continuous run; only the raw per-second curve data is split,
and a curve-plotting tool would just need to read both files for that one run).
Historical queries spanning more than one period need either SQLite's `ATTACH
DATABASE` across files or an application-level loop merging results -- bounded,
well-understood, but real added complexity in whatever future reporting/analysis
tool is built (today, `ReportGenerator` is still a stub, so nothing depends on this
yet). More files to operationally track over time (mitigated by the fact that this
is also the stated goal).

**Operational implications:** `Settings.DATABASE_FILE` needs to become a computed
value (e.g. a function keyed by `datetime.now().strftime("%Y_%m")`) rather than the
frozen class attribute it is today, for the *telemetry* database only -- the
non-rotating index database's filename stays a fixed constant, same as today's
`DATABASE_FILE` currently is for everything. Retention/archival becomes file-level
(delete or move a whole month's telemetry file) -- a real simplification over
row-level deletion.

**Impact on future server migration:** genuinely easier with rotation, as originally
argued -- discrete monthly files import as discrete migration batches, versus one
ever-growing file. The non-rotating index database migrates as a single, small,
one-time import regardless of rotation.

### 5.2 Telemetry / State / Events / Configuration -- does this separation fit?

**Yes -- and it's very close to what Section 3 above already sketched
(`cycle_samples`/`station_state`/`event_log`), just now motivated by concrete
examples instead of abstract categories.** Confirming the fit for each:

**A. Telemetry.** Agreed: sense voltage and NTC temperature should be continuous,
independent of charge/discharge activity; charge/discharge current should stay
sourcing-only (there is no current to measure when nothing is being sourced/sunk --
this one is a property of active sourcing, not a continuously-meaningful physical
value). The existing `measurements` table schema (`id, run_id, channel, timestamp,
elapsed_s, phase, voltage_v, current_a, temp_c`) already supports this **without a
schema change** for the phase distinction (`phase` is a plain `TEXT` column -- a
`"rest"` value alongside today's `"charge"`/`"discharge"` costs nothing structurally).
What the schema does NOT yet support is multiple *simultaneous* voltage sources for
one instant (sense voltage vs. SMU-measured vs. DMM-measured, all real at the same
moment) -- today's one-`voltage_v`-column-per-row design assumes one signal per row.
The smallest fix, staying within "extend existing tables": add a `source` TEXT
column (e.g. `"daq_sense"`/`"smu"`/`"dmm"`/`"ntc"`) so multiple telemetry streams
coexist as separate rows disambiguated by `source` + `phase`, rather than multiplying
columns per signal type. No new table needed for this.

**B. State.** Agreed: state must stay separate from telemetry -- state changes on
transitions (relay switch, phase change, fault), telemetry samples on a clock;
mixing them would mean re-writing the same state value into every high-frequency
telemetry row, which is pure redundancy and conflates two different write cadences.
This validates keeping `station_state` (Section 3) as its own table. One refinement
worth flagging (not urgent): the state list given
(`ACTIVE/REST/CHARGING/DISCHARGING/FAILED/CANCELLED/SAFETY_VIOLATION/COMPLETED`)
actually mixes two different meanings in one column today -- transient *phase*
states (which are frequent and expected) and terminal *stop-reason* states (rare,
final, already formalized as `utils/stop_reason.py::StopReason`). Today's single
`station_state.state` column conflates both, which works fine for Proto Test
Execution's simple case; if state complexity grows, splitting into two columns
(`phase` + `stop_reason`, mirroring `measurements.phase` and `StopReason` exactly)
would be a small, low-risk future refinement -- not needed now.

**C. Events.** Agreed this is valuable and not yet built -- this is exactly Section
3's planned `event_log` category. Events are discrete, point-in-time occurrences
("this just happened": relay activated/deactivated, output enabled/disabled, test
started/completed/failed, operator cancelled, recovery detected) -- structurally
different from both telemetry (continuous sampling) and state (current position):
an append-only table, never updated, one row per occurrence. Shape:
`event_log(id, run_id, timestamp, event_type, relay, detail)`. Cheap schema (a
handful of columns), the real effort is instrumenting the call sites that should
emit an event (relay open/close, SMU output enable/disable, sequence start/end) --
touches multiple files, not one, so this is HIGH VALUE / MEDIUM EFFORT rather than
low effort.

**D. Configuration snapshots.** Agreed on both the Battery and SMU field lists, and
importantly: these must be **snapshots (copied by value at write time), never a
foreign key into a live config table.** A foreign key back to `BATTERY_CONFIGS`-style
reference data would reintroduce the exact problem being solved -- if that reference
row's values change later, historical rows silently become ambiguous again, which is
precisely what "historical runs must remain interpretable without consulting old Git
revisions" is asking to avoid. The resolved numeric values (voltage max/min, charge/
discharge current limits, battery type name) and SMU identity (model, slot, channel,
nickname) and configuration (commanded voltage/current-limit, output-enabled
readback, compliance state) should be copied directly into whichever row anchors a
run/relay-event (`station_state` today, `run_summary` once it exists) -- additive
columns, not a new reference table.

### 5.3 Run summary -- recommended as a new table (the one exception to "extend, don't create")

**Yes, the architecture would clearly benefit from this, and it is the single
highest-leverage addition on this whole list.** Today there is genuinely no way to
answer "list all runs from the last year and their outcome" without scanning
`station_state`/`measurements` row-by-row or reading log files -- `TestRunResult`
(`test_executor.py`) exists only in memory, and `ResultManager.save_run()`
(`result_manager.py:97-113`) is a literal, already-documented no-op ("Future: write
run-level summary row..."). This is the one place a genuinely new table is justified
(not an extension of `measurements` or `station_state`, which are shaped for
different things), because "one row per run, browsable without scanning telemetry"
is precisely the run-summary shape neither existing table has.

Suggested shape: `run_summary(run_id, test_type, start_time, end_time, duration_s,
stop_reason, result, battery_type, battery_voltage_max_v, battery_voltage_min_v,
battery_charge_current_limit_a, battery_discharge_current_limit_a)` -- one row per
run, `INSERT`ed at start, `UPDATE`d once at the end (end_time/duration/stop_reason/
result). This is the table that belongs in the non-rotating index database from
5.1 -- it's the primary "browse history" entry point and must never be split across
monthly files.

### 5.4 Recommended retention strategy

File-level, enabled naturally by rotation: telemetry files can be archived/deleted
by month (or compressed and moved to cold storage) once a retention window passes,
without touching the non-rotating index database (`station_state`/`run_summary`),
which should be retained indefinitely (it's small and is the historical-browsing
entry point). No row-level deletion logic needed anywhere -- whole-file archival is
sufficient and simpler.

### 5.5 Recommended fields to capture before the first real battery test

In priority order, all additive to existing tables/the planned `run_summary`, none
requiring a redesign:

1. `station_state`: `in_compliance`, `output_enabled_readback` -- already computed
   in memory (`hardware/smu.py::SMU.source_dc_voltage_point()`'s return dict),
   currently dropped before reaching storage.
2. `station_state`: full SMU/DMM values on `FAILED`/`SAFETY_VIOLATION`/`CANCELLED`
   rows (today only `relay`+`state` are written on these -- everything else is
   `NULL`, discarding exactly the diagnostic context a future investigation would
   want most).
3. `station_state`: per-relay `duration_s`, `smu_channel`/`smu_nickname`.
4. `station_state` (and `run_summary` once built): battery-config snapshot fields
   (Section 5.2.D) -- low urgency for Proto Test Execution specifically (no battery
   connected), but adding the columns now avoids a second schema touch when real
   battery runs begin.
5. `run_summary` (Section 5.3) -- the highest-value single addition, but it's new
   table + wiring `ResultManager.save_run()`, not a column addition, so it's
   reasonable to schedule as its own follow-up rather than bundling into the
   pre-rack-run checklist.

### 5.6 HIGH VALUE / LOW EFFORT vs. deferred

**HIGH VALUE / LOW EFFORT** (column/parameter additions only): items 1-3 above.

**HIGH VALUE / MEDIUM EFFORT** (new table or cross-file instrumentation): `run_summary`
(5.3); `event_log` (5.2.C); battery-config snapshot columns (item 4 above, low
urgency but cheap once `run_summary` exists to hold them).

**Deferred** (real, but not before the first rack run): continuous telemetry
sampling loop (5.2.A -- a new background sampling control-flow, not a schema
change); database rotation itself (5.1 -- independent of the rack run, can be
decided on its own timeline once real data volume is observed); `phase`/`stop_reason`
column split in `station_state` (5.2.B -- low risk to leave conflated for now);
`errors`/`raw_logs` categories (Section 3 -- valuable, no current consumer).

### 5.7 Risks of not capturing this now

The recurring theme across every item above: everything listed in 5.5 is **already
sitting in memory at the exact code paths that write to `station_state`** --
capturing it now costs a few extra dict keys and column definitions. Not capturing
it means the only way to recover that context later is re-running physical hardware,
which is exactly what this entire review exists to avoid. The run-summary gap (5.3)
compounds over time in a different way: every run performed before it exists is a
run that can only be found later by scanning telemetry or reading logs, never by a
simple "browse historical runs" query -- retrofitting it later cannot back-fill
`start_time`/`stop_reason` for runs that already happened without cross-referencing
logs by hand.
