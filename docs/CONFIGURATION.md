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

### Discharge parameters (CC)

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `DISCHARGE_CURRENT_A` | `0.5` | float (A) | Constant discharge current |
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

---

## config/devices.py

Physical device mapping. Update to match your hardware wiring.

### BATTERY_CONFIGS

Battery type/model catalog -- physical battery specs (chemistry, capacity, voltage/current/temperature limits), independent of which channel a battery currently occupies. Foundation for the future `data/battery_repository.py` (see `docs/DATABASE_ROADMAP.md` Section 2). **Not wired into `safety_monitor.py`/`charge_cycle.py`/`discharge_cycle.py` yet** -- those still read the single global `BAT_VOLTAGE_MAX`/`BAT_VOLTAGE_MIN`/`BAT_CURRENT_MAX`/`BAT_TEMP_MAX_C` from `config/settings.py` for every channel regardless of what's actually installed there.

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

### SMU_ASSIGNMENTS

Maps an SMU label to its VISA resource and which channels it serves:

```python
SMU_ASSIGNMENTS = {
    "SMU1": {
        "resource": "PXI1Slot4",
        "model":    "NI-4140",
        "channels": list(range(1, 9)),   # all 8 channels share SMU1 (multiplexed)
    }
}
```

For multi-SMU configurations (e.g. parallel testing), add `"SMU2"` with its own channel list.

### DAQ_CONFIG

```python
DAQ_CONFIG = {
    "resource":       "PXI1Slot2",
    "model":          "NI-6363",
    "sample_rate_hz": 1.0,
    "voltage_range_v": 5.0,   # input range: set to match expected voltages
}
```

### DMM_CONFIG

```python
DMM_CONFIG = {
    "resource": "PXI1Slot3",
    "model":    "NI-4065",
    "function": "DC_VOLTS",
    "range_v":  10.0,
}
```

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

### NUMATO_RELAY_MATRIX_CONFIG (Ethernet -- PRODUCTION)

`NUMATO_RELAY_MATRIX_CONFIG` / `NumatoRelayMatrix` (Numato Lab 32-Channel Ethernet Relay Module) is the production relay control path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix -> Numato Relay`. `NumatoRelayMatrix.close(ch)`/`open(ch)` enforce a mandatory all-off -> verify -> activate -> verify safety sequence -- see [architecture.md section 6a](architecture.md#6a-mandatory-relay-safety-sequence).

Validated settings (confirmed reachable -- ping, web interface, Telnet login, and relay command/readback all work):

```python
NUMATO_RELAY_MATRIX_CONFIG = {
    "type":          "ethernet",
    "driver":        "RELAY32ETHRL00",
    "name":          "MAIN_MATRIX_ETH",
    "ip":            "169.254.1.1",   # validated -- Numato factory link-local IP
    "port":          23,              # validated -- Numato default Telnet port
    "username":      "admin",         # validated Telnet credentials
    "password":      "admin",         # validated Telnet credentials
    "timeout":       5.0,
    "num_channels":  32,              # physical relay count on the 32-ch module
    "channel_count": 32,
}
```

If the relay is later moved to a routed/DHCP network instead of a direct link-local connection, find its new IP via your router's DHCP table or a network scanner and update `ip` accordingly.

---

## Finding NI resource strings

1. Open **NI-MAX** (Start -> National Instruments -> NI Measurement & Automation Explorer)
2. Expand **Devices and Interfaces**
3. Expand your PXI chassis
4. Right-click each instrument -> **Properties** -> note the **Resource Name** field

Example resource names:

| Slot | Instrument | Resource string |
|------|------------|-----------------|
| 2 | NI 6363 DAQ | `PXI1Slot2` |
| 3 | NI 4065 DMM | `PXI1Slot3` |
| 4 | NI 4140 SMU | `PXI1Slot4` |

Update `config/devices.py` (`SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` `"resource"` fields) accordingly -- not `config/settings.py`.

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
