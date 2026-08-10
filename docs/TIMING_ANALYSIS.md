# Hardware Control Architecture -- Timing, Delay, Timeout, Polling, and Settling-Time Analysis

> **Partially resolved notice:** the top-priority finding below (no
> cancellation checkpoint inside `hold_s`/`STABILIZATION_S` dwells while
> hardware remains energized) has been fixed -- see `docs/architecture.md`
> Section 27 ("Interruptible Wait Mechanism") and `docs/MILESTONES.md` for
> the implementation and verification record. The "no engineered relay
> contact settling time" finding (Section 4, and the Section 9/10
> inconsistencies it caused) has also been fixed -- see
> `docs/architecture.md` Section 43 ("Single Global Relay Settling/Dead-Time
> Constant"): `Settings.RELAY_SETTLE_TIME_S` is now `2.0` s, enforced
> unconditionally in `hardware/relay.py::RelayBase.open()`/`close()` for
> every relay switch in the application, never `0`. A follow-up gap in
> that fix -- `test.py::test_relay_ethernet_test()` bypasses `open()`/
> `close()` by design (Section 24's documented exception) and so received
> no settle delay -- was found from real-hardware behavior and fixed; see
> `docs/architecture.md` Section 44. The other findings in
> this document (dead `sample_rate_hz` config, no DMM/DAQ timeout
> configuration, no `SafetyMonitor.check()` debounce, the first-`initiate()`
> measurement transient) remain open -- see `docs/TODO.md`. This document
> is kept as the historical record of the review, not rewritten in place.

Analysis-only document. No code was modified to produce this review. Every
`time.sleep()`, `timeout`, retry loop, polling loop, verification cycle,
settling interval, and communication latency in the hardware control path
was located and is catalogued below, category by category, with the exact
value, its source, and an assessment of whether it is a real risk or an
acceptable, already-understood characteristic.

---

## Executive summary

**The single most significant finding:** cancellation (Ctrl+C) responsiveness
during a PSU dwell is currently **zero** for the exact duration of that
dwell -- there is no cancellation checkpoint inside
`SMU.source_dc_voltage_point()`'s `hold_s` sleep (`ProtoTestSequence`) or
inside `ChargeCycle`/`DischargeCycle`'s pre-loop `STABILIZATION_S` sleep.
Today this is a bounded, already-known ~5-6 second gap (documented in
`docs/MILESTONES.md` Milestone 2 and `docs/architecture.md` Section 13.7).
**It becomes materially more important before Charge/Discharge Battery are
implemented**, because `Settings.PROTO_TEST_DWELL_S` is explicitly marked
`TEMPORARY -- shortened for the first physical rack validation run. Restore
to 120.0 (~2 min) once the quick end-to-end check passes` -- at that
restored value, an operator's Ctrl+C during a dwell could go unnoticed for
up to ~2 minutes. This is the top item to address before real Charge/
Discharge sourcing exists (see Recommendations).

**Second finding:** the first-relay/SMU measurement transient (no explicit
settling delay between NI-DCPower `session.initiate()` and the first
`session.measure()` call) is a pre-existing, already-documented,
deliberately-deferred gap (`docs/architecture.md` Section 18,
`docs/MILESTONES.md` Milestone 2) -- confirmed still present in the current
source; not new, not modified by this review.

**Third finding:** `config/devices.py`'s per-DAQ `sample_rate_hz` field
(`PXI_SLOTS[2]`, `DAQ_CONFIGS[...]`) is dead configuration -- `hardware/daq.py::DAQ`
never reads it. The sampling rate that actually governs `ChargeCycle`/
`DischargeCycle`'s polling loop comes from the unrelated
`Settings.SAMPLE_RATE_HZ`. Two same-named concepts, only one of which is
wired to anything.

Everything else catalogued below is either a small, bounded, well-understood
latency (Telnet/socket round trips, a single reconnect-and-retry) or a
manually-paced operator prompt (`input()`) with no timeout at all by design.

---

## 1. SMU / PSU (`hardware/smu.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| `hold_s` (post-measurement dwell, output stays enabled) | Caller-supplied, default `0.0` | `source_dc_voltage_point(hold_s=...)` parameter | `ProtoTestSequence` passes `Settings.PROTO_TEST_DWELL_S` (currently `5`, marked temporary; intended production value `120`). SMU Functional Validation (`test.py::_functional_smu()`) never passes `hold_s` -- always `0.0`. |
| `during_hold` callback timing | Whatever the callback itself takes (a DMM read) | `source_dc_voltage_point(during_hold=...)` | Runs once, immediately after the SMU's own `measure()`, BEFORE the `hold_s` sleep -- so a slow DMM read effectively extends the total dwell by its own duration, uncounted. |
| **No cancellation checkpoint inside `hold_s`'s `time.sleep(hold_s)`** | N/A | `source_dc_voltage_point()` | **Finding.** `ProtoTestSequence.run()` checks cancellation once per relay, before `relay.close()` -- never during the SMU's own dwell. At `PROTO_TEST_DWELL_S`'s temporary value (5s) this is a small gap; at its documented intended value (120s) this becomes a ~2-minute worst-case cancellation latency. See Recommendations. |
| Config readback tolerance (not a time value, but a verification bound) | `SMU_VOLTAGE_READBACK_TOLERANCE_V = 1e-4` / `SMU_CURRENT_READBACK_TOLERANCE_A = 1e-4` | `config/settings.py` | An attribute round-trip bound (IEEE-754/instrument-coercion), not a settling-time or measurement-accuracy figure -- see that constant's own extensive docstring. Not itself a delay, but relevant context: no `time.sleep()` exists between `commit()` and reading these attributes back -- the readback is the *stored setpoint* (an IVI property), not a new ADC conversion, so no settling time is physically needed for that specific readback. |
| Settling time between `session.initiate()` and the first `session.measure()` | **None configured** | `source_dc_voltage_point()` | **Pre-existing, already-documented gap** (`docs/architecture.md` Section 18, `docs/MILESTONES.md` Milestone 2) -- produced a one-time measurement transient on the very first relay of the physical rack validation run (the session's first-ever `commit()`/`initiate()` cycle). Deliberately left unfixed pending Battery Integration, where real load/settling dynamics should inform the right fix. Confirmed still present, unchanged by this review or the recent PSU Safety Verification Pattern work (that work added a check-current-state step *before* configuration; it did not touch `initiate()`/`measure()` timing). |
| `emergency_output_off()` / `force_output_off_and_verify()` timing | No sleep -- COMMAND then immediate READBACK+VERIFY | `hardware/smu.py` | Purely a synchronous session-attribute round trip (no ADC settling needed for a *stored setpoint* readback -- see above); as fast as the NI-DCPower driver's own attribute I/O. |
| Bench-workflow operator pacing (`_functional_smu()`) | Unbounded -- waits on `input()` | `test.py::_functional_smu()` | "Press Enter when ready to begin", "Verify reading on handheld DMM, then press Enter to continue" -- these are manually-paced checkpoints, not timers; Ctrl+C during one of these prompts is caught immediately (Python's own `KeyboardInterrupt` on `input()`), unlike the `hold_s` sleep above. |

**Charge/Discharge-specific (not yet real, `SMU.output_enable()`/`set_charge_mode()`/`set_discharge_mode()` are TODO stubs):** no timing exists yet for the real charge/discharge sourcing path itself -- only the *test harness* around it (`ChargeCycle`/`DischargeCycle`, Section 6 below) has real timing today, driving a stub SMU.

---

## 2. DMM (`hardware/dmm.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| Measurement aperture/integration time | **Not explicitly configured** -- `configure_measurement_digits(..., resolution_digits=5.5)` | `measure_dc_voltage()` | NI-DMM derives its own internal aperture time from the requested resolution (5.5 digits); no explicit aperture/settling override, no explicit `read(timeout=...)` value passed to `session.read()` -- relies on the `nidmm` driver's own default read timeout. |
| Connect timeout | **Not configured** -- `nidmm.Session(resource_name=...)` with no timeout option | `connect()` | Relies entirely on the `nidmm` driver's own connection defaults; no project-level override exists. |
| Self-test timing | Whatever the instrument's built-in self-test takes | `identify()` | Synchronous, blocking call; no software timeout wraps it beyond the driver's own default. |

**Finding:** the DMM (and DAQ, below) have no project-configured timeout for their instrument I/O calls -- every SMU/relay call in this codebase has an explicit, documented timeout (`SMU_*_READBACK_TOLERANCE`, relay's `timeout: 5.0`), but DMM/DAQ calls rely entirely on their respective NI driver's built-in defaults, which are not documented or overridden anywhere in this project. Low real-world risk (both are simple, fast, on-demand reads), but worth a deliberate decision (accept the driver defaults explicitly, or configure them) before Charge/Discharge sourcing depends on DMM readings inside a time-bounded loop.

---

## 3. DAQ (`hardware/daq.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| `read_channel()` acquisition | Single on-demand read, `nidaqmx.Task()` created/destroyed per call | `read_channel()` | No explicit sample clock, no explicit settling delay between `add_ai_voltage_chan()` and `task.read()` -- relies on nidaqmx's own default single-sample software-timed acquisition (which includes the ADC's own internal settling as part of the conversion) and default read timeout (not overridden). A fresh `Task()` per call also means channel (re)configuration overhead is paid on every single read -- no task reuse/caching. |
| `sample_rate_hz` (`config/devices.py::PXI_SLOTS[2]`/`DAQ_CONFIGS[...]`) | `1.0` | `config/devices.py` | **Finding: dead configuration.** `hardware/daq.py::DAQ` never reads `self._cfg`'s `sample_rate_hz` at all (only `voltage_range_v`/`resource`/`model`) -- this field currently has no effect on anything. The value that actually governs `ChargeCycle`/`DischargeCycle`'s polling loop is the unrelated `Settings.SAMPLE_RATE_HZ` (Section 6). Two same-named "sample rate" concepts exist; only one does anything. |
| `read_all_batteries()`/`verify_zero_current()` | N/A -- still TODO stubs, return fixed placeholder values instantly | `hardware/daq.py` | No real multi-channel synchronized acquisition (and therefore no real timing characteristics) exists yet; `ChargeCycle`/`DischargeCycle` call this stub today, so their own loop timing (Section 6) is currently the only real timing in that path. |

---

## 4. Numato Relay Matrix (`hardware/relay_eth.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| Socket/Telnet operation timeout | `5.0` s default (`cfg.get("timeout", 5.0)`) | `config/devices.py` (`ETHERNET_DEVICES["MATRIX_NUMATO_201"/"_202"]["timeout"]`, both `5.0`) | Applies to TCP connect (`sock.settimeout()`), and as the default deadline for `_recv_until()`'s login/command waits. |
| Internal poll granularity while waiting for a response | `0.2` s (`self._sock.settimeout(0.2)` inside `_recv_until()`'s loop) | `_recv_until()` | The deadline itself is the real timeout (5.0s default); this 0.2s is just how often the loop re-checks `time.monotonic() < deadline` between short blocking `recv()` attempts -- worst-case added latency to detecting a real timeout is ~0.2s, negligible. |
| Reconnect-and-retry (`_call_with_reconnect()`) | Exactly one reconnect + one retry, no backoff/jitter sleep between attempts | `_call_with_reconnect()` | Worst-case total latency for one failed command that recovers: one failed attempt (bounded by the 5.0s timeout) + one full `connect()` (TCP connect + 3-step login handshake, each step bounded by 5.0s = up to ~15-20s) + one retried command (bounded by 5.0s) -- **~25-30s worst case**, always bounded, never an infinite/unbounded retry loop, no exponential backoff (a single deterministic attempt, by design -- see the driver's own docstring on why blind retry is safe here: every Numato command is idempotent). |
| Mandatory safety sequence timing (per Section 24 of `docs/architecture.md`, the recent Relay Safety Verification Pattern work) | No sleep at all -- `check_current_relay_state()` (Read All -> Verify) then `write_all(0)`/`verify_all(0)` (Force Off -> Verify) then, for `close()`, `write()`/`verify_single()`/`verify_all()` (Action -> Verify) | `_force_all_off_and_verify()`, `close()` | Every step is a synchronous Telnet round trip (bounded by the 5.0s timeout above); no artificial delay is added between "relay on/off" and the subsequent "relay read"/"relay readall" verification. |
| **Mechanical relay contact settling time** | **Not explicitly modeled** | `close()`/`open()` | A physical relay's contact needs a small amount of time (typically milliseconds, sometimes with contact bounce) to reach its final mechanical state after being commanded. This codebase reads back the state immediately via the next Telnet round trip -- there is no deliberately engineered "wait N ms for the contact to settle" delay; whatever incidental delay the Telnet round trip itself provides is not a designed safety margin. Real-rack validation (Milestone 2, `docs/MILESTONES.md`) reports no observed issue from this, but it is worth naming as an *implicit, unverified* margin rather than a configured one, especially as dwell times/switching cadence change for real Charge/Discharge cycling. |
| Emergency all-off reflex (`_emergency_all_off()`) | No sleep -- one native `writeall`+`readall` pair, synchronous | `_emergency_all_off()` | Bounded by the same underlying socket timeout; single, non-recursive, best-effort. |

---

## 5. PXI Relay Matrix

**No timing exists.** `_identify_switch()` (`test.py`) always reports N/A -- there is no `niswitch`-based driver in this codebase (see `docs/architecture.md` Section 23d for the planned future reuse architecture). Nothing to analyze here yet; this section is a placeholder for when a real driver exists, at which point it should inherit the same Relay Safety Verification Pattern (Section 24) and its timing characteristics, per that section's documented plan.

---

## 6. SafetyMonitor-related timing (`test_control/safety_monitor.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| `SafetyMonitor.check()` | **Instantaneous, single-sample, no debounce/averaging window** | `safety_monitor.py::check()` | A single voltage/current/temperature reading over any limit trips `SafetyStatus(False, ...)` immediately -- there is no "N consecutive violations" or moving-average filter. This is a deliberate simplicity/safety-first choice (never risk masking a real violation by waiting to confirm it), but it does mean a single noisy ADC sample (at `Settings.SAMPLE_RATE_HZ` = 1 Hz, i.e. once per second in `ChargeCycle`/`DischargeCycle`) could in principle trip a stop. No timing fix is implied here -- flagged for awareness, not as a defect (the project's stated safety philosophy consistently favors false-trip-safe over risk-masking). |
| `is_safe_to_switch_relay()` | Instantaneous threshold compare (`ZERO_CURRENT_THRESHOLD_A = 0.01` A) | `safety_monitor.py` | No timing component -- a pure comparison against the most recent current reading, whatever its age (the caller decides how fresh that reading is). |
| `emergency_stop()`/`safe_cancel_shutdown()` | No sleep -- PMU `emergency_output_off()` then relay `open_all()`, both synchronous | `safety_monitor.py` | Total latency = SMU output-disable-and-verify latency (near-instantaneous attribute round trip) + relay `open_all()` latency (one Telnet round trip, bounded by the 5.0s relay timeout, per Section 4). |

---

## 7. HardwareManager startup/shutdown timing (`test_control/hardware_manager.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| Connect sequence (`connect_all()`) | No artificial delay between devices -- DAQ -> SMU (+ PMU startup safety check) -> DMM -> Relay (+ relay startup safety check), each device's `connect()` called immediately after the previous | `_connect_all_strict()`/`_connect_all_lenient()` | Total startup latency = sum of each device's own `connect()` latency (DAQ: NI-DAQmx enumeration, near-instant; SMU: NI-DCPower session open, near-instant; DMM: NI-DMM session open, near-instant; Relay: TCP connect + Telnet login handshake, bounded by the ~5-20s worst case in Section 4) -- no additional project-level delay is inserted anywhere in this chain. |
| Shutdown sequence (`disconnect_all()`) | No artificial delay -- PMU output-off+verify, then relay `open_all()`, then each device's `disconnect()` in sequence | `disconnect_all()` | Same bounds as above; errors are logged, never re-raised, so one slow/failing device cannot block the others' disconnect attempts (no timeout wraps the whole sequence as a unit, but each device's own operation is individually bounded). |
| `_atexit_relay_shutdown()` backstop | No sleep -- one `relay.open_all()` call if still connected | `hardware_manager.py` | Runs at Python's `atexit` time; bounded by the same relay timeout as any other `open_all()` call. |

---

## 8. `ProtoTestSequence` (`test_control/proto_test_sequence.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| Per-relay dwell (`dwell_s`) | `Settings.PROTO_TEST_DWELL_S` = **5s (marked TEMPORARY; intended production value 120s -- "~2 min")** | `config/settings.py`, passed to `smu.source_dc_voltage_point(hold_s=dwell_s, ...)` | See Section 1's finding -- no cancellation checkpoint exists during this dwell. At the temporary 5s value this is a small window; **at the documented intended 120s value, this is a ~2-minute cancellation blind spot per relay.** |
| Cancellation checkpoint granularity | Once per relay, before `relay.close(relay_n)` | `ProtoTestSequence.run()` | Never checked mid-relay (correct, per the project's "checkpoints only between atomic hardware operations" rule) -- but also never checked during the dwell itself, which is the gap above. |
| DMM read during hold (`during_hold`) | Whatever `dmm.measure_dc_voltage()` takes (near-instant) | `_read_dmm()` closure | Runs once per relay, immediately after the SMU's own measurement, before the `hold_s` sleep -- adds its own (small, bounded) latency on top of `hold_s`. |
| Relay activation timing per relay | Bounded by Section 4's relay timing (~5s typical-case, up to ~25-30s worst-case on a communication fault requiring reconnect) | `relay.close(relay_n)`/`relay.open(relay_n)` | Sequential per relay across `Settings.ACTIVE_CHANNELS` (8 positions) -- total worst-case wall-clock for a full 8-relay sweep at the *intended* 120s dwell would be on the order of 8 * (120s + relay overhead) ≈ 16+ minutes. |

---

## 9. `MonitorBatterySequence` (`test_control/monitor_battery_sequence.py`)

| Timing element | Value | Source | Notes |
|---|---|---|---|
| Monitoring sample interval | `sample_interval_s: float = 2.0` (default parameter, not currently sourced from `Settings` -- hardcoded default in the method signature) | `MonitorBatterySequence.run()` | **Finding:** this is the one polling-interval value in the codebase that is a plain Python default parameter rather than a `Settings` constant -- every other cadence value (`SAMPLE_RATE_HZ`, `PROTO_TEST_DWELL_S`, `STABILIZATION_S`) lives in `config/settings.py`. Not a bug (the caller, `test.py::_run_monitor_battery()`, never overrides it, so it is always `2.0`), but an inconsistency worth normalizing if this value ever needs to be tuned without a code change. |
| Cancellation checkpoint granularity | Once per loop iteration, before `dmm.measure_dc_voltage()` | `MonitorBatterySequence.run()` | Worst-case Ctrl+C latency ≈ one `sample_interval_s` (2s) + the DMM read's own (small) latency -- acceptable, matches the loop-body-bounded pattern used elsewhere. |
| Relay close/open timing | Bounded by Section 4 | `relay.close(relay_address)`, and `open_all()` via the safety-shutdown path on exit | Same characteristics as every other relay path -- now including the Section 24 pre-check (Read All -> Verify Current Status) added by the recent Relay Safety Verification Pattern work. |

---

## 10. Relay validation tests (`test.py`)

| Test | Per-channel/relay timing | Notes |
|---|---|---|
| "Relay 1 quick check" (`_run_relay_numato_matrix_test`) | Ping (`timeout_s=1.0`, subprocess bounded to `timeout_s+2`=3.0s) -> web-interface check (`timeout_s=2.0`) -> connect+login (bounded by Section 4) -> one relay's read/close/open (bounded by Section 4) | No inter-step artificial delay; each step's own bound is additive to the total. |
| Matrix Scan (`_run_relay_matrix_scan`, group-scoped) | No artificial delay between channels -- `close(ch)` (includes the new Section 24 pre-check) -> `read(ch)` -> `open(ch)`, immediately repeated for the next channel in the scoped range | Cancellation checked once per channel, before `close(ch)`. Total scan time for a full 32-channel sweep ≈ 32 * (per-relay Section 4 latency), no configured pacing/settle time between channels. |
| RelayEthernetTest (native primitives) | Same shape (`check_current_relay_state()` (new, Section 24) -> `write_all(0)`/`verify_all(0)` -> `write()`/`verify_all()` -> `write_all(0)`/`verify_all(0)`), per relay index, no artificial delay | Fails and stops immediately on first mismatch -- no retry beyond the driver's own single reconnect-and-retry (Section 4). |
| Safety Self-Test (`test_relay_safety_selftest`) | `close(ch)` -> `open(ch)` per channel, no artificial delay, stops immediately on first failure | Re-enables `hardware/relay_eth.py`'s DEBUG logging for the duration (a logging verbosity change, not a timing change). |

**Common observation across all four:** none of these commissioning tests insert a deliberate settle/pace delay between relays -- the only "waiting" is the incidental Telnet round-trip time per command. This has not caused an observed problem on the physical rack (per `docs/MILESTONES.md`), but is worth keeping in mind if switching cadence ever needs to be intentionally slowed (e.g. for a relay bank with a longer mechanical settle time than the one validated so far).

---

## 11. NTC acquisition paths

| Timing element | Value | Notes |
|---|---|---|
| Software conversion (`ntc_voltage_to_celsius()`) | Instantaneous, pure math | No timing component at all. |
| DAQ read feeding the conversion | Same as Section 3 (`DAQ.read_channel()`) -- no explicit settle time | The NTC divider circuit itself has a **thermal** settling time (the thermistor needs time to reach equilibrium with the cell/ambient it's measuring) that is a physical property, not a software one -- nothing in this codebase models or waits for thermal equilibration before taking an NTC reading (unlike `STABILIZATION_S`, which is an explicit *electrical* settle dwell for voltage/current after a relay switch). This is worth a deliberate decision once real per-channel NTC acquisition is wired in (`test.py::test_sensors()`'s Test 6, per `docs/architecture.md` Section 23a) -- whether a dwell is needed before trusting an NTC reading after a channel/relay change. |
| `charge_cycle.py`/`discharge_cycle.py`'s `t_c` | Always `None` (`# TODO: get temperature from NTC channel`) | No real NTC timing exists in the actual charge/discharge polling loop yet -- only in the standalone Test Sensors DAQ scan (Section 23a). |

---

## 12. Common hardware abstractions

| Element | Value | Notes |
|---|---|---|
| `hardware/base.py::HardwareBase` | No timing at all | Just `name`/`connected`/`log` bookkeeping -- no shared timeout/retry logic lives here (each driver implements its own, as catalogued above). |
| `hardware/relay.py::RelayBase` | No timing at all | Abstract interface only; `open_all()`/`close_all()`'s default loop-per-channel implementation has no inter-channel delay (irrelevant today since `NumatoRelayMatrix` overrides both with real bulk operations). |
| `hardware/relay_factory.py::RelayFactory` | No timing at all | Pure dispatch, no I/O. |
| `utils/cancellation.py::CancellationToken` | No timing at all | A plain flag; its *effective* responsiveness is entirely a function of where callers place `check_cancellation()` checkpoints (catalogued per-sequence above) -- this module itself introduces no delay or polling. |
| `hardware/relay_serial.py::SerialRelay` | `timeout`/`timeout_s` default `2.0`s | Diagnostic-only, `RELAY_SERIAL_CONFIGS == {}` today (unreachable) -- see `docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`. |
| `hardware/relay_matrix.py::RelayMatrix` | `timeout_s: float = 2.0` (constructor default) | Dead legacy scaffold, unreferenced anywhere -- see `docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`. |
| `hardware/simulated.py` (`SimulatedSMU`/`SimulatedDAQ`/`SimulatedRelay`) | No timing at all -- every method returns instantly | Foundations-only, not wired into `RelayFactory`/`HardwareManager` yet. |

---

## 13. Timing-related constants in `config/settings.py` (complete list)

| Constant | Value | Consumed by |
|---|---|---|
| `STABILIZATION_S` | `5.0` s | `ChargeCycle.run()`/`DischargeCycle.run()` -- pre-loop dwell after `output_enable()`, **before** the sampling loop begins. **No cancellation checkpoint during this sleep** (see below). |
| `SAMPLE_RATE_HZ` | `1.0` Hz (`dt = 1/SAMPLE_RATE_HZ` = 1.0s) | `ChargeCycle`/`DischargeCycle`'s sampling loop `time.sleep(dt)`. Cancellation IS checked once per loop iteration (at the top, before the next `dt` sleep), so worst-case latency ≈ one `dt`. |
| `CHARGE_TIMEOUT_S` / `DISCHARGE_TIMEOUT_S` | `7200` s (2h) each | Loop-exit ceiling in `ChargeCycle`/`DischargeCycle` -- returns `False` (not an exception) if exceeded; this is a *maximum runtime* guard, not a settling/polling value. |
| `PROTO_TEST_DWELL_S` | `5` s, explicitly marked TEMPORARY (intended `120.0`) | `ProtoTestSequence.run()`'s per-relay `hold_s`. See Sections 1/8's finding. |
| `RELAY_TIMEOUT_S` | `2.0` s | `config/settings.py`'s serial-relay diagnostic constant -- **not actually read by `hardware/relay_serial.py`** (that driver reads `cfg.get("timeout", cfg.get("timeout_s", 2.0))` from its OWN config dict, which happens to default to the same `2.0` value independently, not by referencing this `Settings` constant). Another small naming/wiring inconsistency, low impact since the diagnostic relay path is unreachable today (`RELAY_SERIAL_CONFIGS == {}`). |
| `SMU_VOLTAGE_READBACK_TOLERANCE_V` / `SMU_CURRENT_READBACK_TOLERANCE_A` | `1e-4` V / `1e-4` A | `SMU._verify_config_readback()` -- a value tolerance, not a time value; included here only because it is the one other "verification bound" constant closely associated with the settling-time discussion in Section 1. |
| `ZERO_CURRENT_THRESHOLD_A` | `0.01` A | `SafetyMonitor.is_safe_to_switch_relay()` -- a value threshold, not a time value; included for completeness since it gates *when* a relay switch is considered safe. |

**Constants NOT consumed where their name suggests (both already noted above, repeated here for the consolidated list):** `config/devices.py`'s per-DAQ `sample_rate_hz` (dead -- Section 3), `Settings.RELAY_TIMEOUT_S` (not actually referenced by `hardware/relay_serial.py`'s own default -- coincidentally matching value, not a real wiring).

---

## Recommendations (not implemented -- analysis only, per this review's scope)

1. **Add a chunked/interruptible sleep for `hold_s` in `source_dc_voltage_point()`, and for `STABILIZATION_S` in `ChargeCycle`/`DischargeCycle`.** Both currently use one uninterrupted `time.sleep(N)` with no cancellation checkpoint inside it. A small helper (sleep in short increments, e.g. 0.5-1s slices, calling `check_cancellation(token)` between slices) would bound worst-case Ctrl+C latency to that slice size instead of the full dwell -- directly relevant before `PROTO_TEST_DWELL_S` is restored to its intended 120s, and before Charge/Discharge Battery reuse the same `STABILIZATION_S` pattern for real.
2. **Decide, before Charge/Discharge Battery real sourcing is built, whether `SafetyMonitor.check()` should gain a debounce/consecutive-violation option**, or whether the current single-sample-trips-immediately behavior is the permanent, intentional design (it is consistent with the project's stated safety philosophy either way -- this is a decision to make explicitly, not a defect to fix).
3. **Remove or wire up `config/devices.py`'s dead `sample_rate_hz` DAQ field** -- either delete it (if `DAQ.read_channel()`'s single-on-demand-read model is permanent) or actually configure a sample clock with it (if multi-channel synchronized acquisition, `read_all_batteries()`, is built to use it).
4. **Normalize `MonitorBatterySequence.run()`'s `sample_interval_s` default into a `Settings` constant** (e.g. `Settings.MONITOR_SAMPLE_INTERVAL_S`), matching every other cadence value in the codebase, so it can be tuned without a code change.
5. **Explicitly document (or configure) the DMM/DAQ instrument-I/O timeout defaults** currently left to the `nidmm`/`nidaqmx` driver libraries, the same way relay/SMU timeouts are already explicit project constants -- low risk today, but worth closing before a real-time-bounded Charge/Discharge loop depends on a DMM reading.
6. **When Charge/Discharge Battery real sourcing is implemented, decide explicitly whether the first-`initiate()` measurement transient (Section 1) needs a real fix** (a small settling delay before the first `measure()`, informed by real battery-load dynamics) rather than continuing to defer it -- this was always intended to be revisited "once Battery Integration's real load/settling dynamics are known" (`docs/architecture.md` Section 18), and that time is now approaching.

None of the above were implemented as part of this review, per its explicit framing as a full understanding/analysis pass ahead of Charge/Discharge Battery implementation work, not an implementation task itself.
