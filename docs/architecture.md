# NIPXI Architecture

## 1. System Overview

```
                +--------------------------------------------------+
                |              Host PC / Control System            |
                |                                                  |
                |  main.py  (thin orchestration)                   |
                |    +-- validate_settings()                       |
                |    +-- setup_logging()                           |
                |    +-- HardwareManager                           |
                |    |     +-- SMU / DAQ / Relay                   |
                |    +-- ResultManager                             |
                |    |     +-- DataStorage  (SQLite + CSV)         |
                |    |     +-- ReportGenerator                     |
                |    +-- TestExecutor                              |
                |          +-- BatteryTestSequence                 |
                |          +-- ChargeCycle       (CC-CV)           |
                |          +-- DischargeCycle    (CC)              |
                |          +-- SafetyMonitor     (real-time)       |
                +-------------------+------------------------------+
                                    |
                     NI-VISA / nidaqmx / nidcpower / nidmm
                                    |
                +-------------------v------------------------------+
                |                PXI Chassis                       |
                |   Slot 2: NI 6363 DAQ  (V / I / NTC voltages)  |
                |   Slot 3: NI 4065 DMM  (precision V measurement)|
                |   Slot 4: NI 4140 SMU  (CC-CV charge/discharge) |
                |   Slot 5: NI 4130 SMU  (optional second unit)   |
                +---+----------------+----------------------------+
                    |                |
          pyserial or TCP        NI-VISA
                    |
       +------------v-----------------------------+
       |  Relay Matrix (RelayBase)                |
       |                                          |
       |  Serial:   SerialRelay (COM port)        |
       |  Ethernet: EthernetRelay (RELAY32ETHRL00)|
       |                                          |
       |  8 channels, multiplexed (one at a time) |
       +------------+-----------------------------+
                    |  wire connections
       +------------v-----------------------------------------+
       |                  BLOSS Hub PCB (Rev A)               |
       |  8x Li-ion battery connectors                        |
       |  8x 2 A polyfuses                                    |
       |  8x 10k NTC thermistors (3.3 V divider)             |
       |  8x Kelvin sense outputs (voltage accuracy)          |
       +------------------------------------------------------+
```

---

## 2. Module Dependency Map

```
main.py  (thin orchestration)
  +-- config/settings.py
  +-- config/devices.py
  +-- data/logger.py                    setup_logging()
  +-- utils/validators.py               validate_settings()
  +-- test_control/hardware_manager.py  HardwareManager
  +-- test_control/result_manager.py    ResultManager
  +-- test_control/test_executor.py     TestExecutor

test_control/hardware_manager.py
  +-- hardware/smu.py                   SMU
  +-- hardware/daq.py                   DAQ
  +-- hardware/relay_factory.py         RelayFactory.create()

test_control/test_executor.py
  +-- test_control/battery_test.py      BatteryTestSequence
  +-- test_control/charge_cycle.py      ChargeCycle
  +-- test_control/discharge_cycle.py   DischargeCycle
  +-- test_control/safety_monitor.py    SafetyMonitor

test_control/result_manager.py
  +-- data/storage.py                   DataStorage (StorageBackend)
  +-- data/report.py                    ReportGenerator

hardware/relay_factory.py
  +-- hardware/relay.py                 RelayBase (ABC)
  +-- hardware/relay_serial.py          SerialRelay
  +-- hardware/relay_eth.py             EthernetRelay
```

All hardware classes inherit `hardware/base.py::HardwareBase`.  
All exceptions inherit `utils/errors.py::NIPXIError`.

---

## 3. Control Flow

<!-- NOTE: A visual VI flowchart does not yet exist.
     Create flowcharts/vi_flowchart.md to document the full sequence visually. -->

**Startup:**

```
main.py
  validate_settings(Settings)     -- fail-fast on bad config
  setup_logging(Settings)
  [TODO] PXIRack.enumerate()      -- verify expected cards are present
  [TODO] SMU.connect()
  [TODO] DAQ.connect()
  RelayFactory.create(RELAY_CONFIG).connect()
  BatteryTestSequence.run(channels)
```

**Per-channel test sequence:**

```
For channel N in active_channels:
  1. DAQ.verify_zero_current(N)
     -- must read < ZERO_CURRENT_THRESHOLD_A (0.01 A) before relay switch
     -- if not, wait or abort

  2. relay.close(N)
     -- SafetyMonitor.is_safe_to_switch_relay() called first

  3. time.sleep(STABILIZATION_S)
     -- allow voltage to settle after relay closes

  4. ChargeCycle.run(channel=N, data_collector=storage)
     Loop:
       sample = DAQ.read_all_batteries()[N]
       status = safety.check(V, I, T)
       if not status.safe: emergency_stop(); raise SafetyViolationError
       storage.record(N, sample)
       if V >= CHARGE_VOLTAGE_V and I <= CHARGE_CUTOFF_A: break  (CC-CV taper)
       if elapsed > CHARGE_TIMEOUT_S: return False  (timeout)

  5. SMU.output_disable()
     DAQ.verify_zero_current(N)

  6. DischargeCycle.run(channel=N, data_collector=storage)
     Loop:
       sample = DAQ.read_all_batteries()[N]
       status = safety.check(V, I, T)
       if not status.safe: emergency_stop(); raise SafetyViolationError
       storage.record(N, sample)
       if V <= DISCHARGE_CUTOFF_V: break  (end of discharge)
       if elapsed > DISCHARGE_TIMEOUT_S: return False

  7. SMU.output_disable()
     relay.open(N)

Report generation (TODO)
```

**Emergency stop sequence:**

```
SafetyMonitor.emergency_stop(smu, relay, reason)
  smu.output_disable()     -- cut SMU output first
  relay.open_all()         -- disconnect all batteries
  log.error(reason)
```

---

## 4. Safety Rules

| Rule | Limit | Source |
|------|-------|--------|
| Overvoltage | V > `BAT_VOLTAGE_MAX` (4.7 V) | `safety_monitor.py` |
| Undervoltage | V < `BAT_VOLTAGE_MIN` (3.5 V) | `safety_monitor.py` |
| Overcurrent | |I| > `BAT_CURRENT_MAX` (1.0 A) | `safety_monitor.py` |
| Overtemperature | T > `BAT_TEMP_MAX_C` (45 degC) | `safety_monitor.py` |
| Relay switch | |I| > `ZERO_CURRENT_THRESHOLD_A` (0.01 A) | `safety_monitor.py` |

Temperature check is skipped when `temp_c=None` (NTC not yet connected).

---

## 5. Data Flow

```
DAQ.read_all_batteries()
       |
       v
  {voltage_v, current_a, ntc_v}  (raw readings)
       |
       +-- hardware/temperature.py::ntc_voltage_to_celsius()
       |       -> temp_c
       |
       v
  DataStorage.record(channel, sample)
       |
       +---> SQLite: measurements table (nipxi.db)
       |
       +---> CSV: <run_id>_ch<N>.csv  (one per channel per run)
       |
       v
  data/report.py (TODO)
       |
       v
  reports/  (summary, capacity Ah, V/I vs time plots)
```

**SQLite schema:**

```sql
CREATE TABLE measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,    -- "YYYYMMDD_HHMMSS"
    channel     INTEGER NOT NULL,    -- 1-8
    timestamp   TEXT    NOT NULL,    -- ISO 8601
    elapsed_s   REAL,
    phase       TEXT,                -- "charge" or "discharge"
    voltage_v   REAL,
    current_a   REAL,
    temp_c      REAL
);
```

---

## 6. Relay Architecture

The relay system uses a factory/strategy pattern. All callers work against `RelayBase`; the factory decides which concrete class to instantiate based on `cfg["type"]`.

```
config/devices.py
  RELAY_CONFIG        (type="serial")
  RELAY_ETH_CONFIG    (type="ethernet")
       |
       v
hardware/relay_factory.py
  RelayFactory.create(cfg)
       |
       +-- type="serial"   -> hardware/relay_serial.py::SerialRelay
       +-- type="ethernet" -> hardware/relay_eth.py::EthernetRelay
       |
       v
hardware/relay.py::RelayBase
  connect() / disconnect()
  open(ch) / close(ch)
  open_all() / close_all()
  query(ch) -> bool
```

**Ethernet relay protocol (Numato RELAY32ETHRL00):**

```
TCP:23 -> login prompt -> username\r\n -> Password prompt -> password\r\n
       -> "successfully" -> ">"
       -> "relay on N\r\n"  (close relay N, wait for ">")
       -> "relay off N\r\n" (open relay N,  wait for ">")
       -> "relay read N\r\n" -> "on\r\n>" or "off\r\n>"
```

Channel addressing: 1->0, 2->1, ..., 10->9, 11->A, ..., 32->V (Numato 0-based, A-V for 10+).

---

## 7. StorageBackend Interface (MiniSQL path)

```
data/storage.py
  StorageBackend (ABC)
    open() / close()
    record(channel, sample)
    query(run_id, channel) -> list[dict]
    __enter__ / __exit__

  DataStorage(StorageBackend)     <- current implementation (SQLite + CSV)

  MiniSQLStorage(StorageBackend)  <- future implementation (implement when ready)
```

Callers (`ChargeCycle`, `DischargeCycle`, `BatteryTestSequence`) receive a `StorageBackend`
by dependency injection and call only `record()`. Swapping from SQLite to MiniSQL requires
one line change in `main.py` (the instantiation), no changes in business logic.
