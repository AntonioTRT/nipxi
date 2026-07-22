# Database Roadmap

**Status: planning document.** Nothing in this file is implemented yet except the
mode-separated database *location* and the minimal `data/sqlite_manager.py` foundation
(Section 1), which are real today. Everything else here — the repository classes, the
new data categories, and cycle/state recovery — is future architecture, written down
now so it gets designed deliberately instead of improvised later. See
`docs/architecture.md` "System Modes" for how `SYSTEM_MODE` feeds into this.

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
3. `state_repository.py` + `station_state` -- the prerequisite for any recovery work,
   independently useful (a live "what's each channel doing right now" view) even
   before recovery itself exists.
4. Recovery decision logic, gated by `is_recovery_enabled()`, built on top of (3).
5. `battery_repository.py`, `cycle_repository.py`, `event_log`, `errors`, `raw_logs` --
   valuable, but none of them block (3)/(4), so they can come in any order after.
