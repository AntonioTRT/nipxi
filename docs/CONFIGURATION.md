# NIPXI Configuration Reference

This file is the authoritative reference for all configuration parameters.  
Edit `config/settings.py` and `config/devices.py` before running.

---

## config/settings.py

All values are class-level attributes on `Settings`. Access them as `Settings.PARAM` or `self.s.PARAM` inside any class that receives `settings`.

### Project

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `PROJECT_NAME` | `"NIPXI Battery Test System"` | str | Display name |
| `VERSION` | `"0.1.0"` | str | Software version |

### System mode

See `config/system_mode.py` and `docs/architecture.md` Section 9 for the full design.

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `SYSTEM_MODE` | `"DEVELOPMENT"` | str | `"DEVELOPMENT"` \| `"VALIDATION"` \| `"PRODUCTION"`. Controls hardware startup strictness, database location, and (future) recovery. Validated at startup -- an unrecognized value is a `ValidationError`. |
| `RECOVERY_ENABLED_OVERRIDE` | `None` | bool \| None | `None` = use the active mode's default (see `config/system_mode.py` `MODE_POLICIES`). Set `True`/`False` to override regardless of mode. No recovery engine exists yet -- see `docs/DATABASE_ROADMAP.md` -- this is only the configuration hook. |

**DEVELOPMENT** (the default): hardware optional, a missing device warns and startup continues. **VALIDATION**: a missing device is reported as an error but the framework still launches. **PRODUCTION**: any missing device aborts startup (`HardwareInitError`) -- this was the *only* behavior before `SYSTEM_MODE` existed.

### Channel count

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `NUM_CHANNELS` | `8` | int | Total relay / battery channels available |
| `ACTIVE_CHANNELS` | `[1..8]` | list[int] | Channels to test in a run |

### Battery limits

These are safety hard limits. The system raises `SafetyViolationError` if any is exceeded.

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `BAT_VOLTAGE_MAX` | `4.7` | float (V) | Overvoltage cutoff |
| `BAT_VOLTAGE_MIN` | `3.5` | float (V) | Undervoltage cutoff |
| `BAT_CURRENT_MAX` | `1.0` | float (A) | Overcurrent cutoff (absolute value) |
| `BAT_TEMP_MAX_C` | `45.0` | float (degC) | Overtemperature cutoff |
| `BAT_TEMP_MIN_C` | `20.0` | float (degC) | Low-temperature warning (not enforced as e-stop) |

### Charge parameters (CC-CV)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `CHARGE_CURRENT_A` | `0.5` | float (A) | Constant-current phase target |
| `CHARGE_VOLTAGE_V` | `4.2` | float (V) | Constant-voltage phase target |
| `CHARGE_CUTOFF_A` | `0.05` | float (A) | End-of-charge: CV taper current threshold |
| `CHARGE_TIMEOUT_S` | `7200` | int (s) | Maximum charge duration before abort |

**Also consumed by SMU Functional Validation** (`test.py::_functional_smu()`, laboratory-only bench check): `CHARGE_VOLTAGE_V` is reused as the validation voltage, `CHARGE_CURRENT_A` as the current-limit/compliance value, and `BAT_VOLTAGE_MAX` (above) as the SMU's source range -- no separate test-voltage configuration exists or is needed. See README.md Section 8.1b and `docs/architecture.md` Section 12.6.

### Discharge parameters (CC)

**Discharge is a current-sink operation, not a negative-voltage source.** The SMU sinks `DISCHARGE_CURRENT_A` while the battery's own voltage is at (or above) `DISCHARGE_CUTOFF_V` -- it never sources a negative voltage to discharge a cell. See `hardware/smu.py::SMU.set_discharge_mode()`'s docstring ("Configure CC discharge (sink)") and `docs/architecture.md` Section 12.6.

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `DISCHARGE_CURRENT_A` | `0.5` | float (A) | Constant discharge current (sink) |
| `DISCHARGE_CUTOFF_V` | `3.0` | float (V) | End-of-discharge voltage |
| `DISCHARGE_TIMEOUT_S` | `7200` | int (s) | Maximum discharge duration before abort |

> **Warning:** `DISCHARGE_CUTOFF_V` (3.0 V) is below `BAT_VOLTAGE_MIN` (3.5 V).
> The safety monitor triggers on `BAT_VOLTAGE_MIN` first if discharge is still running.
> Verify these two values match your battery chemistry and update as needed.

### Stabilization and sampling

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `STABILIZATION_S` | `5.0` | float (s) | Wait time after relay switch before first sample |
| `SAMPLE_RATE_HZ` | `1.0` | float (Hz) | DAQ acquisition rate during a cycle |

### Safety

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `ZERO_CURRENT_THRESHOLD_A` | `0.01` | float (A) | Current considered zero for relay-switch safety |
| `SMU_VOLTAGE_READBACK_TOLERANCE_V` | `1e-4` | float (V) | Tolerance for `hardware/smu.py::SMU._verify_config_readback()` when comparing NI-DCPower's `voltage_level` attribute readback to the commanded value after `commit()`. This is an **attribute round-trip bound** (floating-point + instrument coercion to its nearest programmable step), NOT a measurement-accuracy figure -- `voltage_level` is a stored IVI setpoint the driver echoes back, not a new ADC measurement. See docs/architecture.md Section 12.6b. |
| `SMU_CURRENT_READBACK_TOLERANCE_A` | `1e-4` | float (A) | Same as above, for the `current_limit` attribute. |

### Proto Test Execution (Milestone 2 -- infrastructure validation, no battery)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `PROTO_TEST_DWELL_S` | `120.0` | float (s) | Per-relay dwell time (output stays enabled this long before disabling and advancing). Reuses `CHARGE_VOLTAGE_V`/`CHARGE_CURRENT_A`/`BAT_VOLTAGE_MAX` above as the bench source point/current-limit/voltage-range and `ACTIVE_CHANNELS` above as the relay sequence -- no new duplicate voltage/current/channel-list constants. See `docs/architecture.md` Section 18 and `test_control/proto_test_sequence.py`. |

### PXI hardware (simulation mode only)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `PXI_SIMULATE` | `False` | bool | NI simulation mode (no hardware needed) |

Setting `PXI_SIMULATE = True` lets NI drivers run in simulation mode — useful for software development without access to the PXI rack. Results will be dummy values.

> **Changed:** VISA resource strings (`PXI_RESOURCE_DAQ`/`PXI_RESOURCE_DMM`/`PXI_RESOURCE_SMU1`/
> `PXI_RESOURCE_SMU2`) used to live here and were read directly by `HardwareManager`, duplicating
> the same values already in `config/devices.py`'s `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG`.
> They have been removed from `Settings` -- **`config/devices.py` is now the only place VISA
> resource strings are set** (see the `config/devices.py` section below). Find your actual VISA
> resource strings in **NI-MAX** (Measurement & Automation Explorer), then edit `config/devices.py`.

### Ethernet relay (production)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `RELAY_COUNT` | `32` | int | Single source of truth for the Numato relay count. Flows into `NUMATO_RELAY_MATRIX_CONFIG["channel_count"]` in `config/devices.py` -- update here, not in the driver or any relay test, and never hardcode `32` elsewhere. |

### Serial relay (diagnostic only -- NOT production)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `RELAY_COM_PORT` | `"COM3"` | str | Windows COM port of the relay controller |
| `RELAY_BAUD_RATE` | `9600` | int | Serial baud rate |
| `RELAY_TIMEOUT_S` | `2.0` | float (s) | Serial read timeout |
| `RELAY_NUM_CHANNELS` | `8` | int | Number of relay channels (serial diagnostic path only) |

### DAQ channel lists

These lists map logical channel indices (0-based) to NI 6363 physical channel names.  
The default assumes a single NI 6363 at `Dev1`. Update if your DAQ resource name differs.

| Parameter | Default range | Description |
|-----------|---------------|-------------|
| `DAQ_VOLTAGE_CHANNELS` | `Dev1/ai0..ai7` | Battery voltage inputs (8 channels) |
| `DAQ_CURRENT_CHANNELS` | `Dev1/ai8..ai15` | Current shunt inputs (8 channels) |
| `DAQ_NTC_CHANNELS` | `Dev1/ai16..ai23` | NTC thermistor inputs (8 channels) |

### Logging

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `LOG_LEVEL` | `"INFO"` | str | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `LOG_FILE` | `"logs/nipxi.log"` | str | Log file path |

### Data storage

**Mode-separated** (driven by `SYSTEM_MODE` above -- see `docs/DATABASE_ROADMAP.md` Section 1): each mode gets its own subdirectory and database file so DEVELOPMENT experiments can never collide with VALIDATION or PRODUCTION data.

| Parameter | Value (DEVELOPMENT, default) | Value (VALIDATION) | Value (PRODUCTION) | Type | Description |
|-----------|-------------------------------|---------------------|----------------------|------|-------------|
| `DATA_DIR` | `"data_output/development"` | `"data_output/validation"` | `"data_output/production"` | str | Root output directory for the active mode |
| `DATABASE_FILE` | `"data_output/development/nipxi_dev.db"` | `".../nipxi_validation.db"` | `".../nipxi.db"` | str | SQLite database path |
| `CSV_DIR` | `"data_output/development/csv"` | `".../csv"` | `".../csv"` | str | Per-channel CSV output directory |
| `REPORT_DIR` | `"data_output/development/reports"` | `".../reports"` | `".../reports"` | str | Generated reports directory |

**`station_state` table** (`data/storage.py::DataStorage`, same `DATABASE_FILE` as `measurements` above): **recovery/current-position only, as of Milestone II Phase 3.** One row per relay processed -- `channel`, `relay`, `state` (`ACTIVE`/`COMPLETED`/`FAILED`/`SAFETY_VIOLATION`/`CANCELLED`, reusing `utils/stop_reason.py::StopReason`), `timestamp`. Written via `DataStorage.record_execution_state()`, read via `DataStorage.get_last_execution_state()` (always the latest row across all run_ids -- used to display, never auto-resume, the previous execution's last known position at startup). The historical SMU/DMM measurement payload columns still exist on this table for backward compatibility with rows written before Phase 3, but new code no longer populates them -- that data now lives in `measurements` (see below), the authoritative historical result store for every test type. Deliberately a separate table from `measurements` -- station/execution position is a different concern from a per-sample result, not part of the `StorageBackend` abstract interface. See `docs/architecture.md` Section 18b.

**`run_summary` table** -- one row per run: `id` (the operator-facing Run Number), `run_id`, `test_type`, `start_time`/`end_time`/`duration_s`, `stop_reason`, `result`, and (nullable, `N/A` for Proto Test) battery-config snapshot and `capacity_ah`/`energy_wh`/`cycle_count`. Written via `DataStorage.start_run_summary()`/`finish_run_summary()`. See `docs/architecture.md` Section 18.

**`event_log` table** -- fine-grained, timestamped runtime narrative (relay activated/deactivated, output enabled, measurement acquired, ...) -- NOT a replacement for logger output; only meaningful runtime transitions are recorded. Written via `DataStorage.log_event()`, read via `get_recent_events()`. See `docs/architecture.md` Section 18a/18b.

### Inspecting the database manually

**Recommended tool: [DB Browser for SQLite](https://sqlitebrowser.org/)** (free, GUI) -- open `data_output/<mode>/nipxi_<mode>.db` directly, browse `measurements`/`station_state`/`run_summary`/`event_log` as tables, and run ad hoc SQL from its "Execute SQL" tab. The `sqlite3` CLI works too for quick one-off queries (`sqlite3 data_output/development/nipxi_dev.db`) if you don't want to install anything.

**Example queries:**
```sql
-- run_summary: list all runs, most recent first
SELECT id AS run_number, run_id, test_type, start_time, stop_reason, result
FROM run_summary ORDER BY id DESC;

-- measurements: everything from one run (channel/relay both shown --
-- see docs/architecture.md's discussion of why they're separate columns)
SELECT channel, relay, test_type, phase_detail,
       smu_measured_v, smu_measured_i, dmm_measured_v, in_compliance
FROM measurements WHERE run_id = '<run_id>' ORDER BY id;

-- station_state: current/last recovery position (narrowed, Phase 3 --
-- no measurement columns expected here anymore)
SELECT channel, relay, state, timestamp FROM station_state ORDER BY id DESC LIMIT 1;

-- event_log: the full runtime narrative for one run
SELECT timestamp, level, message FROM event_log WHERE run_id = '<run_id>' ORDER BY id;
```

After a successful Proto Test Execution run, **all four tables** should contain rows for that `run_id` -- `measurements` (one row per relay cycled), `station_state` (one `ACTIVE` row per relay plus one final terminal row), `run_summary` (exactly one row), and `event_log` (several rows per relay). Before Phase 3, only `station_state` was populated -- if you're comparing against an older run, expect that difference.

---

## config/devices.py

Physical device mapping. Update to match your hardware wiring.

### BATTERY_CONFIGS

Battery type/model catalog -- physical battery specs (chemistry, capacity, voltage/current/temperature limits), independent of which channel a battery currently occupies. Foundation for the future `data/battery_repository.py` (see `docs/DATABASE_ROADMAP.md` Section 2). **Not wired into `safety_monitor.py`/`charge_cycle.py`/`discharge_cycle.py` yet** -- those still read the single global `BAT_VOLTAGE_MAX`/`BAT_VOLTAGE_MIN`/`BAT_CURRENT_MAX`/`BAT_TEMP_MAX_C` from `config/settings.py` for every channel regardless of what's actually installed there.

**Not used by SMU Functional Validation.** An earlier version of `test.py::
_functional_smu()` reused `nominal_voltage_v`/`voltage_max_v` from this dict, but
that was corrected: SMU Functional Validation now reuses `config/settings.py`'s
`CHARGE_VOLTAGE_V`/`CHARGE_CURRENT_A`/`BAT_VOLTAGE_MAX` instead (see below), since
those are the values that actually govern the SMU's real charge-phase behavior
-- more representative of production than a battery-model nameplate value. See
README.md Section 8.1b and `docs/architecture.md` Section 12.6.

```python
BATTERY_CONFIGS = {
    "GENERIC_LIION_18650": {
        "chemistry":               "Li-ion",
        "form_factor":             "18650",
        "nominal_voltage_v":       3.7,
        "voltage_max_v":           4.2,
        "voltage_min_v":           3.0,
        "capacity_ah":             2.5,
        "max_charge_current_a":    1.25,   # 0.5C
        "max_discharge_current_a": 2.5,    # 1C
        "max_temp_c":              45.0,
    },
}
```

### BATTERY_CHANNELS

Maps logical channel index (1-8) to physical wiring, plus which `BATTERY_CONFIGS` entry is currently installed:

```python
BATTERY_CHANNELS = {
    1: {
        "relay_address":   1,           # relay matrix channel number (1-based)
        "daq_voltage_ch":  "Dev1/ai0",  # NI 6363 analog input for battery voltage
        "daq_current_ch":  "Dev1/ai8",  # analog input for current (via shunt resistor)
        "daq_ntc_ch":      "Dev1/ai16", # analog input for NTC thermistor divider output
        "fuse_rating_a":   2.0,         # polyfuse rating (for documentation)
        "battery_type":    "GENERIC_LIION_18650",  # key into BATTERY_CONFIGS
        "enabled":         True,
    },
    # ... channels 2-8
}
```

`utils/device_validator.py` validates every `battery_type` at startup: it must reference a key that actually exists in `BATTERY_CONFIGS`, catching a typo/rename before it surfaces as a confusing `KeyError` later.

**Important:** Verify that `relay_address` matches the physical relay wiring on the BLOSS Hub PCB. Mismatch will connect the wrong battery to the SMU.

### PXI_SLOTS -- single source of truth for the PXI chassis

`config/devices.py::PXI_SLOTS` is the one place every PXI-slot resource string/
model is hand-authored, confirmed against the real rack (NI-MAX detection).
`SMU_ASSIGNMENTS`/`DAQ_CONFIG(S)`/`DMM_CONFIG(S)` below are **derived** from it
by `category` -- edit `PXI_SLOTS`, not those dicts, when hardware changes.

```python
PXI_SLOTS = {
    5: {
        "slot": 5, "resource": "PXI1Slot5", "model": "PXIe-4141",
        "nickname": "PRIMARY_SMU", "driver_family": "nidcpower",
        "category": "smu", "role": "...", "enabled": True,
        "channels": list(range(1, 9)),
        "validation_notes": "...",
    },
    # ... one entry per real slot -- see config/devices.py for the full,
    # current inventory (SMUs, DAQs, DMM, plus the PXI-resident relay/switch
    # card and temperature module that are present but not yet wired into a
    # driver class)
}
```

Every entry has: `slot`, `resource`, `model`, `nickname` (role-based, not just
the model number), `driver_family`, `category` (`smu`/`daq`/`dmm`/`switch`/
`temperature` -- used to derive the per-type dicts below), `role`, `enabled`,
and `validation_notes` where the real rack differs from what was originally
planned (see `flowcharts/vi plan.md`). **Note:** `enabled` is documentation
only today -- no code path reads it (confirmed by inspection: Hardware
Discovery, `test_daq()`/`test_temperature_module()`, and
`utils/device_validator.py` all filter purely by `category`, never by
`enabled`). An entry actually stops being probed/tested only when it is
removed or commented out of `PXI_SLOTS` entirely -- see "Installed vs.
Disabled Hardware" below.

Not a PXI slot: `GPIB_INSTRUMENTS` documents the separate NI-488.2/GPIB0
interface detected in the rack -- no instrument model confirmed at that
address yet, kept `enabled: False` until one is.

### Installed vs. Disabled Hardware

**Currently installed and validated on the physical rack** (Hardware Bring-Up Milestone 1 / Proto Test Execution, `docs/MILESTONES.md`):

| Nickname | Slot/Address | Model |
|---|---|---|
| `PRIMARY_SMU` | Slot 5 | PXIe-4141 |
| `HIGH_POWER_SMU` | Slot 6 | PXIe-4139 |
| `AUX_SMU_1` | Slot 7, channel `"1"` | PXI-4130 |
| `AUX_SMU_2` | Slot 8, channel `"1"` | PXI-4130 |
| `MAIN_DAQ` | Slot 2 | PXIe-6363 |
| `MAIN_DMM` | Slot 3 | PXI-4065 |
| `MATRIX_NUMATO_201` | 169.254.1.201 | Numato 32-ch Ethernet Relay |
| `MATRIX_NUMATO_202` | 169.254.1.202 | Numato 32-ch Ethernet Relay |
| `CHASSIS_RELAY_MATRIX` | Slot 11 | PXIe-2569 (present, no driver -- reported N/A, not disabled) |

**Intentionally disabled -- not physically installed** (hardware cleanup pass following the Milestone II Phase 3 review): the `PXI_SLOTS`/`RELAY_CONFIG` entries below are commented out in `config/devices.py`, not deleted, so they disappear entirely from Hardware Discovery/`Test DAQ`/`Test Temperature Module`'s device lists instead of reporting `[FAIL]` for hardware that was never installed:

| Nickname | Slot/Address | Model | Why disabled |
|---|---|---|---|
| `TEMP_MODULE` | Slot 15 | PXIe-4353 | Not physically installed |
| `EXPANSION_DAQ` | Slot 17 | PXIe-6368 | Not physically installed |
| `PRECISION_DAQ` | Slot 18 | PXIe-6365 | Not physically installed |
| `MAIN_MATRIX` (serial relay) | COM13 | Generic serial relay | Not physically installed/connected -- production has always been the Numato Ethernet relay above; this was diagnostic-only even when present |

**To re-enable:** uncomment the corresponding `PXI_SLOTS` entry (or `RELAY_CONFIG`) in `config/devices.py`. **`RELAY_CONFIG` has one hard dependency to restore correctly:** `RELAY_SERIAL_CONFIGS` references `RELAY_CONFIG` by name -- both must be uncommented/restored together (`RELAY_SERIAL_CONFIGS = {RELAY_CONFIG["name"]: RELAY_CONFIG}`), or `import config.devices` fails immediately with `NameError` for every entry point in the application. Nothing else needs to change -- `SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/`DMM_CONFIGS` are all *derived* from `PXI_SLOTS`, so an uncommented entry reappears in every downstream dict automatically.

**Effect on Proto Test Execution / Battery Cycling:** none. `HardwareManager` never constructs `EXPANSION_DAQ`/`PRECISION_DAQ`/`TEMP_MODULE`/the serial relay regardless of whether they're configured -- disabling them only affects the discovery/validation menu surface.

### SMU_ASSIGNMENTS (derived from PXI_SLOTS, category="smu")

Every SMU-category slot is listed (so Hardware Discovery / test.py can see
and individually test each physical SMU); `HardwareManager` still only ever
connects the FIRST entry (`next(iter(SMU_ASSIGNMENTS.values()))`) as the one
SMU actively driving the battery test sequence -- today that resolves to
`PRIMARY_SMU` (`PXI1Slot5`, `PXIe-4141`), since `PXI_SLOTS` lists it before
the other three. Multi-SMU channel assignment (actually using
`HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2` for real charge/discharge) is a
future scaling task, not implemented.

**`smu_channel` / `channels_per_card`:** every SMU entry also carries the
NI-DCPower channel this instance opens (`smu_channel`, a channel name string
e.g. `"0"`/`"1"`) and the card's physical NI-DCPower channel count
(`channels_per_card` -- `1` for the single-channel `PRIMARY_SMU`/
`HIGH_POWER_SMU`, `2` for the two-channel `AUX_SMU_1`/`AUX_SMU_2` PXI-4130
units). `hardware/smu.py::SMU.connect()` opens its `nidcpower.Session` scoped
to exactly this one channel -- config-driven, never hardcoded in the driver.
This is required for multi-channel cards: an unscoped session on a 2-channel
PXI-4130 raises NI-DCPower error `-1074118522` ("single channel must be
specified") on any repeated-capability property/method, which is exactly
what a real rack bring-up found and this field fixes (see
`docs/architecture.md` Section 12.6a). Confirmed on physical hardware:
`AUX_SMU_1` and `AUX_SMU_2` are both wired to channel `"1"`.

| Nickname | Slot | Model | `smu_channel` | `channels_per_card` |
|---|---|---|---|---|
| `PRIMARY_SMU` | 5 | PXIe-4141 | `"0"` | 1 |
| `HIGH_POWER_SMU` | 6 | PXIe-4139 | `"0"` | 1 |
| `AUX_SMU_1` | 7 | PXI-4130 | `"1"` (confirmed) | 2 |
| `AUX_SMU_2` | 8 | PXI-4130 | `"1"` (confirmed) | 2 |

### DAQ_CONFIG / DAQ_CONFIGS (derived from PXI_SLOTS, category="daq")

`DAQ_CONFIG` (singular) is `DAQ_CONFIGS["MAIN_DAQ"]` -- the one DAQ
`HardwareManager` actually connects. `EXPANSION_DAQ`/`PRECISION_DAQ` (the
other two real DAQ cards in the rack) are present and individually testable
but not wired into the active pipeline.

**Known limitation:** unlike `SMU_ASSIGNMENTS` (which `HardwareManager`
resolves by slot order, `next(iter(SMU_ASSIGNMENTS.values()))` -- nickname-
agnostic), `DAQ_CONFIG` is looked up by the literal string `"MAIN_DAQ"`.
Renaming `PXI_SLOTS[2]`'s `nickname` away from `"MAIN_DAQ"` raises a
`KeyError` at `import config.devices` time -- before any test or hardware
communication runs, since every entry point imports this module. See
"Hardware Replacement Procedure" below.

### DMM_CONFIG / DMM_CONFIGS (derived from PXI_SLOTS, category="dmm")

`DMM_CONFIG` (singular) is `DMM_CONFIGS["MAIN_DMM"]` -- currently the only
DMM in the rack. Same known limitation as `DAQ_CONFIG` above: it is looked
up by the literal nickname `"MAIN_DMM"`, not resolved by slot order.

### Hardware Replacement Procedure

**Goal:** replace a PXI card by editing only `PXI_SLOTS` (model, resource,
nickname, role, etc.), with no other code or config change required.

**What this already covers (PXI_SLOTS-only, verified):**
- Changing a card's `model`/`resource` while keeping its `nickname` --
  any category (SMU, DMM, DAQ, Temperature Module).
- Changing a **SMU**'s `nickname` (e.g. `PRIMARY_SMU` -> `NEW_SMU`) --
  `HardwareManager` resolves the active SMU by slot order
  (`next(iter(SMU_ASSIGNMENTS.values()))`), never by name.

**What still requires a second, explicit edit today:**
- Changing the **DAQ** or **DMM** `nickname` away from `"MAIN_DAQ"` /
  `"MAIN_DMM"` also requires updating the corresponding lookup in
  `config/devices.py`:
  ```python
  DAQ_CONFIG = DAQ_CONFIGS["MAIN_DAQ"]   # update the key, or switch to
  DMM_CONFIG = DMM_CONFIGS["MAIN_DMM"]   # next(iter(...)) to avoid this entirely
  ```
  Forgetting this raises `KeyError` at `import config.devices` time (i.e.
  the whole application fails to start, not just one test).
- Replacing the DAQ card (or moving it to a different chassis slot) may
  change the NI-MAX device alias NI-MAX assigns it. `BATTERY_CHANNELS`'s
  `daq_voltage_ch`/`daq_current_ch`/`daq_ntc_ch` hardcode this alias as the
  literal `"Dev1"`, independent of `PXI_SLOTS[2]["resource"]` -- this is a
  hardware/software boundary limit (NI-MAX assigns the alias; it cannot be
  derived from a chassis slot string in software) and must be reconfirmed
  against NI-MAX and updated by hand after any DAQ change.

**Not required, but recommended to avoid stale documentation:**
- `utils/constants.py`'s `CARD_*` block duplicates model/slot/nickname
  information as comments/constants. It is not read by any code (verified),
  so it cannot break anything, but it will silently go stale after a
  hardware swap unless updated by hand alongside `PXI_SLOTS`.

### RELAY_CONFIG (serial -- diagnostic only, NOT production)

`RELAY_CONFIG` / `SerialRelay` (COM13) is used only for bench diagnostics via `test.py`. It is never selected by `main.py` or by `test.py`'s "Run Main Test" -- production relay control always goes through `NUMATO_RELAY_MATRIX_CONFIG` / `NumatoRelayMatrix` below.

```python
RELAY_CONFIG = {
    "type":         "serial",
    "name":         "MAIN_MATRIX",
    "port":         "COM3",         # Windows COM port -- check Device Manager
    "baud_rate":    9600,
    "timeout":      2.0,
    "num_channels": 8,
    # Command protocol -- replace these placeholders with real commands:
    "command_open":  "OPEN {ch}\r\n",    # {ch} is replaced with channel number 1-8
    "command_close": "CLOSE {ch}\r\n",
    "command_query": "QUERY {ch}\r\n",
}
```

The query response is checked for `"ON"`, `"CLOSED"`, or `"1"` to determine closed state. Adjust `SerialRelay.query()` in `hardware/relay_serial.py` if your controller uses a different response format.

### ETHERNET_DEVICES / NUMATO_RELAY_MATRIX_CONFIG (Ethernet -- PRODUCTION)

`ETHERNET_DEVICES` holds every Numato Lab 32-Channel Ethernet Relay Module in the finalized NIPXI network plan -- currently `MATRIX_NUMATO_201` and `MATRIX_NUMATO_202`, the only two Ethernet relay devices defined, named after their static IP's last octet for easier hardware ID/troubleshooting/rack labeling. `NUMATO_RELAY_MATRIX_CONFIG` is a backward-compat alias for `ETHERNET_DEVICES["MATRIX_NUMATO_201"]`; `NUMATO_RELAY_MATRIX_CONFIGS` aliases the whole `ETHERNET_DEVICES` dict. The old role-based names `MAIN_MATRIX_ETH`/`AUX_MATRIX_ETH_1` are kept as legacy compat aliases pointing at `MATRIX_NUMATO_201`/`MATRIX_NUMATO_202` respectively, for any code that still imports them directly. `NumatoRelayMatrix` is the production relay control path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix -> Numato Relay`. `NumatoRelayMatrix.close(ch)`/`open(ch)` enforce a mandatory all-off -> verify -> activate -> verify safety sequence -- see [architecture.md section 6a](architecture.md#6a-mandatory-relay-safety-sequence).

Validated settings -- static IPs, DHCP disabled on both devices (confirmed reachable -- ping, web interface, Telnet login, and relay command/readback all work):

```python
ETHERNET_DEVICES = {
    # Numato Relay Matrix at 169.254.1.201
    "MATRIX_NUMATO_201": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_201",
        "ip":            "169.254.1.201",  # static -- DHCP disabled
        "port":          23,               # validated -- Numato default Telnet port
        "username":      "admin",          # validated Telnet credentials
        "password":      "admin",          # validated Telnet credentials
        "timeout":       5.0,
        "num_channels":  32,               # physical relay count on the 32-ch module
        "channel_count": 32,
    },
    # Numato Relay Matrix at 169.254.1.202
    "MATRIX_NUMATO_202": {
        "type":          "ethernet",
        "driver":        "RELAY32ETHRL00",
        "name":          "MATRIX_NUMATO_202",
        "ip":            "169.254.1.202",  # static -- DHCP disabled
        "port":          23,
        "username":      "admin",
        "password":      "admin",
        "timeout":       5.0,
        "num_channels":  32,
        "channel_count": 32,
    },
}
```

`169.254.1.1` was the Numato factory default (link-local) on both units before static IPs were assigned -- no longer used in active configuration.

---

## Finding NI resource strings

1. Open **NI-MAX** (Start -> National Instruments -> NI Measurement & Automation Explorer)
2. Expand **Devices and Interfaces**
3. Expand your PXI chassis
4. Right-click each instrument -> **Properties** -> note the **Resource Name** field

Confirmed real rack inventory (see `config/devices.py::PXI_SLOTS` for the
authoritative, current version -- this table is a snapshot, not the source
of truth):

| Slot | Instrument | Resource string | Nickname |
|------|------------|-----------------|----------|
| 2 | PXIe-6363 | `PXI1Slot2` | `MAIN_DAQ` |
| 3 | PXI-4065 | `PXI1Slot3` | `MAIN_DMM` |
| 5 | PXIe-4141 | `PXI1Slot5` | `PRIMARY_SMU` |
| 6 | PXIe-4139 | `PXI1Slot6` | `HIGH_POWER_SMU` |
| 7 | PXI-4130 | `PXI1Slot7` | `AUX_SMU_1` |
| 8 | PXI-4130 | `PXI1Slot8` | `AUX_SMU_2` |
| 11 | PXIe-2569 | `PXI1Slot11` | `CHASSIS_RELAY_MATRIX` (not the active relay driver -- see below) |
| 15 | PXIe-4353 + TB-4353/0 | `PXI1Slot15` | `TEMP_MODULE` (not yet wired into any driver) |
| 17 | PXIe-6368 | `PXI1Slot17` | `EXPANSION_DAQ` |
| 18 | PXIe-6365 | `PXI1Slot18` | `PRECISION_DAQ` |
| -- | NI-488.2 | `GPIB0` | unconfirmed instrument -- see `GPIB_INSTRUMENTS` |

Update `config/devices.py::PXI_SLOTS` accordingly -- not `config/settings.py`,
and not `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` directly (those are
derived from `PXI_SLOTS`, see above).

---

## Validating configuration

Two separate checks, both gate `test.py`'s menu automatically (`preflight_check()`) and can also be run individually:

```bash
python test.py
# Choose: "Test Configuration"           -- validate_settings(): Settings values (voltages,
#                                            currents, timeouts, RELAY_COUNT, ...)
# Choose: "Startup Device Validation"    -- validate_devices(): config/devices.py (every device
#                                            can be instantiated, required fields present, no
#                                            duplicate names/resources/IPs/COM ports/relay
#                                            identifiers, relay count consistency, factory type)
```

Fix any FAIL items before hardware testing -- `main.py` runs the equivalent of both checks at
startup (`validate_settings()` then `validate_devices_or_raise()`) and exits before touching any
hardware if either fails. See `docs/architecture.md` Section 8.3 and README.md Section 17.2 for
the full list of device-level checks.

Configuration is also validated automatically at startup by `main.py`.
