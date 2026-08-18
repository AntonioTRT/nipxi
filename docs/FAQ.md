# NIPXI Pre-Hardware-Validation Architecture / Operational FAQ

This is a living FAQ built directly from code inspection of the current
NIPXI codebase (not from design intent or prior documentation claims),
performed as a pre-hardware-validation review. Every entry below states
what the code actually does today, cites the exact file/line evidence,
and calls out gaps plainly rather than speculating. Where a question is
not addressed by any code path, that is stated as "Not Implemented" and
explained -- this document does not paper over gaps with recommended
future behavior presented as current behavior.

Primary files reviewed: `test_control/battery_operation_sequence.py`,
`test_control/charge_sequence.py`, `test_control/discharge_sequence.py`,
`test_control/safety_monitor.py`, `test_control/hardware_manager.py`,
`config/devices.py`, `utils/validators.py`, `utils/errors.py`,
`utils/cancellation.py`, `utils/stop_reason.py`, `hardware/smu.py`,
`hardware/relay_eth.py`, `data/storage.py`, `data/sqlite_manager.py`,
`test.py`, plus `docs/architecture.md`, `docs/TODO.md`,
`docs/MILESTONES.md`, `docs/DATABASE_ROADMAP.md`.

Scope note: `ChargeSequence`/`DischargeSequence` (built on
`BatteryOperationSequence`) are the current, real execution path for
Charge/Discharge Battery, invoked from `test.py::_run_charge_or_discharge()`.
The legacy `charge_cycle.py`/`discharge_cycle.py`/`TestExecutor`/
`BatteryTestSequence` path is not the active path for these workflows and
is out of scope except where explicitly noted.

---

## SECTION 1 — RELAY HANDLING

### Q: What happens if a relay activation command is sent to a relay that is already active?

**Status:** Implemented

**Answer:** `close(channel)` always forces every relay OFF and verifies an all-off baseline BEFORE activating the requested channel — it never activates a relay "on top of" an already-active one. If the previously-active relay is a *different* channel than requested, it gets forced off as part of this sequence (logged as an unexpected pre-existing state), then the requested relay is activated alone.

**Evidence:**
- hardware/relay_eth.py:412-447 — `NumatoRelayMatrix.close()`: force-all-off → verify → activate requested → verify single → verify all
- hardware/relay_eth.py:590-638 — `check_current_relay_state()` logs a WARNING if the bank isn't already all-off before the forced baseline

**Risks:** None identified for the intended single-relay-at-a-time design.

**Recommendation:** None.

### Q: Can two relays belonging to the same group be active simultaneously?

**Status:** Implemented (prevented)

**Answer:** No. `close()`'s mandatory sequence (force-all-off → verify → activate one → verify single → verify all) makes it structurally impossible for two relays on the same matrix to be active at once — `verify_all(expected_mask)` checks the *entire* bank matches a single-bit mask. `close_all()` is explicitly disallowed and raises `RelayError`.

**Evidence:**
- hardware/relay_eth.py:412-447 — `close()`
- hardware/relay_eth.py:462-472 — `close_all()` raises `RelayError("...only one relay may be energized at a time...")`

**Risks:** None identified.

**Recommendation:** None.

### Q: Can relays from different groups be active simultaneously?

**Status:** Implemented (by construction, not by explicit cross-group interlock)

**Answer:** Groups A/B correspond to physically distinct Numato relay matrices (`MATRIX_NUMATO_201`/`202`), each a separate `NumatoRelayMatrix` instance with its own single-relay-at-a-time interlock. There is no code-level cross-group interlock, but each `HardwareManager`/`ChargeSequence`/`DischargeSequence` invocation from `test.py` connects and drives only ONE relay matrix for ONE operator session at a time — the CLI is single-threaded/single-session, so two groups cannot be driven concurrently within one process invocation today.

**Evidence:**
- config/devices.py:559-596 — `BATTERY_GROUPS["A"]["relay_matrix"]="MATRIX_NUMATO_201"`, `["B"]["relay_matrix"]="MATRIX_NUMATO_202"`
- test.py:4029 — one `HardwareManager` per `_run_charge_or_discharge()` invocation, one relay matrix

**Risks:** If a future multi-process/multi-station deployment runs two `test.py` instances concurrently against Group A and Group B, nothing in this codebase prevents that (no file lock, no shared arbitration) — currently a theoretical risk since only Group A has real hardware.

**Recommendation:** Document that concurrent multi-instance execution against different groups is unverified/unsupported until a cross-process lock exists.

### Q: What protections exist to prevent multiple battery paths from being connected to the same SMU?

**Status:** Implemented (structurally, for the single-SMU-per-group model)

**Answer:** `hardware_for_group()` resolves exactly one SMU per group, and `NumatoRelayMatrix`'s single-relay-at-a-time interlock guarantees only one channel's relay is closed (and thus wired to that SMU) at any time. Multi-SMU channel assignment does not exist yet — every channel in Group A shares `PRIMARY_SMU` exclusively, gated by the same relay interlock.

**Evidence:**
- config/devices.py:350-366 — `SMU_ASSIGNMENTS`, one entry connected per `HardwareManager` (config/devices.py comment: "HardwareManager itself still only ever connects ONE SMU")
- hardware/relay_eth.py:412-447 — single-relay-at-a-time `close()`

**Risks:** None identified for the current single-SMU-per-group model.

**Recommendation:** None.

### Q: Is relay state verified after every activation and deactivation?

**Status:** Implemented

**Answer:** Yes. `close()` verifies via `verify_single()` (individual "relay read N") AND `verify_all()` (bank-wide "relay readall"). `open()`/`open_all()` verify via `verify_all(0)`. There is no relay state change anywhere in this codebase that skips readback verification.

**Evidence:**
- hardware/relay_eth.py:396-447 — `open()`/`close()`
- hardware/relay_eth.py:509-564 — `verify_single()`/`verify_all()`

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if relay state verification fails?

**Status:** Implemented

**Answer:** A mismatch in `verify_single()`/`verify_all()` first triggers a best-effort `_emergency_all_off()` (raw "relay writeall 00.../relay readall", bypassing the normal API to avoid recursion), then always raises `RelayStateVerificationError` regardless of whether the emergency attempt succeeded — the original mismatch is never swallowed. `BatteryOperationSequence.run_guarded()` catches `RelayError` (the parent class), records `StopReason.FAILED`, finishes `run_summary` as FAIL, and calls `safety.emergency_stop()`.

**Evidence:**
- hardware/relay_eth.py:509-564 — `verify_single()`/`verify_all()`
- hardware/relay_eth.py:771-813 — `_emergency_all_off()`
- test_control/battery_operation_sequence.py:133-145 — `run_guarded()`'s `except RelayError` branch

**Risks:** If the emergency all-off itself also fails (no working connection), the exception message says so and logs CRITICAL, but there is no automated escalation (e.g. alarm/notification) beyond the log — an operator must be watching.

**Recommendation:** Consider an audible/visual alarm trigger on a CRITICAL "hardware may still be energized" log line, not just a log entry.

### Q: What happens if a relay does not respond?

**Status:** Implemented

**Answer:** Any Numato command failure (timeout, connection drop, unparseable response) raises `RelayError`/`NIPXITimeoutError` inside `_call_with_reconnect()`, which attempts exactly one reconnect + one retry. If both fail, `_emergency_all_off()` is attempted (best-effort) and the original error re-raised as `RelayError`, propagating up to `run_guarded()`'s safety shutdown.

**Evidence:**
- hardware/relay_eth.py:724-769 — `_call_with_reconnect()`
- hardware/relay_eth.py:870-910 — `_recv_until()` raises `NIPXITimeoutError` with the accumulated buffer on deadline

**Risks:** Only one reconnect attempt is made — a flaky network that needs 2+ retries to recover will fail the operation even though the hardware itself is fine. This is a deliberate design choice (documented as bounding worst-case time), not an oversight.

**Recommendation:** None beyond what's documented; the one-retry bound is a reasonable safety tradeoff.

### Q: Is there a mandatory delay between relay state transitions?

**Status:** Implemented

**Answer:** Yes. `hardware/relay.py::RelayBase.open()`/`close()` are concrete (not overridable) and unconditionally block for `Settings.RELAY_SETTLE_TIME_S` (`2.0` s) via `RelayBase.settle()` immediately after the driver-specific `_open_impl()`/`_close_impl()` returns and is verified — before control returns to the caller. Because every production/validation relay path calls `open()`/`close()` (or, for the one documented native-primitive exception, calls `settle()` explicitly — see next entry's evidence and `docs/architecture.md` Section 24/44), no subsequent relay action can begin less than 2.0 s after the previous one completed. This was previously true only for `ChargeSequence`/`DischargeSequence`'s unrelated `STABILIZATION_S` electrical-settling wait; Monitor Battery, the relay validation/hardware-validation scans, and `RelayEthernetTest`'s native-primitive loop had **no delay at all** until this fix (see `docs/architecture.md` Sections 43-44 for the audit that found and corrected this).

**Evidence:**
- hardware/relay.py — `RelayBase.open()`/`close()`/`settle()`
- hardware/relay_eth.py — `NumatoRelayMatrix._open_impl()`/`_close_impl()` (renamed from `open()`/`close()`)
- test.py::test_relay_ethernet_test() — calls `relay.settle()` explicitly after each native `write()`/`write_all()`, since this test bypasses `open()`/`close()` by design

**Risks:** `2.0` s is a chosen safety-floor value, not yet confirmed against the Numato relay bank's real mechanical settling time on hardware — see `docs/TIMING_ANALYSIS.md` Recommendations.

**Recommendation:** Confirm on first real hardware validation whether `2.0` s is sufficient (or excessive) for the physical relay's actual contact settling time; adjust the single `Settings.RELAY_SETTLE_TIME_S` constant if so — never add a second, path-specific value.

### Q: Is relay settling time configurable?

**Status:** Implemented

**Answer:** Yes. `Settings.RELAY_SETTLE_TIME_S` (`config/settings.py`) is the single global relay settling/dead-time constant, currently `2.0` s. It is the only relay-timing setting in the codebase and is enforced centrally in `RelayBase.open()`/`close()` — there is no per-workflow override and no second relay-timing constant anywhere. `RelayBase.settle()` raises `ValidationError` if it is ever configured `<= 0`, so a 0 s value can never silently take effect.

**Evidence:**
- config/settings.py — `RELAY_SETTLE_TIME_S = 2.0`
- hardware/relay.py::RelayBase.settle() — reads the constant, enforces `> 0`

**Risks:** None beyond the previous entry's (real-hardware confirmation of the value itself).

**Recommendation:** None; already implemented as the single source of truth.

### Q: What happens if cancellation occurs during relay switching?

**Status:** Partially Implemented

**Answer:** `check_cancellation(token)` checkpoints exist immediately BEFORE `relay.close(relay_address)` in both `ChargeSequence`/`DischargeSequence`, but there is deliberately no checkpoint INSIDE the relay's force-off→activate→verify sequence itself — `utils/cancellation.py`'s own docstring states checkpoints "must only ever be placed BETWEEN atomic hardware operations — never inside a relay activate/verify sequence." So a cancellation requested during an in-flight relay operation is not interrupted; it is honored at the next checkpoint (e.g. before the sampling loop's first iteration).

**Evidence:**
- test_control/charge_sequence.py:122-123 — `check_cancellation(token)` then `self.relay.close(relay_address)`
- utils/cancellation.py:14-18 — module docstring: checkpoints never mid-relay-sequence

**Risks:** None beyond the documented, deliberate latency (bounded by one Telnet round trip, ~5s worst case) — interrupting mid-sequence would be less safe, not more.

**Recommendation:** None; current design is intentional and documented.

### Q: Is there any execution path where a relay can remain closed after an exception?

**Status:** Partially Implemented

**Answer:** For `ChargeSequence`/`DischargeSequence`: every raised exception is caught by `run_guarded()`, which calls `safety.emergency_stop()`/`safe_cancel_shutdown()` — both call `relay_matrix.open_all()` unconditionally. So no known exception path in the current sequences leaves a relay closed without at least one force-open attempt. However, `relay.open(relay_address)` (the sequence's own normal-completion relay-open, at charge_sequence.py:207/discharge_sequence.py:214) is only reached on the success path (`break` on EOC/EOD) — every abnormal exit relies entirely on the SAFETY layer's `open_all()`, not the sequence's own code. If `open_all()` itself fails (communication breakdown), the relay CAN remain closed — this is explicitly logged as CRITICAL, not silently accepted.

**Refinement (Monitor Battery review, Section 13 below):** "every exception branch calls `safety.emergency_stop()`/`safe_cancel_shutdown()`" is true only when that branch's own `storage.log_event()`/`record_execution_state()`/`finish_run_summary()` calls (which run *before* the safety call in each branch) succeed. If the database itself is the original failure, those calls raise again and `run_guarded()` exits *without* reaching `safety.emergency_stop()` at that layer — see Section 13's "What happens if the database becomes unavailable during a Monitor Battery run?" entry. This applies identically to ChargeSequence/DischargeSequence (they share the same `run_guarded()`), not just Monitor Battery. Hardware still ends up safe in this specific scenario via `test.py`'s outer `hw_mgr.disconnect_all()` backstop (not analyzed in this entry's original evidence below) — so the risk below is unchanged in severity, only the DB-failure case is now understood as a second, distinct way `emergency_stop()` can be skipped at the `run_guarded()` layer specifically (relay communication breakdown was the only one previously documented here).

**Evidence:**
- test_control/charge_sequence.py:191-212 — `finally` block disables SMU output; relay opened only after, only on success path
- test_control/safety_monitor.py:114-146 — `emergency_stop()` always calls `relay_matrix.open_all()`, logs CRITICAL on failure but does not raise
- test_control/battery_operation_sequence.py:105-159 — `run_guarded()`'s four exception branches all call `safety.emergency_stop()` or `safe_cancel_shutdown()`, but only after their own storage calls succeed (see Refinement above)
- test_control/hardware_manager.py:375-383 — `disconnect_all()`'s independently-guarded relay `open_all()` backstop, called unconditionally from `test.py`'s outer `finally` regardless of what happened inside `run_guarded()`

**Risks:** A total communication breakdown to the relay matrix during an emergency shutdown IS a path where the relay remains closed — this is a hardware-communication failure mode, not a software logic gap, and is honestly logged rather than hidden.

**Recommendation:** None beyond existing logging; a hardware-level fail-safe (e.g. relay board defaults to open on loss of control signal) is the only way to close this residual gap and is outside software's control.

---

## SECTION 2 — SMU / PSU HANDLING

### Q: What happens if output_enable() fails?

**Status:** Implemented

**Answer:** `output_enable()` wraps `session.initiate()` in try/except and raises `SMUError` on any driver failure. It also verifies output is actually ON via `query_output_state()` afterward and raises `SMUError` if verification fails — never assumes success from the command alone. In `ChargeSequence`/`DischargeSequence`, this exception propagates out of `_run_charge()`/`_run_discharge()` before the `try/finally` block is entered (the `finally` starts immediately AFTER `output_enable()`, per the module docstring), so `run_guarded()`'s generic `except Exception` branch handles it (safety.emergency_stop(), relay open_all(), run_summary FAIL).

**Evidence:**
- hardware/smu.py:323-362 — `output_enable()`
- test_control/charge_sequence.py:131-139 — `output_enable()` call, `try:` starts immediately after

**Risks:** None identified — this is the intended fail-fast path.

**Recommendation:** None.

### Q: What happens if output_disable() fails?

**Status:** Implemented

**Answer:** `output_disable()` itself raises `SMUError` if the underlying `output_enabled = False` command fails. Every safety-critical caller (`emergency_output_off()`, `force_output_off_and_verify()`) wraps this in its own try/except and never lets an `output_disable()` failure propagate uncaught — instead it's logged CRITICAL and a `False`/failure indicator is returned so the caller knows the PMU may still be active.

**Evidence:**
- hardware/smu.py:364-392 — `output_disable()`
- hardware/smu.py:552-584 — `emergency_output_off()` catches the exception, logs CRITICAL, returns `False`

**Risks:** A `False` return from `emergency_output_off()` means the PMU may still be sourcing/sinking current — this is a genuine residual hardware risk, but it is never silently swallowed; every caller logs CRITICAL with an explicit "physically disconnect power" instruction.

**Recommendation:** None beyond existing logging.

### Q: What happens if the SMU disconnects during a test?

**Status:** Partially Implemented

**Answer:** A mid-test disconnection would surface as an exception from `measure()`/`session.measure()` or from `emergency_output_off()`'s `output_disable()` call, both wrapped to raise `SMUError`/return `False` respectively — never silently ignored. `run_guarded()`'s generic exception handler catches any resulting `SMUError` and runs the full safety shutdown. There is no dedicated "SMU disconnected mid-test" detection separate from the underlying NI-DCPower call simply failing.

**Evidence:**
- hardware/smu.py:586-617 — `measure()` raises `SMUError` on any measurement exception
- test_control/battery_operation_sequence.py:147-159 — generic `except Exception` branch

**Risks:** No distinct "communication lost" classification exists — a mid-test SMU disconnect is treated the same as any other measurement/command failure. docs/architecture.md Section 17 already documents this as a known, unconfirmed risk ("PMU behavior under communication loss").

**Recommendation:** None beyond what's already tracked in docs/architecture.md Section 17.

### Q: What happens if the SMU reports an unexpected mode?

**Status:** Not Implemented

**Answer:** There is no explicit check anywhere that the SMU's `output_function`/`source_mode` matches what was just commanded, beyond the configuration READBACK+VERIFY performed once immediately after `_configure_current_source()` (`current_level`/`voltage_limit` readback, tolerance-checked). There is no ongoing/per-sample check that the session is still in `DC_CURRENT` mode during the sampling loop.

**Evidence:**
- hardware/smu.py:209-281 — `_configure_current_source()` verifies `current_level`/`voltage_limit` once, at configuration time only
- No code path re-checks `output_function`/`source_mode` inside the charge/discharge sampling loop (test_control/charge_sequence.py:145-190, discharge_sequence.py:156-196)

**Risks:** If the driver or hardware silently changes mode mid-run (e.g. a fault condition), nothing detects it directly — only its symptomatic effect on measured voltage/current, caught by `SafetyMonitor.check()`.

**Recommendation:** Consider a periodic mode-readback check in the sampling loop if real-hardware testing reveals mode drift is a real failure mode.

### Q: What happens if measure() returns None?

**Status:** Not Implemented (cannot occur as coded, but no explicit guard downstream)

**Answer:** `SMU.measure()` never returns `None` — it either returns a `{"voltage_v", "current_a"}` dict or raises `SMUError` (including for non-finite readings). `ChargeSequence`/`DischargeSequence` do not separately guard against a `None` return because the driver contract makes it unreachable; if it somehow occurred, `smu_reading["current_a"]` would raise `TypeError`, which is NOT one of the four types `run_guarded()` explicitly names but IS still caught by its generic `except Exception` branch (safety shutdown still runs).

**Evidence:**
- hardware/smu.py:586-617 — `measure()` always returns a dict or raises
- test_control/battery_operation_sequence.py:147-159 — generic exception branch would still catch a `TypeError`

**Risks:** None practical — the generic exception handler provides a safety net even for an "impossible" case.

**Recommendation:** None.

### Q: What happens if measure() returns NaN?

**Status:** Implemented

**Answer:** `measure()` explicitly checks `math.isfinite()` on both voltage and current and raises `SMUError` if either is non-finite (NaN or Inf) — a NaN reading never reaches the safety check or storage layer un-flagged.

**Evidence:**
- hardware/smu.py:612-616 — `if not (math.isfinite(voltage_v) and math.isfinite(current_a)): raise SMUError(...)`

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if measure() returns values outside expected ranges?

**Status:** Implemented

**Answer:** Every sample from `measure()`/`dmm.measure_dc_voltage()` is passed through `SafetyMonitor.check(v, i, t_c, mode=...)` before being recorded or evaluated for EOC/EOD — an out-of-range voltage/current raises `SafetyViolationError`, caught by `run_guarded()`, triggering `emergency_stop()`.

**Evidence:**
- test_control/charge_sequence.py:165-167 — `status = self.safety.check(...); if not status.safe: raise SafetyViolationError(...)`
- test_control/safety_monitor.py:83-108 — `check()`'s voltage/current/temperature range checks

**Risks:** None identified.

**Recommendation:** None.

### Q: Is output state verified after enabling?

**Status:** Implemented

**Answer:** Yes — `output_enable()` calls `query_output_state()` after `session.initiate()` and raises `SMUError` if the readback doesn't confirm ON.

**Evidence:**
- hardware/smu.py:356-362

**Risks:** None identified.

**Recommendation:** None.

### Q: Is output state verified after disabling?

**Status:** Implemented

**Answer:** Yes — `verify_output_disabled()` performs a real query of `session.output_enabled` (never trusts Python-side bookkeeping); `force_output_off_and_verify()`/`emergency_output_off()` both call it after `output_disable()`.

**Evidence:**
- hardware/smu.py:394-411 — `verify_output_disabled()`
- hardware/smu.py:552-584 — `emergency_output_off()`

**Risks:** None identified.

**Recommendation:** None.

### Q: Is there any execution path where SMU output can remain enabled after an exception?

**Status:** Partially Implemented

**Answer:** In `ChargeSequence`/`DischargeSequence`, the `try/finally` around the sampling loop starts immediately after `output_enable()` and always calls `emergency_output_off()` in `finally` — this covers every exception raised from `output_enable()` onward (stabilization wait, sampling loop, EOC/EOD detection). If `emergency_output_off()` itself fails to verify OFF, the output CAN remain enabled — this is logged CRITICAL ("PMU may still be actively sourcing/sinking current") rather than hidden, but is a genuine residual risk under total communication loss to the SMU.

**Evidence:**
- test_control/charge_sequence.py:139-196 — `try/finally` wrapping the sampling loop
- test_control/discharge_sequence.py:150-202 — same pattern
- hardware/smu.py:552-584 — `emergency_output_off()`'s CRITICAL log path

**Risks:** Same class of residual risk as the relay's emergency-off failure — a total SMU communication breakdown during shutdown is the one path software cannot force closed.

**Recommendation:** None beyond existing logging; matches docs/architecture.md's own documented "PMU behavior under communication loss" risk.

---

## SECTION 3 — CHARGE SEQUENCE

### Q: What happens if the battery is already fully charged when the sequence starts?

**Status:** Implemented

**Answer:** No pre-check exists before starting the CC-CV loop, but the EOC condition (`v >= voltage_limit_v and abs(i) <= CHARGE_CUTOFF_A`) is evaluated on the FIRST sample taken after the stabilization wait — if the battery is already at/above the CV target with tapered current, the loop breaks immediately on its first iteration and the charge is reported complete.

**Evidence:**
- test_control/charge_sequence.py:143-188 — the `while True` loop's first-iteration EOC check

**Risks:** If the battery is fully charged but current has not yet tapered (e.g. instrument settling), one full sample interval elapses before EOC is detected — not a correctness issue, just a description of behavior.

**Recommendation:** None.

### Q: What happens if EOC is detected on the first measurement?

**Status:** Implemented

**Answer:** Same as above — the loop breaks on iteration 1, logs "Charge complete", opens the relay after confirming SMU output OFF, and reports success via `complete()`.

**Evidence:**
- test_control/charge_sequence.py:186-188, 191-212

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if EOC is never reached?

**Status:** Implemented

**Answer:** The loop is bounded by `CHARGE_TIMEOUT_S` — once `elapsed > CHARGE_TIMEOUT_S`, `NIPXITimeoutError` is raised. `run_guarded()` now has a dedicated `except NIPXITimeoutError` branch (placed after `RelayError`, before the generic `except Exception`) that records `StopReason.TIMEOUT` — not the generic `FAILED` — in both `record_execution_state()` and `finish_run_summary()`, then runs the identical `safety.emergency_stop()` shutdown as every other fault path.

**Evidence:**
- test_control/charge_sequence.py:148-152 — timeout raise
- test_control/battery_operation_sequence.py:183-199 — `run_guarded()`'s dedicated `except NIPXITimeoutError` branch, recording `StopReason.TIMEOUT`

**Risks:** None identified — closed this session. Verified via a mocked regression test: a `ChargeSequence` run with `CHARGE_TIMEOUT_S=0.0` now shows `StopReason.TIMEOUT` in both `record_execution_state` and `finish_run_summary` mock call args (previously would have been `FAILED`).

**Recommendation:** None. (Previously recommended: add an explicit `except NIPXITimeoutError` branch to `run_guarded()` — done.)

### Q: Is there a maximum charge timeout?

**Status:** Implemented

**Answer:** Yes — `Settings.CHARGE_TIMEOUT_S`, checked every sampling-loop iteration.

**Evidence:**
- test_control/charge_sequence.py:148-152

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if voltage exceeds the configured limit?

**Status:** Implemented

**Answer:** `SafetyMonitor.check()` (called every sample with `mode="charge"`) compares against `battery_cfg["voltage_max_v"]` (set via `set_battery_limits()` before the loop starts) and returns `safe=False` with an "Overvoltage" reason if exceeded, raising `SafetyViolationError` → `run_guarded()`'s dedicated safety-violation branch → `safety.emergency_stop()`.

**Evidence:**
- test_control/charge_sequence.py:119, 165-167
- test_control/safety_monitor.py:96-97

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if measured current exceeds the configured limit?

**Status:** Implemented

**Answer:** Same mechanism — `_current_max(mode="charge")` resolves `battery_cfg["max_charge_current_a"]`; exceeding it raises `SafetyViolationError` via the same per-sample `safety.check()` call.

**Evidence:**
- test_control/safety_monitor.py:61-81, 102-103

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if cancellation occurs during charging?

**Status:** Implemented

**Answer:** `check_cancellation(token)` is checked at the top of every sampling-loop iteration and inside `interruptible_sleep()` for both the stabilization wait and the inter-sample dwell (bounded to ~poll_interval_s latency, default 0.2s). Raising `OperationCancelledError` propagates through the `try/finally` (SMU output forced off + verified) and is caught by `run_guarded()`'s dedicated cancellation branch: logs INFO, records `StopReason.CANCELLED`, finishes `run_summary` as `STOPPED_BY_OPERATOR`, calls `safety.safe_cancel_shutdown()` (SMU off + relay open_all, logged as a deliberate action not a fault).

**Evidence:**
- test_control/charge_sequence.py:122, 140, 146, 190
- test_control/battery_operation_sequence.py:105-117

**Risks:** None identified — this is the documented, intended behavior.

**Recommendation:** None.

---

## SECTION 4 — DISCHARGE SEQUENCE

### Q: What happens if the battery is already below the cutoff voltage before the test starts?

**Status:** Implemented

**Answer:** Same pattern as charge: no pre-check before the loop, but EOD (`v <= cutoff_v`) is evaluated on the first sample after stabilization — if already below cutoff, the loop breaks on iteration 1 and discharge is reported complete.

**Evidence:**
- test_control/discharge_sequence.py:156-194

**Risks:** The battery briefly sinks discharge current for one stabilization wait + one sample period before EOD is even checked — a battery already at/near the absolute safety floor (`voltage_min_v`) would still have `SafetyMonitor.check()` as the authoritative per-sample guard during that window (undervoltage would raise `SafetyViolationError` before EOD logic runs, since `safety.check()` is called first in the loop body).

**Recommendation:** None — the safety check ordering (before EOD detection) already covers this correctly.

### Q: What happens if EOD is detected on the first measurement?

**Status:** Implemented

**Answer:** Loop breaks immediately, logs "Discharge complete", opens relay after confirming SMU output OFF, reports success.

**Evidence:**
- test_control/discharge_sequence.py:192-194, 197-219

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if EOD is never reached?

**Status:** Implemented

**Answer:** Bounded by `DISCHARGE_TIMEOUT_S`; raises `NIPXITimeoutError`, now caught by `run_guarded()`'s dedicated `except NIPXITimeoutError` branch (same fix as Section 3 — `BatteryOperationSequence.run_guarded()` is shared by both sequences), recording `StopReason.TIMEOUT` instead of `FAILED`.

**Evidence:**
- test_control/discharge_sequence.py:159-163
- test_control/battery_operation_sequence.py:183-199

**Risks:** None identified — closed this session (same fix as Section 3, since both sequences share `run_guarded()`).

**Recommendation:** None. Note: this fix applies only to `ChargeSequence`/`DischargeSequence` (built on `BatteryOperationSequence`) — the legacy `charge_cycle.py`/`discharge_cycle.py`/`ChargeCycle`/`DischargeCycle` classes are a separate, superseded code path and are unaffected.

### Q: Is there a maximum discharge timeout?

**Status:** Implemented

**Answer:** Yes — `Settings.DISCHARGE_TIMEOUT_S`.

**Evidence:**
- test_control/discharge_sequence.py:159-163

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if measured voltage falls below the battery safety floor?

**Status:** Implemented

**Answer:** `SafetyMonitor.check(mode="discharge")` compares against `battery_cfg["voltage_min_v"]` every sample and raises `SafetyViolationError` on undervoltage — this is the authoritative abort path, checked BEFORE the EOD `v <= cutoff_v` comparison in the loop body, and independent of the defensive `cutoff_v = max(target_v, floor_v)` clamp applied at sequence start.

**Evidence:**
- test_control/discharge_sequence.py:126-134 (clamp), 173-175 (safety check ordering)
- test_control/safety_monitor.py:99-100 (undervoltage check)

**Risks:** None identified — documented as a deliberate defense-in-depth design (module docstring explicitly states the clamp is "a defensive measure, not the primary safety mechanism").

**Recommendation:** None.

### Q: What happens if cancellation occurs during discharge?

**Status:** Implemented

**Answer:** Identical mechanism to charging — checkpoints at loop top and in both interruptible sleeps, `OperationCancelledError` caught by `run_guarded()`'s dedicated branch, `safety.safe_cancel_shutdown()` runs.

**Evidence:**
- test_control/discharge_sequence.py:137, 151, 157, 196
- test_control/battery_operation_sequence.py:105-117

**Risks:** None identified.

**Recommendation:** None.

---

## SECTION 5 — BATTERY GROUP CONFIGURATION

### Q: What happens if a group references a non-existent battery type?

**Status:** Implemented

**Answer:** `validate_group_test_config()`'s Stage 2 now checks `battery_type not in dev_cfg.BATTERY_CONFIGS` explicitly, BEFORE the `battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]` lookup, and raises a typed `ConfigurationError` with a clear message if the battery type is unknown — the bare, uncaught `KeyError` this used to produce can no longer occur through this path. `test.py::_run_charge_or_discharge()`'s existing `except (GroupConfigurationError, ConfigurationError, HardwareConfigurationError)` now catches it cleanly (`[FAIL] ConfigurationError: ...`, no hardware activated). The two other code paths that read `BATTERY_CONFIGS[battery_type]` directly without going through `validate_group_test_config()` — `test.py::_run_monitor_battery()` and `_run_monitor_battery_scan()` — each got the identical explicit guard, printed as a `[FAIL]` message in the same style as the pre-existing "has no battery_type configured" check right above it.

**Evidence:**
- utils/validators.py:139-144 — explicit `if battery_type not in dev_cfg.BATTERY_CONFIGS: raise ConfigurationError(...)`, before the line-149 lookup
- test.py:3702-3706 — `_run_monitor_battery()`'s identical guard
- test.py:3889-3893 — `_run_monitor_battery_scan()`'s identical guard
- Verified via a mocked regression test: monkeypatching a group's `battery_type` to an unknown string now raises `ConfigurationError` (not `KeyError`) from `validate_group_test_config()`.

**Risks:** None identified for the three real workflow paths above — closed this session. Residual gap (documented, out of scope for this session): the Safety Monitor Simulator's `_select_safety_simulation_group()` and its two callers (test.py:2623, test.py:2769) still do a bare `BATTERY_CONFIGS[cfg["battery_type"]]` lookup with no equivalent guard. It is simulator/demo-only code (no hardware activation, no real battery, no safety consequence) but is the same class of gap.

**Recommendation:** Apply the same explicit existence check to `_select_safety_simulation_group()`/its callers (test.py:2623, 2769) for consistency, at low priority given it carries no hardware risk.

### Q: What happens if a group references a non-existent SMU?

**Status:** Implemented

**Answer:** `hardware_for_group()` resolves `smu_cfg` via `SMU_ASSIGNMENTS.get(grp["smu"])`, returning `None` for an unknown name (`.get()`, not `[]`) — `validate_group_test_config()`'s Stage 1 checks `hw["smu_cfg"] is None` and raises `GroupConfigurationError` before any hardware is touched.

**Evidence:**
- config/devices.py:668-669 — `.get()` lookup, returns None on miss
- utils/validators.py:108-115 — Stage 1 missing-role check

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if a group references a non-existent relay?

**Status:** Implemented

**Answer:** Same mechanism — `ETHERNET_DEVICES.get(grp["relay_matrix"])` returns `None`, caught by Stage 1's missing-role check.

**Evidence:**
- config/devices.py:667, utils/validators.py:108-115

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if a group references a non-existent DMM?

**Status:** Implemented

**Answer:** Same mechanism — `DMM_CONFIGS.get(grp["dmm"])`, caught by Stage 1 (DMM is one of the three required roles checked: `relay_matrix`, `smu`, `dmm`).

**Evidence:**
- config/devices.py:671, utils/validators.py:108-115

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if required group fields are missing?

**Status:** Implemented

**Answer:** Stage 1 explicitly checks for a missing `battery_type` (raises `GroupConfigurationError`) and missing/incomplete `test_setpoints` (checks all four required keys are present via `_REQUIRED_TEST_SETPOINTS`, raises `GroupConfigurationError` if any are absent).

**Evidence:**
- utils/validators.py:117-130

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if group setpoints exceed battery limits?

**Status:** Implemented

**Answer:** Stage 2 explicitly checks all four setpoints (`charge_current_a`, `charge_voltage_v`, `discharge_current_a`, `discharge_cutoff_v`) against the corresponding `BATTERY_CONFIGS` limit and raises `ConfigurationError` — never silently clamps.

**Evidence:**
- utils/validators.py:138-165

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if group setpoints exceed hardware capabilities?

**Status:** Implemented

**Answer:** Stage 3 checks `charge_current_a`/`discharge_current_a` against the assigned SMU's `max_current_a` (from `PXI_SLOTS[...]["max_current_a"]`, derived into `SMU_ASSIGNMENTS`) and raises `HardwareConfigurationError` if exceeded. Voltage setpoints are NOT checked against any SMU voltage-capability limit (no such field exists in `SMU_ASSIGNMENTS` today) — only current is checked at the hardware-capability stage.

**Evidence:**
- utils/validators.py:167-182
- config/devices.py:350-366 — `SMU_ASSIGNMENTS` carries `max_current_a` only, no voltage capability field

**Risks:** A misconfigured `charge_voltage_v`/`discharge_cutoff_v` that exceeds the SMU's actual voltage compliance range (not modeled anywhere in config) would only be caught electrically at runtime (SMU compliance/readback verification), not by pre-hardware validation.

**Recommendation:** If SMU voltage compliance range data becomes available (datasheet-confirmed), add it to `PXI_SLOTS`/`SMU_ASSIGNMENTS` and extend Stage 3 to check voltage setpoints too.

### Q: Are configuration errors detected before any hardware is activated?

**Status:** Implemented

**Answer:** Yes — `validate_group_test_config()` is called in `test.py::_run_charge_or_discharge()` BEFORE `HardwareManager` is even constructed (test.py:3992-3996, well before `hw_mgr = HardwareManager(...)` at test.py:4029). No relay close, no SMU connect, on any validation failure.

**Evidence:**
- test.py:3992-4029 — validation happens first, `HardwareManager` construction is later
- utils/validators.py:63-71 — module docstring states this explicitly: "run BEFORE any hardware is touched"

**Risks:** None identified.

**Recommendation:** None.

---

## SECTION 6 — SAFETY SYSTEM

### Q: Can a test start if validation fails?

**Status:** Implemented (test cannot start)

**Answer:** No — `test.py` catches `GroupConfigurationError`/`ConfigurationError`/`HardwareConfigurationError` from `validate_group_test_config()` and `return`s immediately with a `[FAIL]` message and "Aborting, no hardware activated" — execution never reaches `HardwareManager` construction.

**Evidence:**
- test.py:3992-3996

**Risks:** None identified — closed this session. (Previously: a bare `KeyError` from an unrecognized `battery_type` string would not have been caught by this except clause; `validate_group_test_config()` now raises a typed `ConfigurationError` for this case instead, see Section 5.)

**Recommendation:** None.

### Q: Can hardware activation occur before validation is complete?

**Status:** Implemented (cannot)

**Answer:** No — see Section 5's "Are configuration errors detected before any hardware is activated?" entry; the call ordering in `test.py` guarantees this.

**Evidence:**
- test.py:3992-4029

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens after a SafetyViolationError?

**Status:** Implemented

**Answer:** `run_guarded()`'s dedicated `except SafetyViolationError` branch: logs ERROR, writes an `event_log` entry, records `StopReason.SAFETY_VIOLATION`, finishes `run_summary` with `result="FAIL"`, calls `safety.emergency_stop(smu, relay, reason)` (SMU output off+verified, relay open_all+verified), then RE-RAISES — propagating to `test.py`'s `except Exception as e: print(f"[FAIL] {operation} aborted: {e}")`.

**Evidence:**
- test_control/battery_operation_sequence.py:119-131
- test_control/safety_monitor.py:114-146

**Risks:** None identified.

**Recommendation:** None.

### Q: Are relay shutdown and SMU shutdown always executed after a safety event?

**Status:** Implemented

**Answer:** Yes, for every exception type `run_guarded()` handles (`OperationCancelledError`, `SafetyViolationError`, `RelayError`, generic `Exception`) — each branch calls either `safety.safe_cancel_shutdown()` or `safety.emergency_stop()`, and both of those unconditionally attempt SMU `emergency_output_off()` then `relay_matrix.open_all()`, regardless of what the triggering error was.

**Evidence:**
- test_control/battery_operation_sequence.py:102-159
- test_control/safety_monitor.py:114-173

**Risks:** As documented throughout Sections 1-2, the shutdown ATTEMPT is always executed, but is not guaranteed to SUCCEED under total hardware communication loss — always logged CRITICAL when it can't be verified.

**Recommendation:** None beyond existing logging.

### Q: Is shutdown behavior identical for errors, cancellations, and emergency stops?

**Status:** Implemented

**Answer:** The underlying hardware sequence is IDENTICAL (SMU `emergency_output_off()` → relay `open_all()`) for both `emergency_stop()` and `safe_cancel_shutdown()` — the only difference is log severity/wording (ERROR/"EMERGENCY STOP" vs. WARNING/"SAFE CANCELLATION"), by design, so cancellation doesn't read as a fault in the logs.

**Evidence:**
- test_control/safety_monitor.py:114-173 — both methods, side by side, identical hardware call sequence

**Risks:** None identified — this is a deliberate, documented design choice.

**Recommendation:** None.

### Q: Is there any execution path that bypasses SafetyMonitor?

**Status:** Partially Implemented

**Answer:** Within `ChargeSequence`/`DischargeSequence`, every sample is checked via `safety.check()` before being recorded, and every exception path funnels through `SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()` — no bypass found in this path. However, other workflows in the codebase (Monitor Battery, Monitor Battery Scan, the legacy `charge_cycle.py`/`discharge_cycle.py`/`TestExecutor` path, and `test.py`'s standalone hardware commissioning tests like SMU Functional Validation / Relay Ethernet Test) construct/drive hardware directly without necessarily routing through this exact `SafetyMonitor` instance in the same way — these are out of scope for this review's primary sequences but represent separate code paths that were not exhaustively re-audited here.

**Evidence:**
- test_control/charge_sequence.py:165-167, discharge_sequence.py:173-175 — every sample checked
- Not verified: full audit of `test_control/battery_test.py`, `test_control/proto_test_sequence.py`, and `test.py`'s commissioning-test functions for SafetyMonitor usage parity — outside this review's file list.

**Risks:** Unquantified — a full audit of every hardware-touching code path in `test.py` (4454 lines) was not performed as part of this review.

**Recommendation:** A follow-up review should specifically audit every `test.py` commissioning/validation workflow (SMU Functional Validation, Relay Ethernet Test, Proto Test Execution) for SafetyMonitor coverage parity with Charge/Discharge Battery.

---

## SECTION 7 — DATABASE / TRACEABILITY

### Q: What happens if the database file does not exist?

**Status:** Implemented

**Answer:** `DataStorage.open()` creates the containing directories (`os.makedirs(..., exist_ok=True)`) and `sqlite3.connect()` creates the `.db` file itself if absent — a missing database file is not an error condition, it's the normal first-run case.

**Evidence:**
- data/storage.py:351-372 — `open()`

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if the database cannot be opened?

**Status:** Implemented

**Answer:** `open()` still catches `(OSError, sqlite3.Error)`, logs ERROR, and re-raises (data/storage.py) — but every real workflow entry point now calls a new helper, `test.py::_open_storage_guarded(hw_mgr=None)`, instead of `DataStorage(...).open()` directly. It wraps that same open() call in `try/except (OSError, sqlite3.Error)`, prints a clean `[FAIL] Database unavailable -- could not open storage: {e}` (no raw traceback shown to the operator), disconnects `hw_mgr` if given, and returns `None` on failure — callers check `if storage is None: return` immediately. Diagnostic detail is preserved because `DataStorage.open()` already logs the exception via `self.log.error(...)` before re-raising; this change only replaces what the OPERATOR sees, not what is logged. All four real workflow entry points use it: `_run_monitor_battery()`, `_run_monitor_battery_scan()`, `_run_charge_or_discharge()`, and `run_proto_test_execution()`.

**Evidence:**
- test.py:3543-3576 — `_open_storage_guarded()` definition
- test.py:4105-4107 — `_run_charge_or_discharge()` using it (`if storage is None: return`)
- test.py:3746-3747, 3920-3921, 4312-4314 — the other three entry points using the same helper
- Verified via a mocked regression test: `_open_storage_guarded()` returns `None` and calls `hw_mgr.disconnect_all()` when `DataStorage.open()` raises `sqlite3.OperationalError`.

**Risks:** None identified — closed this session for the four real workflow entry points. The read-only `_open_real_storage_readonly()` database-viewer tool was deliberately left untouched (it's an inspection tool, not a real test workflow, no hardware risk).

**Recommendation:** None.

### Q: What happens if the database becomes unavailable during a test?

**Status:** Partially Implemented

**Answer:** Every write method (`record_measurement()`, `record_execution_state()`, `log_event()`, `finish_run_summary()`) raises `RuntimeError` if `self._db is None`, but does NOT catch `sqlite3.Error` from an in-flight write failure (e.g. disk full, file locked) except in `record()` (the older, narrower StorageBackend method) and `open()` — `record_measurement()`/`record_execution_state()`/`log_event()` have no try/except around their `self._db.execute()` calls, so a mid-test SQLite failure would raise `sqlite3.Error` uncaught, propagating up through the sequence's sampling loop as an unhandled exception — which IS then caught by `run_guarded()`'s generic `except Exception` branch (safety shutdown still runs), but is reported as "Unexpected error" rather than a database-specific message.

**Evidence:**
- data/storage.py:528-577 — `record_measurement()`, no try/except around `execute()`
- data/storage.py:392-416 — `record()` (older method) DOES catch `sqlite3.Error` and re-raises with a log message

**Risks:** A test would be safely aborted (safety shutdown still runs via the generic exception path) but the failure reason in logs/UI would read as a generic error, not clearly attributable to storage.

**Recommendation:** Add a distinguishing log message (or a dedicated `StorageError`) around `record_measurement()`/`record_execution_state()`/`log_event()`'s `execute()` calls so a database failure is diagnosable at a glance, consistent with `record()`'s existing pattern.

### Q: What happens if run_summary creation fails?

**Status:** Implemented

**Answer:** `start_run_summary()` itself is unchanged (still no try/except around its own `execute()`/`commit()`), but every real workflow entry point now calls it through a new helper, `test.py::_start_run_summary_guarded(storage, test_type, **fields)`, which wraps the call in `try/except sqlite3.Error`, prints a clean `[FAIL] Database unavailable -- could not start run_summary: {e}` message, and returns `True`/`False` — callers check `if not _start_run_summary_guarded(...): return` immediately, before any `log_event()` calls or hardware activation that would otherwise follow. No exception propagates to an unhandled traceback anymore.

**Evidence:**
- data/storage.py:617-647 — `start_run_summary()` (unchanged)
- test.py:3579-3598 — `_start_run_summary_guarded()` definition
- test.py:4116-4126 — `_run_charge_or_discharge()` using it (`if not _start_run_summary_guarded(...): return`)
- test.py:3758-3759, 3930-3931 — the other two entry points using the same helper
- Verified via a mocked regression test: `_start_run_summary_guarded()` returns `False` (no exception propagates) when `start_run_summary()` raises `sqlite3.Error`, and `True` on success.

**Risks:** None identified — closed this session for the four real workflow entry points.

**Recommendation:** None.

### Q: What happens if measurement logging fails?

**Status:** Partially Implemented — see "database becomes unavailable during a test" above; same answer applies to `record_measurement()` specifically.

**Evidence:**
- data/storage.py:528-577

**Risks:** Same as above.

**Recommendation:** Same as above.

### Q: Can a test continue without traceability?

**Status:** Not Implemented (test does not continue — it fails, per above)

**Answer:** No code path deliberately continues a charge/discharge test after a storage write failure — any such failure raises and is treated as a fault by `run_guarded()`'s generic exception branch, aborting the operation (with safety shutdown). There is no "best-effort, keep going without logging" mode.

**Evidence:**
- test_control/battery_operation_sequence.py:147-159

**Risks:** None identified — failing safe (aborting) rather than silently losing traceability is the correct behavior for a regulated test system.

**Recommendation:** None — this is appropriate; only the OPERATOR-FACING error message quality (see above) needs improvement, not the safety behavior.

### Q: Can measurements be created without an associated run?

**Status:** Not Implemented (prevented by construction, not by a foreign-key constraint)

**Answer:** Every `record_measurement()` call uses `self.run_id` (generated once in `DataStorage.__init__()`), and every real caller (`ChargeSequence`/`DischargeSequence`) calls `storage.start_run_summary()` before any `record_measurement()` call in the same `test.py` code path. There is NO database-level foreign-key constraint enforcing `measurements.run_id` references an existing `run_summary.run_id` — the schema does not declare a `FOREIGN KEY`. So it is possible, by a code path that skips `start_run_summary()`, to insert `measurements` rows with a `run_id` that has no matching `run_summary` row — the schema would silently allow it.

**Evidence:**
- data/storage.py:68-95 — `CREATE_TABLE_SQL` for `measurements`, no `FOREIGN KEY` clause
- data/storage.py:178-215 — `CREATE_RUN_SUMMARY_SQL`, also no `FOREIGN KEY`

**Risks:** An orphaned `measurements` row (no matching `run_summary`) would not be flagged by the database itself — only by application logic noticing the absence, e.g. `BatteryOperationSequence._run_number()` returning `None`.

**Recommendation:** Consider adding `FOREIGN KEY (run_id) REFERENCES run_summary(run_id)` (with SQLite foreign key enforcement turned on via `PRAGMA foreign_keys = ON`) if referential integrity becomes important for reporting; currently no code path is known to violate this in practice.

### Q: Can a run exist without a completion status?

**Status:** Implemented (detectable, not prevented)

**Answer:** Yes, this can happen — `finish_run_summary()` is the only thing that sets `stop_reason`/`result`/`end_time`, and it's called from `run_guarded()`'s exception branches and `complete()`'s success path. If the PROCESS ITSELF is killed (Ctrl+C isn't graceful, SIGKILL, power loss, terminal close) before `finish_run_summary()` runs, the `run_summary` row remains with `end_time IS NULL`/`stop_reason IS NULL`/`result IS NULL` indefinitely — this is exactly the "incomplete run" signature. Nothing currently queries for this at startup (see Section 9).

**Evidence:**
- data/storage.py:649-690 — `finish_run_summary()`, only writer of these fields
- No startup code found that queries `run_summary WHERE end_time IS NULL`

**Risks:** An interrupted run is silently left "open" in the database with no automated flag or alert — this is the core traceability gap explored in Section 9.

**Recommendation:** See Section 9's recommendations (startup check for unfinished runs).

---

## SECTION 8 — EXECUTION FLOW

### Q: What happens if Ctrl+C occurs during validation?

**Status:** Implemented

**Answer:** `validate_group_test_config()` runs entirely before the SIGINT handler for the operation is even installed (`signal.signal(signal.SIGINT, ...)` happens at test.py:4076, AFTER validation at test.py:3992-3996 and AFTER the operator confirmation prompt) — so a Ctrl+C during validation is handled by Python's DEFAULT `SIGINT` behavior (raises `KeyboardInterrupt`), not the cooperative cancellation token. Since no hardware has been touched yet at that point, this is safe — `test.py`'s outer per-menu-item `except (KeyboardInterrupt, EOFError)` handlers (present throughout `test.py`, e.g. line 3159/3362/4403) catch it at the menu level.

**Evidence:**
- test.py:3992-3996 (validation), test.py:4076-4078 (SIGINT handler installed later)

**Risks:** None identified — no hardware is active during this window.

**Recommendation:** None.

### Q: What happens if Ctrl+C occurs during relay switching?

**Status:** Partially Implemented — see Section 1's "cancellation during relay switching" entry. Same conclusion: not interrupted mid-sequence by design; honored at the next checkpoint.

**Evidence:**
- utils/cancellation.py:14-18

**Risks:** None beyond the documented, bounded latency.

**Recommendation:** None.

### Q: What happens if Ctrl+C occurs during charging?

**Status:** Implemented — see Section 3's cancellation entry.

**Evidence:**
- test_control/charge_sequence.py:122, 140, 146, 190

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if Ctrl+C occurs during discharging?

**Status:** Implemented — see Section 4's cancellation entry.

**Evidence:**
- test_control/discharge_sequence.py:137, 151, 157, 196

**Risks:** None identified.

**Recommendation:** None.

### Q: What happens if Ctrl+C occurs during shutdown?

**Status:** Not Implemented (no protection)

**Answer:** `safety.emergency_stop()`/`safe_cancel_shutdown()` and `hw_mgr.disconnect_all()` do not install their own SIGINT handler or block signal delivery — Python's default `SIGINT` behavior during these calls raises `KeyboardInterrupt` at whatever line is executing. If that lands mid-shutdown-sequence (e.g. between `smu.emergency_output_off()` and `relay.open_all()`), the shutdown sequence is interrupted and the relay may never receive its `open_all()` call. `test.py`'s SIGINT handler for the operation is restored to the PREVIOUS (usually default) handler in a `finally` block (test.py:4103-4104) BEFORE the outer `finally`'s `storage.close()`/`hw_mgr.disconnect_all()` runs (test.py:4106-4116) — meaning a second Ctrl+C during that final teardown window uses Python's default SIGINT behavior, not the cooperative token.

**Evidence:**
- test.py:4103-4116 — SIGINT handler restored before final teardown
- test_control/safety_monitor.py:114-173 — no signal-blocking in emergency_stop()/safe_cancel_shutdown()

**Risks:** A well-timed second Ctrl+C during the final hardware-teardown window could abort `disconnect_all()` mid-sequence, potentially leaving the relay closed or SMU output uncertain. `HardwareManager`'s `atexit` handlers (`_atexit_relay_shutdown`/`_atexit_smu_shutdown`) are a documented backstop for exactly this class of process-exit gap.

**Recommendation:** This exact risk is already tracked in docs/architecture.md Section 17 ("the `HardwareManager.connect_all()` SIGINT/teardown gap") for the connect path; the same class of gap exists symmetrically on the disconnect/shutdown path and should be added to that tracked list if not already covered there for shutdown specifically.

### Q: Is shutdown guaranteed after user cancellation?

**Status:** Partially Implemented

**Answer:** A shutdown ATTEMPT is guaranteed (via `run_guarded()`'s cancellation branch calling `safe_cancel_shutdown()`), and a SECOND independent backstop exists via `HardwareManager`'s `atexit` handlers. Neither can guarantee successful completion under: total hardware communication loss, a second Ctrl+C during the shutdown window itself (see above), or a hard process kill (SIGKILL) which bypasses all Python-level handling including `atexit`.

**Evidence:**
- test_control/battery_operation_sequence.py:105-117
- test_control/hardware_manager.py:399-449 — atexit backstops
- docs/architecture.md:1330 — "Terminal-close..., Task-Manager kill, and native driver crashes all bypass every safety mechanism in this codebase"

**Risks:** As documented in docs/architecture.md's own Known Risks section — this is an inherent limitation of a pure-userspace application, not a code defect.

**Recommendation:** None beyond what's already documented; a hardware-level fail-safe (relay defaults open on loss of control signal) is the only way to close this gap fully.

---

## SECTION 9 — POWER LOSS / RECOVERY

### Q: What happens if power is lost while the program is running?

**Status:** Not Implemented

**Answer:** No code path detects or specifically handles a power-loss event. If the HOST PC loses power, the Python process simply stops executing — no shutdown code runs (this is the same class of gap as a hard kill). `docs/architecture.md` Section 17 explicitly documents this as "the least-characterized risk in the system," noting it is unresolved even whether PMU/relay hardware shares a power source with the host PC.

**Evidence:**
- docs/architecture.md:1325 — "PMU behavior under power loss: entirely a hardware question... This is the least-characterized risk in the system."

**Risks:** Complete — no software mitigation exists or can exist for host-PC power loss.

**Recommendation:** Confirm (bench test, not software) whether the PXI chassis/Numato relay module retain independent power when the host PC loses power, and whether the SMU output stage fails open on power loss. This is a hardware validation item, not a code change.

### Q: What happens if power is lost during relay switching?

**Status:** Not Implemented — same as above; no software detection possible for host power loss.

**Evidence:** docs/architecture.md:1325

**Risks:** Relay state at the moment of power loss is whatever it physically was — unknown/unconfirmed without a hardware fail-safe.

**Recommendation:** Same as above.

### Q: What happens if power is lost during charging?

**Status:** Not Implemented — same as above.

**Evidence:** docs/architecture.md:1325

**Risks:** Same.

**Recommendation:** Same as above.

### Q: What happens if power is lost during discharging?

**Status:** Not Implemented — same as above.

**Evidence:** docs/architecture.md:1325

**Risks:** Same.

**Recommendation:** Same as above.

### Q: What state will relays be left in after a power loss?

**Status:** Not Implemented (unknown, hardware-dependent)

**Answer:** Not determinable from software — depends entirely on the Numato relay module's own power-loss behavior (fails open? holds last state? depends on its own power supply), which is not characterized anywhere in this codebase or its docs.

**Evidence:** docs/architecture.md:1325 (same passage, generalizes to relay)

**Risks:** Unconfirmed relay fail-safe behavior on power loss is a genuine safety-relevant unknown.

**Recommendation:** Confirm with the Numato hardware datasheet/bench test whether relays default to open (de-energized) on loss of the module's own power.

### Q: What state will the SMU output be left in after a power loss?

**Status:** Not Implemented (unknown, hardware-dependent) — see docs/architecture.md:1325, same unresolved question for the SMU card's output stage.

**Recommendation:** Confirm against NI-4141/4139/4130 datasheets whether the output stage fails open on card power loss.

### Q: What happens to an active database transaction during a power loss?

**Status:** Partially Implemented

**Answer:** Every write in `data/storage.py` calls `self._db.commit()` immediately after its `execute()` (no batching, no long-lived open transaction) — SQLite's own durability guarantees (WAL/rollback journal, whichever mode is active; this codebase does not explicitly configure `PRAGMA journal_mode`) apply. A power loss mid-write would, at worst, lose that single uncommitted row (SQLite's atomic commit semantics prevent partial/corrupt writes reaching the file), not corrupt the database. This is a property of SQLite's default durability model, not something this codebase configures explicitly.

**Evidence:**
- data/storage.py — every write method (`record_measurement()`, `log_event()`, `record_execution_state()`, `start_run_summary()`, `finish_run_summary()`) calls `self._db.commit()` immediately
- No explicit `PRAGMA journal_mode`/`PRAGMA synchronous` setting found anywhere in data/storage.py or data/sqlite_manager.py

**Risks:** Without an explicit `PRAGMA synchronous=FULL` (or confirmation of SQLite's default), durability under real power loss (as opposed to process kill) is not fully guaranteed — this is a nuance beyond "SQLite is generally safe."

**Recommendation:** Explicitly set `PRAGMA journal_mode=WAL` and confirm `PRAGMA synchronous` is at a durability level appropriate for this system's power-loss risk profile, rather than relying on SQLite's un-configured default.

### Q: What information may be lost after an unexpected shutdown?

**Status:** Implemented (characterized, not prevented)

**Answer:** Per-sample measurements up to the last committed `record_measurement()` call are safe (immediate commit). What IS lost: the `run_summary` row's completion fields (`end_time`/`stop_reason`/`result`/duration/aggregate stats) if `finish_run_summary()` never ran, and any `event_log`/`station_state` entries that would have been written during the shutdown sequence itself (since that code never executes). In the worst case (mid-sample), less than one sample interval (`1/SAMPLE_RATE_HZ`) of measurement data is lost.

**Evidence:**
- data/storage.py:649-690 — `finish_run_summary()` is the sole writer of completion fields

**Risks:** An operator/report cannot distinguish "run genuinely still in progress elsewhere" from "run was abandoned by an unexpected shutdown" purely from the `run_summary` row — both look identical (`end_time IS NULL`).

**Recommendation:** See below — a startup scan for `run_summary` rows with `end_time IS NULL` from a PREVIOUS `run_id` (not the one just created) would resolve this ambiguity.

### Q: Can an incomplete run be detected after restart?

**Status:** Not Implemented

**Answer:** No code queries `run_summary` for incomplete rows at startup. `get_last_run_summary()`/`list_run_summaries()`/`get_run_summary()` exist as query methods but nothing calls them automatically at process start to check for `end_time IS NULL`.

**Evidence:**
- data/storage.py:692-711 — query methods exist but are not invoked at startup anywhere found in test.py/main.py

**Risks:** An operator has no automated signal that a previous run was left incomplete — must manually inspect the database or CSV files.

**Recommendation:** Add a startup check (e.g. in `main.py` or `test.py`'s entry flow) that queries the most recent `run_summary` row(s) for `end_time IS NULL` and surfaces a warning to the operator before any new run starts.

### Q: How is an interrupted run identified in the database?

**Status:** Partially Implemented

**Answer:** Implicitly identifiable via `run_summary.end_time IS NULL` (or `stop_reason IS NULL`/`result IS NULL`) — but this is not a dedicated, named state; it's the absence of the normal completion write. There is no explicit "INTERRUPTED"/"ABANDONED" `StopReason` value (only `COMPLETED`/`FAILED`/`SAFETY_VIOLATION`/`TIMEOUT`/`CANCELLED` exist in `utils/stop_reason.py`, and none of them represent "the process died before finishing").

**Evidence:**
- utils/stop_reason.py:19-24 — no INTERRUPTED/ABANDONED value
- data/storage.py:649-690 — completion fields only set by `finish_run_summary()`

**Risks:** Querying for `end_time IS NULL` is a reasonable heuristic but is not a first-class, documented convention anywhere in the codebase today.

**Recommendation:** Formalize "incomplete run" as a query pattern (`end_time IS NULL`) in a startup-check utility function, and document it as the canonical way to detect this state.

### Q: Does the system distinguish between: normal completion / aborted run / safety shutdown / unexpected power loss?

**Status:** Partially Implemented

**Answer:** The first three ARE distinguished via `StopReason`/`result` (`COMPLETED`/PASS, `CANCELLED`/`STOPPED_BY_OPERATOR`, `SAFETY_VIOLATION`/FAIL, `FAILED`/FAIL for relay/generic faults). "Unexpected power loss" (or any process-kill event) is NOT distinguished from "run still legitimately in progress" — both simply leave `run_summary.end_time` NULL, with no code differentiating them.

**Evidence:**
- test_control/battery_operation_sequence.py:105-174 — the four distinguished outcomes
- No code path writes a distinct marker for an in-progress-vs-abandoned run

**Risks:** Same ambiguity as above.

**Recommendation:** Same as above — a startup completeness check would need to infer "abandoned" (heuristically, e.g. by elapsed time since `start_time` with no `end_time`) since there is no direct signal.

### Q: When the application starts after a power loss, where does execution resume?

**Status:** Not Implemented

**Answer:** There is no resume logic at all. `test.py`/`main.py` start at the top-level menu every time, with no memory of any prior in-progress run beyond `DataStorage.get_last_execution_state()` (used only by the legacy Proto Test Execution workflow for DISPLAY purposes, not resume) and `get_last_run_summary()` (available but not automatically invoked at startup for Charge/Discharge Battery). `docs/DATABASE_ROADMAP.md` Section 4 explicitly documents cycle/state recovery as "NOT implemented — explicitly deferred."

**Evidence:**
- docs/DATABASE_ROADMAP.md:102 — "## 4. Cycle/state recovery (NOT implemented — explicitly deferred)"
- data/storage.py:495-516 — `get_last_execution_state()`, display-only per its own docstring

**Risks:** None beyond what's already documented as a known, deliberate gap.

**Recommendation:** None new — this matches docs/DATABASE_ROADMAP.md's own existing, accurate assessment.

### Q: Should the application: restart automatically / return to IDLE / require operator intervention / require run recovery confirmation? (design recommendation, since code likely doesn't implement this)

**Status:** Not Implemented (design question, no code exists)

**Answer/Recommendation:** Given a battery test system where an interrupted charge/discharge could leave a physical battery mid-cycle with unknown state, the safest design is: return to IDLE at the top-level menu (never auto-restart a test), AND require explicit operator acknowledgment of any detected incomplete prior run before allowing a new run on the SAME channel/position — i.e., require run recovery confirmation, not silent continuation. Automatic restart of a charge/discharge sequence without operator awareness of what state the physical battery was left in is unsafe and should never be implemented without an explicit, confirmed battery-state re-verification step (e.g. a fresh voltage/temperature reading compared against expected pre-cycle values) first.

**Evidence:** N/A — this is a forward-looking recommendation, not a code finding.

**Risks:** N/A.

**Recommendation:** Return to IDLE + mandatory operator confirmation of any detected incomplete run before that channel/position can be reused, gated behind `config/system_mode.py::is_recovery_enabled()` (which already exists as a documented, currently-off flag per docs/DATABASE_ROADMAP.md Section 4).

### Q: Is there a startup check for unfinished runs?

**Status:** Not Implemented — see above ("Can an incomplete run be detected after restart?").

**Recommendation:** See above.

### Q: Is there a startup check for unexpected shutdowns?

**Status:** Not Implemented — same as above; no distinct detection exists for this vs. any other incomplete-run cause.

**Recommendation:** See above.

### Q: Can the system recover traceability after a power failure?

**Status:** Partially Implemented

**Answer:** All measurement/event data committed BEFORE the power loss remains fully intact and queryable (SQLite's commit-per-write model) — traceability up to the last committed write is never lost. What cannot be recovered is any data that would have been written during the (never-executed) shutdown sequence, and the run's own completion status.

**Evidence:** data/storage.py's commit-per-write pattern throughout

**Risks:** None beyond what's already characterized above.

**Recommendation:** None new.

### Q: Can the system determine which group was running when power was lost?

**Status:** Implemented (via existing data, not a dedicated feature)

**Answer:** Yes, retroactively — `run_summary.battery_type`/hardware-identity fields (`smu_name`, `relay_matrix_name`, etc., populated by `start_run_summary()` before any relay closes) and `event_log`'s "Group selected: {group}" entry (test.py:4061) together identify which group/hardware a given `run_id` was using. This requires manually querying the last `run_summary`/`event_log` rows for the incomplete `run_id` — there is no automated surfacing of this at startup (see above).

**Evidence:**
- test.py:4046-4074 — group/hardware identity logged before any hardware activation
- data/storage.py:217-242 — `run_summary` schema carries this data

**Risks:** None beyond the general "no automated startup surfacing" gap already noted.

**Recommendation:** Include the group/channel/position in the startup incomplete-run check's operator-facing message (recommended above).

---

## SECTION 10 — BATTERY CONNECTION / WIRING ERRORS

### Q: What happens if a battery is connected with reversed polarity?

**Status:** Implemented

**Answer:** `ChargeSequence.run()`/`DischargeSequence.run()` now take a DMM reading with the SMU output still disabled — immediately after `relay.close()`/`record_execution_state(state="ACTIVE")` and BEFORE `set_charge_mode()`/`set_discharge_mode()`/`output_enable()` — via `interruptible_sleep(STABILIZATION_S)` then `pre_enable_v = self.dmm.measure_dc_voltage()`, then `BatteryOperationSequence._check_battery_polarity(pre_enable_v, ...)`. If `pre_enable_v <= Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V` (-0.5 V), a new `ReversePolarityError(SafetyViolationError)` is raised — an ERROR-level event is logged first via `storage.log_event(...)` — and the SMU output is never enabled. `ReversePolarityError` subclasses `SafetyViolationError`, so it flows through `run_guarded()`'s existing `SafetyViolationError` branch, triggering the identical `SafetyMonitor.emergency_stop()` shutdown (PMU off + all relays forced open, `StopReason.SAFETY_VIOLATION` recorded) — no new shutdown path was introduced.

**Evidence:**
- config/settings.py:118 — `REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = -0.5`
- utils/errors.py:32-47 — `ReversePolarityError(SafetyViolationError)` class + docstring
- test_control/battery_operation_sequence.py:83-114 — `_check_battery_polarity()`
- test_control/charge_sequence.py:143-148, test_control/discharge_sequence.py:154-160 — call sequence before `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`
- Verified via a mocked regression test: a DMM reading of -3.5V causes `ReversePolarityError` before `smu.set_charge_mode()`/`output_enable()` are ever called (asserted not called); a plausible positive reading proceeds normally to a completed charge.

**Risks:** None for the "is it safe to enable the SMU" question. Residual gap (see below): the check does not distinguish a reversed cell from a disconnected lead, a genuinely damaged/over-discharged cell, or a wiring fault — all read identically and raise the same `ReversePolarityError`. This is intentional scope for this check (safety gate, not root-cause diagnosis).

**Recommendation:** None for this entry; see the disambiguation gap noted later in this section.

### Q: Can reversed polarity be detected before enabling the SMU?

**Status:** Implemented

**Answer:** Yes — see above. The DMM reading and `_check_battery_polarity()` check both happen strictly before `set_charge_mode()`/`set_discharge_mode()`/`output_enable()` in both `ChargeSequence.run()` and `DischargeSequence.run()`.

**Evidence:**
- test_control/charge_sequence.py:143-148 — `interruptible_sleep()` → `measure_dc_voltage()` → `_check_battery_polarity()` → `set_charge_mode()` → `output_enable()`
- test_control/discharge_sequence.py:154-160 — same ordering for discharge

**Risks:** None identified — closed this session.

**Recommendation:** None.

### Q: Is there hardware protection against reverse polarity?

**Status:** Not Implemented (not addressed by software; unknown/unconfirmed at the hardware level)

**Answer:** Unchanged this session — no hardware-level reverse-polarity protection (e.g. a series diode, polarity-sensing relay interlock) is referenced anywhere in `config/devices.py`'s hardware inventory or `hardware/relay_eth.py`/`hardware/smu.py`. This is a hardware-datasheet/harness-design question outside this codebase's scope; the new software-side check (see above) is a mitigation for the electrical-enable decision, not a claim about physical hardware protection.

**Evidence:** No relevant reference found in config/devices.py's PXI_SLOTS/BATTERY_CHANNELS entries or hardware/ driver files.

**Risks:** Unconfirmed — this remains a bench/hardware validation question, not a software gap. Carried forward as a hardware blocker for the Real Hardware Validation milestone gate (see docs/architecture.md's readiness assessment).

**Recommendation:** Confirm with the physical connector/harness design documentation (outside this codebase) whether any keying or protection exists.

### Q: Is there software validation for reverse polarity?

**Status:** Implemented — see "What happens if a battery is connected with reversed polarity?" above.

**Recommendation:** None.

### Q: What voltage reading is expected from a reversed battery?

**Status:** Not Implemented (not analyzed in code — this remains an electrical/hardware question)

**Answer:** Unchanged this session — no code documents or predicts the expected reading beyond the new safety threshold (`Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = -0.5 V`, chosen to sit below plausible ADC/DMM offset noise on a near-zero cell per its own comment in config/settings.py) at/below which the SMU is refused. What the ACTUAL real-hardware reading of a reversed cell is has not been characterized on the bench.

**Evidence:** config/settings.py:110-118 (threshold + rationale comment); hardware/smu.py:294-321 (general compliance discussion, not polarity-specific)

**Risks:** Unconfirmed electrical behavior — hardware blocker, not a software gap.

**Recommendation:** Characterize this on the bench (with appropriate current limiting) before relying on any inferred behavior; confirm -0.5 V is an appropriate threshold once real readings are available.

### Q: Does the system classify reverse polarity as a safety event?

**Status:** Implemented

**Answer:** Yes — `ReversePolarityError` is a distinct, named subclass of `SafetyViolationError`. It is not merely the generic "Overvoltage"/"Undervoltage" classification; the raised message explicitly states "pre-enable voltage sanity check failed... at/below reverse-polarity threshold... SMU will NOT be enabled," giving operators an immediately actionable diagnosis distinct from a generic range violation.

**Evidence:**
- utils/errors.py:32-47 — `ReversePolarityError(SafetyViolationError)`
- test_control/battery_operation_sequence.py:104-114 — distinct message text

**Risks:** None identified — closed this session.

**Recommendation:** None.

### Q: Is reverse polarity logged in traceability?

**Status:** Implemented — `_check_battery_polarity()` logs an ERROR-level event via `self.storage.log_event(level="ERROR", source=self.source, channel=channel, relay=relay_address, message=message)` before raising `ReversePolarityError`, and `run_guarded()`'s `SafetyViolationError` branch logs a second event_log entry and records `StopReason.SAFETY_VIOLATION` in `run_summary`.

**Evidence:** test_control/battery_operation_sequence.py:109-114 (dedicated log_event call), 155-167 (run_guarded's SafetyViolationError branch)

**Risks:** None identified — closed this session.

**Recommendation:** None.

### Q: What happens if reverse polarity is detected during charging?

**Status:** Implemented — see "What happens if a battery is connected with reversed polarity?" above; identical mechanism for `ChargeSequence`.

**Evidence:** test_control/charge_sequence.py:143-148

**Recommendation:** None.

### Q: What happens if reverse polarity is detected during discharging?

**Status:** Implemented — same mechanism for `DischargeSequence`.

**Evidence:** test_control/discharge_sequence.py:154-160

**Recommendation:** None.

### Q: Is relay activation blocked when reverse polarity is detected?

**Status:** Not Implemented (by design — unchanged)

**Answer:** No — the relay is still always closed BEFORE the polarity check runs (the check needs the relay closed to take a real DMM reading of the connected battery). What is blocked is the SMU output enable that follows. This is intentional: the check answers "is it safe to apply the SMU output," not "should the relay ever connect to this battery."

**Evidence:** test_control/charge_sequence.py:143-148 — relay closed, then stabilization wait, then DMM read, then polarity check, then (if it passes) `set_charge_mode()`/`output_enable()`

**Risks:** None identified — closing the relay to a reversed/disconnected/damaged battery with the SMU output disabled carries no meaningful electrical risk; this is the correct ordering.

**Recommendation:** None.

### Q: Is SMU output blocked when reverse polarity is detected?

**Status:** Implemented

**Answer:** Yes — `_check_battery_polarity()` runs strictly before `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`; a `ReversePolarityError` prevents all three from ever being called. Verified via mocked regression test (`not smu.set_charge_mode.called`, etc.).

**Evidence:** test_control/charge_sequence.py:143-148, test_control/discharge_sequence.py:154-160

**Risks:** None identified — closed this session.

**Recommendation:** None.

### Q: Is operator intervention required after reverse polarity detection?

**Status:** Implemented — same as any other `SafetyViolationError`: the operation aborts entirely (`emergency_stop()` runs, `run_summary` records `SAFETY_VIOLATION`/FAIL), and the operator must start a new run/confirmation flow from the top — there is no auto-retry.

**Evidence:** test_control/battery_operation_sequence.py:155-167

**Recommendation:** None.

### Q: Can a damaged battery be mistakenly interpreted as reverse polarity?

**Status:** Partially Implemented

**Answer:** Yes, deliberately so, and this is documented as intentional scope rather than an oversight: `_check_battery_polarity()`'s own docstring states it "does not attempt to distinguish a reversed cell from a disconnected lead or a genuinely damaged cell; only that none of those are safe to apply the SMU output to." A damaged/over-discharged cell reading at or below -0.5 V raises the exact same `ReversePolarityError` as a genuinely reversed connection — the check answers "is it safe to enable the SMU," not "what is wrong with the battery." This is a residual, intentional gap.

**Evidence:**
- test_control/battery_operation_sequence.py:96-100 — `_check_battery_polarity()` docstring stating this explicitly
- utils/errors.py:43-47 — `ReversePolarityError` docstring, same statement

**Risks:** Diagnostic ambiguity for operators/maintenance persists — a `ReversePolarityError` in the logs means "unsafe to enable," not "definitely a reversed cell." Low safety risk (the SMU is correctly kept off either way); moderate root-cause-diagnosis friction.

**Recommendation:** If real-hardware validation shows this ambiguity causes meaningful operational friction, consider a secondary diagnostic (e.g. comparing the pre-enable reading's magnitude against the battery's nominal open-circuit voltage range) to suggest "likely reversed" vs. "likely damaged/disconnected" in the log message — out of scope for this session, deferred per explicit instruction.

### Q: How does the system distinguish: disconnected battery / reversed battery / deeply discharged battery / wiring fault?

**Status:** Partially Implemented

**Answer:** It still does not distinguish among these four causes — all four that produce a pre-enable reading at or below -0.5 V now share the single `ReversePolarityError` classification (an improvement over the prior generic Overvoltage/Undervoltage message, but not a root-cause diagnosis). This is intentional, documented scope for this session's fix, not an oversight.

**Evidence:** test_control/battery_operation_sequence.py:96-100 — docstring explicitly disclaiming disambiguation

**Risks:** Same diagnostic-ambiguity risk as above — deferred, not a blocker for hardware validation (the check's job is safety, not diagnosis).

**Recommendation:** Same as above — deferred pending real-hardware operational experience.

### Q: What happens if the measured voltage is outside the physically possible range for the selected battery type?

**Status:** Implemented (generically, not specifically)

**Answer:** `SafetyMonitor.check()`'s `voltage_v > v_max`/`voltage_v < v_min` checks (resolved from `BATTERY_CONFIGS[battery_type]`) DO catch any reading outside the configured battery's expected window and raise `SafetyViolationError` — this is implemented, but it's the same generic overvoltage/undervoltage classification used for every other out-of-range cause, not a "physically impossible for this battery type" specific diagnosis (e.g. a reading of -2V, which is not just "undervoltage" but physically nonsensical for a Li-ion cell, gets the same "Undervoltage: -2.000 V < 3.0 V" message as a merely-low-but-plausible 2.9V reading).

**Evidence:** test_control/safety_monitor.py:96-100

**Risks:** Below the new -0.5 V pre-enable threshold, a reading is now caught distinctly (as `ReversePolarityError`) before the SMU is ever enabled; above that threshold but still outside `voltage_min_v`/`voltage_max_v`, readings remain classified generically as Overvoltage/Undervoltage. No distinction exists between "slightly below floor" and "physically impossible but above -0.5V" readings.

**Recommendation:** None new — covered by this session's fix for the below-threshold case; the residual generic-classification gap above threshold is low-priority.

### Q: Should reverse polarity generate: SafetyViolationError / HardwareConfigurationError / a dedicated error type? (recommendation)

**Status:** Implemented

**Answer:** Implemented exactly as previously recommended — `ReversePolarityError(SafetyViolationError)` now exists in `utils/errors.py`, subclassing `SafetyViolationError` so it is caught by `run_guarded()`'s existing safety-violation branch and triggers the identical `emergency_stop()` shutdown, while carrying a distinct, diagnosable name/message. The detection logic lives in `BatteryOperationSequence._check_battery_polarity()`, called from `ChargeSequence.run()`/`DischargeSequence.run()` immediately before `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`, with the SMU output disabled.

**Evidence:**
- utils/errors.py:32-47 — `ReversePolarityError(SafetyViolationError)`
- config/settings.py:118 — `REVERSE_POLARITY_VOLTAGE_THRESHOLD_V`
- test_control/battery_operation_sequence.py:83-114 — `_check_battery_polarity()`
- test_control/charge_sequence.py:143-148, discharge_sequence.py:154-160 — call sites, before SMU enable

**Risks:** None for the safety-gate function this implements. Residual, intentional: no disambiguation between reversed/damaged/disconnected/wiring-fault (see above) — deferred, not a blocker.

**Recommendation:** None. This was the single highest-value safety addition identified by the prior review and is now closed.

---

## SECTION 11 — SYSTEM RECOVERY

### Q: After an unexpected shutdown, what checks are performed before hardware can be activated again?

**Status:** Implemented (for hardware safety), Not Implemented (for run/database recovery)

**Answer:** `HardwareManager.connect_all()` unconditionally re-verifies a safe baseline on every fresh start regardless of why the previous session ended: PMU output is force-verified OFF (`emergency_output_off("startup safety check")`) and all relays are force-verified OFF (`open_all()`) before any battery test can begin — this happens every time `HardwareManager` is constructed and connected, with no dependency on how the previous process ended. There is NO check of the DATABASE for an incomplete prior run before allowing hardware activation (see Section 9).

**Evidence:**
- test_control/hardware_manager.py:202-233 (strict mode), 247-334 (lenient mode) — startup safety enforced unconditionally in both modes

**Risks:** Hardware-level safety is well covered; run/traceability-level recovery awareness is not (see Section 9).

**Recommendation:** Add the database-level incomplete-run check recommended in Section 9, layered on top of the existing (solid) hardware-level startup safety checks.

### Q: Is hardware validation repeated after restart?

**Status:** Implemented — yes, every `HardwareManager` construction re-runs full connect + startup-safety verification (see above); nothing is skipped or cached from a previous session.

**Evidence:** test_control/hardware_manager.py:164-343

**Risks:** None identified.

**Recommendation:** None.

### Q: Is configuration validation repeated after restart?

**Status:** Implemented — `validate_group_test_config()` runs fresh on every single operator invocation of Charge/Discharge Battery (it's called inside `_run_charge_or_discharge()`, not cached across the process lifetime), so a restart trivially re-validates.

**Evidence:** test.py:3992-3996 (called every invocation, not once at process start)

**Risks:** None identified.

**Recommendation:** None.

### Q: Is operator confirmation required after restart?

**Status:** Implemented (for starting any new run at all, not specifically "after restart")

**Answer:** `_confirm_operation()` requires operator confirmation before ANY Charge/Discharge Battery run starts, restart or not — test.py:4015-4018. There is no restart-specific ADDITIONAL confirmation (e.g. "a previous run was left incomplete, confirm before proceeding") because incomplete-run detection itself doesn't exist yet (Section 9).

**Evidence:** test.py:4015-4018

**Risks:** None beyond the general recovery-detection gap.

**Recommendation:** Once incomplete-run detection is added (Section 9's recommendation), extend this confirmation step to surface that information specifically.

### Q: Can a recovered run continue automatically?

**Status:** Not Implemented — no resume capability exists at all (see Section 9); this question does not apply because there is no concept of a "recovered run" in the current codebase, only a brand-new run every time.

**Recommendation:** See Section 9/Section 11's design recommendation (never auto-continue; always start fresh with operator awareness of the prior incomplete state).

### Q: Should recovered runs always start from the beginning? (recommendation)

**Status:** Not Implemented (design recommendation)

**Answer/Recommendation:** Yes. Given the battery-physical-state uncertainty inherent in any interrupted charge/discharge cycle (unknown SoC, unknown time spent in an ambiguous state, unconfirmed relay/SMU state during the gap), the only safe design is to always treat a "recovered" position as requiring a fresh start — re-validate configuration, re-verify hardware safe-state (already done unconditionally per above), and require the operator to explicitly acknowledge the prior incomplete run before starting the new one. Never attempt to resume a CC-CV/CC-discharge state machine mid-way based on stale, unverified data.

**Evidence:** N/A — forward-looking.

**Recommendation:** As stated; consistent with `is_recovery_enabled()`'s current default-off posture per docs/DATABASE_ROADMAP.md Section 4.

### Q: Is recovery behavior documented and traceable?

**Status:** Partially Implemented

**Answer:** The ABSENCE of recovery is well-documented (docs/DATABASE_ROADMAP.md Section 4 explicitly describes it as deferred, with a sketch of what would be needed) — this is honest and accurate documentation of a gap, not a claim of a feature that doesn't exist. There is no actual recovery behavior to trace because none is implemented.

**Evidence:** docs/DATABASE_ROADMAP.md:102-150

**Risks:** None beyond the feature gap itself.

**Recommendation:** None — the existing documentation of this gap is accurate and should be preserved as-is (verified during this review, no correction needed).

---

## SECTION 12 — PRE-HARDWARE VALIDATION CONCLUSION

*Updated this session to reflect the closure of four MUST-FIX items: reverse polarity protection, battery-type validation, timeout traceability, and database startup hardening. See docs/architecture.md "Pre-Hardware-Validation MUST-FIX Closure" for the consolidated writeup. Sections 1-4, 6, 8, 9, 11 below Section 12 headers are unaffected by this session's changes; only the tallies and lists below have been re-derived from the entries actually changed above.*

*Addendum (later session): Section 1's "Is there a mandatory delay between relay state transitions?" and "Is relay settling time configurable?" both moved from Not Implemented to Implemented -- `Settings.RELAY_SETTLE_TIME_S` (`2.0` s) is now the single global relay settling/dead-time constant, enforced in `RelayBase.open()`/`close()`, with the one documented native-primitive exception (`test.py::test_relay_ethernet_test()`) calling `RelayBase.settle()` explicitly. See `docs/architecture.md` Sections 43-44. GREEN count: 44 (+2). The two former Not-Implemented entries are removed from any RED/YELLOW tracking below; no other item in this list was affected.*

### Classification Summary

**GREEN (already handled) — 42 items**, including everything previously green PLUS this session's five closures: (1) reverse-polarity pre-output-enable voltage sanity check with a dedicated `ReversePolarityError(SafetyViolationError)` classification, distinct traceability logging, and confirmed SMU-output blocking (Section 10); (2) explicit `battery_type` existence validation in `validate_group_test_config()` and in the two direct-lookup `test.py` paths (`_run_monitor_battery()`, `_run_monitor_battery_scan()`), eliminating the bare `KeyError` (Section 5); (3) `StopReason.TIMEOUT` now actually assigned via `run_guarded()`'s dedicated `except NIPXITimeoutError` branch (Sections 3-4); (4) `storage.open()`/`start_run_summary()` now wrapped in clean, operator-facing `[FAIL]` handling (`_open_storage_guarded()`/`_start_run_summary_guarded()`) across all four real workflow entry points, with hardware safely disconnected on failure (Section 7).

**YELLOW (partially handled) — 14 items**, including: reverse-polarity/damaged-battery/disconnected-lead/wiring-fault disambiguation still does not exist (all four now share the single `ReversePolarityError` classification instead of a generic message — an improvement, but still not a root-cause diagnosis; intentionally deferred, Section 10); the Safety Monitor Simulator's `_select_safety_simulation_group()` and its two callers (test.py:2623, 2769) still do a bare `BATTERY_CONFIGS[...]` lookup with no existence guard (simulator/demo-only, no hardware risk; Section 5); database write failures in `record_measurement()`/`record_execution_state()`/`log_event()` (per-write, mid-test) remain unwrapped, unlike the now-guarded `storage.open()`/`start_run_summary()` (Section 7); Ctrl+C during the final shutdown/teardown window not protected; cross-group concurrent execution not interlocked (no known real risk today, single-process CLI); SafetyMonitor coverage not exhaustively re-audited across every `test.py` commissioning workflow; and the general residual "hardware may still be energized" risk under total communication loss (always logged CRITICAL, never silently hidden, but not fully closable in software).

**RED (not handled) — 14 items**, now concentrated almost entirely in **power-loss / incomplete-run recovery** — no startup check for an incomplete `run_summary` row, no distinction between "still running" and "abandoned," no resume/recovery logic (explicitly and accurately documented as deferred, not a silent gap) — plus the unconfirmed hardware fail-safe questions (SMU output stage and Numato relay module behavior on power loss, physical reverse-polarity protection in the harness/connector design). The reverse-polarity-detection RED cluster from the prior review is now closed (moved to GREEN, with two narrow residual items moved to YELLOW above).

### Top 10 Risks

1. **Power-loss/incomplete-run detection does not exist** — an interrupted run is indistinguishable from an in-progress one in the database; no startup check exists. (Unchanged — explicitly out of scope for this session.)
2. **Unconfirmed hardware fail-safe behavior on power loss** — whether the SMU output stage and Numato relay module fail open when their own power is lost is undocumented and unconfirmed against any datasheet (flagged in docs/architecture.md as "least-characterized risk in the system"). (Unchanged — hardware bench-test item, not software.)
3. **Reverse-polarity/damaged-battery/disconnected-lead disambiguation does not exist** — all now correctly raise `ReversePolarityError` (closed: the SMU is never unsafely enabled), but an operator cannot tell which of the four physical conditions actually occurred from the log message alone. Intentionally deferred per this session's scope.
4. **A second Ctrl+C during the final hardware-teardown window is unprotected** — could interrupt `disconnect_all()`/`emergency_stop()` mid-sequence; `atexit` backstops mitigate but don't eliminate this. (Unchanged.)
5. **Database write failures in `record_measurement()`/`record_execution_state()`/`log_event()` are still not wrapped for clean error handling** — an in-flight SQLite failure mid-test propagates as an unhandled exception with a raw traceback rather than a clean operator message (though hardware safety shutdown still runs via the generic exception path). Narrower than before: `storage.open()`/`start_run_summary()` are now guarded.
6. **No foreign-key/referential-integrity enforcement between `measurements`/`event_log`/`station_state` and `run_summary`** — an orphaned row is possible in principle (not observed in practice). (Unchanged.)
7. **A total communication-loss failure during emergency shutdown can leave hardware energized** — logged CRITICAL, never hidden, but not closable by software alone; requires a hardware-level fail-safe. (Unchanged.)
8. **SafetyMonitor coverage across every `test.py` hardware-commissioning workflow (SMU Functional Validation, Relay Ethernet Test, Proto Test Execution, Monitor Battery/Scan) was not exhaustively re-audited in this review.** (Unchanged.)
9. **The Safety Monitor Simulator's `battery_type` lookup (test.py:2623, 2769) has no existence guard**, unlike the three real workflow paths fixed this session — simulator-only, no hardware risk, low priority.
10. **Unconfirmed physical/mechanical reverse-polarity protection in the battery connector/harness design**, outside this codebase — a hardware/bench-test question, not a code gap.

### Top 10 Open Questions

1. Does the PXI chassis / Numato relay module retain independent power when the host PC loses power? (Unconfirmed — docs/architecture.md's own "least-characterized risk.")
2. Do the SMU cards (NI-4141/4139/4130) fail open (de-energize output) on card power loss? (Unconfirmed.)
3. What is the actual, real-hardware voltage/current signature of a reversed-polarity battery connection on this specific SMU configuration, and is the -0.5 V `REVERSE_POLARITY_VOLTAGE_THRESHOLD_V` threshold well-chosen against real readings? (Not characterized — bench test needed.)
4. Is there any physical/mechanical reverse-polarity protection in the battery connector/harness design, outside this codebase? (Unknown from code alone.)
5. What SQLite `PRAGMA journal_mode`/`synchronous` setting is actually in effect (default, unconfigured), and is it adequate for this system's power-loss durability requirements?
6. Is concurrent multi-process execution against different battery groups (A vs. B) ever intended to be supported, and if so, what interlock is needed?
7. What is the intended behavior when an operator restarts the application and a previous run's `run_summary.end_time` is NULL — should this block new runs on that same channel/position specifically, or just warn globally?
8. Should the SMU's electrical mode (`output_function`/`source_mode`) be periodically re-verified during the sampling loop, or is one-time configuration verification sufficient given real-hardware behavior?
9. Has SafetyMonitor coverage been confirmed complete for every non-Charge/Discharge hardware-touching workflow in test.py (commissioning tests, Monitor Battery, Monitor Battery Scan)?
10. Is a secondary heuristic (e.g. comparing pre-enable voltage magnitude against nominal open-circuit range) worth adding to distinguish "likely reversed" from "likely damaged/disconnected" in the `ReversePolarityError` message, once real-hardware operational experience is available?

### Recommended Actions Before Hardware Validation

1. ~~Implement a pre-output-enable voltage sanity check... and a dedicated `ReversePolarityError`...~~ **Done this session.**
2. ~~Add an explicit existence check for `battery_type`...~~ **Done this session** for all three real workflow paths (`validate_group_test_config()`, `_run_monitor_battery()`, `_run_monitor_battery_scan()`); simulator path (test.py:2623, 2769) intentionally deferred as low-priority/no-hardware-risk.
3. ~~Wire `StopReason.TIMEOUT` through `run_guarded()`'s exception handling...~~ **Done this session.**
4. ~~Wrap `storage.open()`/`start_run_summary()` calls... with clean try/except error reporting...~~ **Done this session** for all four real workflow entry points (`_run_monitor_battery()`, `_run_monitor_battery_scan()`, `_run_charge_or_discharge()`, `run_proto_test_execution()`).
5. Confirm (bench test, not code) the power-loss fail-safe behavior of the SMU output stage and Numato relay module — this directly affects what safety claims can honestly be made before batteries are connected. **Still outstanding — hardware-side, cannot be closed in software.**

### Recommended Actions After Hardware Validation

1. Add a startup check for `run_summary` rows with `end_time IS NULL` from a previous session, surfaced to the operator with the associated group/channel identity (available via `event_log`/`run_summary` hardware-identity fields).
2. Explicitly configure SQLite `PRAGMA journal_mode`/`synchronous` rather than relying on the unconfigured default, once the desired durability/performance tradeoff is confirmed against real usage patterns.
3. Audit every `test.py` hardware-commissioning workflow (SMU Functional Validation, Relay Ethernet Test, Proto Test Execution) for SafetyMonitor coverage parity with Charge/Discharge Battery.
4. Design and gate a formal recovery-confirmation flow behind `config/system_mode.py::is_recovery_enabled()`, per docs/DATABASE_ROADMAP.md Section 4's own sketch, once real-world incomplete-run frequency data justifies the investment.
5. Consider adding `FOREIGN KEY` constraints (with `PRAGMA foreign_keys = ON`) between `measurements`/`event_log`/`station_state` and `run_summary` for defense-in-depth referential integrity, if reporting tooling built on top of this database would benefit from database-enforced guarantees rather than code-convention-only guarantees.

---

## SECTION 13 — MONITOR BATTERY OPERATIONAL BEHAVIOR (Real Hardware Validation Readiness Review)

*Findings below come from an implementation-level readiness review of `test_control/monitor_battery_sequence.py::MonitorBatterySequence`, performed against the actual code (not assumptions), ahead of Monitor Battery being the first workflow used for Real Hardware Validation. See docs/architecture.md Section 45 for the full technical writeup this section summarizes.*

### Q: Is Monitor Battery intended to run forever until cancelled?

**Status:** Implemented (by design)

**Answer:** Yes. `_loop()`'s `while True:` has no `break`, no iteration cap, and no duration check anywhere in the file. There is no normal-completion path -- `BatteryOperationSequence.complete()` (the shared "finished successfully" bookkeeping other operations use) is never called by Monitor Battery. Cancellation (Ctrl+C) or a fault are the only two ways the loop ends. This is intentional, not an oversight -- the module's own docstring states monitoring continues "until the operator cancels ... or a real fault occurs."

**Evidence:**
- test_control/monitor_battery_sequence.py:149-186 -- `while True:` loop, no `break`
- test_control/monitor_battery_sequence.py:121-123 -- module docstring stating the intended lifecycle
- test_control/battery_operation_sequence.py:215-222 -- `complete()`'s docstring: "Monitor Battery has no normal completion path ... and never calls this"

**Risks:** None -- this is the correct design for a continuous-monitoring workflow, not a defect.

**Recommendation:** None.

### Q: Is there an automatic timeout or automatic stop condition?

**Status:** Not Implemented (by design)

**Answer:** No. No `NIPXITimeoutError` is ever raised by this sequence's own code, and no maximum-duration setting is read anywhere in `monitor_battery_sequence.py`. The only stops are operator cancellation or an unhandled fault (DMM, relay, or database).

**Evidence:**
- test_control/monitor_battery_sequence.py -- no `NIPXITimeoutError`, no duration/timeout constant referenced
- test_control/battery_operation_sequence.py:183-199 -- `run_guarded()`'s `NIPXITimeoutError` branch exists for other operations but nothing in Monitor Battery ever raises it

**Risks:** None identified -- an unattended, indefinitely-running monitor is the intended use case for this workflow.

**Recommendation:** None.

### Q: Is measurement data buffered, or persisted immediately?

**Status:** Implemented

**Answer:** Persisted immediately, every iteration -- there is no buffering or batching anywhere in the write path. Each of `DataStorage.record_measurement()`/`record_execution_state()`/`log_event()` issues one `INSERT` and calls `commit()` before returning. Per loop iteration: one DMM read, one `record_measurement()` (one commit), one screen render.

**Evidence:**
- data/storage.py -- `record_measurement()`, `record_execution_state()`, `log_event()` each commit synchronously, no `executemany`/queue/batch logic anywhere in the file
- test_control/monitor_battery_sequence.py:152-178 -- one DMM read + one `record_measurement()` + one `_render_frame()` per iteration

**Risks:** DB write latency is on the critical path of every sample -- acceptable for this workflow's cadence (default 2s/sample), but worth keeping in mind if the sample rate is ever increased significantly.

**Recommendation:** None at current sample rates.

### Q: Is the relay switched repeatedly during monitoring, or held closed?

**Status:** Implemented

**Answer:** The relay closes exactly once, before the loop starts, and stays closed for the entire monitoring session -- there is no periodic re-close/re-verify inside the loop. It is opened only on exit (fault or cancellation), via the shared safety-shutdown path.

**Evidence:**
- test_control/monitor_battery_sequence.py:138 -- `self.relay.close(relay_address)`, called once, before `while True:`
- test_control/battery_operation_sequence.py:141-213 -- every `run_guarded()` exception branch calls `safety.safe_cancel_shutdown()`/`safety.emergency_stop()`, both of which open the relay

**Risks:** None -- this matches the intended electrical behavior (one continuous connection for the duration of monitoring).

**Recommendation:** None.

### Q: What is the expected Ctrl+C (cancellation) shutdown sequence?

**Status:** Implemented

**Answer:** `check_cancellation(token)` (checked at loop-top and inside `interruptible_sleep()`) raises `OperationCancelledError`, caught by `run_guarded()`'s dedicated branch:
`OperationCancelledError` -> `log_event()` -> `record_execution_state(CANCELLED)` -> `finish_run_summary(stop_reason=CANCELLED, result="STOPPED_BY_OPERATOR")` -> `safety.safe_cancel_shutdown()` (SMU output-off + relay `open_all()`, both internally guarded, documented as "never raises") -> re-raise -> caught cleanly at `test.py` ("hardware is in a verified safe state") -> outer `finally` closes storage and calls `hw_mgr.disconnect_all()` (its own relay `open_all()` backstop).

**Evidence:**
- test_control/battery_operation_sequence.py:141-153 -- the `OperationCancelledError` branch
- test.py:3849-3850, 3858-3868 -- the outer catch and `finally` cleanup

**Risks:** None -- this is the intended, fully-verified normal termination path.

**Recommendation:** None.

### Q: What happens if the assigned DMM is missing or fails?

**Status:** Implemented (mode-dependent)

**Answer:** Depends on `HardwareManager`'s connect mode:
- **PRODUCTION (strict):** DMM connect failure raises `HardwareInitError` from `connect_all()` -- caught at `test.py`, printed as `[FAIL] Hardware initialization failed`, and the workflow never starts. No storage is opened, no relay is touched.
- **DEVELOPMENT/VALIDATION (lenient):** DMM connect failure is logged and recorded in `hardware_status["DMM"]` but does not block startup. The workflow proceeds: relay closes, monitoring starts, the first `dmm.measure_dc_voltage()` call raises `DMMError`, which is caught by `run_guarded()`'s generic exception branch, triggering the full emergency-shutdown sequence (relay opens) and ending the workflow.

Once running, if the DMM is disconnected mid-loop, the same `DMMError` path applies -- there is no distinction in the code between "never connected" and "disconnected mid-run" once the sequence is executing.

**Evidence:**
- test_control/hardware_manager.py -- `_connect_all_strict()` (all-or-nothing) vs. `_connect_all_lenient()` (per-device, non-blocking)
- hardware/dmm.py -- `measure_dc_voltage()` wraps any underlying failure in `DMMError`
- test_control/battery_operation_sequence.py:201-213 -- the generic exception branch that catches `DMMError`

**Risks:** In lenient/DEVELOPMENT mode specifically, the relay briefly activates before the DMM failure is discovered -- a real (if brief) relay activation with no working measurement path. Not a hardware-safety risk (the shutdown sequence still runs correctly), but worth operator awareness during commissioning.

**Recommendation:** None required for Real Hardware Validation (production mode is the intended real-battery configuration and fails closed, before any hardware activation).

### Q: Does Monitor Battery retry a failed measurement?

**Status:** Not Implemented (by design)

**Answer:** No. There is no retry/backoff logic anywhere in `hardware/dmm.py`'s `connect()`/`measure_dc_voltage()`, nor in `monitor_battery_sequence.py`'s loop, nor in `run_guarded()`. The first measurement failure ends the workflow via the standard fault-shutdown path.

**Evidence:**
- hardware/dmm.py -- `measure_dc_voltage()` raises immediately on first failure, no retry wrapper
- test_control/monitor_battery_sequence.py -- no retry/backoff around the DMM call

**Risks:** A single transient DMM read glitch (rather than a genuine disconnect) would end an otherwise-healthy multi-hour monitoring session. Acceptable given the project's stated safety-first philosophy (never mask a real fault by retrying blindly), but worth operator awareness for long unattended runs.

**Recommendation:** None required to reach GO for Real Hardware Validation; consider only if real-hardware experience shows transient DMM read failures are common enough to matter.

### Q: Is Monitor Battery safe to run continuously for many hours (e.g. an 8-hour unattended session)?

**Status:** Implemented / Reviewed -- Supported

**Answer:** Yes. The review found no memory-growth risk (only four floats are accumulated in `_VoltageStats` across the entire run; every measurement is persisted immediately, not held in memory) and no buffering issue (see the persistence entry above). Database write latency is on the critical path per sample but is not a correctness or stability risk at this workflow's sample rate.

**Evidence:**
- test_control/monitor_battery_sequence.py:70-107 -- `_VoltageStats`, bounded in-memory state
- data/storage.py -- synchronous per-call commits, no accumulating buffer

**Risks:** None identified for the 8-hour case specifically. General long-run software risks (a database or DMM fault at any point) are covered by the fault-handling entries above, not specific to duration.

**Recommendation:** None.

### Q: What happens if the database becomes unavailable during a Monitor Battery run?

**Status:** Partially Implemented

**Answer:** The run always stops, and the relay is always opened -- but not necessarily through the "clean" classified shutdown path. Each `run_guarded()` exception branch calls `storage.log_event()`/`record_execution_state()`/`finish_run_summary()` **before** calling `safety.emergency_stop()`/`safe_cancel_shutdown()`. If the database itself is the original failure, those storage calls raise a second exception (the DB is still broken), which propagates out of `run_guarded()` immediately -- skipping `safety.emergency_stop()` *at that layer*. The relay is still opened, but only via the outer backstop: `test.py::_run_monitor_battery()`'s `finally` block unconditionally calls `hw_mgr.disconnect_all()`, whose own relay `open_all()` step is independently guarded and cannot raise. Net effect: hardware ends up safe either way, but the failure's `run_summary`/`event_log` classification (stop reason, "why did this stop") may be missing or generic for this specific failure mode, rather than cleanly recorded as it would be for a DMM/relay fault.

**Evidence:**
- test_control/battery_operation_sequence.py:141-213 -- storage calls precede `safety.emergency_stop()`/`safe_cancel_shutdown()` in every branch
- data/storage.py -- `record_measurement()`/`record_execution_state()`/`log_event()` do not catch their own `sqlite3.Error`
- test_control/hardware_manager.py:375-383 -- `disconnect_all()`'s independently-guarded relay `open_all()` backstop
- test.py:3858-3868 -- the outer `finally` that always calls `disconnect_all()`

**Risks:** Diagnostic/traceability gap only -- hardware is not left energized in this scenario. This is the one documented caveat carried into the GO decision below.

**Recommendation:** Optional hardening (not a blocker): wrap the storage calls inside `run_guarded()`'s except-branches so a DB failure while handling an unrelated fault does not itself prevent `safety.emergency_stop()` from running at that layer (it would still be redundant with the outer backstop, but would restore clean stop-reason classification for this specific failure mode).

### Q: Is Monitor Battery ready for Real Hardware Validation?

**Status:** Implemented / Reviewed -- **GO**

**Answer:** Yes. The reviewed behavior matches the intended design in every respect: continuous operation until cancelled or faulted, immediate/synchronous persistence, relay held closed for the duration and opened on every exit path (directly or via the outer backstop), and a fully-verified Ctrl+C shutdown sequence. No software blocker was identified. The one caveat -- incomplete failure traceability specifically in the case of a mid-run database failure (see above) -- does not affect hardware safety and is documented, not hidden.

**Evidence:** See all entries in this section, and docs/architecture.md Section 45.

**Risks:** The database-failure traceability gap above; otherwise none identified.

**Recommendation:** Proceed with Real Hardware Validation using Monitor Battery. Track the optional `run_guarded()` hardening above as a low-priority follow-up, not a gate.

## SECTION 14 — PRODUCTION RUNTIME ARCHITECTURE REVIEW (Reuse, Concurrency, Failure Policy)

*Findings below summarize the pre-implementation architecture review performed ahead of designing a production runtime that starts every enabled `BATTERY_GROUPS` group and runs indefinitely. See docs/architecture.md Section 46 for the full technical writeup this section summarizes.*

### Q: Do MonitorBatterySequence, MonitorBatteryScanSequence, ChargeSequence, and DischargeSequence all share one architecture?

**Status:** Implemented

**Answer:** Yes, for the code path `test.py` drives. All four subclass `BatteryOperationSequence` and share its `run_guarded()` (shutdown), `SafetyMonitor` usage, `DataStorage` read/write methods, hardware-identity traceability helpers, and `CancellationToken`-based cancellation -- no per-sequence reimplementation of any of these.

**Evidence:** `test_control/battery_operation_sequence.py`; `test_control/monitor_battery_sequence.py`, `monitor_battery_scan_sequence.py`, `charge_sequence.py`, `discharge_sequence.py`; `test.py::_run_monitor_battery()`/`_run_monitor_battery_scan()`/`_run_charge_or_discharge()`.

**Risks:** None within this code path. See the next question for where this breaks down elsewhere in the repo.

**Recommendation:** None for this path.

### Q: Is ChargeSequence/DischargeSequence really the only implementation of charging/discharging in this codebase today?

**Status:** Not yet -- a real gap

**Answer:** No. `main.py` (the actual `python main.py` entry point) builds and runs a separate, live implementation: `TestExecutor` -> `BatteryTestSequence` -> `ChargeCycle`/`DischargeCycle`. It shares `SafetyMonitor` but not `BatteryOperationSequence`, has no reverse-polarity check, and writes through the legacy `DataStorage.record()` path -- no `event_log`/`run_summary`/hardware-identity traceability for any run it drives.

**Evidence:** `main.py:40,113`; `test_control/test_executor.py:37-40,136-137`; `test_control/battery_test.py`; `test_control/charge_cycle.py` (no polarity/`ReversePolarityError` reference anywhere in the file).

**Risks:** A production Runtime built on `ChargeSequence`/`DischargeSequence` becomes a *third* implementation unless `main.py`'s path is explicitly retired or rewritten onto the same architecture as part of this effort.

**Recommendation:** Make retiring/replacing `main.py`'s `TestExecutor` path an explicit deliverable of the Runtime effort, not an afterthought.

### Q: Does `BATTERY_GROUPS` already contain enough information to derive hardware ownership / concurrency?

**Status:** Data model: yes. Real hardware inventory: not yet for 3-way concurrency.

**Answer:** `hardware_for_group()` already resolves relay_matrix/smu/dmm/daq names per group -- a "hardware set" is a pure derivation (group by shared resource name), no new topology file needed. But `PXI_SLOTS` has only one enabled DMM (`MAIN_DMM`) and one enabled DAQ (`MAIN_DAQ`) in the whole rack, and every configured group references them -- so true independent 3-hardware-set concurrency isn't supported by current hardware inventory yet, only by the data model.

**Evidence:** `config/devices.py::hardware_for_group()` (647-674), `BATTERY_GROUPS`/`PXI_SLOTS`/`ETHERNET_DEVICES`; `docs/TODO.md`'s existing Group B/`MAIN_DAQ` sharing note.

**Risks:** Two "concurrent" hardware sets sharing the one real DMM/DAQ would contend for telemetry; `HardwareManager` also has no built-in concept of a second concurrent owner of the same physical instrument.

**Recommendation:** Add a resource-checkout/scheduling layer in the Cycle Controller (not a new hardware/safety implementation) so a group only starts once every resource name it needs is free.

### Q: If a fault occurs in one active cycle, should the Runtime stop only that hardware set, or everything?

**Status:** Design recommendation (not yet implemented)

**Answer:** Isolate the affected hardware set by default (Option A) -- `SafetyMonitor`'s shutdown methods already operate per-`(smu, relay_matrix)`, and an unrelated hardware set has no causal exposure to another set's fault. Escalate to stopping every hardware set that depends on a resource that is *itself* the failure (today: `MAIN_DMM`/`MAIN_DAQ`, or the shared SQLite file) -- derived from the same ownership tuple used for scheduling, not a blanket global stop.

**Evidence:** `test_control/safety_monitor.py::emergency_stop()`/`safe_cancel_shutdown()` (per-instrument arguments, no global registry); docs/architecture.md Section 45's `run_guarded()` DB-failure-ordering caveat.

**Risks:** A Runtime that only reuses `test.py`'s per-worker outer `finally: disconnect_all()` backstop implicitly, without deliberately reproducing it per worker, would silently lose the mitigation Section 45 already relies on.

**Recommendation:** Isolate by default; escalate only along actual shared-resource dependency; the Cycle Controller must wrap every worker's cycle in the same unconditional `disconnect_all()` `finally` `test.py` already uses.

### Q: Is Runtime Architecture design cleared to start?

**Status:** GO, with conditions carried into the design

**Answer:** Yes. No finding in this review blocks starting the design itself. The design must explicitly address: (1) retiring/replacing `main.py`'s legacy path, (2) a resource-checkout layer given the shared-DMM/DAQ constraint, (3) `CycleSequence` (not yet implemented -- must be built as composition over the existing Charge/Discharge sequences), and (4) that concurrency behavior can be designed now but needs a second real hardware set to be hardware-validated.

**Evidence:** docs/architecture.md Section 46 (full writeup).

**Risks:** See Section 46's Risks list -- none are gates on starting design.

**Recommendation:** Proceed with Production Runtime Architecture design under the conditions above.

## SECTION 15 — ARCHITECTURE STANDARDIZATION REVIEW (Group Naming, Enabled Groups, Group-Centric Workflows, Database, Runtime Prep)

*Findings below summarize the pre-Runtime standardization review of the operator workflow around `BATTERY_GROUPS`. See docs/architecture.md Section 47 for the full technical writeup.*

### Q: Does renaming battery groups (e.g. "A" -> "A1") require code changes?

**Status:** No code changes required -- one open naming-semantics decision remains

**Answer:** `group` is used everywhere as an opaque dict-key string (confirmed by direct search of `test.py`/`config/devices.py`/`utils/validators.py`) -- no code assumes a single uppercase letter. The rename is data-only in `BATTERY_GROUPS`. What remains open is whether today's four groups get a 1:1 rename (A->A1, B->B1, ...) or are reorganized into sub-groups of one family (A1="old A", A2="old B", ...) -- that's a physical-topology decision only the operator can make, not derivable from the code.

**Evidence:** `config/devices.py::BATTERY_GROUPS`, `test.py::_select_battery_group()`, `utils/validators.py::validate_group_test_config()`.

**Risks:** None to the codebase; the open naming-semantics decision must be resolved before the rename itself is executed.

**Recommendation:** Recommend the 1:1 rename reading as lower-risk; confirm with the operator before implementing.

### Q: Is the `enabled` flag on `BATTERY_GROUPS` a new feature?

**Status:** Already implemented

**Answer:** No -- every `BATTERY_GROUPS` entry already has `"enabled": True/False`, already enforced at `_select_battery_group()`, already the sole ownership model (no parallel `ENABLED_GROUPS` list). One nuance must be preserved: `_select_relay_scope()` (Relay Functional Validation) deliberately does NOT gate on `enabled` -- a previously-fixed bug, documented in its own docstring -- since `enabled=False` means "no battery relay matrix wired for battery testing," not "these channels can't be tested."

**Evidence:** `config/devices.py::BATTERY_GROUPS`, `test.py::_select_battery_group()` (test.py:3597-3611), `test.py::_select_relay_scope()` (test.py:1990-2009).

**Risks:** Applying "hide disabled groups" too broadly (to raw hardware-validation scope selectors, not just battery workflows) would regress an already-fixed bug.

**Recommendation:** No change needed; preserve the existing distinction.

### Q: Should hardware tests (Test SMU/DMM/DAQ/Relay Matrix) be switched to Select Group -> Resolve -> Run?

**Status:** Recommend additive, not a replacement

**Answer:** Not as a replacement. `HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2` are physically present but not assigned to any group today -- a pure group-resolved test could never reach them, removing the ability to validate hardware before deciding which group to assign it to.

**Evidence:** `test.py::_run_hardware_category()` (test.py:136), `test.py::test_smu()` (test.py:781), `config/devices.py::PXI_SLOTS` (HIGH_POWER_SMU/AUX_SMU_1/AUX_SMU_2 comments: "not yet assigned to any battery channel").

**Risks:** Replacing the direct device picker would be a real capability loss for pre-commissioning hardware validation.

**Recommendation:** Add group-centric resolution as an additional path alongside the existing device picker.

### Q: What's missing from the database for group-centric reporting (Group History / Last Test From Group / Group Statistics)?

**Status:** One blocking schema gap identified

**Answer:** `run_summary` has no `group` column -- `group`/`position_in_group` exist only as free text inside `event_log.message`. Recommend adding `group_name` (and optionally `position_in_group`) as additive `run_summary` columns, the same migration pattern already used for `battery_type`/hardware-identity columns. Once added, all three requested features are thin queries/aggregations over existing, already-generic code (`list_run_summaries()`, `get_last_run_summary()`, `render_run_summary()`).

**Evidence:** `data/storage.py::CREATE_RUN_SUMMARY_SQL`/`_RUN_SUMMARY_MIGRATION_COLUMNS`, `test_control/run_summary_report.py::_lookup_group()` (the existing event_log-parsing workaround this would replace).

**Risks:** None -- purely additive schema change, no existing data affected.

**Recommendation:** Add `group_name`/`position_in_group` as the prerequisite for Group History/Last Test From Group/Group Statistics.

### Q: Does this standardization move the project closer to the intended Runtime shape, without violating single-source-of-truth or duplicating charge/discharge logic?

**Status:** Yes, consistent -- one prerequisite from Section 46 remains open

**Answer:** Yes. `enabled` already lives inside `BATTERY_GROUPS` (no parallel list); the naming change is a rename within the same structure; `group_name` is additive to the existing schema; the hardware-test recommendation is explicitly additive. `main.py`'s legacy `TestExecutor`/`ChargeCycle`/`DischargeCycle` path (Section 46) is unaffected by this batch and still needs retiring before Runtime ships.

**Evidence:** docs/architecture.md Section 47 (full writeup), Section 46 (main.py finding).

**Risks:** None new; the standing `main.py` retirement prerequisite from Section 46 is restated, not resolved, by this review.

**Recommendation:** Proceed with this standardization work; keep `main.py` retirement tracked as the standing Runtime prerequisite.

## SECTION 16 — GROUP HARDWARE OWNERSHIP MODEL (Multi-Matrix Topology, Position/Channel Storage Fix)

*Findings below resolve Section 15's open group-naming question and review the multi-matrix B1-B4/C1-C4 topology. See docs/architecture.md Section 48 for the full technical writeup.*

### Q: Does the B1/B2/B3/B4, C1/C2/C3/C4 naming scheme fit the current design?

**Status:** Naming: yes, unchanged. Storage model: no -- a real fix is required.

**Answer:** `group` is used everywhere as an opaque string key -- `B1`/`C1` are exactly as valid as `A` was, zero code impact for the naming itself. But `BATTERY_CHANNELS` is a flat, global position/relay-address table with `relay_address` derived from a global sequence number, not scoped per relay matrix. This has been silently correct only because exactly one physical matrix has ever been populated. It breaks the moment a second one (Group C1, `MATRIX_NUMATO_203`) is populated.

**Evidence:** `config/devices.py::BATTERY_CHANNELS`/`resolve_group_position()`; `utils/device_validator.py::_check_duplicate_relay_identifiers()` (lines 142-162), `_check_relay_count_consistency()` (193-224), `_check_battery_groups()` (165-190).

**Risks:** `_check_duplicate_relay_identifiers()` would report a false duplicate-relay collision the moment B1 and C1 both have `relay_address=1` on their own, separate, legitimate physical matrices -- confirmed by direct code inspection, not speculative.

**Recommendation:** Move position/channel ownership into each `BATTERY_GROUPS` entry (a `"positions"` sub-dict, keyed 1-8, holding `relay_address`/DAQ channels local to that group's own matrix) -- eliminating the shared global namespace that causes the bug.

### Q: Should positions stay numbered 1-8 within each group?

**Status:** Yes, unchanged -- only the storage location changes

**Answer:** Operator-facing behavior already matches this (`_select_battery_position()`'s prompt is already "Position within Group {group} (1-{size})" -- global position numbers have always been internal-only). What changes is where that range lives: a group's own `positions` dict key, not an indirection into a separate global table via `position_start`/`position_end` arithmetic.

**Evidence:** `test.py::_select_battery_position()`.

**Risks:** None -- this is a storage-location change, not a behavior change.

**Recommendation:** Keep 1-8 within each group; move ownership into `BATTERY_GROUPS[group]["positions"]`.

### Q: Should group naming be finalized before the database migration?

**Status:** Finalized by this review

**Answer:** Yes -- `<matrix letter><partition number>` (e.g. `B1`/`C1`), confirmed as a new topology (not a rename of today's A/B/C/D): today's Group A becomes B1; C1/C2/etc. are genuinely new, on a second physical matrix. Recommend proceeding with the Section 47 `group_name`/`position_in_group` migration using these final names directly.

**Evidence:** docs/architecture.md Section 48.

**Risks:** None -- this closes the open question Section 47 left unresolved.

**Recommendation:** Implement the position-ownership redesign and the `group_name`/`position_in_group` migration together, using `B1`/`C1`-style names directly.

### Q: Does this topology improve Runtime scalability, and should Runtime operate on groups or relay matrices?

**Status:** Groups -- confirmed, and it strengthens the existing Section 46 concurrency model

**Answer:** A relay matrix is a shared physical resource, not a unit of work. Under this topology, B1-B4 sharing `MATRIX_NUMATO_202` (and the same rack DMM/SMU/DAQ) are exactly one hardware set per Section 46's resource-partition model -- they cannot run concurrently with each other, only with C1-C4 (a different matrix). Runtime should schedule groups; the underlying hardware-set concurrency constraint is derived from shared resource names exactly as Section 46 already designed.

**Evidence:** docs/architecture.md Section 46 (resource-partition model), Section 48 (this topology's concrete instance of it).

**Risks:** None new -- this confirms and sharpens an existing design, not a new concern.

**Recommendation:** Runtime schedules groups; concurrency is derived from shared `relay_matrix`/`smu`/`dmm`/`daq` names, unchanged from Section 46.

## SECTION 17 — FINAL GROUP TOPOLOGY, POSITION OWNERSHIP, VALIDATOR REDESIGN

*Findings below lock the final production group topology and resolve A1's enabled state. See docs/architecture.md Section 49 for the full technical writeup.*

### Q: What is the final, locked group topology?

**Status:** Final

**Answer:** `MATRIX_NUMATO_201 -> A1-A4`, `MATRIX_NUMATO_202 -> B1-B4`, `MATRIX_NUMATO_203 -> C1-C4`, each matrix's 32 channels partitioned into four 8-position groups. Active: B1 (existing rack DMM/SMU/DAQ), C1 (NI USB-6211, NTC-only). A1 disabled (see below). All other groups exist as disabled placeholders with reserved position/`relay_address` ranges on their owning matrix.

**Evidence:** docs/architecture.md Section 49.

**Risks:** None -- this is a naming/topology finalization, not a behavior change.

**Recommendation:** Proceed with implementation using these final names directly.

**Status update (Group Ownership Migration, docs/architecture.md Section 53):** Implemented in code. Every A/C-family group carries its family's `relay_matrix` (`A1-A4 -> MATRIX_NUMATO_201`, `C1-C4 -> MATRIX_NUMATO_203`) with `smu`/`dmm`/`daq`/`ntc_daq` unassigned and `enabled=False`. C1's `NTC_DAQ_USB6211` assignment remains a separate, not-yet-done item (see docs/TODO.md).

### Q: Should A1 be enabled, disabled, or "relay-only"?

**Status:** Resolved -- disabled

**Answer:** Disabled, with zero hardware roles assigned (`relay_matrix="MATRIX_NUMATO_201"` only). "Enabled with full hardware" would require unconfirmed second-instrument hardware or an undesirable cross-matrix resource coupling with B1. "Enabled, relay-only" doesn't actually work given current code -- Monitor Battery/Monitor Battery Scan/Charge/Discharge Battery all require `smu`+`dmm`+`daq` by default (`required_roles=("relay_matrix","smu","dmm","daq")`), so a hardware-incomplete A1 would fail every one of them immediately. C1 is not the same situation despite also lacking `smu`/`dmm`/`daq` -- it has `ntc_daq` assigned, giving it one genuinely working workflow (NTC Group Scan) today; A1 has none.

**Evidence:** `test.py::_select_group_with_hardware_summary()` default `required_roles`; `test.py::_select_relay_scope()`'s documented `enabled`-bypass (confirms disabling A1 costs nothing for relay hardware bring-up).

**Risks:** None -- A1 can be flipped to `enabled=True` the moment any real hardware role is assigned.

**Recommendation:** A1 disabled until it has at least one assigned hardware role.

### Q: Does `relay_address` reset to 1-8 for every group, or stay unique per matrix?

**Status:** Corrected from an earlier sketch -- unique per matrix

**Answer:** `relay_address` must stay unique across every group sharing one `relay_matrix` -- B1-B4 all live on the same 32-channel `MATRIX_NUMATO_202`, so B1 owns channels 1-8, B2 owns 9-16, B3 owns 17-24, B4 owns 25-32. It is only free to repeat across *different* matrices (B1's channel 1 and C1's channel 1 legitimately coexist). This is the precise meaning of "(matrix, relay)-aware" validation.

**Evidence:** docs/architecture.md Section 49's `positions` structure example.

**Risks:** An earlier review sketch (Section 48) incorrectly reset `relay_address` to 1-8 per group regardless of shared matrix -- corrected here before any implementation.

**Recommendation:** Reserve non-overlapping `relay_address` ranges for every group on a shared matrix, including disabled placeholders, from the start.

### Q: Which `utils/device_validator.py` checks need to change, and how?

**Status:** Three functions reviewed, one retired entirely

**Answer:** `_check_duplicate_relay_identifiers()` -- key by `(relay_matrix, relay_address)` instead of `relay_address` alone. `_check_battery_groups()` -- retire entirely; the invariant it protects (a global position falling outside every group's range) becomes structurally impossible once positions live inside their owning group. `_check_relay_count_consistency()` -- loop per group instead of a full matrix x `BATTERY_CHANNELS` cross-product.

**Evidence:** `utils/device_validator.py` lines 142-224.

**Risks:** Disabled groups should still be validated for internal consistency (not skipped) -- catches a `relay_address` collision between a real group and a disabled sibling on the same matrix before it can resurface as a real hardware conflict later.

**Recommendation:** Implement all three changes together with the `positions` restructure.

**Status update (Group Ownership Migration, docs/architecture.md Section 53):** Implemented exactly as recommended above. `_check_duplicate_relay_identifiers()` -> `_check_relay_identifiers()`, keyed by `(relay_matrix, relay_address)`; `_check_battery_groups()` deleted; `_check_relay_count_consistency()` loops per group. Disabled groups (empty `positions` dicts) are included in the loop, not skipped -- a future `relay_address` collision between a real and disabled group on the same matrix is still caught. Regression-verified: same-matrix duplicate flagged, cross-matrix reuse allowed.

## SECTION 18 — TEMPERATURE MONITORING (Dual DAQ Ownership, NTC Acquisition Pipeline)

*Findings below cover the implemented temperature-monitoring feature -- Option A dual-DAQ ownership and the NTC acquisition pipeline for Monitor Battery/Charge Battery/Discharge Battery. See docs/architecture.md Section 51 for the full technical writeup.*

### Q: How does HardwareManager own two DAQ devices (general "daq" vs. NTC-specific "ntc_daq") without double-connecting when they're the same physical instrument?

**Status:** Implemented

**Answer:** `HardwareManager.__init__()` compares `ntc_daq_cfg` to `daq_cfg` by identity. If they're the same dict object (`hardware_for_group()`'s `"ntc_daq"` fell back to `"daq"`), `self._ntc_daq = self._daq` -- one instance, one connection. If they're genuinely different devices (today's real case: `MAIN_DAQ` vs. a temporary USB DAQ), a second `DAQ` instance is constructed. `connect_all()`/`disconnect_all()`/`health_check()` all check `self._ntc_daq is not self._daq` before treating it as a second device.

**Evidence:** `test_control/hardware_manager.py` `__init__()`/`_connect_all_strict()`/`_connect_all_lenient()`/`disconnect_all()`/`health_check()`.

**Risks:** None found -- verified programmatically for both cases (shared-instance and distinct-instance) before considering this done.

**Recommendation:** None -- implemented as designed.

### Q: Is "read only the active position's NTC" (vs. continuously scanning the whole group) the correct model for Monitor Battery/Charge/Discharge?

**Status:** Confirmed correct, implemented

**Answer:** Yes -- it mirrors exactly how these workflows already read only the active position's DMM/SMU, never the whole group's. Full-group scanning is already a separate, deliberate workflow (NTC Group Scan). Cycle Battery will inherit this model automatically once built as a composition over Charge/DischargeSequence.

**Evidence:** `test_control/monitor_battery_sequence.py`/`charge_sequence.py`/`discharge_sequence.py` each read `ntc_channel` (this position's own `daq_ntc_ch`) once per loop iteration, at the same cadence as their existing telemetry.

**Risks:** None.

**Recommendation:** None -- implemented as designed.

### Q: Why does Monitor Battery get a new `SafetyMonitor.check_temperature()` method instead of reusing the existing `check()`?

**Status:** Implemented, deliberate design choice

**Answer:** Monitor Battery never sources/sinks current, so it has no real `current_a` to pass into `check(v, i, temp_c, mode)`. Passing a placeholder (e.g. `0.0`) would also silently start enforcing voltage/current limits Monitor Battery has never enforced before -- a behavior change beyond what a temperature-only integration should introduce, and a real risk to the already-validated "just observe" nature of Monitor Battery. `check_temperature(temp_c)` is a small, additive method reusing the same `_temp_max()` resolution, changing nothing about `check()` itself.

**Evidence:** `test_control/safety_monitor.py::check_temperature()`; `test_control/monitor_battery_sequence.py`'s NTC read block.

**Risks:** None -- `check()` is untouched; existing Charge/Discharge behavior is unaffected.

**Recommendation:** None -- implemented as designed.

### Q: Does adding real temperature acquisition change Charge/Discharge Battery's existing safety behavior?

**Status:** Implemented, additive only

**Answer:** No new behavior beyond what `check()` was already designed to do. `SafetyMonitor.check(v, i, temp_c, mode=...)` already evaluated `temp_c` on every call -- it was simply always `None` before. The sequences now supply a real, classified reading; `check()` itself is unchanged.

**Evidence:** `test_control/charge_sequence.py`/`discharge_sequence.py` -- `t_c` is now computed via `classify_ntc_presence()`/`ntc_voltage_to_celsius()` instead of hardcoded `None`.

**Risks:** None -- an overtemperature stop was always possible in principle; it simply couldn't trigger without a real reading.

**Recommendation:** None -- implemented as designed.

### Q: What does the database look like after this change?

**Status:** Implemented -- see docs/architecture.md Section 51 "Database behavior" for the full data-flow diagram

**Answer:** `measurements.temp_c` (pre-existing column) now holds real values for `monitor`/`charge_battery`/`discharge_battery` runs when NTC hardware is configured; `event_log` gains an `"NTC DAQ selected: ..."` entry plus throttled `WARNING` entries on presence/fault transitions (once per transition, not per sample). `run_summary` gains no new columns in this pass -- deliberately deferred, mirroring the already-standing decision to defer Charge/Discharge's own voltage-stat aggregation. No new query helper needed -- `get_measurements()` already returns `temp_c`.

**Evidence:** docs/architecture.md Section 51.

**Risks:** None -- purely additive population of an existing column plus new, throttled event_log entries.

**Recommendation:** `run_summary`-level temperature aggregation (min/max/avg per run) and a separate, non-fatal warning threshold remain tracked follow-ups, not part of this change.

### Q: Does the USB-to-PXI DAQ migration still require only configuration changes after this implementation?

**Status:** Confirmed, unchanged

**Answer:** Yes. `HardwareManager` receives `ntc_daq_cfg` as a plain config dict; `DAQ` remains device-form-factor-agnostic. Migrating to the future PXI DAQ requires only repointing/removing `BATTERY_GROUPS[group]["ntc_daq"]` and updating `daq_ntc_ch` channel strings -- zero code changes anywhere in this session's implementation.

**Evidence:** docs/architecture.md Section 51 "USB -> PXI migration".

**Risks:** None.

**Recommendation:** None -- confirmed as designed.

## SECTION 19 — GROUP NTC PRE-CHECK (One-Time Full-Group Snapshot Before Charge/Discharge/Monitor Battery)

*Findings below cover the implemented group NTC pre-check feature. See docs/architecture.md Section 52 for the full technical writeup.*

### Q: Is a full-group NTC scan before Charge/Discharge/Monitor Battery low-risk, given it means reading positions other than the one being operated on?

**Status:** Confirmed low-risk, implemented

**Answer:** Yes -- NTC channels are independent per-position DAQ analog inputs, never routed through the relay matrix (the same structural fact that already made NTC Group Scan possible with zero relay/SMU involvement). Reading the whole group's NTC channels requires no relay switching and no SMU/PMU interaction, so it can run entirely before the target position's relay is ever engaged.

**Evidence:** `hardware/daq.py::DAQ.read_channel()`; `config/devices.py::BATTERY_GROUPS[group]["positions"][...]["daq_ntc_ch"]` (fixed per-position DAQ channel, independent of `relay_address`).

**Risks:** None found beyond the DAQError-gating issue below, which was caught and fixed during this same implementation.

**Recommendation:** None -- implemented as designed.

### Q: Does the pre-check duplicate NTC Group Scan's scanning logic?

**Status:** No -- refactored to share it

**Answer:** `test.py::_ntc_group_snapshot()` is a new function extracted from `_run_ntc_group_scan()`'s own per-position loop. `_run_ntc_group_scan()` was refactored to call it (verified: byte-for-byte identical output after the refactor). The pre-check is the shared function's second caller.

**Evidence:** `test.py::_ntc_group_snapshot()`, `_run_ntc_group_scan()`.

**Risks:** None -- dry-run verified unchanged NTC Group Scan output.

**Recommendation:** None -- implemented as designed.

### Q: Does a fault on a position other than the one being charged/discharged/monitored block the operation?

**Status:** No, by design

**Answer:** The pre-check only gates on the *selected* position's own result. A fault on some other position in the group is recorded (`measurements`/`event_log`) but never blocks the run -- it isn't part of what's being operated on.

**Evidence:** `test.py`'s gate check filters `ntc_snapshot` to the matching `position` before evaluating.

**Risks:** None.

**Recommendation:** None -- implemented as designed.

### Q: What happened when this was dry-run tested without real NTC hardware attached?

**Status:** A real issue found and fixed before considering this done

**Answer:** The first version hard-aborted Charge/Discharge/Monitor Battery every time, because a `DAQError` (the temporary NTC DAQ isn't reachable on this dev machine) was treated identically to a genuine `ABSENT`/`FAULT` signal. This was stricter than the *active-monitoring loop* (Section 51), which already degrades gracefully on the same `DAQError` and never aborts. Fixed by adding a `"readable"` field -- `False` for a config gap or `DAQError`, `True` only for a real, classified ADC reading -- and gating only on `readable and presence != PRESENT`.

**Evidence:** `test.py::_ntc_group_snapshot()`'s `"readable"` field; both call sites' gate checks.

**Risks:** None remaining -- re-verified after the fix: the same dry run now proceeds past the pre-check and fails at the expected real-hardware boundary, matching pre-existing behavior.

**Recommendation:** None further -- implemented and fixed within this same session.

### Q: How is the pre-check snapshot stored, and does it create a separate run?

**Status:** Implemented -- same run, no new run_summary row

**Answer:** No separate run -- the pre-check's `measurements`/`event_log` rows share the *same* `run_id` as the Charge/Discharge/Monitor Battery operation it precedes, tagged `phase_detail="NTC_PRECHECK"`. No new `run_summary` columns. On a target-position abort, the run's own `run_summary` row is finished with `stop_reason="SAFETY_VIOLATION"`/`result="FAIL"` via existing columns.

**Evidence:** docs/architecture.md Section 52 "Database behavior".

**Risks:** None.

**Recommendation:** Group Statistics could add a "pre-check catch rate" bucket once the `group_name` migration lands -- not built in this pass.

## SECTION 20 — NTC RUNTIME DAQ SELECTION, USB-6211 RSE FIX, AND POST-RUN CHARGE/DISCHARGE DIAGNOSTIC CLASSIFICATION

*Findings below cover four related passes on Test Sensors (NTC) and Charge/Discharge Battery, plus a review of whether `main.py`'s legacy path inherits any of it. See docs/architecture.md Section 54 for the full technical writeup.*

### Q: Why did Test 6 fail even with a valid DAQ connected?

**Status:** Fixed -- runtime device selection added

**Answer:** Test 6 hardcoded the DAQ via `hardware_for_group("B1")["ntc_daq_cfg"]`. If the configured device (e.g. `Dev2`) didn't match what NI-DAQmx actually enumerated (only `usbdaq` present), the scan failed outright. `hardware/daq.py::DAQ.list_available_devices()` (a static enumeration method) + `test.py::_select_ntc_daq_device()` now let the operator pick the real device for that one session; the configured `daq_ntc_ch` alias prefix is rewritten to the selection, keeping the channel suffix.

**Evidence:** `hardware/daq.py::DAQ.list_available_devices()`; `test.py::_select_ntc_daq_device()`/`test_sensors()` Test 6.

**Risks:** None -- diagnostic-only, verified `BATTERY_GROUPS["B1"]["ntc_daq"]` unchanged after a run.

**Recommendation:** None further -- implemented and verified against the real USB-6211 and a mocked multi-device list.

### Q: Why did Test 6 report 6-8 V on a channel that reads 0 V/2.5 V on a meter?

**Status:** Root cause found and fixed -- real hardware confirmed

**Answer:** `DAQ.read_channel()` never set `terminal_config` on `add_ai_voltage_chan()`, leaving nidaqmx's own ambiguous `TerminalConfiguration.DEFAULT`. On this USB-6211, with only `ai0`-`ai7` wired and `ai8`-`ai15` floating, `DEFAULT` let nidaqmx pair channels differentially with a floating counterpart instead of measuring single-ended against common ground. The value returned was genuinely what the ADC sampled -- no software scaling/unit bug -- just the wrong physical quantity. Fixed by forcing `RSE` explicitly (configurable via `cfg["terminal_config"]`, default `"RSE"`).

**Evidence:** `hardware/daq.py::DAQ.__init__()`/`read_channel()`.

**Risks:** None found. This same driver method is shared by every analog read in the codebase (voltage/current/NTC) -- worth a real-hardware re-check on the voltage/current channels too, not just NTC, though those haven't shown the same symptom.

**Recommendation:** None further for NTC -- confirmed on real hardware (disconnected ~0 V, connected 2.4578 V -> 24.24 degC).

### Q: Why did a disconnected NTC channel still show a temperature like -102 degC even though its Status correctly said ABSENT?

**Status:** Fixed

**Answer:** The per-channel loop called `ntc_voltage_to_celsius()` unconditionally before checking presence -- microvolt-level noise near 0 V is mathematically "valid" (0 < v < v_exc) but physically meaningless for an open channel. Fixed by classifying presence FIRST (`classify_ntc_presence()`) and skipping the temperature call entirely for `ABSENT`.

**Evidence:** `test.py::test_sensors()` Test 6; `hardware/temperature.py::ABSENT_VOLTAGE_THRESHOLD` (renamed from `NTC_OPEN_VOLTAGE_MARGIN_V`, same value -- single source of truth, not a second threshold).

**Risks:** None -- verified real ABSENT channels now show `Temperature: N/A`; a real ~2.46 V reading still computes correctly.

**Recommendation:** None further.

### Q: Does the new `analysis_result` diagnostic classification ever block a Charge/Discharge run or change its stop_reason?

**Status:** No, by design -- verified

**Answer:** Never. It's computed post-hoc from data the sampling loop already collects (`_ChargeDischargeStats`, fed the same `voltage_v`/`current_a` already passed to `_record_measurement()`) and attached via `BatteryOperationSequence.run_guarded()`'s existing `extra_run_summary_fields_fn` hook -- the same mechanism already used for Monitor Battery's own voltage stats. Verified against the real `ChargeSequence`/`DischargeSequence` code (mocked hardware, not just the pure classifier): `stop_reason`/`result` are byte-identical with and without this feature in every case, including the empty-position Discharge scenario (still `SAFETY_VIOLATION`/`FAIL`).

**Evidence:** `test_control/battery_diagnostics.py`; `battery_operation_sequence.py::_ChargeDischargeStats`/`_charge_diagnostic_fields()`/`_discharge_diagnostic_fields()`.

**Risks:** A design bug was caught during implementation: `ChargeDiagnosis.POSSIBLY_EMPTY_POSITION` and `DischargeDiagnosis.POSSIBLY_EMPTY_POSITION` share one string value (matches spec), so a single message-lookup dict let one mode's wording silently overwrite the other's -- fixed with a `mode`-aware `message_for()`.

**Recommendation:** None further -- implemented, fixed, and verified within this pass.

### Q: Why use the pre-enable DMM reading instead of the sampling loop's first sample for `initial_voltage_v`?

**Status:** Corrected during implementation, before considering it done

**Answer:** By the time the sampling loop takes its first sample, the SMU has already been sourcing/sinking current for `Settings.STABILIZATION_S` -- an empty position's voltage may already be compliance-limited toward the commanded target, indistinguishable from a genuinely full battery. The pre-enable reading (`pre_enable_v`, already computed for the existing reverse-polarity check) reflects what's actually connected, before the SMU can mask it.

**Evidence:** `charge_sequence.py`/`discharge_sequence.py`'s `stats.initial_voltage_v = pre_enable_v` line, placed after `_check_battery_polarity()` succeeds (so a reverse-polarity abort keeps its own distinct diagnosis, never double-labeled as an empty position).

**Risks:** None remaining for the new architecture. The legacy `main.py` path (see below) has no equivalent pre-enable reading and inherits this limitation.

**Recommendation:** None further for ChargeSequence/DischargeSequence.

### Q: Does `main.py` (the legacy path) get any of this automatically?

**Status:** No -- reviewed in full, then given the minimum required implementation

**Answer:** Traced `main.py -> TestExecutor -> BatteryTestSequence -> ChargeCycle/DischargeCycle -> ResultManager.generate_report() -> data/report.py`. `ChargeCycle`/`DischargeCycle` don't subclass `BatteryOperationSequence`, call the original narrow `data_collector.record()` (never `record_measurement()`), and -- confirmed via grep across all four files -- **never call `start_run_summary()`/`finish_run_summary()` at all**. A legacy run has no `run_summary` row whatsoever, not just a missing column; `data/report.py::ReportGenerator.generate()` is an unimplemented placeholder that never queries the DB, and `run_summary_report.py::render_run_summary()` is never called anywhere in this chain.

**Evidence:** `test_control/test_executor.py`, `battery_test.py`, `charge_cycle.py`, `discharge_cycle.py`, `result_manager.py`, `data/report.py`.

**Risks:** `analysis_result` is still not persisted to `run_summary` for a legacy run (no row exists to persist it to) -- deliberately not solved here, since it requires the same one-row-per-position-vs-per-run granularity decision the CycleSequence caveat above raises, at multi-channel-run scale. Not silently worked around.

**Recommendation:** Resolve the `run_summary` granularity question before either CycleSequence or a legacy-path `run_summary` retrofit.

### Q: What was actually implemented for main.py, then?

**Status:** Minimum required change -- same classification logic reused, no parallel engine

**Answer:** `ChargeCycle`/`DischargeCycle` now accumulate a `_ChargeDischargeStats` (imported from `battery_operation_sequence.py`, reused not duplicated) from the exact `v`/`i` their existing `daq.read_all_batteries()` loop already reads, and classify via the SAME `test_control/battery_diagnostics.py` functions in a `finally` block, so it fires on every exit (completion, timeout, or a raised `SafetyViolationError`) without touching existing control flow. Logged via the existing Python logger (always available) and via `data_collector.log_event()` if the storage backend duck-typing-supports it (`log_event()` isn't part of the abstract `StorageBackend` interface, so a `MiniSQLStorage` without it still works). Wrapped in its own `try/except` so a classification failure can never mask a real exception.

**Evidence:** `test_control/charge_cycle.py`/`discharge_cycle.py`'s `_log_diagnostic()`.

**Risks:** No pre-enable voltage reading exists in this legacy path (no reverse-polarity check to borrow one from) -- adding one would change already-validated legacy behavior, which this pass deliberately avoided. The first in-loop sample is used instead, making `ALREADY_CHARGED` vs. `POSSIBLY_EMPTY_POSITION` less reliable here than in the new architecture -- documented, not hidden.

**Recommendation:** Verified end-to-end with mocked hardware through the real `ChargeCycle`/`DischargeCycle` code: an empty-position charge logs `ALREADY_CHARGED` via both the logger and `event_log`; an empty-position discharge still raises `SafetyViolationError` exactly as before, with the diagnostic logged via the logger alone when the injected collector has no `log_event`.
