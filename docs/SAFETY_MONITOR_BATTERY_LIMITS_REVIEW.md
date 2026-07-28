# SafetyMonitor vs. BATTERY_CONFIGS -- Architecture Compliance Review

> **Resolved notice:** the gap identified below has been closed. `SafetyMonitor`, `ChargeCycle`, and `DischargeCycle` now consume `BATTERY_CONFIGS` (optional, backward-compatible `battery_cfg` parameter -- `battery_cfg=None` preserves the exact prior global-Settings-only behavior). See `docs/architecture.md` Section 28, "BATTERY_CONFIGS -> SafetyMonitor Integration", for the fix design, code locations, and verification. This document's body below is kept as-is for historical record of the analysis that motivated the fix.

Analysis-only document. No code was modified to produce this review.

**Question this review answers:** is `BATTERY_CONFIGS` (`config/devices.py`)
-> `SafetyMonitor` (`test_control/safety_monitor.py`) still a blocker before
real Charge Battery development begins?

**Short answer: yes, it is a real gap, and it should be closed before Charge
Battery sourcing is implemented -- not because the architecture is fragile,
but because the specific numbers involved (HUB vs. SB current limits differ
by up to 13x) make this a concrete overcurrent risk the moment real
sourcing exists, not a theoretical one.**

---

## 1. Does SafetyMonitor currently consume battery-specific limits from BATTERY_CONFIGS?

**No.** `test_control/safety_monitor.py::SafetyMonitor` never imports,
references, or is passed anything from `config/devices.py::BATTERY_CONFIGS`.

- `SafetyMonitor.__init__(self, settings: Settings)` takes only the global
  `Settings` class -- no `battery_type`/`battery_cfg` parameter exists in
  its signature.
- `SafetyMonitor.check(voltage_v, current_a, temp_c)` compares every
  reading against exactly four `self.s.BAT_*` attributes read from
  `config/settings.py` -- `BAT_VOLTAGE_MAX`, `BAT_VOLTAGE_MIN`,
  `BAT_CURRENT_MAX`, `BAT_TEMP_MAX_C`. Nothing about which battery type is
  under test is passed into this call, and the method has no way to know.
- `SafetyMonitor.is_safe_to_switch_relay(current_a)` compares against
  `Settings.ZERO_CURRENT_THRESHOLD_A` -- also global, also
  battery-type-independent (correctly so -- "is current near zero" is not
  a per-battery-type question).
- Every construction site in the codebase confirms this:
  `SafetyMonitor(settings=Settings)` / `SafetyMonitor(Settings)` -- in
  `test.py` (3 call sites: the unit-test menu item, the Safety Monitor
  Simulator, `run_main_test()`), and `test_control/test_executor.py`. None
  pass a battery config.
- A repo-wide search confirms `BATTERY_CONFIGS` is referenced in exactly
  three `.py` files: `config/devices.py` (where it's defined),
  `utils/device_validator.py` (a comment only -- explicitly states there is
  *no* validation check against it, since battery selection is an explicit
  operator choice read directly from the dict), and `test.py`. It does not
  appear anywhere in `test_control/` or `hardware/`.

## 2. Which limits are still global, and where are they used?

| Limit | Global constant | Value | Used by |
|---|---|---|---|
| Voltage ceiling | `Settings.BAT_VOLTAGE_MAX` | `4.7` V | `SafetyMonitor.check()` |
| Voltage floor | `Settings.BAT_VOLTAGE_MIN` | `3.5` V | `SafetyMonitor.check()` |
| Current ceiling | `Settings.BAT_CURRENT_MAX` | `1.0` A | `SafetyMonitor.check()` |
| Temperature ceiling | `Settings.BAT_TEMP_MAX_C` | `45.0` °C | `SafetyMonitor.check()` |
| Relay-switch current threshold | `Settings.ZERO_CURRENT_THRESHOLD_A` | `0.01` A | `SafetyMonitor.is_safe_to_switch_relay()` |
| **Commanded** charge setpoint | `Settings.CHARGE_CURRENT_A` / `Settings.CHARGE_VOLTAGE_V` | `0.5` A / `4.2` V | `test_control/charge_cycle.py::ChargeCycle.run()` -> `smu.set_charge_mode(current_a=..., voltage_limit_v=...)` |
| **Commanded** discharge setpoint | `Settings.DISCHARGE_CURRENT_A` / `Settings.DISCHARGE_CUTOFF_V` | `0.5` A / `3.0` V | `test_control/discharge_cycle.py::DischargeCycle.run()` -> `smu.set_discharge_mode(...)` |

**Important scope note:** the gap is not only in `SafetyMonitor` (the
safety-net/fault-detection layer) -- it is also in `ChargeCycle`/
`DischargeCycle`, the legacy but still-current charge/discharge test
harness. `set_charge_mode()`/`set_discharge_mode()` are called with the one
global `CHARGE_CURRENT_A`/`DISCHARGE_CURRENT_A` **regardless of which
battery type is under test** -- there is no `battery_type` or
`battery_cfg` parameter anywhere in `ChargeCycle.run()`/`DischargeCycle.run()`'s
signatures either. So this is a two-layer gap: the *intended setpoint*
itself is global (not battery-aware), and the *safety-net ceiling* checking
that setpoint is also global (not battery-aware).

## 3. HUB and SB limits -- confirmed values, and whether SafetyMonitor uses them

```python
BATTERY_CONFIGS = {
    "HUB": {
        "nominal_voltage_v":       3.7,    # confirmed
        "voltage_max_v":           4.2,    # unconfirmed placeholder
        "voltage_min_v":           3.0,    # unconfirmed placeholder
        "capacity_ah":             1.05,   # confirmed -- 1050 mAh
        "max_charge_current_a":    0.525,  # unconfirmed placeholder -- 0.5C
        "max_discharge_current_a": 1.05,   # unconfirmed placeholder -- 1C
        "max_temp_c":              45.0,   # unconfirmed placeholder
    },
    "SB": {
        "nominal_voltage_v":       3.7,    # confirmed
        "voltage_max_v":           4.2,
        "voltage_min_v":           3.0,
        "capacity_ah":             0.16,   # confirmed -- 160 mAh
        "max_charge_current_a":    0.08,
        "max_discharge_current_a": 0.16,
        "max_temp_c":              45.0,
    },
}
```

| | HUB (real limit) | SB (real limit) | Global `Settings` value SafetyMonitor actually enforces | HUB headroom | SB headroom |
|---|---|---|---|---|---|
| Voltage max | 4.2 V | 4.2 V | `BAT_VOLTAGE_MAX` = 4.7 V | +0.5 V (12%) too permissive | +0.5 V (12%) too permissive |
| Voltage min | 3.0 V | 3.0 V | `BAT_VOLTAGE_MIN` = 3.5 V | *over*-protective (trips 0.5 V early) | *over*-protective (trips 0.5 V early) |
| Max charge current | 0.525 A | 0.08 A | `BAT_CURRENT_MAX` = 1.0 A | ~1.9x too permissive | **~12.5x too permissive** |
| Max discharge current | 1.05 A | 0.16 A | `BAT_CURRENT_MAX` = 1.0 A | slightly *over*-protective | **~6.25x too permissive** |
| Max temperature | 45.0 °C | 45.0 °C | `BAT_TEMP_MAX_C` = 45.0 °C | exact match (coincidental) | exact match (coincidental) |

**Is SafetyMonitor using the HUB/SB-specific numbers above? No.** Every one
of those seven `max_charge_current_a`/`max_discharge_current_a`/
`voltage_max_v`/`voltage_min_v`/`max_temp_c` values exists only as data in
`config/devices.py`, displayed once on Monitor Battery's confirmation
screen and recorded once into `run_summary` (see Section 5) -- never read
by `SafetyMonitor.check()`, never compared against a live measurement.

**The current limit is the most safety-relevant number in this table.** A
single global `BAT_CURRENT_MAX = 1.0 A` ceiling is not just "less precise"
for SB -- it is roughly **12.5x too permissive** on charge and **6.25x too
permissive** on discharge for that battery specifically. Voltage is a
smaller but real gap (12% too permissive on the ceiling, for both types
identically, since the current placeholder values happen to be identical
for HUB and SB). Temperature happens not to be a gap today, coincidentally
(both battery types' placeholder value matches the global constant
exactly).

## 4. Would a Charge Battery implementation started today automatically inherit correct HUB/SB-specific limits?

**No.** Nothing in the current architecture would cause this to happen
automatically:

- If Charge Battery is built by extending `ChargeCycle`/`DischargeCycle`
  as they exist today, it would call `smu.set_charge_mode(current_a=
  self.s.CHARGE_CURRENT_A, ...)` -- the **same 0.5 A global setpoint for
  every battery type**, regardless of which one the operator selected via
  `_select_battery_type()`. For SB (real max 0.08 A), this would command
  the PSU to source **over 6x SB's rated charge current** from the very
  first commanded setpoint -- not a safety-net edge case, the *intended*
  operating point itself would already be wrong.
- Even if a future implementation is built well enough to compute its own
  setpoint from `BATTERY_CONFIGS` (correctly targeting, say,
  `battery_cfg["max_charge_current_a"]`), the **independent safety-net
  layer** (`SafetyMonitor.check()`) would still only catch a fault at the
  global `BAT_CURRENT_MAX = 1.0 A` ceiling -- meaning a bug, drift, or
  misconfiguration in that new implementation could push SB to 1.0 A (12.5x
  its real rating) before the safety net reacts at all. A safety monitor
  that can't catch a fault below the correct battery's real limit is not
  doing its job for that battery, even if the "happy path" logic elsewhere
  is written correctly.
- The battery-selection workflow (`_select_battery_type()` ->
  `_select_battery_group()` -> `_select_battery_position()` ->
  `_confirm_monitor_battery()`) and the traceability snapshot
  (`run_summary.battery_voltage_max_v`/etc., see Section 5) give a
  **misleading appearance of enforcement**: the operator sees HUB/SB's real
  limits on the confirmation screen and they are durably recorded in the
  database -- but nothing downstream of that confirmation screen actually
  constrains sourcing or trips a limit based on them.

## 5. Remaining gap between BATTERY_CONFIGS and SafetyMonitor

```
BATTERY_CONFIGS
   |
   |  (battery_cfg["voltage_max_v"], ["voltage_min_v"],
   |   ["max_charge_current_a"], ["max_discharge_current_a"],
   |   ["max_temp_c"] -- all seven fields per type)
   v
test.py::_run_monitor_battery()          <-- consumed HERE (display + traceability snapshot only)
   |
   |  battery_cfg is NOT passed any further
   v
MonitorBatterySequence.run()             <-- never receives battery_cfg; never calls safety.check() at all (read-only mode, no sourcing)
   X  <-- GAP: nothing carries battery_cfg from here into SafetyMonitor
   |
SafetyMonitor.check(voltage_v, current_a, temp_c)
   |
   |  compares against self.s.BAT_VOLTAGE_MAX/MIN, self.s.BAT_CURRENT_MAX,
   |  self.s.BAT_TEMP_MAX_C -- ALWAYS the same four global Settings
   |  constants, regardless of which battery type was selected upstream
   v
(same global ceiling for HUB, SB, or any future battery type)
```

The gap is a **missing parameter, not a missing capability** --
`SafetyMonitor.check()`'s internal comparison logic (`voltage_v >
ceiling`, etc.) is already correct and would work unchanged against a
per-battery-type ceiling if one were passed in; today it simply never
receives one, so it always falls back to the one global set of numbers in
`config/settings.py`.

**A second, related observation worth naming explicitly:** `SafetyMonitor.check()`
has never yet been exercised against a real hardware-sourced measurement in
this project's history -- every call to it so far has used synthetic
values (`test.py`'s 7 unit tests) or simulated values (the Safety Monitor
Simulator, `docs/architecture.md` Section 23e). `MonitorBatterySequence`
(the one real, implemented workflow) never calls `safety.check()` at all,
since it never sources current. This makes the present moment -- before
real Charge Battery sourcing exists -- the correct and lowest-risk time to
close this gap, before `check()` is ever called against a real, energized
PSU output for the first time.

## 6. Minimal implementation, architectural impact, and risk of proceeding without it

**Minimal implementation required** (not performed as part of this
analysis-only review):

1. Add an optional per-battery-limits parameter to `SafetyMonitor`
   (e.g. `SafetyMonitor.check(voltage_v, current_a, temp_c, battery_cfg=None)`,
   or set it once via a new `set_battery_limits(battery_cfg)` method
   called right after the operator's confirmation screen is accepted,
   mirroring how the Relay/PSU Safety Verification Patterns each added one
   new method to an existing class rather than a new framework). When
   `battery_cfg` is given, compare against `battery_cfg["voltage_max_v"]`/
   `["voltage_min_v"]`/`["max_charge_current_a"]` or
   `["max_discharge_current_a"]` (charge vs. discharge context)/
   `["max_temp_c"]` instead of (or in addition to, taking the more
   restrictive of the two, per the project's existing "most conservative
   limit across all sources wins" principle already documented in
   `config/devices.py`'s "Operational Limit Resolution" comment) the
   global `Settings.BAT_*` constants. `battery_cfg=None` (the default)
   should preserve exact current behavior -- every existing caller
   (the 7 unit tests, the Safety Monitor Simulator) continues to work
   unchanged.
2. Thread `battery_cfg` (or the resolved limit values) from
   `test.py::_run_monitor_battery()`'s already-resolved `battery_cfg`
   through to wherever the future Charge/Discharge Battery sequence
   constructs/calls `SafetyMonitor` -- the exact same battery-selection
   plumbing (`_select_battery_type()` -> `battery_cfg`) already exists and
   already reaches the confirmation screen and the `run_summary` snapshot;
   it would simply need one more consumer.
3. Decide whether `ChargeCycle`/`DischargeCycle` (or their future
   Charge/Discharge Battery replacements) should derive their
   *commanded setpoint* from `battery_cfg["max_charge_current_a"]`/
   `["max_discharge_current_a"]` instead of the global
   `Settings.CHARGE_CURRENT_A`/`DISCHARGE_CURRENT_A` -- this is a distinct
   decision from item 1 (safety-net ceiling) but equally necessary, per
   Section 4's finding that the *intended* setpoint is global today, not
   just the *fault* ceiling.

**Architectural impact:** small and additive, consistent with every other
recent safety-pattern change in this codebase (Relay Safety Verification
Pattern, PSU Safety Verification Pattern, Interruptible Wait Mechanism) --
each of those added one or two new methods/parameters to an existing
class, defaulting to prior behavior when unused, rather than introducing a
parallel framework. This fix follows the identical shape: no new class, no
new storage mechanism, no redesign of `SafetyMonitor`'s existing
comparison logic -- just parameterizing which ceiling values it compares
against.

**Risk of proceeding to Charge Battery before fixing this:** concrete and
quantified above, not theoretical -- a Charge Battery implementation that
reuses `ChargeCycle`'s existing global setpoint would command **6.25x
SB's real rated charge current** as its very first sourcing operation, and
`SafetyMonitor`'s independent fault-detection backstop would not trip until
**12.5x** SB's real limit even if the commanded setpoint were fixed
separately. For HUB the gap is smaller (~1.9x on the commanded charge
setpoint headroom, ~1.9x on the safety-net ceiling) but still real. This is
squarely a **battery-damage / thermal-runaway-risk-relevant** gap for the
smaller of the two real battery types this system is built for, not a
low-priority polish item.

## 7. Recommendation

**A real, quantified gap remains -- this is a legitimate item to close
before Charge Battery sourcing, not a false alarm.** It is not, however, an
indication that the broader safety architecture is unsound: every other
recent safety review in this project's history (Relay Safety Verification
Pattern, PSU Safety Verification Pattern, Interruptible Wait Mechanism) has
been resolved with a small, additive, backward-compatible change, and this
gap fits the identical shape -- `SafetyMonitor.check()`'s comparison logic
is already correct; it is simply missing one parameter.

**Recommended next action:** implement the minimal fix in Section 6 (an
optional `battery_cfg`/per-battery-limit parameter on `SafetyMonitor`,
threaded from the already-existing battery-selection plumbing) as the
**first step of Charge Battery development**, not a separate, deferred
task -- since a real Charge Battery implementation cannot be safely
exercised against real hardware without it (per Section 4's finding: the
commanded setpoint itself, not only the safety net, is currently
battery-type-blind). This is small enough in scope to fold directly into
the start of Charge Battery work rather than requiring its own standalone
milestone.
