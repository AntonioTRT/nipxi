# Relay Safety Sequencing -- Architecture Compliance Review

> **Resolved notice:** the steps 1-2 gap this review identified (no
> "Read All -> Verify Current Status" before forcing relays off) has been
> fixed -- see `docs/architecture.md` Section 24 ("Relay Safety
> Verification Pattern") and Section 25 (the same fix extended to PSU
> control) for the implementation, and `docs/MILESTONES.md` for the
> verification record. Every real relay path listed as "Partially
> Compliant" below is now Fully Compliant. This document is kept as the
> historical record of the review that identified the gap, not updated
> in place.

Analysis-only document. No code was modified to produce this review.

**Agreed architecture (the reference standard this review measures every relay usage path against):**

```
1. Read all relay states
2. Verify current status
3. Force all relays OFF
4. Read all relay states again
5. Verify all relays are OFF
6. Perform the requested relay action (close/open/activate/deactivate)
7. Read all relay states again
8. Verify the requested action actually occurred
```

Shorthand used throughout this document: **Read All -> Verify -> All Off -> Verify -> Action -> Verify**.

---

## Executive answer

**Are we currently following the agreed relay safety sequence everywhere relays are used? Mostly yes for steps 3-8, but no for steps 1-2.**

Every real, production-reachable relay code path in this codebase (Monitor Battery, Proto Test Execution, the legacy `BatteryTestSequence`, and every Numato Relay Matrix test in `test.py`) converges on **one single driver method**, `hardware/relay_eth.py::NumatoRelayMatrix._force_all_off_and_verify()`, for the "All Off -> Verify" portion (steps 3-5), and every action is independently re-verified by readback afterward (steps 6-8, with one partial exception -- see B.5). **What is systemically missing everywhere is steps 1-2: an explicit "read the current relay state, then decide/verify it before forcing everything off."** The driver goes straight to "force everything off" without first reading and judging what state it was already in. This is a single, well-isolated gap (one function, one file) rather than something scattered inconsistently across the codebase -- fixing it in one place would bring every call site into compliance simultaneously.

Separately, two **non-production, low/no-risk** relay abstractions are fully non-compliant by design: `hardware/relay_serial.py::SerialRelay` (diagnostic-only, config currently empty) and `hardware/simulated.py::SimulatedRelay` (not wired into `RelayFactory`, in-memory only). A third, `hardware/relay_matrix.py::RelayMatrix`, is dead legacy scaffold code, unreferenced anywhere.

---

## A. List of every relay usage location

| # | Location | Layer |
|---|---|---|
| 1 | `hardware/relay_eth.py::NumatoRelayMatrix.open()`/`close()`/`open_all()`/`close_all()` | Driver -- the shared implementation every path below ultimately calls |
| 2 | `hardware/relay_eth.py`'s native primitives (`write()`/`write_all()`/`read_relay()`/`read_all()`/`verify_single()`/`verify_all()`) | Driver -- native/raw layer |
| 3 | `test_control/monitor_battery_sequence.py::MonitorBatterySequence.run()` | Sequence (Monitor Battery, production) |
| 4 | `test_control/proto_test_sequence.py::ProtoTestSequence.run()` | Sequence (Proto Test Execution, validation) |
| 5 | `test_control/battery_test.py::BatteryTestSequence.run()` | Sequence (legacy Charge/Discharge path, pre-Milestone-II) |
| 6 | `test_control/hardware_manager.py::HardwareManager` (`_connect_all_strict()`/`_connect_all_lenient()`/`disconnect_all()`/`_atexit_relay_shutdown()`) | Lifecycle -- startup/shutdown safe-state enforcement |
| 7 | `test_control/safety_monitor.py::SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()` | Safety shutdown convergence point |
| 8 | `test.py::_run_relay_numato_matrix_test()` ("Relay 1 quick check") | Commissioning test (high-level API) |
| 9 | `test.py::_run_relay_matrix_scan()` (Matrix Scan, group-scoped) | Commissioning test (high-level API) |
| 10 | `test.py::test_relay_ethernet_test()` (RelayEthernetTest) | Commissioning test (native primitives, deliberately bypasses the high-level wrapper) |
| 11 | `test.py::test_relay_safety_selftest()` (Safety Self-Test) | Commissioning test (high-level API) |
| 12 | `hardware/relay_serial.py::SerialRelay` | Driver -- diagnostic-only, non-production (`RELAY_SERIAL_CONFIGS == {}` today) |
| 13 | `hardware/simulated.py::SimulatedRelay` | Scaffolded future-work extension point, not wired into `RelayFactory` |
| 14 | `hardware/relay_matrix.py::RelayMatrix` | Dead legacy scaffold, unreferenced anywhere in the codebase |

`test_control/charge_cycle.py`/`discharge_cycle.py` were checked and contain **no** relay references at all -- relay handling for the legacy charge/discharge path lives entirely in `BatteryTestSequence` (#5), one level up.

---

## B. Exact relay sequence used by each path

### B.1 The shared driver primitive -- `NumatoRelayMatrix` (`hardware/relay_eth.py`)

This is the foundation every high-level call site (#3, #4, #5, #8, #9, #11) ultimately runs through.

**`close(channel)`** (energize):
```
1. write_all(0)                      -- force ALL relays OFF   (native)
2. verify_all(0)                     -- read_all() + compare == 0
3. write(channel-1, True)            -- activate the requested relay (native)
4. verify_single(channel-1, True)    -- read_relay() + compare == expected   (INDIVIDUAL)
5. verify_all(1 << (channel-1))      -- read_all() + compare == expected mask   (BULK)
```
**`open(channel)`** (de-energize) / **`open_all()`**:
```
1. write_all(0)                      -- force ALL relays OFF   (native)
2. verify_all(0)                     -- read_all() + compare == 0
```
**`close_all()`**: raises `RelayError` unconditionally -- deliberately disallowed (interlocked bank, only one relay may ever be energized at once).

**Mapped onto the agreed 8-step pattern:**

| Agreed step | Present in `close()`? | Present in `open()`/`open_all()`? |
|---|---|---|
| 1. Read all relay states (**before** doing anything) | **NO** | **NO** |
| 2. Verify current status (**before** doing anything) | **NO** | **NO** |
| 3. Force all relays OFF | Yes (`write_all(0)`) | Yes (`write_all(0)`) |
| 4. Read all relay states again | Yes (inside `verify_all(0)`, via `read_all()`) | Yes |
| 5. Verify all relays are OFF | Yes (`verify_all(0)` compares to 0) | Yes |
| 6. Perform the requested action | Yes (`write(channel-1, True)`) | N/A -- open()'s target state IS all-off, so step 3 already performed the action |
| 7. Read all relay states again | Yes -- both `verify_single()` (individual read) and `verify_all()` (bulk read) | N/A |
| 8. Verify the requested action occurred | Yes -- individual AND bulk verification | N/A (step 5 already covers it) |

**Assessment: Partially compliant.** Steps 3-8 are implemented correctly and are in fact *stronger* than the minimum agreed pattern for `close()` (both an individual-channel read AND a bulk-bank read are used to verify the action, catching anything a single-channel read alone could miss). **Steps 1-2 are entirely absent** -- the driver never reads or evaluates the pre-existing relay state before forcing everything off. Since every high-level path (open/close/open_all) is built on this one function, this single gap is inherited identically everywhere.

### B.2 Native primitives layer (`write()`, `write_all()`, `read_relay()`, `read_all()`, `verify_single()`, `verify_all()`)

These are thin, direct wrappers around the literal Numato command strings, with no safety sequence of their own by design -- they are the *building blocks* `close()`/`open()` compose, not a second safety-checked API. Calling them directly (as `test_relay_ethernet_test()` does, see B.7) bypasses the mandatory force-off-first discipline entirely; that is an intentional, narrowly-scoped exception (see B.7), not a second production path.

### B.3 Monitor Battery -- `MonitorBatterySequence.run()` (production)

```
1. check_cancellation(token)
2. relay.close(relay_address)         -- full B.1 sequence (steps 3-8 of the agreed pattern)
3. [monitoring loop: DMM read, record_measurement, ExecutionFrame -- no further relay activity]
4. On any exit (Ctrl+C / SafetyViolationError / RelayError / any Exception):
     safety.safe_cancel_shutdown() / safety.emergency_stop()
       -> relay_matrix.open_all()     -- full B.1 all-off sequence
```
**No explicit `relay.open()`** is ever called by this sequence directly -- the loop is infinite (`while True`) and the *only* way it ends is via one of the four exception paths, every one of which routes through `SafetyMonitor`'s shutdown methods (which call `open_all()`). This is actually a clean design: there is no "success" exit that could accidentally skip opening the relay, because there is no such exit at all.

**Assessment: Partially compliant** (same B.1 gap: no read/verify before the initial `close()`'s internal force-off). **Convergence to the approved shutdown mechanism: 100% -- every exit path calls `SafetyMonitor.safe_cancel_shutdown()`/`emergency_stop()`.**

### B.4 Proto Test Execution -- `ProtoTestSequence.run()` (validation)

Per relay `N` in `Settings.ACTIVE_CHANNELS`:
```
1. check_cancellation(token)
2. relay.close(N)                     -- full B.1 sequence
3. smu.source_dc_voltage_point(...)    -- SMU sourcing + DMM read during hold
4. record_measurement() / record_execution_state() / ExecutionFrame render
5a. On success: relay.open(N)          -- full B.1 all-off sequence, explicit
5b. On OperationCancelledError/SafetyViolationError/RelayError/Exception:
      safety.safe_cancel_shutdown() / safety.emergency_stop() -> relay_matrix.open_all()
```
**Assessment: Partially compliant** (same B.1 gap). **Convergence: 100%** -- the happy path explicitly opens the relay via the high-level API (itself compliant for steps 3-8), and every failure/cancellation path converges on the same `SafetyMonitor` shutdown methods as Monitor Battery.

### B.5 Legacy `BatteryTestSequence.run()` (`test_control/battery_test.py`, pre-Milestone-II Charge/Discharge path)

Per channel:
```
1. if not safety.is_safe_to_switch_relay(read_current(ch)): skip channel   -- EXTRA electrical
   safety check, layered ON TOP of the relay driver's own sequence (checks the
   DAQ-measured current is near zero before ever calling relay.close())
2. check_cancellation(token)
3. relay.close(ch)                    -- full B.1 sequence
4. charge.run(ch, ...)  /  discharge.run(ch, ...)
5. if not safety.is_safe_to_switch_relay(...): skip remainder             -- same
   extra check again, between charge and discharge
6a. On success (no exception): relay.open(ch)   -- full B.1 all-off sequence, explicit
6b. On OperationCancelledError/SafetyViolationError/RelayError/Exception:
      safety.safe_cancel_shutdown() / safety.emergency_stop() -> relay_matrix.open_all()
```
**Assessment: Partially compliant** (same B.1 gap in the underlying driver). Notably, this path has an *additional*, independent safety check (`is_safe_to_switch_relay()` against a live current reading) that none of the newer sequences (#3, #4) currently reuse before their own `relay.close()` calls -- see D.3/E.3. **Convergence: 100%.**

### B.6 `HardwareManager` startup/shutdown (`test_control/hardware_manager.py`)

**Startup (`connect_all()` -> `_connect_all_strict()`/`_connect_all_lenient()`):**
```
1. relay.connect()                    -- driver's own connect() issues ONE "relay readall"
                                          immediately as a connection-health check (see B.1's
                                          note) -- this IS a "read all" of sorts, but it is
                                          diagnostic/logging only: nothing is compared against
                                          it, and no ABORT/CONTINUE decision is made from it.
2. relay.open_all()                   -- full B.1 all-off sequence; a verification FAILURE
                                          here is unconditionally fatal to startup, in EVERY
                                          system mode (DEVELOPMENT/VALIDATION/PRODUCTION) --
                                          "unknown relay state = unsafe state" is never relaxed.
```
**Shutdown (`disconnect_all()`):**
```
1. smu.emergency_output_off()
2. relay.open_all()                   -- full B.1 all-off sequence (best-effort; errors are
                                          logged CRITICAL, never re-raised, so other devices
                                          still get a disconnect attempt)
3. relay.disconnect() / dmm.disconnect() / smu.disconnect() / daq.disconnect()
```
**`_atexit_relay_shutdown()`** (registered via `atexit`): a third, independent backstop -- `if self._relay.connected: relay.open_all()` -- covers process-exit paths that bypass a `try/finally` around `disconnect_all()` entirely (e.g. `os._exit()`, an exception during interpreter shutdown).

**Assessment: Partially compliant** for the same B.1 reason, but this is the **strongest** convergence point in the codebase: three independent layers (explicit `disconnect_all()`, the `atexit` backstop, and the driver's own internal `_emergency_all_off()` reflex triggered from inside `verify_all()`/`verify_single()`/`_call_with_reconnect()`) all force-and-verify all-off, and a relay verification failure at startup is fatal in every mode with no exception.

### B.7 `test.py::test_relay_ethernet_test()` -- RelayEthernetTest (native primitives, deliberate exception)

Per relay index (native 0-based), explicitly using the **native primitives layer** (B.2), not `close()`/`open()`:
```
1. write_all(0)                       -- force OFF (native)
2. verify_all(0)                      -- bulk verify OFF
3. write(relay_index, True)           -- activate (native) -- NO individual verify_single() call
4. verify_all(1 << relay_index)       -- bulk verify only
5. write_all(0)                       -- force OFF again (native)
6. verify_all(0)                      -- bulk verify OFF
```
On cancellation mid-loop: `write_all(0)` + `verify_all(0)` (force-off + verify), same as every other exit path.

**Assessment: Partially compliant, with one additional gap beyond B.1.** This is the **one path in the entire codebase** that verifies an activation using **bulk `verify_all()` only** -- it never calls `verify_single()` (the individual-channel read) the way `close()` does. This is a **deliberate, documented design choice** (the function's explicit purpose is to validate the *native command layer* independently of the higher-level safety wrapper `close()`/`open()` provide -- see its own docstring), not an oversight, but it is worth naming explicitly as a place where "verify the requested action occurred" is satisfied only partially (bank-level, not also individually) relative to the full 8-step pattern's spirit.

### B.8 `test.py::_run_relay_numato_matrix_test()` -- "Relay 1 quick check"

```
1. relay.read(1)                      -- native single READ, no verify comparison (informational)
2. relay.close(1)                     -- full B.1 sequence
3. relay.open(1)                      -- full B.1 sequence
```
**Assessment: Partially compliant** (B.1 gap; otherwise fully delegates to the compliant high-level API). The leading `relay.read(1)` is closer in spirit to "read" than any other test path gets, but it reads only channel 1 (not "all"), and nothing is verified/decided from it -- it is reported informationally (PASS/FAIL on whether the READ command itself succeeded), not used as a pre-action safety gate.

### B.9 `test.py::_run_relay_matrix_scan()` -- Matrix Scan (group-scoped)

Per channel in the scoped range:
```
1. relay.close(ch)                    -- full B.1 sequence
2. relay.read(ch)                     -- extra single-channel read (informational, post-activation)
3. relay.open(ch)                     -- full B.1 sequence
```
On cancellation mid-scan: `relay.open_all()` (full B.1 sequence).

**Assessment: Partially compliant** (B.1 gap; steps 3-8 fully delegated to the compliant high-level API for every channel in the scan).

### B.10 `test.py::test_relay_safety_selftest()` -- Safety Self-Test (1..N)

Per channel:
```
1. relay.close(ch)                    -- full B.1 sequence (its own docstring literally
                                          describes this as "OFF ALL -> VERIFY OFF -> ON ch
                                          -> VERIFY ch-only-ON")
2. relay.open(ch)                     -- full B.1 sequence, restores safe state before next channel
```
Stops immediately on first failure of any kind (never continues to remaining channels) -- final `relay.open_all()` after a full, uninterrupted sweep.

**Assessment: Partially compliant** (B.1 gap only) -- this is, together with B.9, the closest any test in the codebase gets to a literal implementation of the agreed pattern, since it's built entirely on the fully-verified `close()`/`open()` API with no shortcuts.

### B.11 `hardware/relay_serial.py::SerialRelay` (diagnostic-only, non-production)

```
open(channel):  _send_cmd(cmd_open.format(ch=channel))     -- ONE command, no readback at all
close(channel): _send_cmd(cmd_close.format(ch=channel))    -- ONE command, no readback at all
```
No force-all-off, no pre-read, no post-verification of any kind -- `query()` exists but is never called by `open()`/`close()` themselves. `disconnect()` does call `open_all()` (which loops `open()` per channel -- still with zero verification per call, since `RelayBase.open_all()`'s default loop just calls the same unverified `open()`).

**Assessment: Non-compliant.** Zero steps of the agreed 8-step pattern are implemented. **However, this is explicitly diagnostic-only** (its own connect() log message: "protocol commands are not implemented because production hardware is Ethernet"), and `config/devices.py::RELAY_SERIAL_CONFIGS` is currently `{}` (empty) following an earlier hardware-cleanup pass -- so this class is not instantiated by any live path today. Real-world risk today: **none** (unreachable with current configuration), but the class itself is a live liability if a serial relay is ever reintroduced without also adding the missing safety sequence.

### B.12 `hardware/simulated.py::SimulatedRelay` (scaffolded future-work, not wired in)

```
open(channel):  in-memory state update only (self._active_channel = None if it matches)
close(channel): in-memory state update only (self._active_channel = channel)
```
No hardware I/O of any kind (by design -- it's a simulation stub), therefore no read/verify steps exist or could exist in the way the agreed pattern means them.

**Assessment: Not applicable / non-compliant by design.** Explicitly documented as "not wired into RelayFactory yet" and "not a safety device" in its own class docstring. Real-world risk today: **none** -- `RelayFactory._DRIVERS` has no `"simulated"` entry, so this class cannot be constructed via the normal dispatch path used everywhere else.

### B.13 `hardware/relay_matrix.py::RelayMatrix` (dead legacy scaffold)

```
close_channel(channel): open_all() [full TODO-stub, no real serial call] then sets
                         self._active_channel = channel  -- comments show WHERE a real
                         CLOSE command and response validation would go, but none of it
                         is implemented.
open_channel(channel):  TODO-stub, no real command sent.
open_all():              TODO-stub, no real command sent.
```
This class does **not** subclass `RelayBase`, is **not** referenced by `RelayFactory._DRIVERS`, and a repo-wide search confirms it is **never imported or instantiated anywhere else in the codebase**.

**Assessment: Non-compliant (by construction -- it does nothing yet), and dead code.** Real-world risk today: **none** (completely unreachable). Flagged here only because the review was asked to include "any future-work implementations already scaffolded."

---

## C. Compliance assessment (summary table)

| Location | Read All (1) | Verify (2) | All Off (3) | Verify (4-5) | Action (6) | Verify (7-8) | Overall |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| `NumatoRelayMatrix.close()`/`open()`/`open_all()` (driver) | NO | NO | YES | YES | YES | YES (both individual + bulk for `close()`) | **Partially compliant** |
| Monitor Battery (`MonitorBatterySequence`) | NO | NO | YES | YES | YES | YES | **Partially compliant** |
| Proto Test Execution (`ProtoTestSequence`) | NO | NO | YES | YES | YES | YES | **Partially compliant** |
| Legacy `BatteryTestSequence` | NO | NO | YES | YES | YES | YES (+ extra current-based check) | **Partially compliant** |
| `HardwareManager` startup/shutdown | partial (diagnostic read only, not a gate) | NO | YES | YES | YES (all-off IS the action) | YES | **Partially compliant** |
| "Relay 1 quick check" (`_run_relay_numato_matrix_test`) | partial (ch.1 only, informational) | NO | YES | YES | YES | YES | **Partially compliant** |
| Matrix Scan (`_run_relay_matrix_scan`) | NO | NO | YES | YES | YES | YES | **Partially compliant** |
| RelayEthernetTest (native primitives) | NO | NO | YES | YES | YES | **bulk-only**, no individual verify | **Partially compliant** |
| Safety Self-Test (`test_relay_safety_selftest`) | NO | NO | YES | YES | YES | YES | **Partially compliant** |
| `SerialRelay` (diagnostic, non-production) | NO | NO | NO | NO | YES (unverified) | NO | **Non-compliant** (unreachable today) |
| `SimulatedRelay` (not wired in) | N/A | N/A | N/A | N/A | in-memory only | N/A | **Non-compliant by design** (unreachable today) |
| `RelayMatrix` (dead legacy scaffold) | N/A | N/A | stub only | stub only | stub only | stub only | **Non-compliant** (dead code, unreachable) |

**Pattern:** every real, production/validation-reachable path is **identically partially compliant** for the exact same reason (steps 1-2 missing, inherited from one shared driver function) and identically strong for steps 3-8. There is no path that is compliant in one place and silently different in another among the live code -- the gap is uniform, not scattered.

---

## D. Safety gaps

1. **Missing pre-action Read All + Verify (steps 1-2), everywhere.** No relay operation anywhere in the codebase reads and evaluates the relay bank's pre-existing state before forcing it off. In practice, this means: if the bank were *already* in an unexpected state when an operation begins (e.g. two relays somehow active, a relay stuck from an external fault, a previous session's hardware left in a bad state that even the all-off-and-verify sequence couldn't detect *as a distinct condition* -- it just force-corrects it silently), that fact is never surfaced to the operator or to `event_log`/logs as "found already in an unsafe state" -- it is corrected without ever having been diagnosed. The eventual outcome (all-off, verified) is the same either way, but the diagnostic visibility the agreed 8-step pattern was designed to provide ("hidden routing issues," per the request's own stated rationale) is not currently produced anywhere.

2. **`RelayEthernetTest`'s activation step is verified in bulk only, not individually.** A theoretical failure mode where `verify_all()`'s bitmask check could be fooled (e.g. a firmware bug reporting the correct popcount but wrong bit) would not be caught by this one path the way `close()`'s individual `verify_single()` + bulk `verify_all()` combination would catch it elsewhere. Low real-world likelihood (would require a specific firmware misbehavior), and this path's entire purpose is to test the native layer independently of the high-level safety wrapper, but it is a real, narrower verification surface than the rest of the codebase.

3. **`is_safe_to_switch_relay()`'s extra electrical pre-check (current-near-zero) is not reused by the two newer, Milestone-II sequences.** `BatteryTestSequence` (legacy) checks the DAQ-measured current is near zero *before* calling `relay.close()`/between charge and discharge. `ProtoTestSequence` and `MonitorBatterySequence` do not perform an equivalent check before their own `relay.close()` calls (Proto Test has no load to check against yet since it's infrastructure-only; Monitor Battery never sources current at all, so a current-based pre-check would currently always read zero and add no real protection -- but this asymmetry would become materially more important the moment Charge/Discharge Battery are implemented on top of the same pattern).

4. **`SerialRelay`/`RelayMatrix`/`SimulatedRelay` are non-compliant by construction.** Zero real-world risk today (all three are unreachable via current configuration/wiring), but any of the three becoming reachable in the future (re-adding a serial relay device to `RELAY_SERIAL_CONFIGS`, wiring `SimulatedRelay` into `RelayFactory`, or someone rediscovering and reusing `RelayMatrix`) would silently reintroduce a fully non-compliant relay path with no automatic warning, since nothing in `utils/device_validator.py` currently checks "does this relay type implement the mandatory safety sequence."

## E. Recommended fixes (if any) -- analysis only, not implemented per this review's scope

1. **Add an explicit "read + log current state" step at the very start of `_force_all_off_and_verify()`** (`hardware/relay_eth.py`), before `write_all(0)` -- a `read_all()` call whose result is logged (and, ideally, compared against the caller's own expectation of "what should already be true here," e.g. "all off" at the start of `open()`, or "whatever it was before this action" at the start of `close()`) rather than fed straight into the force-off with no record of what was there beforehand. Since this is the one function every high-level path already funnels through, this single change would bring every real relay usage path in the codebase into full 1-2-3-4-5-6-7-8 compliance simultaneously, with no per-call-site changes needed anywhere else.
2. **Consider whether `RelayEthernetTest` should add an individual `verify_single()`-equivalent check** (using its own native `read_relay()`) alongside its existing bulk `verify_all()`, if the native-primitives test is meant to reach full parity with `close()`'s verification strength rather than deliberately testing a narrower surface. If the narrower surface is intentional (testing native primitives independently of the wrapper's exact verification strategy), document that explicitly in its docstring as a deliberate scope boundary rather than leaving it as an implicit difference a future reader must rediscover.
3. **When Charge/Discharge/Cycle Battery are implemented**, decide explicitly whether `BatteryTestSequence`'s `is_safe_to_switch_relay()` pre-check should be carried forward into the new sequences (it likely should, once real current sourcing is involved) -- track this alongside the Charge/Discharge Battery implementation work already in `docs/TODO.md`, rather than as a new standalone item.
4. **Add a startup/device-validation check** (`utils/device_validator.py`) that flags (WARNING, not FAIL) any configured relay `"type"` other than `"ethernet"` as "does not implement the mandatory safety sequence -- diagnostic/non-production use only," so re-enabling `SerialRelay` (or wiring in `SimulatedRelay`) in the future surfaces this gap automatically at startup instead of silently.
5. **Remove or clearly mark `hardware/relay_matrix.py::RelayMatrix` as dead/retired** -- it is unreferenced anywhere, implements nothing, and its presence could mislead a future reader into thinking it's an active or supported relay path.

None of the above were implemented as part of this review, per its explicit scope ("architecture review only, do not modify any code").

## F. Overall compliance score for current relay architecture

**6.5 / 10 -- Partially compliant, uniformly.**

- **+** Steps 3-8 (All Off -> Verify -> Action -> Verify) are implemented correctly, consistently, and in one single shared function that every real, production-reachable relay path in the codebase converges on -- there is no scattered/inconsistent implementation among the live code paths.
- **+** Exception/cancellation/failure convergence is excellent: every sequence class (`MonitorBatterySequence`, `ProtoTestSequence`, `BatteryTestSequence`) and every commissioning test in `test.py` routes every failure exit through either the driver's own `_emergency_all_off()` reflex, `SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()`, `HardwareManager.disconnect_all()`, or the `atexit` backstop -- no path was found that can leave a relay energized on an exception, Ctrl+C, a `SafetyMonitor` abort, or a test failure, across the driver's own internal reflex, the sequence layer, or the process-lifecycle layer.
- **--** Steps 1-2 (Read All -> Verify, before ever forcing an all-off) are absent from every single relay usage path, including the shared driver function itself -- this is the single, well-localized architectural gap keeping the score from being "fully compliant."
- **--** Three non-production relay abstractions (`SerialRelay`, `SimulatedRelay`, `RelayMatrix`) are fully non-compliant by construction, though currently unreachable with zero real-world risk.
- **--** One narrow, deliberate exception (`RelayEthernetTest`) verifies activation via bulk-only readback rather than the individual+bulk combination used everywhere else, by explicit design (testing the native command layer).

**In one sentence: the codebase reliably forces relays off, verifies they're off, performs the requested action, and re-verifies it -- everywhere, with excellent shutdown convergence -- but it never first reads and records what state the relay bank was already in before doing so, which is the one piece of the agreed architecture not yet built, and it is missing uniformly rather than inconsistently.**
