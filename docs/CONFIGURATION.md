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

### PXI hardware

Find VISA resource strings in **NI-MAX** (Measurement & Automation Explorer).

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `PXI_RESOURCE_DAQ` | `"PXI1Slot2"` | str | NI 6363 DAQ VISA resource |
| `PXI_RESOURCE_DMM` | `"PXI1Slot3"` | str | NI 4065 DMM VISA resource |
| `PXI_RESOURCE_SMU1` | `"PXI1Slot4"` | str | Primary SMU VISA resource |
| `PXI_RESOURCE_SMU2` | `"PXI1Slot5"` | str | Optional second SMU |
| `PXI_SIMULATE` | `False` | bool | NI simulation mode (no hardware needed) |

Setting `PXI_SIMULATE = True` lets NI drivers run in simulation mode — useful for software development without access to the PXI rack. Results will be dummy values.

### Serial relay

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `RELAY_COM_PORT` | `"COM3"` | str | Windows COM port of the relay controller |
| `RELAY_BAUD_RATE` | `9600` | int | Serial baud rate |
| `RELAY_TIMEOUT_S` | `2.0` | float (s) | Serial read timeout |
| `RELAY_NUM_CHANNELS` | `8` | int | Number of relay channels |

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

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `DATA_DIR` | `"data_output"` | str | Root output directory |
| `DATABASE_FILE` | `"data_output/nipxi.db"` | str | SQLite database path |
| `CSV_DIR` | `"data_output/csv"` | str | Per-channel CSV output directory |
| `REPORT_DIR` | `"data_output/reports"` | str | Generated reports directory |

---

## config/devices.py

Physical device mapping. Update to match your hardware wiring.

### BATTERY_CHANNELS

Maps logical channel index (1-8) to physical wiring:

```python
BATTERY_CHANNELS = {
    1: {
        "relay_address":   1,           # relay matrix channel number (1-based)
        "daq_voltage_ch":  "Dev1/ai0",  # NI 6363 analog input for battery voltage
        "daq_current_ch":  "Dev1/ai8",  # analog input for current (via shunt resistor)
        "daq_ntc_ch":      "Dev1/ai16", # analog input for NTC thermistor divider output
        "fuse_rating_a":   2.0,         # polyfuse rating (for documentation)
        "enabled":         True,
    },
    # ... channels 2-8
}
```

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

### RELAY_CONFIG (serial)

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

### RELAY_ETH_CONFIG (Ethernet)

```python
RELAY_ETH_CONFIG = {
    "type":         "ethernet",
    "driver":       "RELAY32ETHRL00",
    "name":         "MAIN_MATRIX_ETH",
    "ip":           "192.168.1.50",   # REQUIRED: set to actual relay IP
    "port":         23,               # Numato default Telnet port
    "user":         "admin",          # default Numato credentials
    "password":     "admin",
    "timeout":      5.0,
    "num_channels": 8,
}
```

To find the relay IP: connect it to a network switch and check your router's DHCP table, or use a network scanner. The factory default is usually `169.254.1.1` on link-local.

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

Update `config/settings.py` `PXI_RESOURCE_*` accordingly.

---

## Validating configuration

Run the test framework and choose "Test Configuration":

```bash
python test.py
# Choose: 9 (Test Configuration)
```

This runs `validate_settings()` and checks every required parameter. Fix any FAIL items before hardware testing.

Configuration is also validated automatically at startup by `main.py`.
