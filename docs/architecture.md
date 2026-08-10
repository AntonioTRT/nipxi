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
                |     PXI Chassis (config/devices.py::PXI_SLOTS)    |
                |   Slot 2:  PXIe-6363  MAIN_DAQ         (active) |
                |   Slot 3:  PXI-4065   MAIN_DMM         (active) |
                |   Slot 5:  PXIe-4141  PRIMARY_SMU      (active) |
                |   Slot 6:  PXIe-4139  HIGH_POWER_SMU            |
                |   Slot 7:  PXI-4130   AUX_SMU_1                 |
                |   Slot 8:  PXI-4130   AUX_SMU_2                 |
                |   Slot 11: PXIe-2569  CHASSIS_RELAY_MATRIX (n/a)|
                |   Slot 15: PXIe-4353  TEMP_MODULE (identity only)|
                |   Slot 17: PXIe-6368  EXPANSION_DAQ             |
                |   Slot 18: PXIe-6365  PRECISION_DAQ             |
                |   GPIB0:   NI-488.2   unconfirmed instrument    |
                |   See Section 14 for the full inventory table.  |
                +---+----------------+----------------------------+
                    |                |
          pyserial or TCP        NI-VISA
                    |
       +------------v-----------------------------+
       |  Relay Matrix (RelayBase)                |
       |                                          |
       |  Ethernet: NumatoRelayMatrix (Numato 32-ch)  |  <- PRODUCTION
       |  Serial:   SerialRelay (COM13)           |  <- diagnostic only
       |                                          |
       |  32 channels, interlocked (one at a time)|
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
  +-- utils/validators.py               validate_settings()          -- Settings-level
  +-- utils/device_validator.py         validate_devices_or_raise()  -- config/devices.py-level
  +-- test_control/hardware_manager.py  HardwareManager
  +-- test_control/result_manager.py    ResultManager
  +-- test_control/test_executor.py     TestExecutor

test_control/hardware_manager.py
  +-- config/devices.py                 SMU_ASSIGNMENTS / DAQ_CONFIG / DMM_CONFIG (default cfgs --
  |                                      all three are DERIVED from PXI_SLOTS, see Section 14)
  +-- hardware/smu.py                   SMU(cfg)
  +-- hardware/daq.py                   DAQ(cfg)
  +-- hardware/dmm.py                   DMM(cfg)         -- optional, off by default
  +-- hardware/relay_factory.py         RelayFactory.create(cfg)

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
  +-- hardware/relay_eth.py             NumatoRelayMatrix

utils/device_validator.py
  +-- hardware/smu.py, daq.py, dmm.py   construction-only checks (never connect())
  +-- hardware/relay_factory.py         RelayFactory.create() + RelayFactory.supported_types()

test.py
  +-- hardware/smu.py, daq.py, dmm.py   test_smu()/test_dmm()/test_daq() AND
  |                                      test_hardware_discovery()'s _identify_*() helpers both
  |                                      call the SAME driver classes -- connect()/identify() has
  |                                      exactly one implementation per device type, not two.
  +-- hardware/relay_factory.py         test_hardware_discovery(), test_relay_ethernet_test(),
                                          and every test_relay_*() function all go through
                                          RelayFactory.create(cfg) -- never a bare NumatoRelayMatrix()/
                                          SerialRelay() constructor call, never raw socket/pyserial/
                                          nidcpower/nidmm/nidaqmx calls outside hardware/.
```

All hardware classes inherit `hardware/base.py::HardwareBase`.  
All exceptions inherit `utils/errors.py::NIPXIError`.

**config/devices.py is the single source of truth for every device's resource string / address.** `config/settings.py` no longer duplicates PXI slot numbers (the removed `PXI_RESOURCE_DAQ`/`PXI_RESOURCE_DMM`/`PXI_RESOURCE_SMU1`/`PXI_RESOURCE_SMU2` constants) -- `HardwareManager` used to read those instead of `config/devices.py`'s `SMU_ASSIGNMENTS`/`DAQ_CONFIG`, silently diverging from it if the two were ever edited independently. `HardwareManager.__init__()` now defaults `smu_cfg`/`daq_cfg` from `config/devices.py` directly. See Section 8 for the full pipeline and the reasoning for why relay has a `Factory` class but SMU/DAQ/DMM construct directly from a config dict instead.

---

## 3. Control Flow

<!-- NOTE: A visual VI flowchart does not yet exist.
     Create flowcharts/vi_flowchart.md to document the full sequence visually. -->

**Startup:**

```
main.py
  validate_settings(Settings)          -- fail-fast on bad Settings values
  validate_devices_or_raise(dev_cfg)   -- fail-fast on bad config/devices.py (Section 8)
                                           -- construction-only, no hardware I/O yet
  setup_logging(Settings)
  [TODO] PXIRack.enumerate()      -- verify expected cards are present
  HardwareManager(Settings, relay_cfg=NUMATO_RELAY_MATRIX_CONFIG).connect_all()
     -- constructs SMU(smu_cfg)/DAQ(daq_cfg) from config/devices.py, then
        SMU.connect(), DAQ.connect(), RelayFactory.create(relay_cfg).connect()
  BatteryTestSequence.run(channels)
```

Production path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix -> Numato Relay`.
Validated Numato settings: `MATRIX_NUMATO_201` IP `169.254.1.201`, `MATRIX_NUMATO_202` IP `169.254.1.202`
(both static, DHCP disabled), port `23`, user/password `admin`/`admin` on both. (`MAIN_MATRIX_ETH`/
`AUX_MATRIX_ETH_1` are legacy compat aliases for these two, kept for old code that still imports them.)
Serial COM13 (`RELAY_CONFIG`) is diagnostic-only and is never used by `main.py`.

Before `main.py` reaches this point at all, the test framework's own gate
(`test.py`'s `preflight_check()`) already runs both `validate_settings()` and
`validate_devices_or_raise()`-equivalent checks and blocks the menu on
failure -- see Section 8 ("Test execution order").

**Per-channel test sequence:**

```
For channel N in active_channels:
  1. DAQ.verify_zero_current(N)
     -- must read < ZERO_CURRENT_THRESHOLD_A (0.01 A) before relay switch
     -- if not, wait or abort

  2. relay.close(N)
     -- SafetyMonitor.is_safe_to_switch_relay() called first
     -- NumatoRelayMatrix.close(N) internally runs the mandatory safety
        sequence (see section 6a) -- N is never activated directly

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
  relay.open_all()         -- disconnect all batteries (force-off + verify)
  log.error(reason)
```

**Relay verification faults are safety faults, not warnings:**

```
BatteryTestSequence.run()
  try: relay.close(ch) / charge / discharge
  except (SafetyViolationError, RelayError) as e:
      -- RelayError includes RelayStateVerificationError (readback mismatch,
         multiple relays active, unexpected relay state, comms timeout)
      safety.emergency_stop(smu, relay, str(e))
      raise   -- never falls through to relay.open(ch) or the next channel

TestExecutor.run()
  except (SafetyViolationError, RelayError) as e:
      result.aborted = True   -- whole run stops, no partial continuation
```

No warning-only mode exists for a relay verification failure anywhere in the stack.

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

The relay system uses a factory/strategy pattern. All callers work against `RelayBase`; the factory decides which concrete class to instantiate based on `cfg["type"]`. `config/devices.py` is the single source of truth for device discovery -- relays, SMUs, DMMs, and DAQs are all enumerated the same `name -> config` way, so the test framework and `main.py` never need code changes to pick up a new device.

**Production is the Numato Relay Matrix** (`NUMATO_RELAY_MATRIX_CONFIG` / `NumatoRelayMatrix` -- `EthernetRelay` is kept as a backward-compat alias for the class, and `RELAY_ETH_CONFIG`/`RELAY_ETH_CONFIGS` as aliases for the config dicts). Serial (`RELAY_CONFIG` / `SerialRelay`, COM13) is diagnostic-only.

```
config/devices.py
  NUMATO_RELAY_MATRIX_CONFIG    (type="ethernet")   <- PRODUCTION
  RELAY_CONFIG        (type="serial")     <- diagnostic only, COM13
       |
       v
hardware/relay_factory.py
  RelayFactory.create(cfg)
       |
       +-- type="ethernet" -> hardware/relay_eth.py::NumatoRelayMatrix   (production)
       +-- type="serial"   -> hardware/relay_serial.py::SerialRelay  (diagnostic)
       |
       v
hardware/relay.py::RelayBase
  connect() / disconnect()
  open(ch) / close(ch)
  open_all() / close_all()
  query(ch) -> bool
```

**Ethernet relay protocol (Numato Lab 32-Channel Ethernet Relay Module):**

```
TCP:23 -> login prompt -> username\r\n -> Password prompt -> password\r\n
       -> "successfully" -> ">"
       -> "relay on N\r\n"           (energize relay N, wait for ">")
       -> "relay off N\r\n"          (de-energize relay N, wait for ">")
       -> "relay read N\r\n"         -> "on\r\n>" or "off\r\n>"
       -> "relay writeall 00000000\r\n" -> force every relay off
       -> "relay readall\r\n"        -> hex bitmask of every relay's state
```

Channel addressing: 1->0, 2->1, ..., 10->9, 11->A, ..., 32->V (Numato 0-based, A-V for 10+).

No custom protocol is invented -- the driver is built directly on Numato's own command set (module docs / readall-writeall reference linked in `hardware/relay_eth.py`'s module docstring). `RELAY_COUNT` in `config/settings.py` (default 32) is the single source of truth for relay count and flows into `NUMATO_RELAY_MATRIX_CONFIG["channel_count"]`; it is never hardcoded in the driver or in any relay test.

**Two API layers, both in `hardware/relay_eth.py::NumatoRelayMatrix`:**

- **Native primitives** (Numato's own 0-based numbering): `write(relay_number, state)` / `read_relay(relay_number)` / `write_all(mask)` / `read_all() -> mask` / `verify_single(relay_number, expected_state)` / `verify_all(expected_mask)` / `reset()`. `verify_single` uses `relay read N` (individual verification); `verify_all` uses `relay readall` (bulk verification -- detects unexpected states and multiple relays active in one round trip).
- **Public `RelayBase` API** (1-based, matches `BATTERY_CHANNELS`): `connect()/disconnect()`, `open(ch)/close(ch)`, `query(ch)/read(ch)`, `open_all()/close_all()`. `open()`/`close()` are the *only* methods that ever change relay state and are built entirely on the native primitives above.

**Telnet layer:** every command waits for `">"` and is checked for an `"invalid"` firmware rejection (command acknowledgement validation); every native read/write is wrapped in one automatic reconnect-and-retry on connection loss or timeout (bounded to a single attempt -- safe because every Numato command is idempotent and every safety-critical write is independently re-verified by hardware readback regardless); `connect()` issues one `relay readall` immediately after login as a connection-verification handshake.

### 6a. Mandatory relay safety sequence

`close(N)`/`open(N)` never activate/deactivate the requested relay directly. Both route through the same write -> read-back -> verify -> continue sequence (never write -> assume success):

```
1. Turn OFF all relays            write_all(0)          -> "relay writeall 00000000"
2. Read back, verify ALL OFF      verify_all(0)          -> "relay readall"
      -> mismatch: raise RelayStateVerificationError, STOP
3. Turn ON the requested relay    write(n, True)         -> "relay on N"      [close() only]
4. Individual verification        verify_single(n, ON)   -> "relay read N"
      -> mismatch: raise RelayStateVerificationError, STOP
5. Bulk verification              verify_all(1<<n)       -> "relay readall"
      -> mismatch (any OTHER relay active): raise RelayStateVerificationError, STOP
6. Continue only if both verifications succeeded
```

`open(N)`'s target state is all-off, so step 2 is also its final verification. `close()` deliberately verifies twice -- individually (per the spec's individual-verification requirement) and in bulk (the only way to catch an unrelated relay unexpectedly energized).

Every step logs: requested relay, command sent, raw readback, decoded mask, decoded active channels, and PASS/FAIL (`hardware/relay_eth.py::_parse_readall_response()` / `_force_all_off_and_verify()`).

**Failure policy -- these are safety faults, never warnings:** `RelayStateVerificationError`, Telnet/TCP timeout, communication failure, readback mismatch, multiple relays active, unexpected relay state, invalid state transition. Each raises and propagates: `BatteryTestSequence.run()` and `TestExecutor.run()` both catch `RelayError` (which `RelayStateVerificationError` subclasses), trigger `SafetyMonitor.emergency_stop()`, and abort the run -- no continuation past a verification failure anywhere in the stack.

`NumatoRelayMatrix.close_all()` raises `RelayError` rather than energizing every channel: under this interlock, only one relay may ever be active at a time.

**Developer note:** do not call `_send_raw`/`_send_and_capture`/raw `relay on|off|writeall` commands from outside `hardware/relay_eth.py`. Any future relay driver must preserve the same write -> read-back -> verify guarantee before being used in production.

### 6b. Relay validation tests

Two functions validate the two layers above, both `RELAY_COUNT`-driven (never hardcoded) and both stopping immediately on the first failure. Both are Functional Validation (Section 8.2b) -- reached from "Test Numato Relay Matrix (Ethernet)" -> Functional Validation in the menu, not separate top-level menu items:

```
"RelayEthernetTest" (native layer, 0-based):
  for relay_index in range(RELAY_COUNT):
      write_all(0) / verify all OFF / write(relay_index, True) / verify relay_index ON, rest OFF
      write_all(0) / verify all OFF

"Relay Safety Self-Test" (public 1-based layer):
  for relay N = 1 .. num_channels:
      OFF ALL / VERIFY OFF / ON N / VERIFY N only
  then: OFF ALL / VERIFY OFF
```

Both report Relay Number / Expected State / Actual State / Cause on the first failure and stop -- neither continues past it. Both temporarily re-enable `hardware/relay_eth.py`'s per-command logging so RAW/MASK/ACTIVE lines are visible, e.g. `RAW: 00000001  MASK: 0x00000001  ACTIVE: [1]`.

**Confirmed against the physical Numato unit**: a live run of the matrix scan (all 32 channels, ON -> READ -> OFF) and Hardware Discovery both passed end-to-end, including login/authentication, the `relay readall` hex-bitmask parsing, and per-channel verification -- see Section 6c below for the authentication root cause this uncovered and fixed.

### 6c. Authentication debugging (root cause confirmed and fixed)

**Symptom:** the framework reported "Authentication failed" connecting to the Numato Relay Matrix, while a manual Telnet session to the same IP/port/credentials succeeded. Ping, the web UI, and manual Telnet login were all independently confirmed working, narrowing the problem to `hardware/relay_eth.py::NumatoRelayMatrix._login()` itself.

**Root cause (confirmed by a live run against the physical unit):** the firmware sends a Telnet IAC option-negotiation request ("IAC DO 45", RFC 854) mid-handshake. A real Telnet client (used for the successful manual login) always auto-answers this kind of request; the previous implementation had zero IAC handling and never replied. This is exactly the "manual Telnet works, raw socket doesn't" symptom class.

**Fix:** `_handle_iac()` scans every inbound chunk for IAC sequences, strips them from the text stream (so prompt matching only ever sees visible banner/prompt bytes), and answers with a blanket decline (IAC DONT/WONT) -- the same safe default a plain terminal-mode client negotiates to. Confirmed in the live transcript: the server proceeded normally immediately after receiving the decline.

**Secondary finding, same investigation:** the real login prompt is "User Name: ", not "login:" -- the previous exact-match implementation (copied from Numato's own reference script) only happened to still work because the word "login" incidentally appears in the banner's instructional sentence ("Enter your user name and password to login"), which is fragile. `_login()` now matches case-insensitively against a set of known-plausible prompt words (`_read_until_any()`: "login"/"username"/"user name" for the login prompt, "password" for the password prompt) and treats the ">" command prompt as the authoritative success signal, both confirmed correct in the live transcript.

**Diagnostics added, always available:** every login step is logged at DEBUG level -- raw RX chunks, detected prompts, TX sent (username and password, in cleartext, since these are lab default credentials -- see the caveat in `_login()`'s docstring if credentials are ever changed to something sensitive), IAC negotiation replies, final response, and PASS/FAIL classification. `test.py`'s `_numato_relay_debug_logging()` context manager re-enables this output (test.py silences all logging by default) and wraps every relay-touching test menu item, so this transcript is always available, not just during dedicated relay tests. `_classify_relay_error()` was also fixed to never collapse a failure down to a bare "Authentication failed" -- the full underlying diagnostic is always appended.

### 6d. Emergency Shutdown Strategy

**Design principle:** an unknown relay state is an unsafe state. When in doubt, force all relays OFF and verify. FAIL SAFE, never fail-and-leave-energized.

This is enforced in layers, from the moment the application starts to the moment it exits:

**1. Startup safe-state enforcement.** `HardwareManager.connect_all()` calls `relay.open_all()` (force OFF + verify) immediately after the relay connects, before `connect_all()` returns -- the framework never starts operating from an unknown relay state. If this fails, startup aborts via the same rollback path as any other `connect()` failure (`HardwareInitError`, already-connected devices are disconnected). Guarantee: **program starts with all relays OFF, or does not start.**

**2. Runtime failure behavior (driver level, `hardware/relay_eth.py::NumatoRelayMatrix`).** Every relay state change already goes through the mandatory all-off -> verify -> activate -> verify sequence (Section 6a). On top of that, any of the following triggers an immediate, best-effort `_emergency_all_off()` (force every relay off, verify, log the outcome) BEFORE the exception propagates -- see the module docstring's "Emergency Shutdown Strategy" section for the full list and `_emergency_all_off()`'s implementation:
   - `RelayStateVerificationError` (a commanded relay didn't reach the expected state, or the bank doesn't match expectations)
   - `RelayError` / communication failure that survives the one permitted automatic reconnect-and-retry
   - Telnet timeout, readback failure, parser failure, unexpected firmware response (all surface as `RelayError`/`NIPXITimeoutError`, covered by the same paths above)

   If the emergency shutdown itself also fails (typically: no working connection at all, so there is no way to force anything from software), that is logged as **CRITICAL** with explicit "hardware may still be energized -- physically disconnect power" wording -- never silently swallowed. Either way, the ORIGINAL exception is still what propagates; the emergency outcome is appended to its message.

**3. Emergency stop (test-workflow level).** `test_control/safety_monitor.py::SafetyMonitor.emergency_stop()` -- called by `BatteryTestSequence.run()` on any `SafetyViolationError` or `RelayError` (which `RelayStateVerificationError` subclasses) -- disables the SMU output then calls `relay.open_all()`. By the time this can still raise, the driver has already made its own internal emergency attempt (layer 2), so a failure here is logged as **CRITICAL**, not a warning.

**4. Application exit protection.** `HardwareManager.disconnect_all()` is the primary shutdown path: disable SMU output -> `relay.open_all()` (force OFF + verify; failure logged CRITICAL, same reasoning as layer 3) -> disconnect every device. It is called from:
   - `main.py`'s `finally:` block (runs on normal completion, `KeyboardInterrupt`, and any other exception -- Python's `finally` always executes), itself wrapped so a shutdown failure is logged critically instead of silently masking whatever was propagating;
   - `test.py`'s `run_main_test()` `finally:` block, same reasoning;
   - `HardwareManager.__exit__` for any caller using it as a context manager.

   A second, independent safety net is registered via `atexit.register()` in `HardwareManager.__init__()` (`_atexit_relay_shutdown()`): it no-ops if the relay was already safely disconnected (the normal case), but catches process-exit paths that bypass the `try/finally` above (an exception during interpreter shutdown, `os._exit()` called elsewhere, etc). **Known limitation:** nothing in userspace can catch `SIGKILL` / a hard process kill -- this is a fundamental OS-level limitation, not a gap in this implementation.

**5. Relay cleanup manager -- where this is centralized.** `HardwareManager` is the single place responsible for making hardware safe on both ends of the application lifecycle (`connect_all()` for startup, `disconnect_all()` + the `atexit` hook for shutdown) -- not scattered across `main.py`, `test.py`, and the battery-test workflow independently. Those callers all route through the same `HardwareManager`/`NumatoRelayMatrix` methods; `SafetyMonitor.emergency_stop()` is the one exception, and it also just calls `relay.open_all()`, the same underlying operation.

**Guarantees confirmed by this design:**

| Guarantee | Enforced by |
|-----------|-------------|
| Program starts with all relays OFF | `HardwareManager.connect_all()` (layer 1) |
| Relay changes always go through safety verification | `NumatoRelayMatrix.open()`/`close()` (Section 6a) |
| Any relay failure forces all relays OFF | `NumatoRelayMatrix._emergency_all_off()` (layer 2) |
| Any safety violation forces all relays OFF | `SafetyMonitor.emergency_stop()` (layer 3) |
| Any unhandled exception attempts to force all relays OFF | `main.py`/`test.py` `finally:` blocks -> `HardwareManager.disconnect_all()` (layer 4) |
| Application exit attempts to force all relays OFF | `disconnect_all()` (primary) + `atexit` hook (backstop) (layer 4) |
| Never *intentionally* leaves relays energized after termination | All of the above; hard process kill (`SIGKILL`) is the one case no userspace code can intercept |

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

---

## 8. Hardware Abstraction Pipeline & Hardware Discovery

```
config/devices.py
     |
     v
Factory                    (RelayFactory for relay -- 2 implementations to dispatch;
     |                       direct construction SMU(cfg)/DAQ(cfg)/DMM(cfg) -- 1 each, no dispatch needed)
     v
HardwareManager             connect_all() / disconnect_all() / health_check()
     |
     v
Device Drivers               hardware/smu.py, daq.py, dmm.py, relay_eth.py, relay_serial.py
     |
     v
Hardware Discovery Test       test_hardware_discovery() -- connectivity + identification only
     |
     v
Functional Hardware Tests     test_smu / test_dmm / test_daq / test_relay_* -- deeper per-device checks
     |
     v
Battery Test Workflows        BatteryTestSequence / TestExecutor -- charge/discharge cycling
```

### 8.1 Why relay has a Factory and SMU/DAQ/DMM do not

Relay has two real implementations (`SerialRelay`, `NumatoRelayMatrix`) selected by `cfg["type"]` at runtime -- `RelayFactory.create(cfg)` genuinely dispatches between them. SMU, DAQ, and DMM each have exactly one driver class today, so `SMU(cfg)`/`DAQ(cfg)`/`DMM(cfg)` direct construction from a `config/devices.py` dict already completes the "config -> Factory -> driver" step with no dispatch to perform. This is intentional, not a missing abstraction -- adding a second SMU/DAQ/DMM implementation is the point at which a factory becomes justified for that type too (follow the `RelayFactory` pattern then).

### 8.2 Hardware Discovery (`test_hardware_discovery()` in test.py)

A hardware **presence** test -- not a measurement test, not a battery-workflow test, not an instrument-accuracy test. For every device configured it validates: the device exists in config, was discovered, its driver loaded, its communication channel opened, the instrument responds, identification succeeds, and (new) the identity the instrument itself reports is compared against the configured model. Config-driven only, and specifically **grouped by category from `config/devices.py::PXI_SLOTS`** (`smu`/`dmm`/`daq`/`temperature`), plus the non-PXI-slot Numato/serial relay dicts and `GPIB_INSTRUMENTS` -- no resource string, IP, COM port, or relay count is hardcoded anywhere in it. See Section 14 for what `PXI_SLOTS` actually contains.

Printed output is grouped exactly by category, e.g.:

```
DMM Devices
-----------
Slot 3
PXI-4065
MAIN_DMM

SMU Devices
-----------
Slot 5
PXIe-4141
PRIMARY_SMU
...
```

followed by each device's PASS/WARNING/FAIL result (via the standard `TestResult.print_detail()` pass `run_section()` already does for every test). A device whose identity string doesn't match its configured model is reported **WARNING**, not silently accepted, via `_compare_identity()` -- a genuinely wrong/swapped card is caught, without flagging harmless formatting differences (e.g. `"PXIe-4141"` vs `"NI PXIe-4141"`) as false positives.

Categories with no driver class in this codebase are reported **N/A**, never faked as a real check:
- `switch` (`CHASSIS_RELAY_MATRIX`, PXIe-2569, slot 11) -- no `niswitch`-based driver exists; see Section 14.4.
- `GPIB_INSTRUMENTS` -- no confirmed instrument model at that address yet.

It uses the SAME production driver classes as `HardwareManager` and the deeper `test_smu()`/`test_dmm()`/`test_daq()`/`test_temperature_module()` tests -- every `_identify_*()` helper is exactly `driver = DriverClass(cfg); driver.connect(); driver.identify(); driver.disconnect()`, never a direct `nidcpower`/`nidmm`/`nidaqmx`/`pyserial` call. `_identify_temperature()` deliberately reuses `hardware.daq.DAQ` rather than inventing a new driver class -- NI-4353 is an NI-DAQmx-family device, so the same generic device-enumeration + self-test call works, with no channel acquisition attempted. `test_relay_ethernet_test()` and Hardware Discovery's Ethernet-relay check both go through `RelayFactory.create(NUMATO_RELAY_MATRIX_CONFIG)`, i.e. the same `NumatoRelayMatrix` instance type. A failure on one device never stops discovery of the rest (each `_identify_*()` catches its own exceptions and returns a result), and a full PASS/WARNING/FAIL summary is always produced (`print_summary()`).

### 8.2a Device Selection Workflow -- Identity vs Functional Validation

**The current bring-up goal is hardware identification and readiness validation, not functional testing** -- confidence that the correct devices are detected, reachable, and ready, before any lab visit. `test_smu()`, `test_dmm()`, `test_daq()`, `test_temperature_module()`, `test_relay_numato()`, and `test_pxi_relay_matrix()` all follow the same pattern via one shared helper, `test.py::_run_hardware_category(label, devices, identify_fn, functional_fn=None)`:

```
Step 1: list every configured device of THIS category (from PXI_SLOTS, or the
        Numato Relay Matrix dict):

    SMU

    [1] PRIMARY_SMU
    [2] HIGH_POWER_SMU
    [3] AUX_SMU_1
    [4] AUX_SMU_2
    0. Back

    Select device:

Step 2: second-level menu, for the ONE device just selected:

    PRIMARY_SMU

    [1] Identity Validation
    [2] Functional Validation (future)
    [0] Back
```

**Identity Validation** always calls `identify_fn(name, cfg)` -- the SAME function Hardware Discovery itself uses, so the two paths can never disagree. It opens a driver session, verifies the configured resource exists, verifies communication, reads device identity/model/serial where supported, verifies the detected model matches `config/devices.py`, and confirms the device is ready for the next validation stage -- and it never enables an output, sources voltage/current, closes a relay, or performs any other state-changing action (Section 10, Instrument Verification Philosophy, still applies in full).

**Functional Validation** calls `functional_fn(name, cfg)` if the category has one implemented (`_functional_dmm()` -- a real DC voltage measurement; `_functional_daq()` -- a real deep channel read; `_functional_relay_numato()` -- a submenu of the existing relay-energizing tests, Section 8.2b); otherwise the menu reports "Functional Validation not yet implemented for this hardware category" rather than faking a PASS (SMU sourcing and the Temperature Module's TC/RTD read have no implementation yet; the PXI Relay Matrix has no driver at all).

**Selecting one device never touches any other device, of that category or any other.** Selecting `PRIMARY_SMU` runs Identity/Functional Validation against `PRIMARY_SMU` only -- `HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2` are untouched.

`_run_hardware_category()` is the one shared abstraction this workflow introduces, reusing existing pieces (`identify_fn`/`functional_fn`, `TestResult`/`_ok`/`_warn`/`_fail` from the existing result model) -- not a second inventory framework or a parallel config source. It replaces the earlier `_discover_and_select()` picker, which combined a live reachability scan with running the full (identity + functional) test body in one step -- that mixing of identity and functional concerns is exactly what this workflow now separates.

### 8.2b Functional Validation (existing tests, relocated not deleted)

Functional Validation is intentionally a separate, later phase from Identity Validation (see Section 8.2a) -- but existing, already-implemented functional tests are kept, not deleted, and reached from their category's Functional Validation option instead of being separate top-level menu items:

- **SMU -> Functional Validation** (`_functional_smu()`): laboratory-only, operator physically present with a handheld DMM on the SMU output. Verifies the SMU can source DC voltage correctly via `hardware/smu.py::SMU.source_dc_voltage_point()` -- COMMAND (configure DC voltage output + enable) -> READBACK (`query_in_compliance()` + the SMU's own voltage measurement) -> VERIFY (not in compliance) -> always disable output again in a `finally` block, regardless of outcome. Sequence: safe state -> 0 V (baseline) -> charge validation voltage -> 0 V (return to baseline) -> output OFF. This sequence is deliberately **positive-voltage only** -- it mirrors how the SMU is actually used in NIPXI (charging: source voltage + source current; discharging: source voltage + **sink** current, never a negative source voltage -- see Section 12.6), not a generic bipolar power-supply validation. This is also deliberately NOT the battery charge/discharge path itself -- no relay, no battery channel, `set_charge_mode()`/`set_discharge_mode()` remain untouched placeholders. See Section 12.6 for the safety-sequence detail and where the validation voltage/current/range values come from (`config/settings.py`'s existing Charge/Battery-limit constants).
- **DMM -> Functional Validation** (`_functional_dmm()`): laboratory-only, operator connects a known external DC source. A real DC voltage measurement (`DMM.measure_dc_voltage()`), verified finite and within the configured range, with the "Measured Voltage" displayed to the operator. Deliberately minimal first implementation: no current measurement, no calibration validation, no accuracy certification, no automated metrology limits -- answers only "can the DMM successfully perform a voltage measurement?".
- **DAQ -> Functional Validation** (`_functional_daq()`): a real deep channel read via `hardware/daq.py::DAQ.read_channel()`, verified finite and within the configured ADC range. `test.py` itself only calls `daq.read_channel(test_ch)` -- no `nidaqmx` import or `Task()` construction remains in `test.py`; see Section 8 (Hardware Abstraction Pipeline) for the architecture this now completes.
- **Numato Relay Matrix -> Functional Validation** (`_functional_relay_numato()`): a submenu of `test_relay_numato_matrix()` (relay-1 quick check), `test_relay_matrix_scan()` (full channel scan), `test_relay_ethernet_test()` (native-primitive test), and `test_relay_safety_selftest()` (mandatory-sequence self-test) -- each of these four functions still accepts no arguments for standalone/scripted use, and now also accepts an optional preselected `(name, cfg)` so the submenu can route to them without prompting for the device twice.

`test_relay_serial()` (bench-only serial relay) and the GPIB/MiniSQL stubs (`test_electronic_load()`, `test_minisql()`) are no longer in the operator-facing `MENU` list -- out of scope for the current NIPXI bring-up stage (Section 4/17 of README.md) -- but their code is unchanged and still importable directly from `test.py`.

### 8.3 Startup device validation (`utils/device_validator.py`)

Runs before any hardware communication -- `main.py` calls `validate_devices_or_raise(dev_cfg)` right after `validate_settings()`, and `test.py`'s `preflight_check()` runs the equivalent check before showing the menu. Construction-only (never `connect()`); collects every problem before reporting rather than stopping at the first one. See Section 3 ("Startup") and README.md Section 17.2 for the full list of checks.

### 8.4 Adding a new instrument

A new instance of an existing type (second SMU/DAQ/DMM/temperature module) in an existing PXI slot: add an entry to `config/devices.py`'s `PXI_SLOTS` (with a unique nickname, `category`, `driver_family`, `role`, `enabled` flag) -- `SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/`DMM_CONFIGS` are derived from it automatically (see Section 14.2), so Hardware Discovery, the device-selection workflow (Section 8.2a), device validation, and preflight all pick it up with no further code change.

A genuinely new device type: `hardware/<type>.py` (a `HardwareBase` subclass, constructed from a config dict, with `connect()`/`disconnect()`/`identify()`), a `<TYPE>_CONFIG`/`<TYPE>_CONFIGS` pair in `config/devices.py`, a registry entry in `utils/device_validator.py::_build_registry()`, and either a `_PXI_CATEGORY_TARGETS` entry (PXI-slot devices) or a `_NON_PXI_TARGETS` entry (Ethernet/serial-style devices) in `test.py`, plus a `test_<type>()` menu function built on `_run_hardware_category()` (Section 8.2a) to expose it as its own hardware category. See README.md Section 17.3 for the full walkthrough.

### 8.5 Test execution order

```
1. Startup Validation        validate_settings() + validate_devices_or_raise()
2. Hardware Discovery         connectivity + identification, every configured device
3. RelayEthernetTest           native relay primitives, validated before any relay use elsewhere
4. Functional Hardware Tests   test_smu / test_dmm / test_daq / test_relay_* / Safety Self-Test
5. Battery Test Workflows       BatteryTestSequence / TestExecutor (Run Main Test / main.py)
```

---

## 9. System Modes

**Problem this solves:** `HardwareManager` used to try to initialize every production
device unconditionally, which made laptop software development (no PXI chassis
attached) fail on things like `DAQ 'PXI1Slot2' not found` even when the work at hand
had nothing to do with the DAQ. `config/system_mode.py` formalizes three operating
modes so hardware strictness (and, going forward, database location and recovery) is
driven by ONE setting instead of scattered assumptions.

### 9.1 The three modes

| Mode | Purpose | Hardware startup behavior |
|------|---------|---------------------------|
| `DEVELOPMENT` | Daily software work, laptop development, UI/architecture/database work, simulation | Hardware optional. A missing device is logged as a **warning** and startup continues. |
| `VALIDATION` | Hardware integration, driver validation, system testing | Real hardware preferred. A missing device is logged as an **error** ("test failure"), but the framework still launches. |
| `PRODUCTION` | Real battery cycling | Strict. Any missing/unreachable device **aborts startup** (rollback + `HardwareInitError`), exactly as `HardwareManager.connect_all()` already did before this change. |

Set via `config/settings.py`: `SYSTEM_MODE = "DEVELOPMENT"` (the default). Validated at
startup by `utils/validators.py::validate_settings()` (an unrecognized value is a
`ValidationError`, same as any other bad Settings value). Resolved to its `ModePolicy`
via `config.system_mode.get_mode_policy(settings)`.

### 9.2 What is -- and is NOT -- relaxed by mode

`HardwareManager.connect_all()` dispatches to `_connect_all_strict()` (PRODUCTION,
unchanged from before this feature) or `_connect_all_lenient()`
(DEVELOPMENT/VALIDATION, new). In the lenient path, each device connects
independently; a connection failure is recorded in `self.hardware_status` and logged
at the mode's `hardware_failure_log_level`, but does not stop startup or roll back
devices that already connected.

**This only ever applies to a device that is missing/unreachable.** It does NOT
relax the Emergency Shutdown Strategy (Section 6d): if the relay driver *does*
connect but its startup force-off/verify cannot be confirmed, that is always fatal --
in every mode, including DEVELOPMENT -- because at that point real hardware is
attached and in an unknown state, which is exactly what "unknown relay state = unsafe
state" exists to prevent. Missing hardware is tolerated; unverifiable hardware never is.

### 9.3 Database location per mode

Also driven by `SYSTEM_MODE` (see `config/settings.py` and `docs/DATABASE_ROADMAP.md`
Section 1 for the full reasoning, including why the output root is `data_output/` and
not `data/`):

| Mode | Database |
|------|----------|
| DEVELOPMENT | `data_output/development/nipxi_dev.db` |
| VALIDATION | `data_output/validation/nipxi_validation.db` |
| PRODUCTION | `data_output/production/nipxi.db` |

### 9.4 Recovery hook (not implemented)

`config.system_mode.is_recovery_enabled(settings)` resolves whether cycle/state
recovery should run -- DEVELOPMENT off, VALIDATION off-by-default-but-overridable,
PRODUCTION on, overridable via `Settings.RECOVERY_ENABLED_OVERRIDE`. No recovery engine
exists yet; this is only the configuration surface it will read once built. See
`docs/DATABASE_ROADMAP.md` Section 4.

### 9.5 Simulation extension points (not wired in)

`hardware/simulated.py` defines `SimulatedSMU`/`SimulatedDAQ`/`SimulatedRelay`/
`SimulatedBattery` as foundations for eventually letting `DEVELOPMENT` mode
(`ModePolicy.allow_simulated_devices`) run without any physical hardware at all, not
just tolerate its absence. None of them are constructed by `HardwareManager` or
`RelayFactory` today -- see the module's docstring for exactly how that wiring is
expected to work once it's built.

---

## 10. Instrument Verification Philosophy

**Never COMMAND and assume success. Always COMMAND -> READ BACK -> VERIFY -> PASS.**
A test that merely calls an API and reports PASS because nothing raised gives false
confidence -- an instrument can answer "what model are you" (a bare identity query)
even while its actual measurement or sourcing hardware is faulty. This is the same
principle the Numato Relay Matrix's mandatory safety sequence already enforces
(Section 6a: never activate a relay without forcing and verifying a baseline first,
never trust a command send on its own) applied to every other instrument driver.

| Device | Command | Readback | Verify | Where |
|--------|---------|----------|--------|-------|
| Relay | `relay on/off <n>` | `relay read <n>` + `relay readall` | Commanded state matches, all others unaffected | `hardware/relay_eth.py` (Section 6a) -- unchanged, this is what the others now mirror |
| SMU | Instrument built-in self-test | Self-test result code + message | Code indicates success, else raise `SMUError` | `hardware/smu.py::SMU.identify()` |
| DMM | Instrument built-in self-test | Self-test result code + message | Code indicates success, else raise `DMMError` | `hardware/dmm.py::DMM.identify()` |
| DMM | Configure + trigger a DC volts measurement | The measured value | Finite (not NaN/inf) and within the configured range (+5% overrange margin) | `hardware/dmm.py::DMM.measure_dc_voltage()` |
| DAQ | Instrument built-in self-test | `self_test_device()` (nidaqmx raises on failure) | No exception raised | `hardware/daq.py::DAQ.identify()` |
| DAQ | Configure + read one analog channel | The read value | Finite and within the configured `voltage_range_v` (+5% overrange margin) | `hardware/daq.py::DAQ.read_channel()` |

**What is deliberately NOT verified yet, and why:** SMU sourcing (`set_charge_mode`,
`output_enable`, `measure`) is still a placeholder (`docs/TODO.md`). Testing "source a
current and measure it back" around a stub that returns a fixed value would be a FAKE
PASS -- exactly what this philosophy exists to prevent. Self-test is the strongest
verification available for the SMU until real sourcing is implemented; it is real
(a genuine hardware health check, not a bare string query) but it does not exercise
sourcing/measurement, and the driver's docstrings say so explicitly rather than
implying more coverage than actually exists.

**Consequence for Hardware Discovery (Section 8.2):** because `identify()` itself now
performs a real self-test for SMU/DMM (and already did for DAQ), Hardware Discovery's
"the instrument responds correctly" / "instrument identification succeeds" criteria
are satisfied by a real verification for every device type, not just the relay --
with no changes needed in `test_hardware_discovery()` itself, since it already just
calls `driver.identify()` for every device.

## 11. Operational Limit Resolution (Future Architecture -- LimitResolver)

**`BATTERY_CONFIGS` (`config/devices.py`) is not the sole authority for operating
limits.** It describes battery *capabilities* and *recommended* operating ranges --
what a given battery model can tolerate. It is deliberately not treated as the final
word on what a test is allowed to do, because a battery's rated capability can exceed
what the rest of the system can safely provide, and vice versa:

- A battery may support a charge current the PMU (SMU) cannot actually source.
- A PMU may be capable of sourcing more current than the battery, DAQ, or the
  station's safety configuration should ever allow.

**Rule: the effective operational limit is always the most conservative value across
every applicable limit source.** For any given quantity (voltage, current,
temperature, power), the system must compute the intersection of:

- Battery Limits (`BATTERY_CONFIGS`) -- what the cell is rated for
- PMU Limits -- what the SMU hardware can actually source/sink
- DAQ Limits -- what the acquisition hardware can accurately measure
- Safety Limits (`config/settings.py` `BAT_*` constants today) -- station-level
  safety policy, which may be tighter than any single device's rating
- User/Test Limits -- whatever a specific test explicitly requests

...and use the smallest (safest) value. Worked example, exactly as specified:

```
Battery: Max charge current             = 3.0 A
PMU:     Max current capability         = 2.0 A
Safety configuration: Max allowed current = 1.5 A
--------------------------------------------------
Effective charge current limit          = 1.5 A   (never 3.0 A)
```

The same rule applies uniformly to Voltage, Current, Temperature, and Power --
whichever source is most restrictive for a given quantity wins, never the battery's
nameplate rating and never the PMU's raw hardware capability in isolation.

**Planned: `LimitResolver`.** A future component (name/shape not finalized) whose sole
job is:

```
effective_limits = LimitResolver.resolve(
    battery_limits, pmu_limits, daq_limits, safety_limits, test_limits
)
```

producing the single set of Effective Operational Limits that
`charge_cycle.py`/`discharge_cycle.py` (and the future battery cycling engine) would
actually enforce, replacing today's direct reads of `config/settings.py`'s global
`BAT_*` constants in `safety_monitor.py`. **This section is documentation only --
no `LimitResolver` class or module exists yet.** `BATTERY_CONFIGS` and `battery_type`
(Section on device config in `docs/CONFIGURATION.md`) are the first input this
resolver will eventually consume; PMU/DAQ hardware-capability limits and a
first-class Safety Limits config are not yet modeled as data today and would need to
be defined before `LimitResolver` itself can be implemented.

**First real consumer of existing configuration for a bench check:** SMU Functional
Validation (`test.py::_functional_smu()`, Section 12.6) reuses
`Settings.CHARGE_VOLTAGE_V` as its bench validation voltage, `Settings.
CHARGE_CURRENT_A` as the current-limit/compliance value, and `Settings.
BAT_VOLTAGE_MAX` as the SMU's source range -- deliberately reusing the same
charge-parameter and battery-limit constants this section's `BATTERY_CONFIGS`
philosophy already governs, rather than introducing a second, independent set of
test voltages. This is a bench sanity check (no battery/relay involved), not an
"operational limit" in the `LimitResolver` sense above, but it draws from the same
single source of truth. It deliberately sources only the charge-phase (positive)
voltage -- see Section 12.6 for why discharge's current-sink behavior is out of
scope for this check.

## 12. PMU Safety Philosophy

**"PMU" in this project is the `SMU` class (`hardware/smu.py`) -- there is no separate
PMU/PSU hardware or config.** Everything in this section governs `hardware/smu.py`
and its callers.

**Core principle -- unknown PMU state = unsafe state**, exactly mirroring the Relay's
Emergency Shutdown Strategy (Section 6d): when in doubt, disable PMU output and verify
it. This rule takes precedence over continuing a test.

### 12.1 PMU Failure Handling (non-negotiable)

If **any** error occurs during battery charging or discharging, the PMU must
immediately transition to a safe state:

```
PMU Output OFF -> Verify Output OFF -> Raise Exception -> Abort Operation
```

Triggering conditions include (non-exhaustive): communication failure, timeout,
verification failure, safety violation, unexpected measurement, invalid state
transition, recovery conflict, charge/discharge control error, and any unhandled
exception. The framework must never leave a PMU actively sourcing or sinking current
after an error condition of any kind -- including exceptions that were not explicitly
anticipated by the calling code.

Implementation: `hardware/smu.py::SMU.emergency_output_off(reason) -> bool` is the
single, public, non-recursive PMU fail-safe reflex (COMMAND `output_disable()` ->
READBACK+VERIFY `verify_output_disabled()` -> log CRITICAL and return `False` on any
failure, else `True`; never raises itself). Callers:

- `test_control/charge_cycle.py` / `discharge_cycle.py`: the sampling loop is wrapped
  in `try/finally`, so `emergency_output_off()` runs exactly once on every exit path
  -- normal completion, timeout, a raised `SafetyViolationError`, or any other
  unhandled exception propagating out of the loop (e.g. a DAQ read failure). Before
  this change, only the anticipated exit paths called `output_disable()`, and it was
  never verified -- an unhandled exception mid-loop left the PMU state untouched.
- `test_control/safety_monitor.py::emergency_stop()`: calls
  `smu.emergency_output_off(reason)` instead of a bare `output_disable()` call, and
  escalates a failed verification to CRITICAL logging (PMU may still be actively
  sourcing/sinking current).

### 12.2 PMU Startup Safe State

At system startup, the PMU output must be forced OFF and verified **before any
battery operation is allowed** -- the system must never assume a PMU starts in a safe
state. `test_control/hardware_manager.py` calls
`smu.emergency_output_off("startup safety check")` immediately after the SMU
connects, in both `_connect_all_strict()` (PRODUCTION) and `_connect_all_lenient()`
(DEVELOPMENT/VALIDATION). This check is unconditional in every mode: an SMU that
connects but cannot be verified OFF always aborts startup (`HardwareInitError`),
exactly mirroring the relay's startup force-off/verify rule (Section 9) -- only a
genuinely *missing* SMU is tolerated in DEVELOPMENT/VALIDATION, never an unverifiable
one.

### 12.3 PMU Shutdown Safe State

Every shutdown path disables and verifies PMU output:

- Normal application shutdown -- `HardwareManager.disconnect_all()` calls
  `smu.emergency_output_off("normal shutdown")` before disconnecting the session.
- Emergency shutdown / safety violation -- `SafetyMonitor.emergency_stop()`
  (Section 12.1).
- Battery test abort -- any exception path through `charge_cycle.py`/
  `discharge_cycle.py`'s `try/finally` (Section 12.1).
- Process-exit safety net -- `HardwareManager._atexit_smu_shutdown()`, registered via
  `atexit.register()` alongside the existing `_atexit_relay_shutdown()`
  (Section 6d), catching process-exit paths that bypass a `try/finally` around
  `disconnect_all()`.

### 12.4 Design goal

The future battery cycling engine must never be capable of continuing after a PMU
fault, and must never leave a battery connected to an actively sourcing/sinking PMU
after an error. The safest state is always: **PMU Output OFF, verified.**

### 12.5 DAQ -- explicitly out of scope for now

The DAQ is currently used only for measurements and does **not** get equivalent
shutdown/fail-safe behavior in this pass -- DAQ handling is intentionally left
unchanged. Future DAQ safety behavior (if any) should be reviewed once the final DAQ
architecture (multi-channel measurement ownership, calibration, etc.) is established,
not retrofitted onto the current placeholder driver.

### 12.6 SMU Functional Validation sourcing (bench-only, not battery charge/discharge)

**Charging/discharging architecture, and why this validation is positive-voltage
only:**

```
Charging:     source voltage,  source current
Discharging:  source voltage,  SINK current   (SMU acts as a current sink)
```

NIPXI never relies on negative-voltage sourcing to implement discharge -- the SMU
sinks current at a positive voltage, exactly as `hardware/smu.py::
set_discharge_mode()`'s docstring already states ("Configure CC discharge (sink)").
An earlier version of this validation sourced a negative voltage point to
demonstrate bipolar capability; that was corrected because it does not reflect
this project's actual application and is not guaranteed to be representative of
every configured SMU model in `PXI_SLOTS` (only `PRIMARY_SMU`/PXIe-4141 is
documented in this codebase as 4-quadrant -- see `PXI_SLOTS[5]`'s
`validation_notes`; `HIGH_POWER_SMU`/PXIe-4139 and `AUX_SMU_1`/`AUX_SMU_2`/PXI-4130
have no polarity capability documented here). The validation below therefore
exercises only the polarity the real charge path will actually use.

`hardware/smu.py::SMU.source_dc_voltage_point(voltage_v, current_limit_a,
voltage_range_v)` is the first real PMU sourcing capability implemented in this
codebase -- but it is deliberately scoped to laboratory Functional Validation
(`test.py::_functional_smu()`, Section 8.2b), not the battery charge/discharge
path (`set_charge_mode()`/`set_discharge_mode()`/`output_enable()` remain
untouched placeholders, Section 12.1's PMU failure handling and this section's
principles apply identically to both). The method itself is polarity-agnostic
(it sources whatever `voltage_v` it is given) -- the positive-only constraint is
enforced by the caller (`test.py::_functional_smu()`), not by the driver.

Same "unknown PMU state = unsafe state" principle as the rest of this section,
applied to a single bench voltage point:

```
COMMAND    configure DC_VOLTAGE output at voltage_v, current_limit_a
           compliance, voltage_range_v range -> enable output -> commit()
READBACK   query_in_compliance() + the SMU's own voltage measurement
           (session.measure(MeasurementTypes.VOLTAGE))
VERIFY     not in current-limit compliance (a compliance hit means a short
           or unexpected load, not a successful source point) -> else raise
SMUError
ALWAYS     output_disable() runs in a `finally` block -- on PASS, FAIL, or
           an exception of any kind, before the method returns or the
           exception propagates
```

`test.py::_functional_smu()`'s sequence is: safe state (forced off + verified)
-> 0 V (baseline) -> charge validation voltage (`Settings.CHARGE_VOLTAGE_V`) ->
0 V (return to baseline) -> output OFF (forced + verified again). It wraps this
with the same fail-safe reflex used everywhere else in this section:
`emergency_output_off()` is called once before the first sourcing step (start
from a verified safe state, mirroring Section 12.2's startup check) and once
more in its own `finally` block after the last step, on FAIL, or on operator
cancellation (Ctrl+C / blank input at a prompt) -- the operator can never be
left with an energized output. No cancellation checkpoint/token is used here
(unlike the relay scan loops, Section 13) -- this is a short, discrete 3-step
sequence with an explicit per-step operator prompt, not a long-running loop, so
a plain `try/except (KeyboardInterrupt, EOFError)` around each `input()` call,
combined with the `finally` block's `emergency_output_off()`, gives the same
safety guarantee with less machinery.

The **measured** voltage from the SMU's own readback (`session.measure(VOLTAGE)`,
the physical output) is reported to the operator as **informational context
only** -- there is no project-configured measurement tolerance to verify it
against, so no PASS/FAIL decision is made on it. The operator's handheld DMM
is the actual verification instrument for this step, per the laboratory
bring-up workflow (README.md Section 8.1a/8.1b). This is a distinct question
from **configuration** verification (Section 12.6b below), which IS a
PASS/FAIL gate: "did the instrument accept 4.200 V as its setpoint" is
verified programmatically; "did the physical output settle to exactly
4.200 V" is not, and never should be equality-checked (a real battery load
makes the two diverge by design -- see 12.6b).

**Not yet covered:** discharge's current-sink behavior has no Functional
Validation of its own yet -- this is future work (see `docs/TODO.md`), not part
of this bench voltage-sourcing check, and no current-sink capability exists in
`hardware/smu.py` today (`set_discharge_mode()` remains a placeholder).

### 12.6b SMU configuration verification (`_verify_config_readback`)

Added during SMU/PSU hardening after Hardware Bring-Up Milestone 1, applying
the same COMMAND -> READBACK -> VERIFY -> fail-on-mismatch philosophy already
proven in `hardware/relay_eth.py` (`verify_single()`/`verify_all()`) to the
SMU driver. `source_dc_voltage_point()` now reads back `voltage_level`,
`current_limit`, and `output_enabled` from the NI-DCPower session after
`commit()` and compares each to what was just commanded
(`SMU._verify_config_readback()`), raising `SMUStateVerificationError`
(`utils/errors.py`, mirrors `RelayStateVerificationError`) on any mismatch --
execution stops rather than proceeding with an unverified configuration. The
`finally` teardown now also calls `verify_output_disabled()` after
`output_disable()` (previously command-only at that call site), logging
CRITICAL rather than raising if it fails to confirm OFF -- consistent with
`emergency_output_off()`'s existing "never let a safety teardown mask the
original exception" rule.

**Tolerance rationale (`config/settings.py`
`SMU_VOLTAGE_READBACK_TOLERANCE_V`/`SMU_CURRENT_READBACK_TOLERANCE_A`, both
`1e-4`):** `voltage_level`/`current_limit` are NI-DCPower **attribute**
properties -- reading them back returns the driver's stored IVI setpoint
(an IEEE-754 double round-tripped through `commit()`), not a new ADC
measurement. The only legitimate discrepancy sources are floating-point
round-trip and instrument coercion to its nearest programmable step, both
far smaller than any electrical accuracy spec -- so the tolerance is
deliberately tight (an attribute round-trip bound), not a percentage-of-range
"measurement accuracy" figure. A wider tolerance here would risk silently
accepting a real failure (wrong channel, stale attribute, a value the
instrument silently rejected/clamped). `output_enabled` is a bool -- exact
match, no tolerance. This is separate from, and must never be confused
with, the physical-measurement tolerance discussed above (12.6), which
does not exist by design.

**Future contract for real battery charge/discharge sourcing** (once
`set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()`
graduate from placeholders -- see `docs/TODO.md`): they must follow the
exact same pattern established here --

- `output_enable()`/mode-setting methods must read back and verify their
  own commanded attributes via `_verify_config_readback()`, exactly like
  `source_dc_voltage_point()` does today, raising `SMUStateVerificationError`
  on mismatch.
- `measure()` must return real `session.measure(VOLTAGE)`/`session.measure(CURRENT)`
  values (plus `output_enabled`/`query_in_compliance()` state) -- never
  cached Python values, matching `DMM.measure_dc_voltage()`/
  `DAQ.read_channel()`'s existing real-readback pattern.
- **Limit enforcement stays entirely in `test_control/safety_monitor.py::check()`**,
  fed by these real measured values exactly as `charge_cycle.py`/
  `discharge_cycle.py` already do today (currently via `DAQ.read_all_batteries()`,
  itself still a stub) -- `hardware/smu.py` must NOT duplicate any
  `BAT_VOLTAGE_MAX`/`CHARGE_CURRENT_A`/`DISCHARGE_CURRENT_A` limit logic;
  its responsibility ends at configure/verify-configure/measure/safe-state.
- **Never assert measured voltage/current equals the commanded setpoint.**
  `Configured Voltage = 4.200 V` / `Measured Battery Voltage = 3.700 V` mid-CC-charge
  is normal and expected, not a fault -- the only thing that must ever be
  equality/tolerance-checked against a commanded value is the SMU's own
  **configuration** readback (12.6b above), never the resulting physical
  battery measurement, which is validated only against limits (battery and
  SMU), never against the setpoint.

### 12.6a NI-DCPower channel selection (`smu_channel`)

Root-caused during the first real PXIe rack bring-up (Hardware Bring-Up
Milestone 1, `docs/MILESTONES.md`): `AUX_SMU_1`/`AUX_SMU_2` (PXI-4130, a
2-channel card) failed SMU Functional Validation with NI-DCPower error
`-1074118522` ("the requested function only allows a single channel to be
specified"), while Identity Validation on the same device passed. The cause
was `SMU.connect()` opening `nidcpower.Session(resource_name=self.resource)`
with no channel specified -- for a 2-channel card this implicitly opens
**both** channels as one ambiguous session. `identify()` never surfaced the
problem because `self_test()`/`instrument_model` are session-level (not
channel-repeated-capability) calls; the error only appears the moment a
channel-scoped property (`voltage_level`, `output_enabled`, `measure()`,
etc.) is set in `source_dc_voltage_point()`. `PRIMARY_SMU`/`HIGH_POWER_SMU`
(4141/4139, single-channel cards) never hit this, since a bare resource
string resolves to exactly one channel on those cards.

Fix: every `PXI_SLOTS` SMU entry carries two new config-driven fields --
`smu_channel` (the NI-DCPower channel name string this instance operates on,
e.g. `"0"`/`"1"`) and `channels_per_card` (the card's physical NI-DCPower
channel count -- `1` for 4141/4139, `2` for the 4130 units). `SMU.connect()`
now opens `nidcpower.Session(resource_name=self.resource, channels=self._channel,
options=options)`, scoping the session to exactly one channel every time --
never hardcoded in the driver, always read from config. This is what makes
the identical driver code work for both single- and multi-channel cards.
Confirmed on physical hardware: both `AUX_SMU_1` and `AUX_SMU_2` are wired to
channel `"1"`. `config/devices.py::device_display_name()` also surfaces this
in the operator-facing label (e.g. `NI4130-Slot7-Ch1`) precisely because
which channel is active is safety/bring-up relevant, not just cosmetic.

## 13. Safe Cancellation Architecture

Lets an operator stop a running test safely, via Ctrl+C, without relying on an
uncontrolled `KeyboardInterrupt` landing on an arbitrary line. This section
describes the implementation exactly as it exists today -- Emergency Abort (a
separate, faster, operator-typed-`ABORT` mechanism) was designed but explicitly
**not implemented**; only Safe Cancellation exists in code.

### 13.1 Components

| Component | File | Role |
|---|---|---|
| `CancellationToken` | `utils/cancellation.py` | Single-threaded, single-severity flag. `request_cancel(reason)` (idempotent), `.requested`/`.reason`, `.check()` (raises if requested). |
| `check_cancellation(token)` | `utils/cancellation.py` | No-op if `token is None`; otherwise `token.check()`. Every checkpoint call site uses this, not `token.check()` directly, so callers that omit a token need no special-casing. |
| `OperationCancelledError` | `utils/errors.py` | `NIPXIError` subclass raised at a checkpoint. Not a fault -- a deliberate, expected operator action. |
| `StopReason` | `utils/stop_reason.py` | `COMPLETED` / `FAILED` / `SAFETY_VIOLATION` / `TIMEOUT` (defined, not yet wired end-to-end) / `CANCELLED`. |

No threads, no stdin listeners, no keyboard polling exist anywhere in this
implementation -- the design deliberately stayed single-threaded (see the
"No Emergency Abort" scoping decision that produced this feature). A
`signal.signal(signal.SIGINT, ...)` handler, installed once per cancellable
operation in `main.py`/`test.py`, is the only producer of `request_cancel()`
calls today.

### 13.2 Cancellation Flow

```
  Operator presses Ctrl+C
           |
           v
  SIGINT handler (installed by main.py / test.py)
           |  does NOT raise KeyboardInterrupt into running code --
           |  only sets a flag
           v
  token.request_cancel("Ctrl+C")            [idempotent -- first reason wins]
           |
           v
  ... hardware keeps running until the NEXT checkpoint ...
           |
           v
  check_cancellation(token)  at a safe checkpoint
           |
           v
  OperationCancelledError raised
           |
           v
  ChargeCycle/DischargeCycle's own `finally:` (if inside a sampling loop)
           |  smu.emergency_output_off(reason)
           |  -> PMU output OFF, verified (or CRITICAL logged if not)
           v
  propagates to BatteryTestSequence.run()
           |
           v
  except OperationCancelledError:
      safety.safe_cancel_shutdown(smu, relay, reason)
           |  smu.emergency_output_off(reason)   -- idempotent 2nd call if already off
           |  relay.open_all()                    -- relay OPEN ALL, verified by real
           |                                          hardware readback (relay readall)
           v
  raise   (original OperationCancelledError re-raised, unmodified)
           |
           v
  TestExecutor.run() absorbs it:
      result.stop_reason = CANCELLED
      result.aborted     = True
      (does NOT re-raise -- caller always gets a normal TestRunResult back)
           |
           v
  main.py / test.py:
      result.stop_reason == CANCELLED
      -> logged as "cancelled by operator", never as a failure
      -> main.py: sys.exit(3)  (distinct from 0=success, 1=init error, 2=test issues)
           |
           v
  outer  finally: hw.disconnect_all()   -- always runs regardless (confirmed:
                                            SystemExit does not skip `finally`)
```

### 13.3 Safe Checkpoints

Checkpoints are placed **only between atomic hardware operations**, never
inside one (never inside a relay activate/verify sequence, never inside a PMU
verify sequence) -- interrupting mid-sequence would leave hardware state less
certain, not safer.

| Location | Checkpoint placement |
|---|---|
| `ChargeCycle.run()` | Before `set_charge_mode()`/`output_enable()` (skips entirely if already cancelled -- PMU never energized) **and** top of the sampling `while` loop |
| `DischargeCycle.run()` | Same shape |
| `BatteryTestSequence.run()` | First line inside the per-channel `try:` block, before `relay.close(ch)` -- deliberately *inside* the `try` so a checkpoint firing here funnels through the same `except OperationCancelledError` handler as a cancellation detected deeper inside charge/discharge |
| `test.py::_run_relay_matrix_scan()` | Before each channel's ON/READ/OFF triplet |
| `test.py::test_relay_ethernet_test()` | Before each relay index's 6-command native-primitive sequence |
| `test.py::test_relay_safety_selftest()` | **Not yet wired** -- known inconsistency, see Section 13.7 |

### 13.4 StopReason Model

`stop_reason` and "how much completed" (`channel_results`) are deliberately
independent fields on `TestRunResult`, not folded into one value -- a run can
be `CANCELLED` after 2 of 8 channels passed.

```
StopReason
  |-- COMPLETED         normal exit, no exception
  |-- FAILED            RelayError, HardwareInitError, or any other
  |                      unanticipated exception
  |-- SAFETY_VIOLATION  SafetyViolationError (SafetyMonitor detected a
  |                      limit breach -- correct system behavior, not a bug)
  |-- TIMEOUT           defined, NOT yet wired end-to-end -- charge/discharge
  |                      still return False on timeout but
  |                      BatteryTestSequence.run() discards that value
  |                      (pre-existing gap, unrelated to this feature)
  \-- CANCELLED         OperationCancelledError (operator action, never
                         reported as FAILED)
```

`TestRunResult.summary()` reports `stop_reason` directly whenever it is not
`COMPLETED` (e.g. `status=CANCELLED`), instead of the older generic
`ABORTED`/`PARTIAL` wording, so a cancelled run is never visually
indistinguishable from a genuine failure in logs.

**Known limitation (pre-existing, not introduced by this feature):**
`TestExecutor._run_sequence()` still marks every requested channel as fully
completed once `BatteryTestSequence.run()` returns without raising, so the
`"PARTIAL"` branch of `summary()` is presently unreachable, and `stop_reason`
does not yet reach the persisted database/report (`ResultManager`/
`ReportGenerator` operate purely off per-sample DB records today, not the
in-memory `TestRunResult`).

### 13.5 Relay Immediate Fault Response

Before this feature, `BatteryTestSequence.run()` only forced the relay open
immediately for `SafetyViolationError`/`RelayError` -- any other exception
(e.g. `DAQError`, a raw `KeyboardInterrupt` under the old handling, or any
unanticipated failure) fell through uncaught, leaving the relay closed until
the outer `HardwareManager.disconnect_all()` eventually ran at process
teardown. The PMU never had this gap (`ChargeCycle`/`DischargeCycle`'s own
`try/finally` already forced it off on any exception type).

This is now closed with a new `except Exception` clause in
`BatteryTestSequence.run()`, positioned after the specific
`OperationCancelledError`/`SafetyViolationError`/`RelayError` clauses (so
none of those are accidentally caught by the broader one):

```python
except Exception as e:
    self.log.error("Unexpected error on channel %d: %s", ch, e, exc_info=True)
    self.safety.emergency_stop(self.smu, self.relay, str(e))
    raise
```

Any exception during a channel's charge/discharge now reaches PMU-off +
relay-open-all, both verified, at the fault location -- not just at final
process teardown.

**Known adjacent gap (pre-existing, not fixed by this change):** a `continue`
(not an exception) after charge, triggered when
`is_safe_to_switch_relay()` returns `False`, still bypasses both the
`else: self.relay.open(ch)` clause and `emergency_stop()` -- currently
unreachable because `DAQ.read_all_batteries()` is still a stub that always
reports zero current, but will become live once real DAQ acquisition exists.

### 13.6 Timeout Audit Results (re-confirmed during this review)

| Path | Bound | Where configured |
|---|---|---|
| Numato relay TCP (`hardware/relay_eth.py`) | ~5.0s per command (default) | `cfg["timeout"]` in `config/devices.py` |
| nidcpower (SMU) | **Unconfigured** | No explicit timeout set anywhere in `hardware/smu.py` |
| nidmm (DMM) | **Unconfigured** | No explicit timeout set anywhere in `hardware/dmm.py` |
| nidaqmx (DAQ) `read_channel()` | **Unconfigured** | No explicit timeout set anywhere in `hardware/daq.py` -- now a real blocking call (`task.read()`) |
| nidaqmx (DAQ) `read_all_batteries()` | N/A today | Still a stub -- no real blocking call exists yet |
| Charge/discharge sampling loop | ~1s (`SAMPLE_RATE_HZ = 1.0`) | `config/settings.py` |
| Pre-loop stabilization sleep | ~5.0s, single unchunked `time.sleep()` | `STABILIZATION_S` in `config/settings.py` |

Worst-case cancellation latency inside a charge/discharge cycle today is
therefore approximately **STABILIZATION_S + one sample interval (~6s)** if
cancellation lands right as a cycle's stabilization sleep begins; typically
much faster (~1s) once sampling has started. The SMU/DMM timeout gap is
unchanged from the prior hardware-safety audit and remains a prerequisite
finding for real-hardware validation, not something this feature fixes.

### 13.7 Current Known Risks

- `HardwareManager.connect_all()` runs before the SIGINT handler/token is
  installed and before the `try/finally: hw.disconnect_all()` net exists, in
  both `main.py` and `test.py::run_main_test()` -- a raw `KeyboardInterrupt`
  during connect bypasses both the internal per-device rollback (which only
  catches `Exception`, not `BaseException`) and the outer teardown. Low
  consequence today (no real sourcing exists yet) but should be closed before
  `output_enable()` is implemented for real.
- `test.py::test_relay_safety_selftest()` has no cancellation checkpoint,
  unlike its two structural siblings -- an inconsistency against the
  project-wide "same behavior everywhere" goal.
- `safe_cancel_shutdown()` and the innermost `ChargeCycle`/`DischargeCycle`
  `finally` both call `smu.emergency_output_off()` when cancellation fires
  mid-cycle -- harmless and idempotent in software, but the real-hardware
  behavior of a repeated `output_enabled = False` write has not been
  empirically validated.
- `stop_reason` is not yet persisted to the database or the generated report
  -- it exists only on the in-memory `TestRunResult` for the duration of one
  process run.
- The pre-loop `STABILIZATION_S` sleep is not chunked, so cancellation
  latency can reach ~6s in the worst case (see 13.6).

### 13.8 Emergency Abort -- explicitly not implemented

A separate, faster, escalating "Emergency Abort" mechanism (operator types
`ABORT`, skips graceful per-channel completion) was designed in an earlier
architecture discussion but **deliberately deferred**. No `ABORT` command, no
listener thread, and no `EMERGENCY_ABORT` stop reason exist in this codebase
today -- only Safe Cancellation, as described above.

## 14. PXI Rack Inventory & Hardware Architecture

The real PXI rack inventory (confirmed via NI-MAX detection, not assumed) is
fully described in `config/devices.py::PXI_SLOTS` -- this section is the
narrative summary; `PXI_SLOTS` and `docs/CONFIGURATION.md` are the
authoritative, detailed references.

### 14.1 Current inventory

| Slot | Model | Nickname | Category | Driver family | Status |
|---|---|---|---|---|---|
| 2 | PXIe-6363 | `MAIN_DAQ` | daq | nidaqmx | Active -- the one DAQ `HardwareManager` connects |
| 3 | PXI-4065 | `MAIN_DMM` | dmm | nidmm | Active -- only DMM in the rack |
| 5 | PXIe-4141 | `PRIMARY_SMU` | smu | nidcpower | Active -- the one SMU `HardwareManager` connects and cycles |
| 6 | PXIe-4139 | `HIGH_POWER_SMU` | smu | nidcpower | Present, individually testable, not assigned to a channel |
| 7 | PXI-4130 | `AUX_SMU_1` | smu | nidcpower | Present, individually testable, not assigned to a channel |
| 8 | PXI-4130 | `AUX_SMU_2` | smu | nidcpower | Present, individually testable, not assigned to a channel |
| 11 | PXIe-2569 | `CHASSIS_RELAY_MATRIX` | switch | niswitch | Present, **not the active relay driver** -- see 14.4 |
| 15 | PXIe-4353 + TB-4353/0 | `TEMP_MODULE` | temperature | nidaqmx | Present, identity/presence check only -- see 14.5 |
| 17 | PXIe-6368 | `EXPANSION_DAQ` | daq | nidaqmx | Present, individually testable, not wired into `HardwareManager` |
| 18 | PXIe-6365 | `PRECISION_DAQ` | daq | nidaqmx | Present, individually testable, not wired into `HardwareManager` |
| GPIB0 | unconfirmed | `UNCONFIRMED_GPIB_INSTRUMENT` | -- | NI-488.2 | Interface detected, no instrument model confirmed -- see 14.6 |

Every entry also carries a `role` (one-line intended purpose) and
`validation_notes` (discrepancies against the original plan in
`flowcharts/vi plan.md`, or anything not to assume without checking) -- read
those directly in `config/devices.py` rather than duplicating them here,
since duplicating free-text notes across two files is exactly the kind of
drift this whole refactor exists to prevent.

### 14.2 Single source of truth: `PXI_SLOTS`

`PXI_SLOTS` is the only place a PXI resource string or model is
hand-authored. `SMU_ASSIGNMENTS`, `DAQ_CONFIG`/`DAQ_CONFIGS`, and
`DMM_CONFIG`/`DMM_CONFIGS` are **derived** from it by filtering on
`category`, not hand-duplicated:

```python
def _slots_by_category(category):
    return {slot: cfg for slot, cfg in PXI_SLOTS.items() if cfg["category"] == category}

SMU_ASSIGNMENTS = {cfg["nickname"]: {...} for cfg in _slots_by_category("smu").values()}
DAQ_CONFIGS     = {cfg["nickname"]: {...} for cfg in _slots_by_category("daq").values()}
DAQ_CONFIG      = DAQ_CONFIGS["MAIN_DAQ"]
DMM_CONFIGS     = {cfg["nickname"]: {...} for cfg in _slots_by_category("dmm").values()}
DMM_CONFIG      = DMM_CONFIGS["MAIN_DMM"]
```

`HardwareManager` still only ever connects ONE SMU (`next(iter(SMU_ASSIGNMENTS.values()))`, which resolves to `PRIMARY_SMU` because `PXI_SLOTS` lists slot 5 before slots 6/7/8) and ONE DAQ (`DAQ_CONFIG`) for the active battery test sequence -- multi-device channel assignment (actually driving `HIGH_POWER_SMU`/`AUX_SMU_1`/`AUX_SMU_2`/`EXPANSION_DAQ`/`PRECISION_DAQ` for real charge/discharge) is a future scaling task, not implemented by this refactor.

`GPIB_INSTRUMENTS` is a separate dict (not derived from `PXI_SLOTS`, since GPIB0 is not a chassis slot) documenting the detected NI-488.2 interface.

### 14.3 Nicknames

Nicknames reflect intended system role, not just the model number (`PRIMARY_SMU`, `MAIN_DMM`, `TEMP_MODULE`), per the same principle already established for the Numato relays (`MATRIX_NUMATO_201`, `MATRIX_NUMATO_202` -- named after their static IP's last octet for easier hardware ID) and battery channels (`BAT_1`..`BAT_8`). Every driver-facing config dict is now keyed by nickname, not an arbitrary label like the old `"SMU1"` -- this changed a real call site: `test.py`'s Configuration Validation used to hardcode `dev_cfg.SMU_ASSIGNMENTS.get("SMU1", {})`, which silently returned `{}` after the rename; fixed to `next(iter(dev_cfg.SMU_ASSIGNMENTS.values()), {})`, matching how `HardwareManager` already picks the primary device.

### 14.4 Current relay architecture

**Numato Lab 32-Channel Ethernet Relay Module remains the only active relay driver** (`NUMATO_RELAY_MATRIX_CONFIG` / `hardware/relay_eth.py::NumatoRelayMatrix`, Section 6) -- confirmed reachable and validated end-to-end against physical hardware (Section 6b/6c).

`PXIe-2569` (slot 11, chassis-resident electromechanical relay/switch module) is physically present in the rack -- the original plan (`flowcharts/vi plan.md: '2569 relay'`) anticipated this card as *the* relay matrix, but the implemented production path is the Numato Ethernet module instead. The 2569 has no driver class in this codebase (`niswitch` is a distinct NI driver package, not reused by anything here) and is not connected, tested functionally, or used anywhere in `HardwareManager`/`BatteryTestSequence`. Hardware Discovery lists it as **N/A -- no driver implemented**, never as a fake PASS/FAIL. Repurposing it (as an alternative or supplement to the Numato relay) is an open decision, tracked in `docs/TODO.md`, not something this documentation pass resolves.

### 14.5 Temperature module

`PXIe-4353` (slot 15, + terminal block `TB-4353` connector 0) is an NI-DAQmx-family universal thermocouple/RTD input module. Not present in the original VI plan -- a new finding from the real rack inventory, and the most likely real hardware source for the per-channel temperature reading (`t_c`) that `charge_cycle.py`/`discharge_cycle.py` currently stub as `None`. `test.py::test_temperature_module()` / `_identify_temperature()` deliberately reuse `hardware.daq.DAQ` for connect/identify (NI-4353 enumerates and self-tests the same way any other NI-DAQmx device does) -- no thermocouple/RTD channel is configured or read anywhere; that would need a new driver and is explicitly not implemented.

### 14.6 GPIB

An NI-488.2 interface (`GPIB0`) was detected in the rack with no specific instrument confirmed at that address. `equipment_Requirement.md` documents an intended "Programmable Electronic Load" and "Programmable Power Supply" -- GPIB0 is the most likely connection point for one of those, but this is unconfirmed. `test.py::test_electronic_load()` reports this honestly (`GPIB_INSTRUMENTS`'s `validation_notes`) rather than a generic "not configured" stub. No GPIB driver class exists in this codebase.

## 15. Testing Architecture Summary

Consolidates the testing-related sections above into one map of guarantees:

| Concern | Where enforced | Guarantee |
|---|---|---|
| Hardware Discovery | Section 8.2 | Every configured device (by category, from `PXI_SLOTS`) gets a real connect+identify(+model-compare) check, never faked for categories with no driver |
| Device verification | Section 8.2 | COMMAND -> READBACK -> VERIFY -> PASS for every device type (Section 10) -- never "the API call didn't throw" |
| Device selection workflow | Section 8.2a | Reachability scan shown BEFORE selection, not after -- no blind picks |
| Category-specific testing | Section 8.2a | `test_smu()`/`test_dmm()`/`test_daq()`/`test_temperature_module()`/relay tests each only ever construct/connect devices of their OWN category |
| User-selected device testing | Section 8.2a | The functional test step operates on exactly the one device chosen -- verified directly, not assumed |
| No automatic testing of unrelated hardware | Section 8.2a | Selecting a DMM never connects an SMU, DAQ, or relay, and vice versa |

## 16. State Model

The canonical vocabulary for why a run (or a single channel within one) stopped -- defined in `utils/stop_reason.py::StopReason`, deliberately kept independent from "how much completed" (`channel_results`/`success`), since a run can be `CANCELLED` after 2 of 8 channels passed.

| State | Meaning | Triggered by |
|---|---|---|
| `COMPLETED` | Ran to natural completion | Normal exit, no exception |
| `FAILED` | Stopped due to an unexpected error | `RelayError`, `HardwareInitError`, or any other unanticipated exception |
| `SAFETY_VIOLATION` | Stopped because `SafetyMonitor` detected a limit breach | `SafetyViolationError` -- correct, intentional safety behavior, not a defect |
| `TIMEOUT` | A charge/discharge cycle hit its configured deadline | Defined in `StopReason`, **not yet wired end-to-end** -- `ChargeCycle`/`DischargeCycle` already return `False` on timeout, but `BatteryTestSequence.run()` still discards that value (pre-existing gap, see Section 13.4) |
| `CANCELLED` | Operator requested a graceful stop (Ctrl+C) | `OperationCancelledError` -- never reported as `FAILED` |

`TestRunResult.stop_reason` is set exactly once per run, in `TestExecutor.run()`'s exception handling (Section 13.2's flow diagram shows the `CANCELLED` path end-to-end). `TestRunResult.summary()` prints `stop_reason` directly whenever it is not `COMPLETED`, so a log line reads `status=CANCELLED` or `status=SAFETY_VIOLATION`, never a generic `ABORTED` that would conflate the two.

**Known limitation:** `stop_reason` does not yet reach the persisted database or generated report -- `ResultManager`/`ReportGenerator` operate purely off per-sample DB records today, not the in-memory `TestRunResult` (see Section 13.4 and Section 17).

## 17. Known Risks

Consolidated from the cancellation-architecture review (Section 13.7) and the standalone hardware-safety audit performed before real-hardware validation. Organized by category; nothing here has been fixed by a documentation pass -- these are open items, tracked in `docs/TODO.md`.

**Hardware timeout characterization (blocking-call bounds):**
- NI-DCPower (SMU) sessions have **no explicit timeout configured** anywhere in `hardware/smu.py` -- behavior relies entirely on the driver's own default, which is unconfirmed. This is also the upper bound on cancellation latency for any SMU call (Section 13.6).
- NI-DMM sessions have the same gap -- no explicit timeout configured in `hardware/dmm.py`.
- NI-DAQmx (DAQ) `read_channel()` is now a real blocking call (`task.read()`) with **no explicit timeout configured** anywhere in `hardware/daq.py` -- same unconfirmed-driver-default gap as SMU/DMM above. `read_all_batteries()` is still a stub, nothing to characterize there yet.
- The Numato relay TCP/Telnet path IS bounded (~5.0s per command, `cfg["timeout"]`) -- the one instrument type with a confirmed, configured timeout.

**PMU behavior under communication loss:** if the VISA/PXI link to the SMU is down, `emergency_output_off()` cannot force the hardware off -- `output_disable()` raises, is caught, logged CRITICAL ("PMU may still be actively sourcing/sinking current"), and returns `False` rather than pretending success. There is no hardware-level fail-safe confirmed for this scenario -- unverified against the real NI-4141/4139/4130 cards.

**PMU behavior under power loss:** entirely a hardware question, not something software can characterize. Two specific unknowns, neither confirmed against a datasheet or bench test: (1) whether the SMU cards' output stage fails open (de-energizes) when the card itself loses power, and (2) whether the Numato relay module (typically its own separate power supply) and the PXI chassis share a power source with the host PC, which determines whether "PC power loss" implies "PMU/relay power loss" too. This is the least-characterized risk in the system.

**Electrical verification limitations:**
- PMU output verification (`verify_output_disabled()`) trusts the NI-DCPower driver's *self-reported* `session.output_enabled` state -- not an independent electrical measurement, unlike the relay's genuinely separate readback command (`relay readall`, a different query over the same Telnet channel but a distinct command/response, not just a cached property read).
- No PMU safety logic has been exercised against real current flow -- `output_enable()`/`set_charge_mode()` are still stubs, so `emergency_output_off()` has only ever been verified turning off a session that was never actually sourcing anything.
- Terminal-close (Windows `CTRL_CLOSE_EVENT`), Task-Manager kill, and native driver crashes all bypass every safety mechanism in this codebase (no code runs) -- this is an inherent limitation of a pure-userspace application, not something any amount of Python-level engineering closes.

**Cancellation-specific risks** (see Section 13.7 for full detail, repeated here for a single consolidated risk list): the `HardwareManager.connect_all()` SIGINT/teardown gap, `test_relay_safety_selftest()`'s missing cancellation checkpoint, the harmless-but-real duplicate `emergency_output_off()` call on a mid-cycle cancellation, and the unchunked `STABILIZATION_S` sleep's ~6s worst-case cancellation latency.

## 18. Proto Test Execution (Milestone 2)

**Objective:** exercise the real production architecture end-to-end -- `main.py`-equivalent orchestration, `HardwareManager`, relay control, SMU control, DMM measurement, SQLite persistence, state display, safe shutdown, and Ctrl+C cancellation -- using already-validated hardware (Milestone 1), with **no battery connected**. This is infrastructure validation, not battery validation: it proves the layers work together correctly, not that a battery test passes.

**Entry point:** `test.py::run_proto_test_execution()` (MENU item "Proto Test Execution"), built the same way `run_main_test()` already is -- constructs `HardwareManager` (with `dmm_cfg` explicitly passed, since Proto Test Execution requires the DMM unlike the default battery path), a `CancellationToken` with the same SIGINT-handler pattern, and a `DataStorage` instance directly (not via `ResultManager`, since Proto Test Execution's persistence need -- station/execution state, not per-sample battery measurements -- doesn't match `ResultManager`'s report-generation responsibility). No Proto-Test-specific logic lives in `main.py`.

**Sequence:** `test_control/proto_test_sequence.py::ProtoTestSequence` -- a second member of the same "sequence" family as `BatteryTestSequence` (same constructor shape, same `try/except OperationCancelledError/SafetyViolationError/RelayError/Exception` structure, same `safety.emergency_stop()`/`safety.safe_cancel_shutdown()` calls on failure/cancellation), not a parallel framework. Per relay N:

1. `relay.close(N)` -- unchanged, reuses `hardware/relay_eth.py`'s full mandatory force-all-off -> verify -> activate -> verify-single -> verify-all sequence.
2. `smu.source_dc_voltage_point(voltage_v=Settings.CHARGE_VOLTAGE_V, current_limit_a=Settings.CHARGE_CURRENT_A, voltage_range_v=Settings.BAT_VOLTAGE_MAX, hold_s=dwell_s, during_hold=<DMM read>)` -- reuses the fully-verified configure -> readback -> verify -> enable sequence from SMU Verification Hardening (Section 12.6b) completely unchanged. Two new, backward-compatible optional parameters were added to make this call possible without touching any existing verification logic:
   - `hold_s` (default `0.0`): keeps output enabled for `hold_s` seconds after the SMU's own measurement, before the method's existing `finally` block disables and verifies OFF. Default preserves the exact prior timing for every existing caller (`test.py`'s SMU Functional Validation).
   - `during_hold` (default `None`): an opaque callback invoked once, immediately after the SMU's own measurement and before the `hold_s` sleep -- lets `ProtoTestSequence` take a DMM reading while output is genuinely still active, without `hardware/smu.py` knowing anything about the DMM. Its return value is passed back as `"during_hold_result"`.
3. `storage.record_execution_state(...)` (new `DataStorage` methods, `data/storage.py`) -- persists relay number, timestamp, SMU commanded/readback/measured values, and the DMM reading, to a new `station_state` table (kept separate from `measurements` -- a different concern: station/execution position, not a per-sample battery reading. This is the `station_state` table anticipated but not built in `docs/DATABASE_ROADMAP.md` Section 4 / `docs/TODO.md`'s cycle-recovery item).
4. `relay.open(N)` on success, advance to relay N+1.

**State persistence and recovery display:** at startup, `run_proto_test_execution()` calls `storage.get_last_execution_state()` (reads the last `station_state` row across all prior run_ids -- deliberately not scoped to the new run's own run_id, since the point is showing what the *previous* run left off at) and prints it (relay/state/timestamp) to the operator. Per this milestone's explicit scope, there is **no automatic resume** -- display only. On any abnormal exit (safety violation, relay fault, unexpected exception, operator cancellation), `ProtoTestSequence` writes one additional `station_state` row recording the abnormal `state` (reusing `utils/stop_reason.py`'s `StopReason` constants -- `FAILED`/`SAFETY_VIOLATION`/`CANCELLED` -- rather than inventing a second vocabulary) before the safety shutdown runs, so the next startup's display reflects the correct last-known position. A final `COMPLETED` row is written if every relay cycles successfully.

**Reused unchanged:** `HardwareManager` (connect/disconnect/atexit safety nets), `SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()`, `CancellationToken`/`check_cancellation()`, `hardware/relay_eth.py::NumatoRelayMatrix`, `hardware/smu.py::SMU.source_dc_voltage_point()`'s entire verification logic, `hardware/dmm.py::DMM.measure_dc_voltage()`, `data/storage.py::DataStorage`'s existing `measurements` table/CSV output (untouched), and `test.py`'s centralized "return to Main Menu" dispatch (Section 8.2a) and `device_display_name()` labeling.

**Physical rack validation: PASSED** -- see `docs/MILESTONES.md` Milestone 2 for the full record (hardware used, results, the first-relay startup transient and its root cause, validation status). Summary: all 8 relays cycled successfully against `AUX_SMU_1` (PXI-4130, Slot 7, channel `"1"`) -> `MAIN_DMM` (NI-4065, Slot 3), no battery/load connected. `Settings.PROTO_TEST_SMU_NAME` (default `"AUX_SMU_1"`) was added so `run_proto_test_execution()` targets a specific SMU by name instead of `HardwareManager`'s positional `next(iter(SMU_ASSIGNMENTS...))` default (which always resolves to `PRIMARY_SMU`) -- scoped to this one function; `HardwareManager`'s own default and `main.py` are untouched. Console progress (`print()`, relay/phase/measurements) was also added to `ProtoTestSequence`, since `test.py` never configures a logging handler for this workflow.

**Known, documented, not-yet-fixed gap:** no explicit settling delay exists between NI-DCPower `session.initiate()` (`hardware/smu.py::source_dc_voltage_point()`) and the first `session.measure(VOLTAGE)` call taken immediately afterward. This produced a one-time measurement transient on the very first relay of the rack validation run (the session's first-ever `commit()`/`initiate()` cycle) -- see `docs/MILESTONES.md` Milestone 2 for the full root-cause analysis. Deliberately left undocumented-but-unfixed pending Battery Integration, where real load/settling dynamics should inform the right fix rather than one tuned against an unloaded bench condition.

**Not yet done / explicitly out of scope for this milestone:** automatic resume from a previous execution position; any battery-limit logic (deliberately absent from `ProtoTestSequence` -- `SafetyMonitor` remains the sole owner of limit/abort decisions, per SMU Verification Hardening's separation-of-responsibilities decision).

## 18a. Execution UI Architecture (Milestone II, Phase 2)

**`test_control/execution_screen.py`** is the canonical runtime UI for every execution view in this project -- Proto Test Execution today, and future Battery Charge/Discharge/cycle execution, the Historical Results Viewer, and `UI Preview Test`, all build an `ExecutionFrame` and pass it to the one shared `render_execution_frame()`. No caller prints its own execution screen; that is the entire reason this module exists.

**Why `ExecutionFrame` exists:** before this, `ProtoTestSequence` hand-rolled its own inline `print()` calls for operator visibility. A second UI implementation would have appeared the moment Battery Charge/Discharge needed a screen too, and the two would have drifted the first time either one's formatting changed independently. `ExecutionFrame` is the one place "what does an execution screen show" is defined -- every caller only ever answers "what are the values right now," never "how do I print them."

**Why both `from_live()` and `from_database()` were built together, from the first version of this module:** `ExecutionFrame.from_live()` builds a frame from in-memory hardware readings during a real run; `ExecutionFrame.from_database()` builds the *identical* frame shape from historical rows (`run_summary`/`measurements`/`event_log`, via `data/storage.py::DataStorage`) for `UI Preview Test` and the Historical Results Viewer. Building one constructor now and the other later is exactly the mechanism by which a live screen and a replayed screen quietly drift apart -- every field the renderer can show had to be reachable from both a live run and a historical read before either constructor shipped, not added to one at a time.

**How runtime and replay share the same renderer:** `render_execution_frame(frame)` never inspects how `frame` was built -- it only reads field values and substitutes `"N/A"` for any that are `None`. This is verified structurally, not just by convention: `execution_screen.py` has zero imports beyond Python's own `dataclasses` module -- no `hardware.*`, no `HardwareManager`, nothing that could touch real instruments. `from_database()`'s "no hardware access" guarantee for `UI Preview Test` follows from this directly, not from a runtime check.

### 18b. Proto Test migration to the Milestone II infrastructure (Phase 3)

`test_control/proto_test_sequence.py::ProtoTestSequence` was migrated to write through the Milestone II storage/UI infrastructure -- **this was an architecture migration only.** The following are byte-for-byte unchanged from the pre-Phase-3 version: `relay.close(N)`/`relay.open(N)` call sites and arguments, `smu.source_dc_voltage_point(voltage_v, current_limit_a, voltage_range_v, hold_s, during_hold)`'s call site and every argument, the `_read_dmm()` callback's actual `dmm.measure_dc_voltage()` call, `check_cancellation(token)`'s placement, and every `safety.emergency_stop()`/`safety.safe_cancel_shutdown()` call site and the exception types that trigger them. The physical validation path (relay sequencing, SMU sourcing, DMM measurement, dwell timing, safety behavior) is electrically identical to the version already validated on the physical rack (`docs/MILESTONES.md` Milestone 2).

What changed, all storage/UI-layer only:
- The full measurement result (SMU commanded/readback/measured, DMM measured, compliance, output state) now goes to `storage.record_measurement(test_type="proto", channel=relay_n, relay=relay_n, ...)` -- `measurements`, not `station_state`.
- `storage.record_execution_state(channel=relay_n, relay=relay_n, state=...)` is now called with only recovery-relevant fields, on every exit path (the `ACTIVE` row per relay, plus `CANCELLED`/`SAFETY_VIOLATION`/`FAILED`/`COMPLETED` on the corresponding exit -- unchanged in *when* these are written, only *what* they carry).
- `storage.start_run_summary(test_type="proto")` at the start of `run()`, and `storage.finish_run_summary(stop_reason=..., result=...)` on every exit path (success and every failure/cancellation branch) -- new; a `run_summary` row now exists for every Proto Test run, closing the "browse history without scanning telemetry" gap.
- Every previous inline `print()` phase-transition message became a `storage.log_event(...)` call (same message content, now durable in `event_log` instead of ephemeral console-only text), plus one `render_execution_frame(ExecutionFrame.from_live(...))` call per relay (at the point real measurement data exists) -- replacing the ad hoc console formatting with the shared Phase 2 renderer. No secondary renderer was introduced.

**`state` vs. `phase_detail` in a historical frame:** `from_database()` populates `state` from `run_summary.stop_reason` (the same `StopReason` vocabulary -- `COMPLETED`/`FAILED`/`SAFETY_VIOLATION`/`CANCELLED` -- `station_state.state` already uses for a live run) and `phase_detail` from the last `measurements` row's `phase_detail` column. These deliberately stay two different concepts with two different lifetimes: a completed historical run has no "ACTIVE"/"DWELLING" moment left to replay, only its final outcome (`state`) and whatever phase was in effect when the last measurement was actually taken (`phase_detail`). A live frame's `state`/`phase_detail` can show the fine-grained in-progress values a replay never can -- this is an accepted, intentional difference between live monitoring and historical playback, not a bug.

**`recent_measurements`/`recent_events` are required fields, not an add-on:** both are populated from day one by both constructors -- `from_live()` from whatever in-memory buffer the calling sequence maintains, `from_database()` from `DataStorage.get_measurements()`/`get_recent_events()` -- so the same data source powers the Runtime Screen, `UI Preview Test`, and the Historical Results Viewer identically.

## 19. Battery Definitions, Groups, and Positions (Milestone II)

**Battery types (`config/devices.py::BATTERY_CONFIGS`):** the operator-facing battery catalog is now the two real battery types -- `HUB` (1050 mAh, 3.7 V nominal) and `SB` (160 mAh, 3.7 V nominal). The previous placeholder `GENERIC_LIION_18650` entry was removed; voltage/current/temperature limit fields on each entry not yet confirmed against a datasheet are marked with an inline `# unconfirmed placeholder` comment rather than silently presented as verified.

**Confirmed vs. assumed values:** only `nominal_voltage_v` (3.7 V) and `capacity_ah` (`HUB` = 1.05 Ah/1050 mAh, `SB` = 0.16 Ah/160 mAh) come from the actual battery spec. `chemistry`, `form_factor`, `voltage_max_v`, `voltage_min_v`, `max_charge_current_a`, `max_discharge_current_a`, and `max_temp_c` are all assumptions -- a standard Li-ion voltage window and 0.5C/1C charge/discharge ratios applied to the confirmed capacity, not measured or specified values -- and remain marked `# unconfirmed placeholder`/`# unconfirmed` inline in `config/devices.py` until validated against a real datasheet. These assumed values must not be silently treated as production limits; `Settings.BAT_*` in `config/settings.py` remains the actually-enforced safety ceiling in the meantime (see "Operational Limit Resolution" below).

**Battery type selection is explicit and operator-controlled, never inferred.** `BATTERY_CHANNELS` is physical wiring information only (`id`, `relay_address`, `daq_voltage_ch`, `daq_current_ch`, `daq_ntc_ch`, `fuse_rating_a`, `enabled`) -- it has no `battery_type` field and never did after this change. The operator picks a battery type from a menu (`test.py::_select_battery_type()`) independently of which position is wired up; nothing in this codebase infers "which battery" from "which channel."

**Battery Groups are a relay routing architecture, not a purely logical grouping.** `config/devices.py::BATTERY_GROUPS` maps each group of `Settings.GROUP_SIZE` (8) battery positions to one physical relay matrix:

| Group | Positions | Relay matrix | Status |
|---|---|---|---|
| A | 1-8   | `MATRIX_NUMATO_201` | enabled -- real hardware today |
| B | 9-16  | `MATRIX_NUMATO_202` | disabled -- pre-wired, no matrix installed yet |
| C | 17-24 | none | disabled -- future |
| D | 25-32 | none | disabled -- future |

Every future relay expansion is expected to arrive in additional groups of 8 positions, each backed by its own relay matrix entry in `BATTERY_GROUPS` -- this is the intended, documented scaling path, not a one-off for Group A.

Two helpers resolve between a group-relative position and the global position number `BATTERY_CHANNELS` is keyed by: `resolve_group_position(group, position_in_group) -> global_position` and `group_for_position(global_position) -> group_name`. `utils/device_validator.py::_check_battery_groups()` validates at startup that every `BATTERY_CHANNELS` key is covered by exactly one `BATTERY_GROUPS` range (replacing the removed `_check_battery_types()`, which validated the now-deleted `battery_type` field).

**Operator workflow order is always Battery Type -> Battery Group -> Battery Position**, and a position is always displayed and entered relative to its group (e.g. "Group A Position 3"), never as a raw global number ("Position 11") -- see Section 20 below for where this is implemented.

**`BATTERY_POSITIONS`/`GROUP_SIZE` (renamed from `NUM_CHANNELS`, `config/settings.py`):** `NUM_CHANNELS` read as a generic DAQ/electrical term but has only ever meant "how many battery positions exist" (confirmed by its only two real call sites: `test.py`'s `BATTERY_CHANNELS` count-check and `utils/validators.py`'s range validation, neither DAQ-channel-related). `BATTERY_POSITIONS = 8` replaces it directly; `GROUP_SIZE = 8` is new, expressing that one relay matrix serves `GROUP_SIZE` positions. `Settings.ACTIVE_CHANNELS` (the list every real sequence -- `BatteryTestSequence`, `ProtoTestSequence`, `TestExecutor` -- actually iterates) was deliberately left unrenamed: doing so would touch `test_control/` files outside this change's scope. A `ACTIVE_CHANNELS` -> `ACTIVE_POSITIONS` rename is recommended future cleanup, not performed here.

## 20. Monitor Battery (Milestone II)

**Objective:** the first real mode of the new battery-centric Run Main Test menu -- read-only battery monitoring, **no charging, no discharging**. Reuses the same Milestone II infrastructure Proto Test Execution already validated (`measurements`/`run_summary`/`event_log`/`station_state`, `ExecutionFrame`/`render_execution_frame()`) rather than any new storage design.

**Run Main Test is now a submenu**, not a single legacy action: `1. Monitor Battery` / `2. Charge Battery` / `3. Discharge Battery` / `4. Cycle Battery`. Only Monitor Battery is implemented; the other three print "not yet implemented" and take no action. The previous `run_main_test()` body (`TestExecutor`/`ResultManager`, the same path `main.py` uses) was retired from this menu entry -- `main.py`'s own production path is untouched.

**Workflow (`test.py::_run_monitor_battery()`):** Select Battery Type (`_select_battery_type()`) -> Select Battery Group (`_select_battery_group()`, only enabled groups selectable) -> Select Battery Position (`_select_battery_position()`, relative to the chosen group) -> Confirmation Screen (`_confirm_monitor_battery()`) -> Configuration Snapshot Logged -> Relay Close -> Start Monitoring.

**Confirmation screen** displays Mode, Battery Type, Capacity, Group, Position (as "Group X Position N"), Max/Min Voltage, Max Charge/Discharge Current, and Max Temperature, then prompts `Continue? (Y/N)`. Declining (`N` or anything else) exits without constructing `HardwareManager`, opening `DataStorage`, or touching any hardware -- verified by a mocked smoke test asserting `HardwareManager` is never called on decline.

**Configuration traceability (critical, mandatory):** once the operator accepts the confirmation screen, and **before** the relay closes / monitoring starts / any measurement is acquired, `_run_monitor_battery()` calls `storage.start_run_summary(test_type="monitor", battery_type=..., battery_voltage_max_v=..., battery_voltage_min_v=..., battery_charge_current_limit_a=..., battery_discharge_current_limit_a=..., capacity_ah=...)` (populating the `run_summary` battery-config snapshot columns added in Milestone II Phase 1) followed by a fixed sequence of `event_log` entries, all tied to the run's `run_id`:

1. "Run started"
2. "Mode selected: Monitor"
3. "Battery selected: `<type>`"
4. "Battery capacity: `<n>` mAh"
5. "Group selected: `<group>`"
6. "Position selected: `<n>` (Group `<group>` Position `<n>`)"
7. "Configuration snapshot recorded"

Only after all seven are written does `MonitorBatterySequence` get constructed and `sequence.run()` called. This ordering is verified by a mocked smoke test (`storage`/`HardwareManager`/`MonitorBatterySequence` all mocked) that asserts every traceability `log_event` call precedes the sequence's relay-close/monitoring call. Because these are ordinary `event_log`/`run_summary` rows, the full configuration a run was executed under is already visible via the Historical Results Viewer, `UI Preview Test`, and `ReportGenerator` -- no new read path was needed.

**`test_control/monitor_battery_sequence.py::MonitorBatterySequence`** mirrors `ProtoTestSequence`'s structure deliberately (same constructor shape, same `try/except OperationCancelledError/SafetyViolationError/RelayError/Exception` handling, same `safety.emergency_stop()`/`safety.safe_cancel_shutdown()` calls) rather than a parallel design. Per monitoring session:

1. `relay.close(relay_address)` -- unchanged, reuses `hardware/relay_eth.py`'s full mandatory force-all-off -> verify -> activate -> verify-single -> verify-all sequence.
2. A loop: `dmm.measure_dc_voltage()` (see "Temporary DMM-based monitoring" below -- `current_a`/`temp_c` are `None`), `storage.record_measurement(test_type="monitor", channel=..., relay=..., voltage_v=..., current_a=..., temp_c=...)`, then `ExecutionFrame.from_live()`/`render_execution_frame()` (the live-refreshed voltage display, updated once per loop iteration), then a fixed sample interval sleep -- repeated until the operator cancels.
3. **Cancellation (Ctrl+C) is the expected, normal way a monitoring session ends** -- there is no bounded "success" exit the way Proto Test's fixed relay cycle has one. `OperationCancelledError` is handled as a deliberate operator action (`run_summary.result = "STOPPED_BY_OPERATOR"`, `stop_reason = StopReason.CANCELLED`), not a failure.

**No new `measurements` columns were needed.** `battery_voltage`/`battery_current`/`battery_temp` (the three new `ExecutionFrame` fields added for this mode, Section 18a) reuse the **original**, pre-Milestone-II `voltage_v`/`current_a`/`temp_c` measurement columns -- the same ones `charge_cycle.py`/`discharge_cycle.py` already write -- populated via `record_measurement()`'s existing `**fields` mechanism. These are kept as distinct `ExecutionFrame` fields from `smu_voltage`/`smu_current`/`dmm_voltage` because Monitor Battery never sources through the SMU -- overloading the SMU/DMM-named fields would have mislabeled a plain reading of the battery itself as an SMU measurement.

### 20a. Temporary DMM-based monitoring (current implementation)

**Why the DAQ path was replaced with the DMM:** the original design read battery voltage/current per battery position via `hardware/daq.py::DAQ.read_channel()` against `BATTERY_CHANNELS[i]["daq_voltage_ch"]`/`daq_current_ch"]`. During real-hardware validation this failed due to channel/device configuration issues (the NI-MAX alias/wiring for these channels is not yet confirmed -- see `docs/CONFIGURATION.md`'s `BATTERY_CHANNELS` note and `docs/TODO.md`). Rather than block Milestone II architecture validation on that hardware bring-up work, `MonitorBatterySequence` was changed to take one **DMM** voltage reading per loop iteration (`hardware/dmm.py::DMM.measure_dc_voltage()`, the same fully-verified call `ProtoTestSequence` already uses) -- the DMM is already validated and available, and basic voltage-only monitoring is sufficient to prove the storage/UI/traceability architecture end-to-end on real hardware.

**This is explicitly temporary and documented as such** (module docstring TODO in `test_control/monitor_battery_sequence.py`, and this section):

> Temporary implementation: Monitor Battery currently acquires voltage from the DMM. Future charging/discharging workflows must migrate battery telemetry acquisition to the final DAQ-based architecture once channel mapping and hardware integration are completed.

**Consequences of the DMM being the source:**
- `current_a` and `temp_c` are always `None` in every `measurements`/`ExecutionFrame` row this mode writes -- the DMM measures voltage only. This is a temporary, documented limitation, distinct from the pre-existing NTC-temperature TODO `charge_cycle.py`/`discharge_cycle.py` already carry.
- Only one DMM exists in this configuration (`config/devices.py::DMM_CONFIG`, `MAIN_DMM`) -- monitoring is effectively single-instrument, not per-battery-position, regardless of which Group/Position the operator selected. The relay is still switched to the selected position exactly as before; only the voltage read itself is not (yet) sourced from that position's own dedicated channel.
- `test.py::_run_monitor_battery()` now passes `dmm_cfg=dev_cfg.DMM_CONFIG` into `HardwareManager(...)` (same pattern `run_proto_test_execution()` already uses) so `hw.dmm` is actually constructed/connected; `MonitorBatterySequence`'s constructor parameter is `dmm`, not `daq`.
- An `event_log` entry (`"Monitoring source: DMM"`) is written once, right after the relay-activation event log for each session, so the acquisition source is explicit in the historical record -- not just in code comments.

**Future DAQ-based battery telemetry architecture (not yet implemented):** once `BATTERY_CHANNELS[i]["daq_voltage_ch"]`/`daq_current_ch"]`/`daq_ntc_ch"]` are confirmed against real NI-MAX aliases and wiring, Monitor Battery (and Charge/Discharge/Cycle Battery when built) should read from the DAQ per selected battery position again -- giving independent, simultaneous per-position voltage/current/temperature acquisition instead of sharing one DMM across the whole rack. This migration only touches `MonitorBatterySequence`'s acquisition call (`dmm.measure_dc_voltage()` -> `daq.read_channel(...)` per channel) -- the storage/traceability/`ExecutionFrame` architecture around it does not change.

**Run summary voltage statistics:** `run_summary` gained six new columns (additive migration, `data/storage.py::_RUN_SUMMARY_MIGRATION_COLUMNS`) populated by `MonitorBatterySequence` on every exit path via `finish_run_summary(...)`: `start_voltage`, `end_voltage`, `min_voltage`, `max_voltage`, `average_voltage`, `sample_count`. These are tracked in-memory across the loop (`monitor_battery_sequence.py::_VoltageStats`, plain accumulation, no new storage mechanism) and are `NULL` for every non-Monitor `test_type` row.

**SMU involvement:** although Monitor Battery never sources or sinks current, `MonitorBatterySequence` still takes an `smu` reference and still calls `safety.emergency_stop()`/`safety.safe_cancel_shutdown()` (both require an SMU argument) on every exit path -- a cheap, idempotent no-op in this mode, kept so every mode shares one safety-shutdown entry point rather than a Monitor-specific relay-only shutdown path.

**Not yet done / explicitly out of scope for this milestone:** Charge Battery, Discharge Battery, Cycle Battery (menu placeholders only); NTC temperature reads (still `None`, same pre-existing gap as the charge/discharge cycle modules); migrating Monitor Battery from the temporary DMM source to the final per-position DAQ architecture (see 20a above); any battery-limit enforcement logic in `MonitorBatterySequence` itself (`SafetyMonitor` remains the sole owner of limit/abort decisions, unchanged from every other sequence).

## 21. Relay Functional Validation -- Group-Scoped Matrix Scan

**Objective:** let relay validation be scoped to one group's channel range on the currently selected relay matrix device, or the full configured population, rather than always scanning every channel -- future-proof for additional relay matrices as Groups B/C/D come online. Group A = channels 1-8, Group B = 9-16, Group C = 17-24, Group D = 25-32 (`config/devices.py::BATTERY_GROUPS`' `position_start`/`position_end`).

**Menu:** `test.py::_functional_relay_numato()`'s "Matrix Scan" option routes through `_test_relay_matrix_scan_scoped()`, which calls `_select_relay_scope()` first -- `1. All Groups` / `2. Group A` / `3. Group B` / `4. Group C` / `5. Group D`.

**Bug found and fixed (initial implementation):** `_select_relay_scope()` originally gated the scope resolution on `BATTERY_GROUPS[group]["enabled"]` and fell back to "All Groups" (scanning all 32 channels) whenever it was `False` -- which is every group except A today. Selecting "Group B", "Group C", or "Group D" therefore silently scanned channels 1-32 instead of the requested 8-channel range, with only an easy-to-miss one-line print explaining why. Root cause: `enabled` means "no battery relay matrix has been deployed/wired for this group yet for actual battery testing" -- a battery-wiring concern that only matters to `_select_battery_group()`/Monitor Battery. Relay Functional Validation tests raw relay hardware on whichever device is *already selected*, completely independent of whether a battery is wired to those channels -- channels 9-32 are just as real and safely testable on a 32-channel Numato matrix as channels 1-8, even before any battery relay matrix exists for Group B/C/D. `_select_relay_scope()` no longer checks `enabled` at all -- it resolves purely from `BATTERY_GROUPS[group]["position_start"/"position_end"]`, for any group key that exists in the dict.

**Before/after (mocked, `MATRIX_NUMATO_201` selected, 32-channel device):**

| Scope selected | Before (buggy) | After (fixed) |
|---|---|---|
| All Groups | 1-32 | 1-32 |
| Group A | 1-8 | 1-8 |
| Group B | **1-32** (bug) | 9-16 |
| Group C | **1-32** (bug) | 17-24 |
| Group D | **1-32** (bug) | 25-32 |

**Off-by-one / 1-based vs. 0-based addressing:** reviewed and confirmed correct, unaffected by this fix. `test_relay_matrix_scan()`/`_run_relay_matrix_scan()`'s scan loop (`for ch in range(channel_start, channel_end + 1)`) calls only `hardware/relay_eth.py::NumatoRelayMatrix`'s public 1-based API (`relay.close(ch)`/`relay.read(ch)`/`relay.open(ch)`), which itself converts to the Numato device's native 0-based addressing internally (`close()`'s `relay0 = channel - 1`) -- e.g. requesting channel 9 (Group B's first relay) correctly activates native relay index 8. This conversion lives entirely inside the driver and was never touched by the group-scoping feature or this fix.

**Visibility (added by this fix):** `test_relay_matrix_scan()` gained a `scope_label` parameter; when set (i.e. whenever the scope-selection menu was used), it prints an explicit, unmissable banner before the scan starts, for every choice including "All Groups" -- not just non-default ones:
```
INFO Relay validation scope: Group B
INFO Relays under test: 9-16
```
Standalone callers (`scope_label=None`, no scope selection happened) print no banner, matching prior unscoped behavior exactly.

**Implementation:** `test_relay_matrix_scan()`/`_run_relay_matrix_scan()` retain the `channel_start`/`channel_end` parameters added for group-scoping (both 1-based, inclusive, clipped to the selected device's configured channel count), defaulting to the full range when omitted. `_select_relay_scope()` now returns `(label, channel_start, channel_end)` instead of just the bounds, so the caller can print the scope banner above.

**ON-state dwell for physical inspection (added later):** per-channel
sequence in `_run_relay_matrix_scan()` is now `relay.close(ch)` (ON) ->
`relay.read(ch)` (READ) -> **5.0s dwell** (`Settings.
RELAY_MATRIX_SCAN_DWELL_S`, `config/settings.py`) -> `relay.open(ch)`
(OFF), instead of turning the relay back off immediately after the read.
This is a Matrix-Scan-only constant, separate from and in addition to
`Settings.RELAY_SETTLE_TIME_S` (still `2.0` s, still enforced
unconditionally by `RelayBase.open()`/`close()` on every relay action --
unchanged by this addition). The dwell gives an operator on the physical
rack time to observe relay activation, verify LEDs, verify physical
routing/measurements/wiring, and confirm correct relay selection before
the relay is deactivated. `relay.log` (`nipxi.hw.<device>`, visible via
this test's existing `_numato_relay_debug_logging()` wrapper) now logs
"activated" / "dwell starting" / "dwell complete -- deactivating" /
"deactivated" for each channel. No other relay workflow (Monitor Battery,
Monitor Battery Scan, ChargeSequence, DischargeSequence,
`RelayEthernetTest`) reads or is affected by this constant.

## 22. Hardware Identity Traceability (Milestone II)

**Objective:** a historical run in `run_summary`/`event_log` should be able to answer "which physical instruments were used?", "which hardware configuration produced these measurements?", and "what test bench configuration existed when the run executed?" -- not just electrical values and battery configuration. Before this extension, device identity (SMU/DMM/DAQ/relay-matrix model, resource string, IP) was only ever printed to the console (`test.py`'s `"Selected Hardware"` block) and lost the moment the session ended; nothing in `measurements`, `run_summary`, `event_log`, or `station_state` recorded which instrument produced a given row.

**Reviewed instrument categories (`config/devices.py`):** `PXI_SLOTS` is the single source of truth for every PXI-slot device (`SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/`DMM_CONFIGS` are all derived from it), plus `ETHERNET_DEVICES` for the Numato relay matrices. `HardwareManager` connects exactly one SMU, one DAQ, one relay matrix (always), and one DMM (optional) per run -- never the whole rack inventory at once -- so "which instrument was used for this run" is naturally a single answer per role, not a multi-valued set. `TEMP_MODULE` (temperature) has no driver wired in yet (`docs/TODO.md`) and is never connected by `HardwareManager`, so there is nothing to capture for it today; the schema below is additive, so a `temp_module_name`/`_resource`/`_model` triplet can be added the same way once it is.

**Identity fields chosen, per instrument:** `name` (the `config/devices.py` dict key HardwareManager actually built the driver from -- e.g. `"PRIMARY_SMU"`, `"AUX_SMU_1"`, `"MAIN_DMM"`, `"MATRIX_NUMATO_201"`), `resource` (the NI-MAX VISA resource string for PXI devices, or `"ip:port"` for the Ethernet relay matrix), and `model` (the real instrument model string for PXI devices, or the driver identifier -- e.g. `"RELAY32ETHRL00"` -- for the relay matrix). These three fields together are sufficient to answer every question in the Goal without modeling anything not already present in `config/devices.py`.

**Schema (additive, `data/storage.py`):** `run_summary` gains twelve nullable `TEXT` columns -- `smu_name`/`smu_resource`/`smu_model`, `dmm_name`/`dmm_resource`/`dmm_model`, `daq_name`/`daq_resource`/`daq_model`, `relay_matrix_name`/`relay_matrix_resource`/`relay_matrix_model` -- added to `CREATE_RUN_SUMMARY_SQL`/`_RUN_SUMMARY_COLUMNS` and to `_RUN_SUMMARY_MIGRATION_COLUMNS` (the existing additive-migration list, already wired into `DataStorage.open()`) so a pre-existing database gets these columns via `ALTER TABLE ... ADD COLUMN` without touching any existing row. `dmm_*` stays `NULL` for a run with no DMM configured -- absence is not an error. No new table was created.

**Traceability pattern -- identical to the battery-configuration snapshot:** hardware identity is written via the same two-part mechanism already established for battery configuration (Section 20's "Configuration traceability"):
1. A durable snapshot passed as `**fields` into `start_run_summary()` -- `start_run_summary()`'s existing generic mechanism (any key in `_RUN_SUMMARY_COLUMNS` is accepted and stored) required **no code change** to accept the twelve new fields; only the schema/column-list changed.
2. One `event_log` entry per connected instrument, plus a final confirmation entry, written **before** the first relay closes:
   - `"SMU in use: <name> (<model>, <resource>)"`
   - `"DMM in use: <name> (<model>, <resource>)"` (omitted entirely if no DMM is configured for this run -- an event_log entry implies "this instrument was in use")
   - `"DAQ in use: <name> (<model>, <resource>)"`
   - `"Relay matrix in use: <name> (<model>, <resource>)"`
   - `"Hardware configuration snapshot recorded"`

**Shared, reused logic (no duplicate sources of truth):**
- `config/devices.py::find_config_name(configs, cfg)` -- reverse-looks-up a device's dict key from its resolved cfg dict via identity comparison (`is`, not `==`), so `dmm_name`/`daq_name` are always derived from the *same* `config/devices.py` dict `HardwareManager` was actually constructed with, never a second hardcoded literal (e.g. `"MAIN_DMM"`) that could silently drift.
- `config/devices.py::hardware_traceability_messages(snapshot)` -- builds the event_log message list from a hardware-identity snapshot dict; used identically by `test_control/proto_test_sequence.py::ProtoTestSequence.run()` and `test.py::_run_monitor_battery()` so the wording can never drift between test types.
- `test.py::_hardware_snapshot_fields(smu_name, smu_cfg, dmm_name, dmm_cfg, daq_name, daq_cfg, relay_cfg)` -- builds the `run_summary` snapshot dict from the exact cfg dicts passed into `HardwareManager(...)`, shared by `run_proto_test_execution()` and `_run_monitor_battery()`.

**`ProtoTestSequence.run()` gained one new, optional, backward-compatible parameter:** `hardware_snapshot: dict = None`. `test.py` builds it and passes it in; `ProtoTestSequence` merges it into its own `start_run_summary(test_type="proto", **(hardware_snapshot or {}))` call (which it already made) and logs the hardware `event_log` entries immediately after, before the per-relay loop begins. Passing `None` (the old call signature) reproduces the exact prior behavior -- every hardware column stays `NULL`, as it always was. `MonitorBatterySequence` needed no equivalent change -- its `start_run_summary()` call already lives in `test.py::_run_monitor_battery()` itself, so the snapshot fields were added directly to that existing call's `**fields`.

**Why `daq_cfg`/`smu_cfg` are now passed explicitly to `HardwareManager(...)` in both workflows:** previously, `_run_monitor_battery()` omitted `smu_cfg`/`daq_cfg` (relying on `HardwareManager`'s internal `next(iter(SMU_ASSIGNMENTS.values()))`/`DAQ_CONFIG` defaults) and `run_proto_test_execution()` omitted `daq_cfg` (relying on the `DAQ_CONFIG` default). Both now resolve the same values themselves and pass them in explicitly -- identical behavior, same actual hardware connected -- purely so the hardware-identity snapshot is guaranteed to describe the *exact* cfg dict `HardwareManager` built its driver from, not a value independently re-derived that could theoretically diverge from an internal default if it ever changed.

**Historical analysis this enables:** `SELECT smu_name, dmm_name, relay_matrix_name FROM run_summary WHERE run_id = ?` answers "which instruments were used for this run"; joining `measurements`/`event_log` on `run_id` against that `run_summary` row answers "which hardware configuration produced these measurements"; the full row (battery snapshot + hardware snapshot + timing) answers "what test bench configuration existed when the run executed" -- all without a new table, via the Historical Results Viewer, `UI Preview Test`, or `ReportGenerator`, exactly as the battery-configuration snapshot already does.

**Future multi-group/multi-instrument alignment:** `relay_matrix_name`/`relay_matrix_resource` already vary per run based on whichever `relay_cfg` was actually connected -- when Group B/`MATRIX_NUMATO_202` (or a second SMU/DAQ for a future group) comes online, no schema change is needed; only the caller's cfg-resolution logic (already group-aware via `BATTERY_GROUPS`) needs to pass the correct `relay_cfg`/`smu_cfg`/`daq_cfg` into `HardwareManager`/`_hardware_snapshot_fields()`, which already exist as the single point of resolution.

**Not yet done / explicitly out of scope:** `TEMP_MODULE` identity (no driver wired in yet -- nothing to capture); Charge/Discharge/Cycle Battery hardware traceability (menu placeholders only, not implemented); a queryable multi-instrument-per-run model (not needed today, since exactly one of each role is ever connected per run).

## 23. Menu Restructuring Review (post `docs/EXECUTION_TREE_REVIEW.md` annotations)

Following a full execution-tree review of `test.py` (`docs/EXECUTION_TREE_REVIEW.md`), nine annotated architectural questions were reviewed and acted on. Each is documented below with the decision made and the rationale.

### 23a. NTC Sensor Acquisition -- DAQ Architecture

**Decision: implemented.** `test_sensors()` gained a new Test 6: a real DAQ-based NTC channel scan, iterating every `config/devices.py::BATTERY_CHANNELS` entry whose existing `enabled` flag is `True` and reading its existing `daq_ntc_ch` field via `hardware/daq.py::DAQ.read_channel()`, converting each reading with the existing `ntc_voltage_to_celsius()`. No new configuration variable was introduced -- `enabled`/`daq_ntc_ch` already existed per position (added during the Monitor Battery/hardware-traceability work) and are reused directly, satisfying "config-driven, no hardcoded channel list" without a duplicate source of truth. Tests 1-5 (pure NTC-thermistor math, no hardware) are unchanged -- this is additive, not a replacement, so the menu item still completes cleanly on a laptop with no DAQ attached (Test 6 reports a clean per-channel FAIL with reason in that case, never raises).

This IS the future DAQ acquisition architecture: temperature monitoring is expected to come entirely through this per-position DAQ NTC channel path, not through a separate module -- see 23b below.

### 23b. Test Temperature Module -- Retired as a Standalone Menu Entry

**Decision: retired from the top-level `MENU`, function kept.** The PXIe-4353 Temperature Module (`TEMP_MODULE`) has never had a thermocouple/RTD channel driver and none is planned now that 23a's DAQ path covers per-position battery temperature. `test_temperature_module()`/`_identify_temperature()` are NOT deleted -- `test_hardware_discovery()` (MENU item 4) still reports `TEMP_MODULE`'s presence/identity via `_identify_temperature()` unchanged, and the standalone function remains callable directly for one-off bring-up diagnosis. Only its own top-level `MENU` slot was removed, since it duplicated ground Hardware Discovery already covers and offered no Functional Validation (none is planned).

### 23c. Numato Relay Matrix -- Timing/Delay Review (no code change)

**Reviewed, no inconsistency found requiring a fix.** There is no hardcoded `time.sleep()`/dwell/settle timer anywhere in `test_relay_numato_matrix()`, `test_relay_matrix_scan()`, `test_relay_ethernet_test()`, or `test_relay_safety_selftest()` (confirmed by exhaustive search) -- the perceived difference in "wait time between switching" across these four tests is entirely a function of how many Telnet round trips each one issues per channel, which is intentional:

- **`test_relay_matrix_scan()`/`test_relay_safety_selftest()`** use the public 1-based `close()`/`open()` API, which by design (`hardware/relay_eth.py`'s mandatory safety sequence) re-runs force-all-off + verify-all-off + activate + verify-single + verify-all on **every single relay operation, for every channel** -- the most Telnet round trips per channel, and deliberately so: "never touch a relay without first forcing a known, verified all-off baseline" is a safety requirement, not an accidental inefficiency.
- **`test_relay_ethernet_test()`** uses the native 0-based primitives (`write()`/`write_all()`/`verify_all()`) directly, bypassing `close()`'s extra individual-verification step -- fewer round trips per channel, because this test's entire purpose is validating the native command layer independent of the safety wrapper above it.
- **`test_relay_numato_matrix()`** ("Relay 1 quick check") only ever touches one channel, so its total wall-clock time is naturally the shortest regardless of which API layer it uses.

**Recommendation: do not standardize.** Collapsing these to one uniform round-trip count would either weaken the safety-wrapper test's mandatory baseline-first behavior, or stop the native-primitive test from exercising the layer it exists to validate. The four tests intentionally exercise four different things (one channel via the safety wrapper, every channel via the safety wrapper, every channel via native primitives, and a scoped subset via the safety wrapper) -- differing speed is the correct, expected signature of that, not an inconsistency. No code was changed for this item.

### 23d. Test PXI Relay Matrix -- Future Reuse Architecture (no code change, no driver exists)

**Reviewed and documented; not implemented (no PXI relay hardware exists to validate against, and this project's testing philosophy is to never fake a check against hardware that isn't there).** The good news found during review: most of the "Numato relay validation suite" is **already** hardware-agnostic and would apply to a future PXI/`niswitch` relay driver with zero changes:

- `test_relay_matrix_scan()` and `test_relay_safety_selftest()` operate entirely through `hardware.relay_factory.RelayFactory.create(cfg)` and the generic `RelayBase` interface (`close()`/`open()`/`read()`/`open_all()`) -- neither references anything Numato-specific. Once a PXI relay driver class implements `RelayBase` and `RelayFactory.create()` gains a branch for its config `"type"` (e.g. `"pxi_switch"`), these two tests work against it unchanged, exactly as they already work identically against `MATRIX_NUMATO_201`/`MATRIX_NUMATO_202` today.
- Only `test_relay_ethernet_test()` (native 0-based Numato primitives: `write()`/`write_all()`/`verify_all()`) is genuinely Numato-protocol-specific and would NOT apply to a PXI relay -- a future PXI-native equivalent (if the `niswitch` driver exposes comparable native primitives) would need its own dedicated test, following the same "test the native layer independently of the generic `RelayBase` wrapper" pattern, not reusing this function's body.
- `_functional_relay_numato()`'s 4-option submenu shape (device-agnostic wrapper picking Identity vs. Functional Validation, then a menu of specific checks) is itself the reusable pattern -- a future `_functional_pxi_relay()` should mirror that shape (quick single-channel check / scoped matrix scan / native-primitive test / safety self-test) rather than inventing a new submenu style.

**Recommended path when PXI relay hardware/driver work begins:** (1) implement a `niswitch`-based driver class satisfying `RelayBase`, (2) add a `"pxi_switch"` (or similar) branch to `RelayFactory.create()`, (3) add a `PXI_RELAY_MATRIX_CONFIGS`-equivalent enumeration dict in `config/devices.py` (mirroring `NUMATO_RELAY_MATRIX_CONFIGS`), (4) `test_relay_matrix_scan()`/`test_relay_safety_selftest()` then work against it with **zero changes** to those two functions, (5) build a native-primitives test only if the `niswitch` API has an equivalent worth validating independently. `test_pxi_relay_matrix()`'s Identity Validation (`_identify_switch()`) and its "not yet implemented" Functional Validation remain exactly as-is until step 1 exists.

### 23e. Safety Monitor Workflow Simulator -- Development Reference Implementation

**Decision: implemented, and later enhanced into a full step-by-step operational walkthrough tool.** `test_safety_monitor()` (MENU item 11, "Test Safety Monitor (workflow simulator)") keeps its original 7 pure-logic unit tests (Part 1, unchanged) and, in Part 2, presents an interactive workflow-selection menu (`_select_safety_simulation_workflow()`: `1. Monitor Battery` / `2. Charge Battery` / `3. Discharge Battery` / `4. Cycle Battery` / `0. Skip`). Once a workflow is selected, `_run_workflow_walkthrough()` walks its full **operational sequence** step by step, pausing for the operator (Enter to continue) between every step -- not just the safety decisions, but every action a real implementation performs, in order: load configuration, resolve group/position/relay routing, close the relay, configure/enable the PSU, acquire a measurement, run the **real** `SafetyMonitor.check()`/`is_safe_to_switch_relay()` logic, update `ExecutionFrame`, store the measurement, evaluate phase transitions, and so on.

**This IS the development reference implementation for Monitor/Charge/Discharge/Cycle Battery.** A future developer implementing Charge/Discharge/Cycle Battery for real should be able to read `_charge_phase_steps()`/`_discharge_phase_steps()`/`_cycle_battery_walkthrough_steps()` (or run the simulator interactively) and map each displayed step directly onto production code -- the step lists themselves are written as that blueprint, not just illustrative filler. `_monitor_battery_walkthrough_steps()` mirrors the *already-implemented* `_run_monitor_battery()`/`MonitorBatterySequence.run()` exactly, so it also serves as a working example of "simulator step" <-> "real code" correspondence.

**Per-step display (`_render_and_check_step()`):**
```
------------------------------------------------------------
Workflow     : <workflow name>
Current Phase: <phase>
Current Step : <n>/<total>
Description  : <operational action, e.g. "Close relay 3">
------------------------------------------------------------
Voltage      : <simulated V, or N/A for a non-measurement step>
Current      : <simulated A, or N/A>
Temperature  : <simulated degC, or N/A>
Safety Evaluation: <the exact SafetyMonitor call and result, or "N/A" if this step performs no check>
Decision     : CONTINUE | ABORT -- <reason>
Next Action  : <what happens next>
[Note        : <e.g. "Development-only simulation -- NOT written to ...">]
```
A step that only describes an operational action (load config, resolve routing, update `ExecutionFrame`, evaluate a transition, ...) passes no voltage/current/temperature and neither check flag -- it still renders the full block (`N/A` fields, `Decision: CONTINUE`), since the walkthrough's point is showing the **entire** sequence, not only the steps that happen to run a safety check.

**Four workflows, one engine, no parallel framework:** every step is a plain dict (`phase`/`description`/`voltage_v`/`current_a`/`temp_c`/`run_safety_check`/`relay_switch_check`/`next_action`/`note`) consumed by the single `_run_workflow_walkthrough()` engine -- adding a fifth workflow or more steps to an existing one means adding dict entries, never a second engine.

- **Monitor Battery** (`_monitor_battery_walkthrough_steps()`, 16 steps) -- load config -> resolve group/position/relay routing -> close relay -> two full monitoring samples (acquire -> safety check -> update frame -> store) -> operator-requested stop -> open relay. Mirrors the real, already-implemented workflow. Expected: PASS.
- **Charge Battery** (`_charge_phase_steps()`, 16 steps) -- load config -> resolve relay routing -> close relay -> configure PSU limits -> enable output -> CC charge (acquire/check/update/store) -> evaluate CC/CV transition -> CV taper (acquire/check) -> evaluate cutoff -> disable PSU -> open relay. Reuses `Settings.CHARGE_VOLTAGE_V`/`CHARGE_CURRENT_A`/`CHARGE_CUTOFF_A` -- no new hardcoded constants. Expected: PASS.
- **Discharge Battery** (`_discharge_phase_steps()`, 14 steps) -- the discharge-shaped equivalent: close relay -> configure PSU sink limits -> enable sink -> CC discharge (acquire/check/update/store) -> continue discharge -> evaluate cutoff -> disable sink -> open relay. Reuses `Settings.DISCHARGE_CURRENT_A`. Expected: PASS.
- **Cycle Battery** (`_cycle_battery_walkthrough_steps()`, 31 steps) -- a full charge phase (`_charge_phase_steps(cycle_number=1)`) + a `TRANSITION` step (`cycle_count += 1`) + a discharge phase with `inject_fault=True`. Demonstrates charge -> transition -> discharge -> fault -> abort in one continuous walkthrough, reusing the charge/discharge step builders rather than a third copy of the sequence. Expected (and verified): **correctly aborts** at the discharge phase's "Run SafetyMonitor checks" step on the injected overtemperature reading -- reported as PASS ("correctly aborted"), since aborting on an unsafe reading is the desired behavior, not a failure.

**A real Settings inconsistency was surfaced while building the Discharge steps**: `Settings.DISCHARGE_CUTOFF_V` (3.0 V) is itself below `Settings.BAT_VOLTAGE_MIN` (3.5 V, the value `SafetyMonitor.check()` actually enforces) -- already tracked in `docs/TODO.md`. Using `DISCHARGE_CUTOFF_V` directly would make every discharge walkthrough always abort on Undervoltage, misrepresenting the tool as broken. The cutoff step instead stops at `max(DISCHARGE_CUTOFF_V, BAT_VOLTAGE_MIN) + 0.05` -- documented inline as a workaround, not a silent fix of the underlying Settings values (still tracked separately).

**Development-only, verified by inspection:** no step, at any point, imports or calls `HardwareManager`, `DataStorage`, `RelayFactory`, or any `hardware/*.py` driver -- every "Close relay"/"Configure PSU"/"Store measurement" step is a console message plus (where applicable) a call into the real `SafetyMonitor`, never an actual relay/instrument/database operation. Steps that would, in the real workflow, write to `measurements`/`event_log`/`run_summary`/`station_state` (e.g. "Store measurement") carry an explicit `note` stating this is a development-only simulation and nothing is written -- confirmed by grepping the entire simulator code path for any storage/hardware import, which returns nothing.

This is the first concrete deliverable of the "development and validation tool" planned ahead of deploying Charge/Discharge logic to real hardware -- the dict-driven step-list design is deliberately structured so a future iteration can add more injected-fault scenarios, or wire the same simulated values through to `ExecutionFrame`/`render_execution_frame()` for a full mock execution screen (see Section 23i's UI Test), without inventing a second simulator framework.

### 23f. Test Configuration -- Removed from MENU

**Decision: removed the standalone MENU entry; function unchanged.** `test_configuration()` already runs automatically inside `preflight_check()` before the menu is ever shown -- the standalone MENU entry only ever repeated that same check on demand. Per explicit review direction, the top-level entry was removed; `test_configuration()` itself is untouched and `preflight_check()`'s call to it is unaffected (config validation still gates startup exactly as before).

### 23g. Database Tools (replaces "Test SQLite (foundation)" + "Test Database Layer")

**Decision: implemented.** The two standalone top-level entries were consolidated into one new MENU item, **Database Tools** (`test_database_tools()`), with a 7-option submenu (same style as `_functional_relay_numato()`'s options-list pattern -- no new submenu framework):

1. View Latest Run (`run_summary`)
2. View Latest Event Log (`event_log`)
3. View Latest Measurements (`measurements`)
4. View Station State -- last execution (`station_state`)
5. Database Statistics -- row counts per table + file size
6. Run Storage Layer Self-Test -- the original `test_database()`, unchanged, temp DB
7. Run SQLite Foundation Self-Test -- the original `test_sqlite()`, unchanged, temp DB

Options 1-5 are new: read-only inspection of the **real** project database (`Settings.DATABASE_FILE` -- the same file Monitor Battery/Proto Test Execution actually write to), built entirely on `DataStorage`'s existing read methods (`get_last_run_summary()`/`get_recent_events()`/`get_measurements()`/`get_last_execution_state()`) -- no new read path, no new storage mechanism. A helper, `_open_real_storage_readonly()`, opens `DataStorage` against the real database file and returns `None` (printing a clear message) if the file doesn't exist yet, rather than creating one -- a "view" must never mutate the real database as a side effect of looking at it. Options 6-7 are the original functions, unchanged, simply relocated into this submenu instead of their own top-level slots (see 23h).

### 23h. Test Database Layer -- Merged into Database Tools

**Decision: implemented as part of 23g.** `test_database()` is no longer a standalone top-level MENU entry -- it is submenu option 6 under Database Tools, called with the exact same function, unchanged. This directly answers the review question: yes, it should be (and now is) a submenu within the consolidated database-tools area rather than a standalone entry.

### 23i. UI Test (replaces "Run All Tests")

**Decision: implemented.** "Run All Tests" (the `fn=None` MENU entry that aggregated every other menu item into one combined summary) was replaced with **UI Test** (`test_ui_preview()`), a hardware-and-database-free preview environment. Every option builds a demo `ExecutionFrame` via `ExecutionFrame.from_live()` with hardcoded, clearly-labeled sample values, then renders it through the exact same `render_execution_frame()` Proto Test Execution/Monitor Battery use live -- this previews the real renderer against static data, it is never a second UI implementation:

1. Proto Test Execution screen (demo data)
2. Monitor Battery screen (demo data)
3. Charge/Discharge/Cycle Battery screens -- reported as "not yet implemented" (no real workflow exists to preview)
4. Historical Results Viewer style screens -- reported as "not yet implemented" (no Historical Results Viewer has been built yet; faking one would misrepresent a feature that doesn't exist)

No `HardwareManager`, no `DataStorage`, no relay, no SMU/DMM/DAQ import anywhere in this code path -- verified by inspection and by the mocked smoke test that ran every MENU entry with `input()` returning `"0"`/a chosen index.

**Trade-off, called out explicitly:** removing "Run All Tests" also removes the one-button "run every menu item and summarize" regression convenience this project's session-verification workflow has used throughout Milestone II. `_dispatch_menu_choice()`'s `fn is None` aggregation branch was removed alongside it (no MENU entry has `fn=None` anymore) rather than left as unreachable dead code -- consistent with `docs/EXECUTION_TREE_REVIEW.md`'s "Remove candidates" finding about not leaving unreachable code behind. If a "run everything and summarize" capability is wanted again in the future, `run_section()`/`print_summary()` (still used by every individual MENU entry) remain available to rebuild it.

### 23j. Updated Final Menu Structure

```
1.  Run Main Test
2.  Proto Test Execution (infrastructure validation, no battery)
3.  Startup Device Validation (config/devices.py -- no hardware I/O)
4.  Hardware Discovery (connectivity + identification, config-driven)
5.  Test SMU (PSU)
6.  Test DMM
7.  Test DAQ
8.  Test Numato Relay Matrix (Ethernet)
9.  Test PXI Relay Matrix
10. Test Sensors (NTC)                          -- now includes a DAQ-based NTC scan (Test 6)
11. Test Safety Monitor (workflow simulator)     -- now includes 4 workflow simulations
12. Database Tools                               -- NEW, replaces items 14+15 below
13. UI Test (demo screens -- no hardware, no database)  -- replaces "Run All Tests"
0.  Exit
```

Removed from the top level: **Test Temperature Module** (retired -- 23b; function still callable, still covered by Hardware Discovery), **Test Configuration** (removed -- 23f; function still called by `preflight_check()`), **Test SQLite (foundation)** and **Test Database Layer** (merged into Database Tools -- 23g/23h), **Run All Tests** (replaced by UI Test -- 23i).

## 24. Relay Safety Verification Pattern

**Root cause (from the relay architecture compliance review, `docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`):** every real relay path in the codebase converged on one shared function, `hardware/relay_eth.py::NumatoRelayMatrix._force_all_off_and_verify()`, for "force everything off, then verify" -- but that function went straight to forcing off without ever first reading and recording the relay bank's pre-existing state. The consequence: if the bank was already in an unexpected state (a relay left active from an earlier fault, a stale session, a hidden routing issue) when an operation began, that fact was silently corrected by the force-off step and never surfaced anywhere -- diagnostically invisible, even though the eventual outcome (all relays confirmed off) was the same either way.

**The agreed pattern:**
```
Read All -> Verify Current Status -> Force All OFF -> Verify All OFF -> Action -> Verify Action
```

**Fix -- solved centrally, in the one function every real path already shares:**

- `NumatoRelayMatrix.check_current_relay_state(context: str = "")` (new) -- implements steps 1-2. Calls `read_all()` (which now also records the result on `self.last_known_mask`, a new instance attribute -- "store the queried state if useful," per the same principle later applied to the PSU pattern in Section 25) and logs a WARNING naming exactly which channels were unexpectedly active if the mask is non-zero, or an INFO confirming the bank was already all-off. Never raises, never changes relay state -- a diagnostic checkpoint, not a gate. A failed read is logged and the caller proceeds to force-off regardless (fail-safe: the force-off-and-verify step that always follows is the real safety net, not this read).
- `NumatoRelayMatrix._force_all_off_and_verify()` (existing, modified) -- now calls `check_current_relay_state()` first, then performs the unchanged `write_all(0)`/`verify_all(0)` (steps 3-5).

**Why this brings every real path into compliance simultaneously, with zero per-caller changes:** `open()`, `close()`, and `open_all()` all call `_force_all_off_and_verify()` internally -- and every real relay usage path in the codebase calls one of those three:

| Path | How it reaches `_force_all_off_and_verify()` |
|---|---|
| `MonitorBatterySequence.run()` | `relay.close(relay_address)` |
| `ProtoTestSequence.run()` | `relay.close(relay_n)` / `relay.open(relay_n)` |
| Legacy `BatteryTestSequence.run()` | `relay.close(ch)` / `relay.open(ch)` |
| `HardwareManager` startup/shutdown/`atexit` | `relay.open_all()` |
| `SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()` | `relay_matrix.open_all()` |
| `test.py` "Relay 1 quick check" | `relay.close(1)` / `relay.open(1)` |
| `test.py` Matrix Scan (group-scoped) | `relay.close(ch)` / `relay.open(ch)` / `relay.open_all()` (on cancel) |
| `test.py` Safety Self-Test | `relay.close(ch)` / `relay.open(ch)` / `relay.open_all()` |

**The one deliberate exception: `test.py::test_relay_ethernet_test()` (RelayEthernetTest).** This test exercises the native command layer (`write_all()`/`write()`/`verify_all()`) directly, by design, to validate that layer independently of the `close()`/`open()` safety wrapper -- it does not call `_force_all_off_and_verify()`. To ensure this path is not left bypassing steps 1-2 (per requirement 6, "verify no new relay path bypasses the shared safety sequence"), it now calls the same shared `relay.check_current_relay_state(context=...)` explicitly, once per relay index, immediately before its own native `write_all(0)` -- reusing the identical logic, not a duplicate implementation.

**Verified (mocked socket, no real hardware):** a `close(1)` call against a bank with relay 3 already active issues, in order: `relay readall` (read all -- reports mask=0x04, logs a WARNING naming channel 3) -> `relay writeall 00` (force off) -> `relay readall` (verify off) -> `relay on 0` (action) -> `relay read 0` (individual verify) -> `relay readall` (bulk verify) -- the full 8-step pattern, confirmed command-by-command.

**Compliance status:** every real, production/validation-reachable relay path -- previously "Partially Compliant" (steps 3-8 only) per `docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md` -- is now **Fully Compliant** with the agreed 6-stage pattern. The three non-production/scaffolded abstractions flagged in that review (`hardware/relay_serial.py::SerialRelay`, `hardware/simulated.py::SimulatedRelay`, `hardware/relay_matrix.py::RelayMatrix`) were **not** modified -- they remain unreachable via current configuration (`RELAY_SERIAL_CONFIGS == {}`, `SimulatedRelay` not wired into `RelayFactory`, `RelayMatrix` unreferenced dead code) and were out of scope for this fix, which targeted the shared production mechanism specifically.

## 25. PSU Safety Verification Pattern

Extends the same philosophy (fix the one shared mechanism, not each caller) to PSU/SMU output control.

**The agreed pattern, enable side:**
```
Query PSU State -> Verify Current State -> Force Output OFF -> Query PSU State ->
Verify Output OFF -> Configure PSU -> Enable Output -> Query PSU State -> Verify Output ON
```
**Shutdown side:**
```
Disable Output -> Query PSU State -> Verify Output OFF
```

**What already existed (`hardware/smu.py::SMU`, before this change):** `output_disable()` (COMMAND), `verify_output_disabled()` (READBACK+VERIFY, fail-safe: treats "disconnected" as safe/True and a failed query as unsafe/False), and `emergency_output_off(reason)` (the existing public, non-raising Disable -> Query -> Verify shutdown reflex -- already implements the shutdown-side pattern above exactly, unchanged by this work). `source_dc_voltage_point()` (the one real, implemented output-enabling method in the codebase today -- `output_enable()`/`set_charge_mode()`/`set_discharge_mode()` remain TODO placeholders, see `docs/TODO.md`) already did Configure -> Enable -> Query+Verify ON (via `_verify_config_readback("output_enabled", True, readback_output_enabled)`) and Disable -> Query -> Verify OFF in its `finally` teardown -- but, like the relay driver before Section 24's fix, it went straight to configuring/enabling on every call with no pre-check that the PSU was actually starting from a verified-off baseline.

**New (mirroring Section 24's relay methods exactly):**

- `SMU.query_output_state() -> bool | None` -- pure READBACK of `session.output_enabled`, no safety-gate fail-safe assumption (distinct from `verify_output_disabled()`, whose "assume unsafe on failure" default is correct for a safety gate but wrong for a diagnostic record). Returns `None` (not `True`/`False`) when the state genuinely isn't known -- no session, or the query failed. Stores the result on `self.last_known_output_state` (new instance attribute -- "store the queried PSU state internally," e.g. exactly the `output_enabled`/`psu_output_state` naming the requirements suggested).
- `SMU.check_current_output_state(context: str = "") -> bool | None` -- steps 1-2: calls `query_output_state()`, logs a WARNING if output is unexpectedly already enabled, INFO if already confirmed off, WARNING if the query itself failed. Never raises. Mirrors `check_current_relay_state()` exactly.
- `SMU.force_output_off_and_verify(context: str = "") -> bool` -- steps 1-2 + 3-4-5 together: calls `check_current_output_state()`, then `output_disable()` + `verify_output_disabled()` (existing, unchanged). Returns `True`/`False`, never raises -- same non-raising contract as `verify_output_disabled()`/`emergency_output_off()`; callers decide whether `False` is fatal for their own workflow.
- `source_dc_voltage_point()` (modified) -- now calls `force_output_off_and_verify(context="source_dc_voltage_point pre-check")` as its very first action (before the session is configured at all), raising `SMUError` immediately if a safe baseline cannot be verified. This one change covers every real caller today: test.py's SMU Functional Validation and `ProtoTestSequence` (Proto Test Execution) both go through this single method, the same "fix centrally" principle used for the relay driver.

**Verified (mocked NI-DCPower session, no real hardware):** a `source_dc_voltage_point()` call against a session already reporting `output_enabled=True` triggers the pre-check WARNING ("PSU output NOT off before this operation"), forces off + verifies, then proceeds through configure -> enable -> verify-ON exactly as before, with `self.last_known_output_state` correctly tracking `True` (already-on, detected) then `True` again (re-enabled, confirmed) across the call. A second run starting from a genuinely-off session confirms the normal (no pre-existing fault) path and the `finally` teardown still leave the session `output_enabled == False` afterward, unchanged from before this work.

**Compliance status:** the one real PSU-output-enabling path in the codebase (`source_dc_voltage_point()`, and therefore SMU Functional Validation and Proto Test Execution) now implements the full agreed pattern. `output_enable()`/`set_charge_mode()`/`set_discharge_mode()` remain unimplemented placeholders (see `docs/TODO.md`) -- when Charge/Discharge Battery are built on top of them, they should call `force_output_off_and_verify()` first (mirroring `source_dc_voltage_point()`) rather than reinventing the pre-check, so this pattern extends to them automatically once written that way.

## 26. PSU/Relay Cross-Validation (Future)

**Evaluated per Part 3 of the PSU/relay safety review -- deliberately NOT implemented yet, extension points only.**

**The problem this would catch:** an instrument's *reported* state (`session.output_enabled`, or a relay's `readall` bitmask) is a claim from the instrument's own firmware/driver, not an independent physical measurement. It is possible (firmware bug, stuck relay contact, a session attribute that silently didn't commit) for the reported state to disagree with reality:
- PSU reports ON, but a DMM (or the SMU's own `session.measure()` ADC readback) shows ~0 V.
- PSU reports OFF, but voltage is still physically present.
- (Analogously, though considered lower-priority for now -- see below -- a relay reports a channel closed but the routed signal doesn't reflect it.)

**Why PSU, not relay, is the priority extension point:** the relay driver's `readall`/`read` commands are already a direct, positive hardware confirmation of the switch contact itself -- there is no intermediate "reported vs. physical" gap the way there is for a PSU, where `output_enabled` is an internal instrument attribute that may not perfectly track the actual analog output under all fault conditions. This is why `cross_validate_output_state()` (below) was added to `SMU`, and no relay equivalent was added -- not an oversight, a judgment that the relay side's existing `verify_single()`/`verify_all()` readback IS already close to this kind of cross-validation (it reads the bank's actual reported switch state, which is the same signal the switch operation itself commanded), whereas the PSU's `output_enabled` flag and the battery's actual voltage are two genuinely independent signals today.

**Extension point added:** `SMU.cross_validate_output_state(measured_v: float = None, measured_i: float = None)` -- a stub method that currently only raises `NotImplementedError` with a message pointing back to this section. It is never called anywhere in the codebase today. It exists so that when cross-validation IS built, there is one obvious place to put it (comparing `self.last_known_output_state`/`query_output_state()` against a caller-supplied external measurement) rather than requiring a redesign of `SMU`, `source_dc_voltage_point()`, or the PSU safety sequence in Section 25.

**Where a future implementation would plug in, without redesigning anything above:**
- Inside `source_dc_voltage_point()`, after the existing runtime measurement (`measured_v`/`measured_i`, already captured from `session.measure()`) -- call `cross_validate_output_state(measured_v=measured_v, measured_i=measured_i)` there instead of only logging them as "informational context," once a real accuracy tolerance/threshold is decided.
- In `test_control/proto_test_sequence.py::ProtoTestSequence`, which already takes an independent DMM reading (`during_hold`) while the SMU's own output is active -- that DMM value is exactly the kind of external measurement `cross_validate_output_state()` is meant to accept once wired in.
- In a future Charge/Discharge Battery implementation, alongside `force_output_off_and_verify()`/`output_enable()`, using either the DMM (if present) or the SMU's own ADC readback as the independent signal.

**Explicitly not done:** no threshold/tolerance was chosen, no DMM wiring was added, and `cross_validate_output_state()` is not called from anywhere -- this section documents where the hook belongs, per the review's explicit instruction not to implement external validation yet.

## 27. Interruptible Wait Mechanism

**Root cause (from `docs/TIMING_ANALYSIS.md`, the timing/delay/settling-time review):** several real dwells in this codebase held hardware energized for their full configured duration with **no cancellation checkpoint at all** inside the wait itself:

- `hardware/smu.py::SMU.source_dc_voltage_point()`'s `hold_s` sleep -- PSU output enabled/sourcing for the entire dwell. Used by `ProtoTestSequence`, where `hold_s` = `Settings.PROTO_TEST_DWELL_S`, a value explicitly marked `TEMPORARY -- shortened for the first physical rack validation run. Restore to 120.0 (~2 min) once the quick end-to-end check passes` -- at that restored value, an operator's Ctrl+C during a dwell could go unnoticed for up to ~2 minutes. **This was the single highest-priority finding of the timing review.**
- `test_control/charge_cycle.py::ChargeCycle.run()`/`discharge_cycle.py::DischargeCycle.run()`'s pre-loop `STABILIZATION_S` (5.0s) sleep -- PSU output already enabled at that point, and (see the latent bug below) this sleep was OUTSIDE the `try/finally` that guards `emergency_output_off()`.

A related, smaller gap: `ChargeCycle`/`DischargeCycle`'s per-sample `time.sleep(dt)` and `MonitorBatterySequence.run()`'s `time.sleep(sample_interval_s)` already had a cancellation checkpoint at the top of the *next* loop iteration, bounding worst-case latency to roughly one sample period (~1s / ~2s respectively) -- acceptable, but inconsistent with a fully interruptible wait now that one exists.

**Fix -- one reusable primitive, `utils/cancellation.py::interruptible_sleep(duration_s, token=None, poll_interval_s=0.2)`:**

- `token=None` (the default): sleeps the full `duration_s` via a single `time.sleep()` call -- **byte-for-byte identical to before this function existed.** Every existing caller that doesn't pass a token sees zero behavior change.
- `token` given: checks `check_cancellation(token)` before the first sleep slice (so a cancellation already requested before the wait even begins is caught immediately, never sleeping at all) and again before every subsequent slice, sleeping in increments of at most `poll_interval_s` (default `0.2`s, matching the polling granularity `hardware/relay_eth.py::_recv_until()` already uses internally) until either `duration_s` has fully elapsed -- **identical total wait time to a plain `time.sleep(duration_s)`, verified** -- or a cancellation is detected, raising `OperationCancelledError` immediately and bounding worst-case latency to ~`poll_interval_s` instead of the full duration.
- `duration_s <= 0`: returns immediately, matching `time.sleep()`'s own behavior -- every caller passing `hold_s=0.0` (the default, e.g. SMU Functional Validation) is unaffected.

**Where it was wired in (every real dwell identified by the timing review):**

| Location | Before | After |
|---|---|---|
| `SMU.source_dc_voltage_point()` | `time.sleep(hold_s)`, no `token` parameter existed | `interruptible_sleep(hold_s, token=token)` -- new `token=None` parameter added to the method signature; `ProtoTestSequence.run()` now passes its own `token` through |
| `ChargeCycle.run()` | `time.sleep(self.s.STABILIZATION_S)` | `interruptible_sleep(self.s.STABILIZATION_S, token=token)` |
| `ChargeCycle.run()`'s sampling loop | `time.sleep(dt)` | `interruptible_sleep(dt, token=token)` |
| `DischargeCycle.run()` | Same two spots as `ChargeCycle` | Same fix, identical shape |
| `MonitorBatterySequence.run()` | `time.sleep(sample_interval_s)` | `interruptible_sleep(sample_interval_s, token=token)` |

**A real latent bug was found and fixed while wiring this in, in `ChargeCycle`/`DischargeCycle`:** the `STABILIZATION_S` sleep was located OUTSIDE the `try/finally` block that calls `smu.emergency_output_off()`. This was harmless before this change (a plain `time.sleep()` cannot raise), but the moment it became interruptible, a cancellation during stabilization would have raised `OperationCancelledError` *before* the `try` block was ever entered -- skipping the PMU shutdown entirely and leaving output energized. Fixed by moving the `try/finally` to start immediately after `output_enable()`, so it now wraps the stabilization wait *and* the sampling loop. Verified: a mocked `ChargeCycle.run()` cancelled 0.1s into a 5.0s `STABILIZATION_S` window raises `OperationCancelledError` after ~0.2s (not 5.0s) and `smu.emergency_output_off()` is confirmed called.

**`SMU.source_dc_voltage_point()`'s exception handling was also updated** to add `except OperationCancelledError: raise` (mirroring the existing `except SMUStateVerificationError: raise` clause) *before* the generic `except Exception as e: raise SMUError(...)` -- without this, a cancellation raised by `interruptible_sleep()` inside the method would have been silently wrapped into a generic `SMUError`, indistinguishable from a real fault to `ProtoTestSequence.run()`'s `except OperationCancelledError` handler. The `finally` block (disable + verify output OFF) still runs on every exit path, including a mid-hold cancellation.

**Reusability for future Charge/Discharge/Cycle Battery workflows:** `interruptible_sleep()` is a pure timing primitive with no hardware dependency -- it lives in `utils/cancellation.py` alongside `CancellationToken`/`check_cancellation()`, not inside any driver or sequence class, specifically so a future `ChargeBatterySequence`/`DischargeBatterySequence`/`CycleBatterySequence` (or an extension of the existing `ChargeCycle`/`DischargeCycle`) can call it exactly the same way `ProtoTestSequence`/`MonitorBatterySequence` do today, with no new pattern to invent. As documented in `test_control/proto_test_sequence.py`'s own module docstring and the Safety Monitor Simulator (`docs/architecture.md` Section 23e, the designated development-reference blueprint for these future workflows), any real Charge/Discharge dwell should be built on `interruptible_sleep()` from the start.

**Explicitly not changed:** `hardware/relay_eth.py::_recv_until()`'s internal 0.2s socket-timeout poll loop was deliberately left untouched -- it waits for a single atomic Telnet command/response, and per this project's established rule (`utils/cancellation.py`'s own module docstring: "Checkpoints must only ever be placed BETWEEN atomic hardware operations -- never inside a relay activate/verify sequence"), making a mid-command wait interruptible would risk leaving relay state less certain, not safer. `CHARGE_TIMEOUT_S`/`DISCHARGE_TIMEOUT_S` (the 7200s max-runtime ceilings) are unaffected -- they are a loop-exit *condition*, not a sleep, and were already checked every iteration.

**Verified:** `interruptible_sleep()` unit-tested in isolation (no-token behavior identical to `time.sleep()`; token-present-but-never-cancelled preserves full duration; token-cancelled-mid-wait raises in ~`poll_interval_s`, not the full duration). Each of the three real call sites re-tested with a mocked cancellation firing mid-dwell: `source_dc_voltage_point(hold_s=10.0)` cancelled after ~0.2s (not 10s), output still confirmed OFF via the `finally` block; `ChargeCycle.run()` cancelled 0.1s into a 5.0s `STABILIZATION_S` window, `emergency_output_off()` confirmed called; `MonitorBatterySequence.run()` cancelled ~0.3s into a 2.0s `sample_interval_s` window, safe-cancel shutdown confirmed. A normal (non-cancelled) `hold_s=0.6` run confirmed to take the full ~0.6s, not less -- normal timing behavior is unchanged. Full non-hardware MENU regression (13 entries) re-run with no failures.

## 28. BATTERY_CONFIGS -> SafetyMonitor Integration

**Root cause (from `docs/SAFETY_MONITOR_BATTERY_LIMITS_REVIEW.md`):** `config/devices.py::BATTERY_CONFIGS` (per-battery-type `voltage_max_v`/`voltage_min_v`/`max_charge_current_a`/`max_discharge_current_a`/`max_temp_c` for HUB and SB) existed and battery selection already flowed through the UI, but `SafetyMonitor.check()` and `ChargeCycle`/`DischargeCycle` only ever read the global `Settings.BAT_*`/`CHARGE_*`/`DISCHARGE_*` constants -- `SafetyMonitor` was battery-type-blind. Quantified impact: SB could be commanded/charged at ~6.25x-12.5x its configured charge/discharge current limits before the global ceiling (sized for HUB) would trip; HUB had ~1.9x headroom versus its own configured charge limit.

**Fix -- additive, backward-compatible, following the same optional-parameter pattern as every other safety fix in this project (Relay/PSU Safety Verification Patterns, Interruptible Wait Mechanism):**

- `SafetyMonitor.__init__(settings, battery_cfg: dict = None)` and a new `set_battery_limits(battery_cfg: dict = None)` method store an optional active `BATTERY_CONFIGS[...]` entry.
- Four private resolvers -- `_voltage_max()`, `_voltage_min()`, `_temp_max()`, `_current_max(mode: str = None)` -- prefer the corresponding `battery_cfg` field when a battery is set, else fall back to `Settings.BAT_VOLTAGE_MAX`/`BAT_VOLTAGE_MIN`/`BAT_TEMP_MAX_C`/`BAT_CURRENT_MAX` unchanged. `_current_max()` additionally takes a `mode` ("charge"/"discharge") to pick `max_charge_current_a` vs `max_discharge_current_a`; if `mode` is omitted while a `battery_cfg` is active, it resolves to the `min()` of both fields, so an unspecified mode is never accidentally more permissive than either real limit.
- `SafetyMonitor.check(voltage_v, current_a, temp_c, mode: str = None)` gained the `mode` parameter (default `None`, zero effect when no `battery_cfg` is set) and now calls the four resolvers instead of reading `self.s.BAT_*` directly.
- `is_safe_to_switch_relay()` and `emergency_stop()`/`safe_cancel_shutdown()` are **unchanged** -- the near-zero-current relay-switch guard is a universal safety rule, not battery-capacity-dependent, and the e-stop/cancel sequences reference no limits at all.
- `ChargeCycle.run(channel, data_collector, token=None, battery_cfg: dict = None)` and `DischargeCycle.run(..., battery_cfg: dict = None)`: when `battery_cfg` is given, the commanded PSU setpoint is now resolved from it (`battery_cfg["max_charge_current_a"]`/`["voltage_max_v"]` for charge; `["max_discharge_current_a"]`/`["voltage_min_v"]` for discharge) instead of `Settings.CHARGE_CURRENT_A`/`CHARGE_VOLTAGE_V`/`DISCHARGE_CURRENT_A`/`DISCHARGE_CUTOFF_V`; each cycle calls `self.safety.set_battery_limits(battery_cfg)` once at the start of `run()` and passes `mode="charge"`/`mode="discharge"` into every `self.safety.check(...)` call; the end-of-charge/end-of-discharge threshold comparisons (`v >= ...`/`v <= ...`) now use the resolved voltage instead of the hardcoded `Settings` constant. `battery_cfg=None` (the default) is byte-for-byte the prior behavior.
- `BatteryTestSequence.run(channels=None, token=None, battery_cfg: dict = None)` threads `battery_cfg` through to both `self.charge.run()`/`self.discharge.run()` for completeness, even though this legacy sequence is no longer reachable from the live MENU (Section 23).

**Deliberate scope boundary -- `Settings.CHARGE_CUTOFF_A` stays global:** the CV-taper tail-current detection threshold has no `BATTERY_CONFIGS` equivalent field. Inventing one was not requested and was not needed to close the quantified gap (the commanded CC current and CV voltage -- the values that actually determine how hard a cell is driven -- are now battery-specific; the tail-current *detection* threshold is a measurement-noise-floor concern, not a per-battery protection limit). This is a reasoned boundary, not an oversight.

**Safety Monitor Simulator updated (`test.py`, Section 23e's designated blueprint):** a new `_select_safety_simulation_battery()` menu (listing each `BATTERY_CONFIGS` entry with capacity/max-charge/max-discharge, plus a skip option) runs after workflow selection in `test_safety_monitor()`; the resolved `battery_cfg` is applied via `monitor.set_battery_limits(battery_cfg)` and forwarded into `_charge_phase_steps()`/`_discharge_phase_steps()`/`_cycle_battery_walkthrough_steps()`, which now derive their simulated setpoints from `battery_cfg` instead of hardcoded `Settings.CHARGE_*`/`DISCHARGE_*`/`BAT_TEMP_MAX_C` constants when a battery is selected (falling back to the prior global-constant simulation when skipped). `_render_and_check_step()`/`_run_workflow_walkthrough()` gained a `mode` field so the simulated `SafetyMonitor.check()` calls exercise the same charge/discharge-aware resolution as the real cycles. Side benefit: the Discharge simulation's pre-existing `max(DISCHARGE_CUTOFF_V, BAT_VOLTAGE_MIN)+margin` workaround (needed only because global `DISCHARGE_CUTOFF_V` (3.0 V) sat below global `BAT_VOLTAGE_MIN` (3.5 V) -- tracked in `docs/TODO.md`) is no longer needed for a selected battery, since `battery_cfg["voltage_min_v"]` (3.0 V for both HUB/SB) is now what's actually enforced, exactly matching `DISCHARGE_CUTOFF_V` with no gap.

**Verified:** direct unit checks confirm SB now trips Overcurrent above its own 0.08 A (charge) / 0.16 A (discharge) limits while passing just below them, and HUB trips above its own 0.525 A charge limit while passing at 0.5 A -- neither was previously distinguishable from the shared global 1.0 A ceiling. `battery_cfg=None` reproduces the exact prior global-Settings-only pass/fail behavior. All four Safety Monitor Simulator workflows (Monitor/Charge/Discharge/Cycle) re-run for both HUB and SB and for "skip battery selection" -- all PASS, including the Cycle walkthrough's expected overtemperature abort. Full non-hardware regression (`test_sqlite`, Part 1 SafetyMonitor unit tests) re-run with no failures.

---

## 29. SMU Implementation Status -- Re-Verified From Source

**Purpose of this section:** a prior architecture review's conclusions about `hardware/smu.py` were re-verified directly against the current source (not assumed from that review) before any further Charge/Discharge work began. The findings are unchanged -- this section exists so the next reader doesn't have to re-derive them, and so this document stops referring to SMU sourcing capability in the abstract ("still a TODO") without a concrete, current method-by-method status.

| Method | Status (verified against current source) | Real hardware validated? |
|---|---|---|
| `set_charge_mode(current_a, voltage_limit_v)` | **Stub.** Logs args at DEBUG; body is `# TODO: configure nidcpower for CC-CV source`. No `nidcpower` calls. | No -- nothing to validate. |
| `set_discharge_mode(current_a, voltage_limit_v)` | **Stub.** Logs args at DEBUG; body is `# TODO: configure nidcpower for current sink`. No `nidcpower` calls. | No. |
| `output_enable()` | **Stub.** Body is `# TODO: self._session.initiate()` plus a log line -- does not actually enable anything on the session. | No. |
| `output_disable()` | **Fully implemented, real.** `self._session.output_enabled = False`, raises `SMUError` on failure. Load-bearing -- every safety shutdown path depends on this being real. | Yes -- exercised on every real rack run to date via every `finally`/`emergency_output_off()` call. |
| `measure()` | **Stub.** Body is `# TODO: return self._session.measure(...)`; unconditionally returns `{"voltage_v": 0.0, "current_a": 0.0}` regardless of session state. | No. |

**What this means concretely:** the SMU driver can safely turn output OFF and verify it (real, proven, exercised on hardware), but cannot yet turn output ON in a battery charge/discharge sense, configure CC-CV/CC-sink mode, or return a real measurement. The one real *sourcing* capability in the codebase, `source_dc_voltage_point()` (Section 12.6/25), is a separate, narrow, bench-only DC voltage point -- it does not call, and is not built from, `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()`.

**Missing functionality:** real NI-DCPower configuration for CC-CV (charge) and CC-sink (discharge) modes; a real `session.initiate()` to actually start sourcing/sinking; a real `session.measure()` call for live V/I telemetry; the COMMAND->READBACK->VERIFY discipline already proven in `source_dc_voltage_point()` has not yet been applied to these four methods -- that is new work, not a copy-paste of the existing pattern.

**Risks:** current-sink configuration for discharge is unvalidated territory -- nothing in this codebase has ever configured an NI-DCPower session as a positive-voltage current sink; this is a materially different mode from the voltage-source path `source_dc_voltage_point()` already exercises. `output_enable()` doing nothing today means any caller that assumes it starts sourcing is silently wrong until this is implemented.

**Does this block ChargeSequence?** Yes, materially. A `ChargeSequence`/`DischargeSequence` built on `BatteryOperationSequence` can be fully scaffolded (relay routing, confirmation screen, traceability, `ExecutionFrame` rendering, `SafetyMonitor` wiring) without these methods, but cannot charge/discharge anything, and critically cannot be validated end-to-end until they are real. **SMU Functional Validation (Section 32) is the correct next step, not ChargeSequence orchestration first** -- see the revised roadmap in Section 35.

## 30. Discharge Cutoff Policy (Target vs. Safety Floor)

**Do not treat `Settings.DISCHARGE_CUTOFF_V` and `Settings.BAT_VOLTAGE_MIN` (or a battery's `voltage_min_v`) as conflicting values.** They answer two different questions:

- **Discharge target = cycle objective.** `DISCHARGE_CUTOFF_V` (global) / a battery's own target, if one is ever introduced, is *where a discharge cycle intends to stop* -- a protocol choice.
- **Battery minimum voltage = absolute safety limit.** `BAT_VOLTAGE_MIN` (global) / `BATTERY_CONFIGS[type]["voltage_min_v"]` (per battery) is the floor the cell must never be driven below, regardless of what any cycle's target says.

**The battery safety limit always has priority.** `DischargeCycle.run()` (`test_control/discharge_cycle.py`) now resolves both values and clamps:

```python
target_v = self.s.DISCHARGE_CUTOFF_V   # cycle objective, not the safety floor
floor_v = self.s.BAT_VOLTAGE_MIN        # absolute safety floor
if battery_cfg is not None:
    floor_v = battery_cfg.get("voltage_min_v", floor_v)
    target_v = battery_cfg.get("voltage_min_v", target_v)
cutoff_v = max(target_v, floor_v)       # floor always wins
```

The system must never discharge below the active floor. This clamp is a defensive measure, not the primary safety mechanism -- `SafetyMonitor.check(..., mode="discharge")` remains the authoritative abort path on every sample regardless of where the cutoff/target sits; the clamp exists so the EOD-detection target itself can never be configured to sit below the floor in the first place.

**Why the historical "contradiction" (`DISCHARGE_CUTOFF_V`=3.0 V < `BAT_VOLTAGE_MIN`=3.5 V) was never actually a bug in the battery-aware path:** once a battery type is selected, `battery_cfg["voltage_min_v"]` (3.0 V for both HUB and SB today) supplies *both* the target and the floor identically, so `cutoff_v` already equalled the enforced safety limit exactly. The apparent contradiction only existed in the global-Settings-only fallback (no `battery_cfg`), which the new clamp now also resolves correctly: `max(3.0, 3.5) = 3.5` -- the more conservative value wins, rather than silently using the un-conservative 3.0 V target as if it were also the floor.

**Battery type must remain explicitly selected -- never inferred.** This is unchanged, existing, correct behavior, re-confirmed here as a formal policy rather than an implicit convention: `config/devices.py::BATTERY_CHANNELS` carries no `battery_type` field (wiring-only), and nothing in the codebase infers battery type from group, position, channel, or relay address. `test.py::_select_battery_type()` is the sole source of `battery_cfg`, and it must remain an explicit operator choice for any future Charge/Discharge/Cycle workflow, exactly as Monitor Battery already established. Limits always come from `BATTERY_CONFIGS[selected_type]`, never guessed from wiring.

**`config/settings.py` comments updated** to state this policy inline at `DISCHARGE_CUTOFF_V`/`BAT_VOLTAGE_MIN`'s definitions, and `test.py::test_configuration()`'s cross-check (Configuration self-test) no longer reports `DISCHARGE_CUTOFF_V < BAT_VOLTAGE_MIN` as a WARN/misconfiguration -- it now reports it as informational (`_ok`), explaining that the floor takes priority by design. The Safety Monitor Simulator's discharge fallback (`test.py::_discharge_phase_steps()`, no-`battery_cfg` branch) was updated from an ad hoc `max(DISCHARGE_CUTOFF_V, BAT_VOLTAGE_MIN) + 0.05` margin workaround to the exact same `max(DISCHARGE_CUTOFF_V, BAT_VOLTAGE_MIN)` clamp `DischargeCycle.run()` now applies for real, so the simulator (Section 23e's designated development reference) continues to mirror real behavior precisely, per its own stated purpose.

## 31. Telemetry Source Strategy (DMM Now, DAQ Future)

**Current official policy:** ChargeSequence/DischargeSequence (and Cycle Sequence) development must use the **DMM** as the telemetry source, mirroring Monitor Battery (Section 20a) -- not `DAQ.read_all_batteries()`. This is a deliberate decision, not an oversight, and it must not be silently reversed by a future implementer reaching for "the more correct" DAQ path.

**Why:** as of this review, `DAQ.read_all_batteries()`/`verify_zero_current()` remain unimplemented stubs (`hardware/daq.py`, confirmed from source -- see Section 8.2b/Section 29's sibling verification), per-position DAQ channel routing (`BATTERY_CHANNELS[i]["daq_voltage_ch"]`/`daq_current_ch"]`) is not approved/finalized against real NI-MAX wiring (Section 20a, `docs/TODO.md`), and Monitor Battery already established a working, validated precedent for using the DMM as a stand-in voltage source rather than blocking on DAQ mapping work.

**Consequence for Charge/Discharge/Cycle development:** this work must not be blocked by DAQ mapping/wiring confirmation. Build ChargeSequence/DischargeSequence against the DMM (single shared instrument per group, exactly as `MonitorBatterySequence` already does), accepting the same known limitation Monitor Battery carries today -- `current_a` is not available from the DMM (voltage-only), so a real Charge/Discharge implementation will need its own plan for current telemetry (candidates: the SMU's own runtime measurement, once `measure()` is implemented for real -- see Section 29 -- rather than waiting on `DAQ.read_all_batteries()`).

**Future state, unchanged from the existing roadmap:** DAQ remains the intended final per-position telemetry architecture (voltage + current + NTC temperature, `BATTERY_CHANNELS[i]`'s three channel fields) once channel mapping is confirmed against real wiring. Migrating Monitor Battery and any Charge/Discharge/Cycle implementation built in the interim onto DAQ is tracked as a `[MUST]`-tagged, but explicitly *not currently blocking*, item in `docs/TODO.md`.

## 32. SMU Functional Validation (No Load) -- New Milestone

**Purpose:** validate SMU hardware capability -- once `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()` are implemented for real (Section 29) -- before building Charge/Discharge Sequence on top of them. This is a software/hardware integration validation step, deliberately performed **without a battery and without a load**, exactly as Proto Test Execution (Section 18) validated the relay/PSU/DMM pipeline with no battery before Monitor Battery was built.

**Validation scope:**
- `set_charge_mode()` / `set_discharge_mode()` -- configuration accepted and read back correctly (mirroring `_verify_config_readback()`'s existing pattern in `source_dc_voltage_point()`).
- `output_enable()` -- output state transitions to ON, verified via readback (`query_output_state()`), not assumed.
- `output_disable()` -- already proven; re-exercised as part of this milestone's full sequence for completeness.
- `measure()` -- returns a real ADC reading (voltage/current), not the current fixed-zero stub.
- State verification -- the same COMMAND->READBACK->VERIFY discipline used everywhere else in this codebase (Sections 10, 12.6b, 25).
- Safety shutdown behavior -- `emergency_output_off()`/`force_output_off_and_verify()` still correctly recover to a safe state after these new methods are exercised.

**What CAN be validated with no load:**
- Mode configuration (CC-CV parameters, CC-sink parameters accepted by the session without error).
- Output state transitions (OFF -> ON -> OFF, each verified by readback).
- Readback behavior (commanded vs. echoed configuration attributes matching, within the existing attribute-round-trip tolerance).
- Safety shutdown behavior (the PSU Safety Verification Pattern, Section 25, still holds with the new methods in the loop).
- Command verification (every COMMAND has a corresponding READBACK+VERIFY step, no step trusts a bare command to have succeeded).

**What CANNOT be validated without a load** -- these are explicitly out of scope for this milestone and must not be claimed as validated by it:
- Real current flow (no load means no real current path to measure against a known reference).
- CC operation (constant-current regulation behavior requires an actual electrical load).
- CV operation (constant-voltage regulation/taper behavior requires an actual electrical load).
- EOC verification (end-of-charge current-taper detection needs a real charging profile).
- EOD verification (end-of-discharge voltage-droop detection needs a real discharging profile).
- Charge/discharge performance (capacity, energy, rate accuracy -- all require a real cell or an equivalent electronic load).

**Sequencing implication:** SMU Functional Validation (this milestone) must pass before ChargeCycle/DischargeCycle logic is harvested into a real `ChargeSequence`/`DischargeSequence` (Section 33), and real battery/load validation of EOC/EOD/CC/CV behavior is a separate, later milestone that follows Charge/Discharge Sequence implementation -- not a prerequisite to it, and not satisfied by this one.

## 33. ChargeCycle / DischargeCycle Harvest Plan

**Scope:** `test_control/charge_cycle.py::ChargeCycle` and `test_control/discharge_cycle.py::DischargeCycle`, re-read in full for this review (unchanged since the prior architecture review -- confirmed from source, not assumed). Classified below as KEEP / MIGRATE / REMOVE / RETIRE. **No large-scale migration performed as part of this review** -- this is a documented plan only, per the explicit instruction not to migrate ahead of the roadmap (Section 35).

| Piece | Classification | Notes |
|---|---|---|
| EOC logic: `if v >= voltage_limit_v and abs(i) <= CHARGE_CUTOFF_A: return True` | **KEEP / MIGRATE** | Real, working, combined CV-voltage + tail-current threshold check. Port as-is into a future `ChargeSequence`. `CHARGE_CUTOFF_A` deliberately stays a global Settings constant (Section 28's documented scope boundary) -- preserve that. |
| EOD logic: `if v <= cutoff_v: return True` | **KEEP / MIGRATE** | Simple voltage-cutoff check, correct for CC-only discharge (no taper phase). Now uses the target-vs-floor-clamped `cutoff_v` (Section 30) -- carry the clamp forward into the migrated version, not just the bare comparison. |
| CV taper detection | **KEEP, flag as coarse** | Today it is a same-sample combined check (`v >= voltage_limit_v and abs(i) <= CHARGE_CUTOFF_A`), not a distinct "entered CV phase" state. Fine to carry forward as-is for a first `ChargeSequence`; if phase-accurate `ExecutionFrame.phase_detail` reporting (CC vs CV) is wanted later, that is a small, separate REFACTOR (track an explicit `constant_voltage_entered` flag) -- not required to harvest the existing logic. |
| PSU sequencing: `set_*_mode()` -> `output_enable()` -> sampling loop -> `emergency_output_off()` in `finally`, with the `try/finally` starting immediately after `output_enable()` (the fixed ordering from Section 27) | **KEEP / MIGRATE** | Correct and already timing-hardened. Port this exact sequencing and exception-safety shape into `ChargeSequence`/`DischargeSequence`, built through `BatteryOperationSequence.run_guarded()` rather than a bespoke try/finally (see below). |
| Emergency shutdown logic (`smu.emergency_output_off()` in `finally`, unconditional on every exit path) | **KEEP / MIGRATE** | Must be preserved exactly. This is the one piece of Section 27's fix that must not regress during migration. |
| `battery_cfg` threading pattern (`battery_cfg.get(...)` resolving commanded current/voltage, `safety.set_battery_limits(battery_cfg)`, `mode="charge"/"discharge"` passed into every `safety.check()`) | **KEEP / MIGRATE** | Already proven (Section 28); maps directly onto how `ChargeSequence`/`DischargeSequence` should resolve setpoints. |
| Reusable state machine | **N/A -- none exists to harvest** | Neither cycle uses `test_control/state_machine.py::StateMachine` (confirmed unused/unreferenced by both, same finding as the prior review). Both are plain `while True` sampling loops with inline threshold checks. Keep this shape in the migrated version -- do not force it through the dead `StateMachine` class. |
| `daq.read_all_batteries()` call for telemetry | **REMOVE (from the migrated version)** | Per Section 31's DAQ Strategy decision, `ChargeSequence`/`DischargeSequence` must source telemetry from the DMM, not `DAQ.read_all_batteries()` (currently a stub returning fixed zeros regardless). This is the one piece of `ChargeCycle`/`DischargeCycle`'s logic that should NOT be carried forward as-is. |
| `t_c = None  # TODO: read from NTC` | **MUST FIX in the migrated version, not deferred again** | Both cycles currently never read temperature -- the overtemperature safety check silently no-ops (`temp_c is not None and ...` in `SafetyMonitor.check()`). This is the single most safety-relevant piece of debt in scope for Charge/Discharge Sequence work; do not carry the stub forward unexamined. |
| Direct construction inside `TestExecutor.__init__` (`self._charge = ChargeCycle(hw.smu, hw.daq, self._safety, settings)`) | **REMOVE / RETIRE** | This orchestration shell -- `TestExecutor` constructing `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence` directly -- is legacy-path coupling. A `ChargeSequence`/`DischargeSequence` should be constructed the same way `MonitorBatterySequence` is today (by `test.py`, given already-resolved hardware handles), not by a `TestExecutor`-equivalent. |
| `BatteryTestSequence` coupling: `charge.run()` then `discharge.run()` always called back-to-back per channel inside one loop | **REMOVE / RETIRE** | The new Charge Sequence and Discharge Sequence must be independently invokable (the menu already implies this: separate "Charge Battery"/"Discharge Battery"/"Cycle Battery" entries) -- not hardwired to always run charge immediately before discharge in a single call. `CycleSequence` (Section 35, roadmap step 8) is where composition belongs, as a thin wrapper, not baked into each individual sequence. |
| `data_collector.record(channel, sample)` call shape (raw `DataStorage.record()`, not `record_measurement()`/`_render_frame()`) | **REMOVE / RETIRE** | Replace with `BatteryOperationSequence`'s established pattern (`storage.record_measurement()`, `self._render_frame()`, `run_guarded()`) -- the legacy `record()`/narrow `measurements` write path predates the Milestone II schema and UI work and should not be extended further. |
| `TestExecutor`/`BatteryTestSequence`/legacy workflow infrastructure as a whole | **RETIRE** (after Charge/Discharge/Cycle Sequence are validated on the new stack -- see Section 35 roadmap step 9, not immediately) | Do not delete yet -- `main.py` still depends on this chain and it must remain the working production path until its replacement is validated end-to-end on real hardware. |

**Migration strategy:** do not merge the two stacks wholesale. Build `ChargeSequence`/`DischargeSequence` as new classes on `BatteryOperationSequence` (preserving it as the target execution architecture, per explicit instruction), porting only the KEEP/MIGRATE logic above -- not the surrounding orchestration, which `run_guarded()`/`_render_frame()`/`hardware_for_group()` already replace, as `MonitorBatterySequence` already demonstrates. Only once Charge/Discharge/Cycle Sequence are validated against real hardware should `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence`/`TestExecutor` be retired and `main.py` repointed (Section 35, roadmap step 9).

## 34. ProtoTestSequence Review Findings

Re-examined against current source for this review (unchanged since the prior review's findings -- confirmed, not assumed).

**1. Does it duplicate `BatteryOperationSequence` behavior? Yes.** `ProtoTestSequence` (`test_control/proto_test_sequence.py`) does **not** subclass `BatteryOperationSequence` -- it is a standalone class with its own constructor and its own hand-rolled `try`/`except OperationCancelledError`/`except SafetyViolationError`/`except RelayError`/`except Exception`/`else` block inside `run()`, structurally identical to `BatteryOperationSequence.run_guarded()`'s four-branch pattern but implemented independently.

**2. What parts are duplicated specifically:**
- The exact 4-exception-type handling shape (`OperationCancelledError` -> `safe_cancel_shutdown`; `SafetyViolationError`/`RelayError`/generic `Exception` -> `emergency_stop`), each branch logging, writing `storage.record_execution_state()` + `storage.finish_run_summary()`, then re-raising -- this is `run_guarded()`'s logic, hand-duplicated rather than reused.
- Inline `ExecutionFrame.from_live()` construction + `render_execution_frame()` call, built directly inside `run()`'s per-relay loop rather than via `BatteryOperationSequence._render_frame()` (which additionally centralizes pulling `recent_measurements`/`recent_events` from storage -- `ProtoTestSequence` does this inline too, duplicating that fetch pattern as well).
- `hardware_traceability_messages()`/`start_run_summary()` traceability logging at the top of `run()` -- conceptually the same pattern `test.py::_run_monitor_battery()` uses, implemented separately here rather than through a shared helper.

**3. Is migration recommended? Yes, eventually** -- for the same reason `MonitorBatterySequence`/`MonitorBatteryScanSequence` were migrated onto `BatteryOperationSequence`: `ProtoTestSequence` is a second, independently-maintained copy of the same skeleton, one release behind its own sibling classes, meaning any future fix to the shared exception/shutdown/rendering pattern (as already happened twice -- the Relay+PSU Safety Verification Pattern, Section 24-25, and BATTERY_CONFIGS integration, Section 28) must be manually reapplied here if it isn't kept in sync.

**4. Is migration urgent? No.** `ProtoTestSequence` is validated, working, real-hardware-tested infrastructure code (Section 18) that is not part of any workflow currently under active development. Migrating it carries real regression risk (its own docstring states explicitly: "Relay sequencing, SMU sourcing configuration/timing, DMM measurement logic, dwell timing, and the safety-exception handling ... are byte-for-byte the same calls, in the same order" as the pre-migration version -- i.e. it was deliberately *not* touched during the last architecture pass specifically to preserve this guarantee) for a benefit (avoiding future duplicate-fix risk) that has not yet materialized into an actual missed fix. No large-scale refactor is warranted right now, per this review's explicit scope.

**5. Risks of leaving it unchanged:** the primary risk is exactly the failure mode named above -- a future safety/traceability fix applied to `BatteryOperationSequence.run_guarded()` or `_render_frame()` could be forgotten here, silently leaving Proto Test Execution one fix behind its siblings. This is a process risk (remember to check `ProtoTestSequence` when touching the shared pattern), not a current functional defect. Recommendation: track this migration as low-priority future cleanup (see `docs/TODO.md`), not urgent work, and note it explicitly any time `run_guarded()`/`_render_frame()` changes so the parallel implementation isn't silently missed.

## 35. Revised Roadmap Priority (This Review)

Supersedes the priority ordering implied by earlier "Recommended next milestone" notes in `docs/MILESTONES.md`. **`BatteryOperationSequence` remains the target execution architecture** -- nothing below changes that; this only reorders and grounds the near-term sequence in the SMU status verified in Section 29.

1. **Review SMU implementation** -- done, this review (Section 29).
2. **Complete missing SMU functionality** -- `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()`, following the COMMAND->READBACK->VERIFY discipline and PSU Safety Verification Pattern (Section 25) already proven in `source_dc_voltage_point()`.
3. **Perform SMU Functional Validation (no load)** -- Section 32's new milestone; validates mode configuration, output state transitions, readback, safety shutdown, and command verification, explicitly without a battery or load.
4. **Validate behavior/results** -- confirm step 3's results against expected instrument behavior before building anything on top of it.
5. **Harvest ChargeCycle/DischargeCycle logic** -- per Section 33's plan (KEEP/MIGRATE the EOC/EOD/CV-taper/PSU-sequencing/emergency-shutdown logic; REMOVE/RETIRE the DAQ telemetry call and legacy orchestration coupling).
6. **Implement ChargeSequence** -- on `BatteryOperationSequence`, DMM telemetry (Section 31), real NTC temperature wiring (no longer deferred).
7. **Implement DischargeSequence** -- on `BatteryOperationSequence`, applying the Discharge Cutoff Policy (Section 30) from the start.
8. **Implement CycleSequence** -- a thin composition of Charge Sequence -> rest -> Discharge Sequence, not a third independent state machine; this is where charge-then-discharge composition belongs (Section 33 flags the old `BatteryTestSequence`-style hardwired coupling as something to explicitly avoid re-introducing here).
9. **Legacy retirement** -- once Charge/Discharge/Cycle Sequence are validated on real hardware, retire `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence`/`TestExecutor` and repoint (or retire) `main.py`. Not before.

---

## 36. SMU Charge/Discharge Implementation + ChargeSequence/DischargeSequence

**Executes roadmap steps 2, 3, 6, and 7 from Section 35.** Re-verifies nothing (the Section 29/30/31/32/33/34 findings still hold as documented) -- this section records what changed once implementation actually began.

### Step 2: SMU functions implemented

`hardware/smu.py::SMU.set_charge_mode()`/`set_discharge_mode()`/`output_enable()`/`measure()` are now real, extending the existing driver rather than redesigning it:

- **`_configure_current_source(current_a, voltage_limit_v, context)`** (new, private) -- the one shared configuration path both `set_charge_mode()` (positive `current_a`) and `set_discharge_mode()` (`current_a` negated internally -- a current SINK, never a negative-voltage source) call, since charge and discharge are the same NI-DCPower `DC_CURRENT` configuration differing only in sign. Reuses `force_output_off_and_verify()` (Section 25) as its first action and `_verify_config_readback()` (Section 12.6b) for the post-`commit()` readback -- no new safety/verification helpers invented, both existing ones applied to `DC_CURRENT` instead of `DC_VOLTAGE`.
- **`output_enable()`** -- COMMAND (`output_enabled = True`, `commit()`, `session.initiate()`, left open rather than scoped to a `with` block, since a caller's sampling loop must be able to call `measure()` repeatedly afterward) -> READBACK+VERIFY (`query_output_state()`, raising `SMUError` if not confirmed ON).
- **`output_disable()`** (extended, not replaced) -- now attempts `session.abort()` before `output_enabled = False`, swallowing the case where nothing was initiated (the common case for every existing caller, including `source_dc_voltage_point()`'s own `with initiate():` block, which already aborts itself on exit). Backward compatible: the abort attempt is a no-op for every prior caller.
- **`measure()`** -- COMMAND (`session.measure(VOLTAGE)`/`session.measure(CURRENT)`, the exact call `source_dc_voltage_point()` already uses) -> READBACK -> VERIFY (`math.isfinite()` on both, mirroring `hardware/dmm.py::DMM.measure_dc_voltage()`'s own finite check) -> return `{"voltage_v", "current_a"}`. Raises `SMUError` on a non-finite reading or if called before `output_enable()` -- never silently returns the previous stub's fixed `{"voltage_v": 0.0, "current_a": 0.0}`.

**Safety implications:** every new method follows "unknown/failed state = unsafe" exactly as the rest of this driver -- a failed configuration, a failed enable-verify, or a non-finite measurement all raise, never substitute a default. `_configure_current_source()` re-establishes a verified-off baseline before every configuration, so a stale prior configuration (e.g. switching from charge to discharge on the same channel) can never leak into the new one unverified.

**Limitations (see Section 32 for the full no-load/with-load boundary):** these methods have been validated without a battery or load only. Real CC/CV regulation behavior, EOC/EOD accuracy under an actual electrical load, and charge/discharge current accuracy remain unvalidated and are explicitly out of scope for this step.

### Step 3/4: SMU Functional Validation (no load) -- performed

Validated directly against `nidcpower`'s own `simulate=True` mode (the real NI-DCPower driver runtime, not a hand-rolled mock) -- this environment has the NI-DCPower runtime installed, so `set_charge_mode()`/`output_enable()`/`measure()`/`output_disable()`/`set_discharge_mode()`/`emergency_output_off()` were exercised through the actual, unmodified production code path, not a substitute.

**Confirmed:** configuration + readback + verification (commanded vs. readback `current_level`/`voltage_limit` match); output state transitions (OFF -> ON -> OFF, each independently verified via `query_output_state()`/`verify_output_disabled()`); `measure()` returns real ADC values distinguishing charge (positive current) from discharge (negative current, confirming the sink direction); `force_output_off_and_verify()`'s pre-check genuinely engaged (the simulated session initialized with output already reporting enabled, and the pre-check correctly detected and corrected it -- a real exercise of the safety net, not a hypothetical); `emergency_output_off()` confirmed working; `measure()` correctly raises `SMUError` (not a silent bad value) when called against a non-running session.

**One real finding, resolved as a test-environment artifact, not a code defect:** `nidcpower`'s default simulated instrument model is unipolar (`current_level` domain strictly positive) -- a negative `current_level` (discharge/sink) was rejected the first time, against that default model. Re-tested against a simulated PXIe-4141 (a real, bipolar production card model, via `driver_setup`) and confirmed negative `current_level` configures, reads back, and measures correctly. `SMU.connect()` does not pass `driver_setup` when `self._simulate` is set, so this default-model mismatch will recur for anyone using this project's own `simulate: True` config path for future dev-mode testing -- **not fixed in this session** (out of the requested scope), but worth a small future fix (pass `driver_setup={"Model": ...}` derived from `cfg["model"]` when simulating) so simulate-mode testing exercises a representative model automatically. Does not affect real hardware: every configured production SMU (`PRIMARY_SMU`/PXIe-4141, `HIGH_POWER_SMU`/PXIe-4139, `AUX_SMU_1`/`AUX_SMU_2`/PXI-4130) is a bipolar card.

**Not validated (explicitly out of scope, unchanged from Section 32):** real current flow into an actual load, CC/CV regulation behavior, EOC/EOD accuracy, charge/discharge performance/capacity.

**No major blocker found** -- implementation proceeded to steps 5-7 below.

### Step 5 (harvest) + Steps 6/7: ChargeSequence / DischargeSequence implemented

**New files:** `test_control/charge_sequence.py::ChargeSequence`, `test_control/discharge_sequence.py::DischargeSequence` -- both subclass `BatteryOperationSequence` (preserved, unmodified, as the target execution architecture), following `MonitorBatterySequence`'s exact shape (constructor, `run_guarded()`, `_render_frame()`, `complete()`).

**Harvested from `ChargeCycle`/`DischargeCycle` unchanged in shape** (EOC: `v >= voltage_limit_v and abs(i) <= CHARGE_CUTOFF_A`; EOD: `v <= cutoff_v`; PSU sequencing with the `try/finally` starting immediately after `output_enable()`; `battery_cfg`-driven setpoint resolution + `safety.set_battery_limits()` + `mode="charge"/"discharge"` on every `safety.check()`). **Not carried forward:** `daq.read_all_batteries()` (replaced by DMM voltage + the SMU's own `measure()` for current -- see below) and the `TestExecutor`/`BatteryTestSequence` construction/orchestration shell (replaced by direct construction in `test.py`, exactly as `MonitorBatterySequence` already is).

**Telemetry (Section 31's DAQ Strategy applied):** `battery_voltage` is the DMM's independent reading (`dmm.measure_dc_voltage()`); `battery_current` is the SMU's own ADC readback (`smu.measure()`) -- the only real current signal available without DAQ. Both are additionally recorded as `smu_measured_v`/`smu_measured_i`/`dmm_measured_v` (the same `measurements` columns `ProtoTestSequence` already populates), so a future DAQ migration has this session's data to cross-check against. `battery_temp` remains `None` (NTC not wired -- unchanged, tracked gap).

**Discharge Cutoff Policy (Section 30) applied from the start:** `DischargeSequence.run()` resolves `target_v`/`floor_v` from `battery_cfg["voltage_min_v"]` (today's `BATTERY_CONFIGS` defines only one voltage per type) and clamps `cutoff_v = max(target_v, floor_v)`, logging a warning if a target is ever configured below the floor -- the floor always wins.

**Battery type required, never inferred:** both `run()` signatures take `battery_cfg: dict` as a required parameter (no `battery_cfg=None` fallback, unlike the legacy cycles) -- `test.py` resolves it via the existing `_select_battery_type()` before either sequence is constructed.

**Timeout wiring improved over the legacy cycles:** a charge/discharge timeout now raises `NIPXITimeoutError` (instead of `ChargeCycle`/`DischargeCycle`'s `return False`, which `docs/TODO.md` already flagged as discarded/unwired by `BatteryTestSequence`/`TestExecutor`), so it flows through `run_guarded()`'s existing generic-exception handling -- relay-open, PMU-off, and `run_summary`/`event_log` finalization all happen on timeout now, reusing existing machinery rather than adding a parallel timeout-specific shutdown path.

**Wired into `test.py`:** new `_run_charge_or_discharge()` (shared skeleton, factored once since Charge/Discharge Battery differ only in sequence class/event-log source/confirmation-screen limit line) plus `_run_charge_battery()`/`_run_discharge_battery()`, called from `run_main_test()`'s menu choices 2/3 (previously "not yet implemented" prints). Uses `hardware_for_group()`, `_confirm_operation()`, the same traceability `event_log` sequence, `CancellationToken`/SIGINT handling, and `HardwareManager`/`DataStorage` construction as `_run_monitor_battery()` -- no new workflow shape, no `TestExecutor`/`BatteryTestSequence` involved. `daq` is deliberately NOT a required hardware role for this workflow (unlike Monitor Battery Scan) -- Charge/Discharge Battery must not be blocked by an unassigned or unapproved DAQ.

**Verified:** `py_compile` clean on all touched/new files. Mocked end-to-end smoke tests (real `DataStorage`/`SafetyMonitor`, mocked SMU/DMM/relay): `ChargeSequence.run()` reaches EOC and returns `True`, `run_summary` finalized `COMPLETED`/`PASS`; `DischargeSequence.run()` reaches EOD and returns `True`, same finalization; a forced overcurrent condition correctly raises `SafetyViolationError`, triggers `safety.emergency_stop()` (`smu.emergency_output_off()` + `relay.open_all()`), and finalizes `run_summary` as `SAFETY_VIOLATION`/`FAIL` -- confirming the safety-abort path works identically to every other `BatteryOperationSequence` subclass. Not validated: real hardware, real battery/load behavior (unchanged scope boundary from Section 32).

**Remaining before real hardware use:** NTC temperature is still not wired (`t_c = None`); `BATTERY_CONFIGS` voltage/current limits are still unconfirmed placeholders pending datasheet confirmation; no physical rack validation of either sequence has been performed (mocked-hardware validation only, matching the same maturity stage Monitor Battery was at before its own physical rack validation). `CycleSequence` (charge -> rest -> discharge composition) remains unimplemented -- roadmap step 8.

**Remaining safety architecture status:** with this fix, `SafetyMonitor`/`ChargeCycle`/`DischargeCycle` are now battery-type-aware end to end, closing the last gap identified by `docs/SAFETY_MONITOR_BATTERY_LIMITS_REVIEW.md`. No further significant safety-architecture blocker is known before implementing a real Charge Battery workflow; remaining items (e.g. NTC temperature wiring, `cross_validate_output_state()`'s stub status per Section 26) are pre-existing, separately tracked, and not blocking.

---

## 37. Post-Implementation Validation Review -- ChargeSequence/DischargeSequence

A thorough, adversarial validation pass over the Section 36 implementation, performed before adding any new functionality. Re-verified everything from current source, not from Section 36's own claims. Two real, confirmed defects were found and fixed; the architecture review found no duplication, no legacy coupling, and no bypass of `hardware_for_group()`/explicit battery selection.

### Architecture review (Phase 1) -- clean

Confirmed by direct inspection: `ChargeSequence`/`DischargeSequence` import only `BatteryOperationSequence`, `SafetyMonitor`, `utils.cancellation`, `utils.errors` -- no `TestExecutor`/`BatteryTestSequence` reference anywhere. `test.py::_run_charge_or_discharge()` resolves hardware exclusively via `config/devices.py::hardware_for_group()` (no direct `SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/`DMM_CONFIGS` lookup), and battery type is exclusively operator-selected via `_select_battery_type()` -- never inferred from channel/group/position/relay. `config/devices.py` remains the only hand-authored device/battery config. No new architectural layer was introduced.

### Bug 1 (confirmed, fixed): relay never opened on successful completion

**Finding:** `ChargeSequence.run()`/`DischargeSequence.run()` closed the relay at the start of their guarded function, but neither the function itself, `BatteryOperationSequence.run_guarded()` (which only opens relays via `safety.emergency_stop()`/`safe_cancel_shutdown()` on the four *exception* paths), nor `BatteryOperationSequence.complete()` (which only does `record_execution_state`/`finish_run_summary`/`log_event` -- confirmed from source, no relay call) ever opened the relay on the *success* path. A charge or discharge that reached EOC/EOD normally left the relay closed indefinitely -- inconsistent with every other real relay path in this codebase (legacy `BatteryTestSequence.run()`'s `else: self.relay.open(ch)`, `ProtoTestSequence.run()`'s identical `else:` clause, `MonitorBatteryScanSequence`'s explicit open/close per position) and with the relay-isolation principle documented throughout (`docs/RELAY_SAFETY_COMPLIANCE_REVIEW.md`, `MonitorBatteryScanSequence`'s "only one relay ever energized at a time is structural"). This answers Phase 3 question 2 ("Can a relay remain selected after failure?") more sharply than asked -- it could remain selected after *success*, which is worse, since an operator has no reason to suspect anything is wrong.

**Fix:** both sequences now `self.relay.open(relay_address)` immediately after their inner `try/finally` (which confirms PMU output OFF) completes on the EOC/EOD success path, with its own `event_log` entry ("Relay N deactivated -- charge/discharge complete"). Every failure/cancellation path is unaffected -- those already force-open every relay via `safety.emergency_stop()`/`safe_cancel_shutdown()`'s `relay_matrix.open_all()`.

**Verified:** mocked smoke test asserts `relay.open(relay_address)` is called exactly once on a successful EOC/EOD run; a separate forced-overcurrent test confirms `relay.open()` (singular) is NOT called on the abort path (only `relay.open_all()`, via `emergency_stop()`, as before) -- no double-handling introduced.

### Bug 2 (confirmed, fixed): discharge SMU compliance voltage was set to the EOD cutoff, not a ceiling

**Finding (the more serious of the two):** `DischargeSequence.run()` called `self.smu.set_discharge_mode(current_a=current_a, voltage_limit_v=cutoff_v)` -- passing the low EOD cutoff/safety floor (~3.0 V) as the SMU's compliance `voltage_limit`. Confirmed directly against `nidcpower`'s real driver (not assumed): the default `compliance_limit_symmetry` is `SYMMETRIC`, meaning a `DC_CURRENT` session's `voltage_limit` bounds the terminal voltage to **+/-voltage_limit**, not a one-sided floor. A real battery starts discharge near its `voltage_max_v` (e.g. 4.2 V for HUB/SB) -- well outside a +/-3.0 V compliance window. Had this shipped unmodified, the SMU would have sat in voltage compliance (unable to actually sink the commanded discharge current) for virtually the entire discharge, only able to sink freely once voltage happened to fall inside +/-3.0 V -- by which point EOD (`v <= cutoff_v`) would already have triggered on the same sample. **This would have silently invalidated every real CC discharge test** -- the SMU would never have actually performed constant-current discharge as commanded.

This defect was inherited unchanged from the harvested `DischargeCycle.run()` (same `voltage_limit_v=cutoff_v` call), which could never have exposed it, since `set_discharge_mode()` was a no-op stub until this session's predecessor implemented it for real. The no-load SMU Functional Validation (Section 32/36) also could not have caught it -- no-load validation never establishes a nonzero starting terminal voltage, so the compliance mismatch never manifests without a real (or realistically modeled) battery attached. Every mocked smoke test run so far also missed it, since mocking `smu.measure()`'s return value bypasses real compliance behavior entirely. Found only by reasoning through NI-DCPower's actual compliance semantics and confirming the default symmetry mode against the real driver.

**Fix:** `DischargeSequence.run()` now passes `battery_cfg["voltage_max_v"]` as the SMU compliance ceiling (mirroring how `set_charge_mode()`'s `voltage_limit_v` is already the CV ceiling, i.e. the same kind of bound, not a floor) -- a new local `compliance_voltage_v`, kept explicitly distinct from `cutoff_v` (still used, unchanged, only for the EOD-detection comparison and as the input to the target/floor clamp). `hardware/smu.py::SMU.set_discharge_mode()`'s and `_configure_current_source()`'s docstrings (written in the prior session with the incorrect "safety-floor compliance limit" characterization) were corrected to state the real requirement plainly, so a future reader/implementer doesn't reintroduce the same conflation.

**Verified:** mocked smoke test asserts `smu.set_discharge_mode()` is called with `voltage_limit_v == battery_cfg["voltage_max_v"]`, not `cutoff_v`; the compliance-window arithmetic itself (`+/-voltage_max_v` contains the real battery's full discharge range, `+/-cutoff_v` does not) was confirmed directly against `nidcpower`'s real `simulate=True` driver, not assumed.

---

### Safety review (Phase 3) -- answers

1. **Can hardware remain energized unintentionally?** No PMU-energized case found (every exit path -- success, timeout, safety violation, cancellation, unexpected error -- ends in a verified `emergency_output_off()`, either the sequence's own `finally` or `run_guarded()`'s `safety.emergency_stop()`/`safe_cancel_shutdown()`, both idempotent-safe to call twice). The relay-left-closed gap (Bug 1) was real but is not "energized" in the PMU-output sense -- the SMU output was already confirmed off; the relay simply stayed connected to a de-energized output. Fixed regardless, since it violates the isolation principle.
2. **Can a relay remain selected after failure?** No -- every failure/cancellation path already force-opens every relay via `open_all()`. (It *could* remain selected after **success**, until Bug 1's fix.)
3. **Can PSU output remain enabled after failure?** No confirmed case -- `emergency_output_off()` is called on every abnormal exit, and its own internal READBACK+VERIFY means a `False` return is loudly logged CRITICAL rather than silently assumed safe.
4. **Is shutdown behavior consistent?** Yes, and redundantly so by design -- both `ChargeSequence`/`DischargeSequence`'s own local `finally` and `run_guarded()`'s exception-branch `safety.emergency_stop()` independently call `emergency_output_off()` on any abnormal exit (the same accepted double-call pattern already present in legacy `ChargeCycle`/`DischargeCycle` + `BatteryTestSequence`).
5. **Hidden failure paths?** One found and fixed (Bug 2) -- a "successful-looking" failure mode where the code runs without raising any exception, but the underlying electrical behavior would have been wrong (SMU in compliance, not truly doing CC discharge). This is the most dangerous class of bug in this kind of system: it produces plausible logs and a `PASS` result while not doing what it claims.

### Traceability review (Phase 4) -- confirmed working, no gaps found

`event_log`: full traceability sequence (Run started -> Operation selected -> Battery selected -> capacity -> group -> position -> configuration snapshot -> hardware assignment -> per-instrument "in use" messages -> operator confirmation) confirmed to complete entirely before `sequence.run()` is invoked (i.e. before the first relay close), identical in ordering to `_run_monitor_battery()`. `run_summary`: battery-config snapshot (`battery_type`/`battery_voltage_max_v`/`battery_voltage_min_v`/`battery_charge_current_limit_a`/`battery_discharge_current_limit_a`/`capacity_ah`) and hardware-identity snapshot (`smu_*`/`dmm_*`/`daq_*`/`relay_matrix_*`) both populated via `start_run_summary()` before hardware activation; `finish_run_summary(stop_reason=..., result=...)` confirmed correct for both the success path (`COMPLETED`/`PASS`, via `complete()`) and the safety-abort path (`SAFETY_VIOLATION`/`FAIL`, via `run_guarded()`). `measurements`: `record_measurement()` calls include `test_type`/`channel`/`relay`/`phase_detail`/`voltage_v`/`current_a`/`temp_c` plus `smu_measured_v`/`smu_measured_i`/`dmm_measured_v` every sample -- confirmed schema-compatible by successful real (non-mocked) `DataStorage` writes during validation, not assumed. `station_state`: `record_execution_state()` called on entry (`"ACTIVE"`) and on every terminal outcome via `run_guarded()`/`complete()`. No missing traceability found.

### SMU review (Phase 5) -- see Bug 2 above for the compliance-polarity finding

Beyond Bug 2, `set_charge_mode()`/`output_enable()`/`measure()` re-confirmed correct: NI-DCPower usage matches the documented COMMAND->READBACK->VERIFY pattern, `output_enable()`'s `session.initiate()` correctly left open (not scoped to a `with` block) for repeated `measure()` calls, `output_disable()`'s new `session.abort()` call correctly precedes `output_enabled = False` and is safely swallowed when nothing was initiated. Charge/discharge polarity confirmed correct (`current_a` positive for charge, negated for discharge -- verified against `nidcpower`'s real driver, both directions measured correctly in Section 36's original validation). Weakness identified (not a bug, a documentation/config gap): `SMU.connect()` still does not pass `driver_setup` when simulating, so simulate-mode testing keeps hitting an arbitrary (and possibly unipolar) default model -- unchanged from Section 36, still not fixed, still low priority.

### New finding: PRIMARY_SMU's real current rating may not cover BATTERY_CONFIGS' commanded currents

**Not a code bug -- a hardware/configuration-level risk surfaced by this review, requiring a wiring/hardware decision, not a silent code fix.** `config/devices.py::BATTERY_GROUPS["A"]["smu"]` is `"PRIMARY_SMU"` (`PXI_SLOTS[5]`, model `PXIe-4141`). Confirmed directly against `nidcpower`'s real simulated model data (not assumed): a simulated PXIe-4141 session rejects any `current_level_range` above 100 mA (`Maximum Value: 100.0e-3`). Compared against `BATTERY_CONFIGS`:

| Battery | `max_charge_current_a` | `max_discharge_current_a` | Within PXIe-4141's 100 mA limit? |
|---|---|---|---|
| HUB | 0.525 A | 1.05 A | **No -- both exceed it (5.25x / 10.5x over)** |
| SB | 0.08 A | 0.16 A | Charge: yes. Discharge: **no (1.6x over)** |

If this is accurate for the real (not just simulated) PXIe-4141 hardware, `_configure_current_source()`'s `current_level_range = abs(current_a)` line would raise `SMUError` immediately for every real combination except SB charging, the moment `ChargeSequence`/`DischargeSequence` are run against Group A's actual assigned SMU. `docs/TODO.md` already tracks "Multi-SMU/multi-DAQ channel assignment... not yet assigned to any battery channel" as open work (`HIGH_POWER_SMU`/PXIe-4139 and `AUX_SMU_1`/`AUX_SMU_2`/PXI-4130 are all configured but unassigned) -- this finding sharpens that existing gap into a concrete, quantified blocker for real hardware use specifically, not a generic scaling task. **Not fixed here** -- reassigning `BATTERY_GROUPS["A"]["smu"]` is a physical wiring/capacity decision requiring a real datasheet check against the actual installed hardware, not something to silently change in config without that confirmation. Flagged as the top blocker before any real hardware validation of `ChargeSequence`/`DischargeSequence`.

### Verification performed this review

`py_compile` clean on all touched files (`hardware/smu.py`, `test_control/charge_sequence.py`, `test_control/discharge_sequence.py`, `test.py`). Mocked end-to-end smoke tests re-run after both fixes: `ChargeSequence`/`DischargeSequence` happy paths (EOC/EOD reached, `relay.open()` confirmed called once, `run_summary` `COMPLETED`/`PASS`); forced-overcurrent safety-abort path (`SafetyViolationError` raised, `relay.open_all()` confirmed called, singular `relay.open()` confirmed NOT called, `run_summary` `SAFETY_VIOLATION`/`FAIL`). Compliance-window arithmetic and default `compliance_limit_symmetry` confirmed directly against `nidcpower`'s real driver (`simulate=True`), not assumed from documentation memory.

---

## 38. Battery Group Assignment Architecture -- Review and Recommendations

Reviewed `BATTERY_GROUPS`, `BATTERY_CHANNELS`, `BATTERY_CONFIGS`, `hardware_for_group()`, and the current A-D assignments, per this review's Phase 7. No redesign performed or recommended -- the existing architecture (groups as relay-routing sections, `hardware_for_group()` as the single resolver, `resolve_group_position()`/`group_for_position()` as the group<->global-position translation) is sound and should be extended, not replaced.

**Concrete gap found: `BATTERY_CHANNELS` only covers global positions 1-8.** Its dict comprehension is hardcoded `for i in range(1, 9)`, independent of `BATTERY_GROUPS`' `position_start`/`position_end` ranges (Group B alone claims positions 9-16). If Group B were enabled today without first extending `BATTERY_CHANNELS`, `resolve_group_position("B", n)` would correctly compute a global position (e.g. 11), but `BATTERY_CHANNELS.get(11)` would return `None` -- already handled gracefully (`test.py` checks for this and aborts with `[FAIL]`, never a crash), but it means Group B cannot actually be used yet even if `enabled` were flipped to `True`.

**Sharper, previously-undocumented risk found: naively extending `BATTERY_CHANNELS`' existing per-position DAQ-channel formula to Group B would collide with Group A on the shared `MAIN_DAQ`.** `BATTERY_CHANNELS[i]`'s `daq_voltage_ch`/`daq_current_ch`/`daq_ntc_ch` are computed as `Dev1/ai{i-1}` / `ai{i+7}` / `ai{i+15}` -- for positions 1-8 this maps to `ai0-7`/`ai8-15`/`ai16-23` (24 channels on `MAIN_DAQ`). `BATTERY_GROUPS["B"]["daq"]` is currently `"MAIN_DAQ"` (the same device as Group A) -- extending the *same formula* to positions 9-16 would compute `ai8-15`/`ai16-23`/`ai24-31`, directly **overlapping Group A's current and NTC channel ranges on the identical physical DAQ**. This is silent and easy to miss (no error until real channel data from two "different" positions turns out to be reading the same physical wire).

**Recommendation (extend existing architecture, already partially present):** `config/devices.py::PXI_SLOTS` already has commented-out entries for `EXPANSION_DAQ` (PXIe-6368, slot 17) and `PRECISION_DAQ` (PXIe-6365, slot 18) -- hardware anticipated for exactly this scaling step but not yet installed/enabled. When Group B is wired for real:
1. Enable `EXPANSION_DAQ` in `PXI_SLOTS` (uncomment, confirm real installation).
2. Set `BATTERY_GROUPS["B"]["daq"] = "EXPANSION_DAQ"` instead of `"MAIN_DAQ"` -- giving Group B its own physical channel space, not a shared/overlapping one.
3. Extend `BATTERY_CHANNELS` for positions 9-16 using the same per-position formula shape (`ai{n-1}`/`ai{n+7}`/`ai{n+15}`) but with `n` re-based to 1-8 *within* Group B (i.e. `position_in_group`, not the raw global position `i`) -- so Group B's channels are `ai0-7`/`ai8-15`/`ai16-23` on `EXPANSION_DAQ`, mirroring Group A's layout on its own device rather than continuing the global numbering onto a shared one.
4. Do the same for Groups C/D with `PRECISION_DAQ` (or a further additional DAQ) once real relay matrices/SMUs exist for them -- `relay_matrix`/`smu`/`dmm`/`daq` are already `None` placeholders for both, so this is additive, not a rename.

This keeps `hardware_for_group()`'s return shape, `resolve_group_position()`, and every caller (`test.py`, `ChargeSequence`/`DischargeSequence`/`MonitorBatterySequence`) completely unchanged -- only `PXI_SLOTS`/`BATTERY_GROUPS`/`BATTERY_CHANNELS` data grows, exactly matching this project's established "config is the single source of truth, extend the data not the code" pattern.

**Other recommendations (lower priority, no urgent action needed):**
- **Group scalability:** the `hardware_for_group()`/`BATTERY_GROUPS` shape already scales cleanly to N groups with zero code changes -- the only real work is the DAQ-channel-collision point above, plus confirming each new group's SMU has adequate current capacity for `BATTERY_CONFIGS` (see this session's PRIMARY_SMU finding -- this should be checked for every group's assigned SMU as it's wired, not just Group A after the fact).
- **Battery assignment workflow:** `_select_battery_type()`/`_select_battery_group()`/`_select_battery_position()` are already clean, explicit, and consistently reused by every workflow (Monitor/Monitor Scan/Charge/Discharge) -- no change needed. One minor usability gap: `_select_battery_group()` shows "(no relay matrix installed yet)" for a disabled group but doesn't distinguish *why* a group might be unusable once `enabled=True` but a specific role (e.g. `smu`) is still `None` (Group B today: `relay_matrix` is real, `smu` is `None`) -- `_missing_hardware_roles()` already catches this correctly at confirmation time with a clear `[FAIL]` message, so this is a display polish opportunity, not a gap in the safety-relevant path.
- **Station usability:** no change recommended -- the confirmation screen already shows resolved hardware per role, and a missing role aborts before any hardware activation.
- **Future multi-group execution (running Group A and Group B concurrently):** not yet a concern -- `HardwareManager`/`ChargeSequence`/`DischargeSequence` are single-group/single-channel per invocation today, and nothing in this review found an assumption that would block a future operator from running two groups' sequences in separate processes/invocations, since each resolves its own hardware via `hardware_for_group()` independently. True concurrent multi-group execution (one process, multiple simultaneous sequences) is a larger future design question (thread-safety of `DataStorage`/shared `run_id`, etc.) -- out of scope for this review, not identified as blocking anything planned now.
- **Configuration consistency:** `BATTERY_GROUPS`' comment block already documents the `None`-means-unassigned convention well; the one gap (the DAQ-channel-collision risk above) is now documented here and in `docs/TODO.md`.

---

## 39. Battery Group Test Configuration Architecture

**SUPERSEDED IN PART by Section 40.** This section's "Resolving the battery-type-inference tension" subsection below describes battery type as operator-selected-and-cross-checked. That has since been corrected: battery type is no longer operator input at all, in any workflow -- it is read directly from the group. Section 40 is authoritative on this point; the rest of this section (the conceptual distinction between `BATTERY_CONFIGS` and `test_setpoints`, the SMU capability data, the validation pipeline's Stage 2/3 logic) is unchanged and still accurate. Kept here, not rewritten, as a record of the intermediate design and why it changed.

**Objective:** formalize each `BATTERY_GROUPS` entry into a complete, self-contained operational test definition -- one place defines battery type, hardware assignment, and test configuration, without a parallel config system.

### The conceptual distinction this section formalizes

`BATTERY_CONFIGS[type]` is, and remains, **battery safety limits** -- absolute ceilings/floors describing what the battery can tolerate, used only for enforcement (`SafetyMonitor`) and validation. It has never been, and must never become, a source of *commanded setpoints*. Before this session, `ChargeSequence`/`DischargeSequence` violated this distinction in practice: they read `battery_cfg["max_charge_current_a"]`/`voltage_max_v` etc. directly as the commanded PSU setpoint, conflating "the most this battery can take" with "what we command it to." A group's `test_setpoints` (new, below) is the **chosen operating point** for that group's protocol -- which may legitimately sit well below the battery's own limit (a conservative/slow-rate recipe), and is never required to equal it.

### Resolving the battery-type-inference tension

This project has a standing, safety-motivated rule: battery type is always an explicit operator choice, never inferred from group/position/channel/relay (`test.py::_select_battery_type()`, restated in Section 30). Adding `battery_type` to `BATTERY_GROUPS` could easily have violated this. It does not, by design: `BATTERY_GROUPS[group]["battery_type"]` is a **declaration** of which battery that group/station is wired and qualified to test -- not a substitute for selection. The operator still always explicitly picks a battery type; `utils/validators.py::validate_group_test_config()` then cross-checks the selection against the group's declaration and raises `GroupConfigurationError` on a mismatch. Selection remains mandatory; a wrong pick is caught, never silently resolved.

### The new structure (additive, no new file)

```python
BATTERY_GROUPS = {
    "A": {
        "relay_matrix": "MATRIX_NUMATO_201", "position_start": 1, "position_end": 8,
        "enabled": True, "smu": "PRIMARY_SMU", "dmm": "MAIN_DMM", "daq": "MAIN_DAQ",
        "battery_type": "SB",                 # declaration, not inference (see above)
        "test_setpoints": {                   # the chosen recipe, not a limit
            "charge_current_a": 0.05, "charge_voltage_v": 4.2,
            "discharge_current_a": 0.08, "discharge_cutoff_v": 3.0,
        },
    },
    # B/C/D: "battery_type": None, "test_setpoints": None -- not yet configured,
    # same "None = unassigned" convention already used for hardware roles.
}
```

`config/devices.py::group_test_config(group)` -- new, pure accessor (mirrors `hardware_for_group()`'s shape) returning `{"battery_type", "test_setpoints"}`. It never validates; it just reads.

**Hardware capability data, also new and additive:** `PXI_SLOTS[...]["max_current_a"]` on every SMU entry, threaded through `SMU_ASSIGNMENTS`'s existing per-field reshape (a real gap found and fixed during implementation -- see "Implementation finding" below). Confirmed via `nidcpower`'s own simulated model data (the same method used in Section 37, not assumed from memory): `PRIMARY_SMU`/PXIe-4141 = 0.1 A, `HIGH_POWER_SMU`/PXIe-4139 = 3.0 A, `AUX_SMU_1`/`AUX_SMU_2`/PXI-4130 = 1.0 A (all per-channel current_level_range ceilings).

**Why Group A is declared for SB, not HUB:** `PRIMARY_SMU`'s 0.1 A cap is below HUB's own limits (0.525/1.05 A) entirely, and even below SB's own discharge limit (0.16 A) -- so Group A's `test_setpoints` were deliberately chosen as a conservative recipe (0.05 A charge / 0.08 A discharge) that fits inside PRIMARY_SMU's real capability while staying under SB's limits, rather than commanding SB's own max. This makes Group A a fully valid, three-stage-validated configuration today; HUB requires reassigning Group A's `smu` to a higher-current card first (a wiring decision, not made here -- see `docs/TODO.md`).

### Validation architecture (Phase 3)

`utils/validators.py::validate_group_test_config(group, battery_type) -> dict` -- the pipeline, run by `test.py::_run_charge_or_discharge()` immediately after `hardware_for_group()`'s missing-role check and before `HardwareManager` is constructed (no relay, no PSU touched on any failure):

```
Group Configuration  ->  Battery Limits Validation  ->  Hardware Capability Validation  ->  Execution
```

- **Stage 1 (Group Configuration):** group exists; `relay_matrix`/`smu`/`dmm` all resolve (reuses `hardware_for_group()`); `battery_type` is declared and matches the operator's selection; `test_setpoints` is present with all four required keys. Raises `GroupConfigurationError`.
- **Stage 2 (Battery Limits Validation):** each setpoint compared against `BATTERY_CONFIGS[battery_type]` -- `charge_current_a <= max_charge_current_a`, `charge_voltage_v <= voltage_max_v`, `discharge_current_a <= max_discharge_current_a`, `discharge_cutoff_v >= voltage_min_v` (the floor -- see Section 30). Raises `ConfigurationError`, never silently clamps.
- **Stage 3 (Hardware Capability Validation):** `charge_current_a`/`discharge_current_a` compared against the assigned SMU's `max_current_a` (skipped, not failed, if that field is absent -- a card without capability data is not blocking, matching this project's "unconfirmed, not unsafe" convention elsewhere). Raises `HardwareConfigurationError`.

Returns the validated `test_setpoints` dict, threaded unchanged into `ChargeSequence.run(battery_cfg=..., test_setpoints=..., ...)` / `DischargeSequence.run(...)`.

### Error handling (Phase 4)

**Exception hierarchy** -- all three new exceptions subclass the existing `ValidationError` (itself a `NIPXIError`), the same pattern `DeviceConfigError` already established -- no parallel hierarchy:
- `GroupConfigurationError` -- missing/mismatched group definition.
- `ConfigurationError` -- setpoint exceeds the battery's own limit.
- `HardwareConfigurationError` -- setpoint exceeds the assigned hardware's capability.

**Validation flow:** all three stages run inside one `try` in `test.py`, before `HardwareManager(...)` is even constructed -- fail early, fail loud, by construction (there is no hardware object yet to touch). **Logging behavior:** the validator itself does not log (it's a pure function -- raise or return); `test.py` prints `f"[FAIL] {type(e).__name__}: {e}"` and returns immediately, matching every other pre-flight abort in this codebase (missing hardware role, `HardwareInitError`, etc.). **Operator-facing messages:** each raised exception's message names the exact group, the exact field, the exact configured value, and the exact limit/capability it exceeds (e.g. `"Group 'A': configured discharge_current_a (0.150 A) exceeds PRIMARY_SMU's rated capability (0.100 A)."`) -- never a generic "invalid configuration."

### Implementation finding: a real propagation gap, caught by testing the validator, not assumed

While wiring Stage 3, `hw["smu_cfg"]["max_current_a"]` came back `None` even though `PXI_SLOTS[5]["max_current_a"]` was set to `0.1`. Root cause: `SMU_ASSIGNMENTS` (what `hardware_for_group()` actually returns) is built by explicitly re-shaping each `PXI_SLOTS` entry field-by-field (`{"type": ..., "slot": cfg["slot"], "resource": cfg["resource"], ...}`), not by passing the dict through -- so a new field added to `PXI_SLOTS` silently does not appear in `SMU_ASSIGNMENTS`/`DAQ_CONFIGS`/`DMM_CONFIGS` unless that comprehension is also updated. Fixed by adding `"max_current_a": cfg.get("max_current_a")` to `SMU_ASSIGNMENTS`'s comprehension. **This is a structural trap worth remembering:** any future field added to a `PXI_SLOTS` entry needs a matching edit in whichever of the three derived-dict comprehensions applies, or it silently vanishes downstream with no error -- confirmed the hard way here (`validate_group_test_config()`'s Stage 3 test initially passed when it should have failed, because the capability data it needed wasn't actually there).

### Verification

Direct calls to `validate_group_test_config()` confirmed all three stages independently reachable and correctly ordered: Group A + SB passes; Group A + HUB raises `GroupConfigurationError` (mismatch); Group B raises `GroupConfigurationError` (missing hardware role); an unknown group raises `GroupConfigurationError`; a setpoint exceeding SB's own limit raises `ConfigurationError`; a setpoint within the battery limit but exceeding `PRIMARY_SMU`'s capability raises `HardwareConfigurationError` (only after the `SMU_ASSIGNMENTS` propagation fix above). Mocked end-to-end smoke tests re-run for both `ChargeSequence`/`DischargeSequence` with the new `test_setpoints` parameter -- `set_charge_mode()`/`set_discharge_mode()` confirmed called with the group's setpoint values (not `battery_cfg`'s), `relay.open()` confirmed called once on success, matching Section 37's already-established fix.

### Multi-group scalability assessment (Phase 5)

Adding Group B/C/D as real, usable configurations now requires **only data** -- populate `battery_type`/`test_setpoints` alongside the hardware roles already anticipated -- zero code changes to `hardware_for_group()`, `resolve_group_position()`, `validate_group_test_config()`, or either sequence class. The DAQ-channel-collision risk (Section 38) is unrelated to and unaffected by this change. Future DAQ integration is likewise unaffected -- `test_setpoints` never touches DAQ. Operator workflow gains one new possible abort (`GroupConfigurationError` on a battery-type/group mismatch) and a clearer confirmation screen (setpoints now shown as "commanded", distinct from the battery's own limits) -- no regression to Monitor Battery/Monitor Battery Scan, which are untouched by this section's changes.

---

## 40. Architectural Correction: Battery Type Is Never Operator Input

**Corrects Section 39's "Resolving the battery-type-inference tension" subsection.** That design let the operator still explicitly select a battery type, with the group's own declaration used only as a cross-check (raising `GroupConfigurationError` on a mismatch). This has been corrected: **battery type is not, and must never become, operator input, in any workflow.** It is engineering-configured entirely within `config/devices.py::BATTERY_GROUPS[group]["battery_type"]`, read directly, with no operator prompt and no cross-check to perform (there is nothing left to cross-check against).

### The corrected model

- **`BATTERY_CONFIGS[type]`** -- battery characteristics and safety limits (`max_charge_current_a`, `max_discharge_current_a`, `voltage_max_v`, `voltage_min_v`, `max_temp_c`, `nominal_voltage_v`, `capacity_ah`). Defines what the battery *allows*. Unchanged by this correction.
- **`BATTERY_GROUPS[group]`** -- the complete operational test definition: `relay_matrix`/`smu`/`dmm`/`daq` (hardware), `position_start`/`position_end` (positions), `battery_type` (which battery this group is engineering-configured for), `test_setpoints` (`charge_current_a`/`charge_voltage_v`/`discharge_current_a`/`discharge_cutoff_v` -- the chosen recipe). Defines *how the test will actually run*.
- **Operator responsibility: select Group. Only.** Battery type, charge/discharge current, charge voltage, and cutoff voltage are all engineering-controlled settings the operator never chooses at runtime -- they come from whichever group was selected.

### Why this is not the same as the previous "declaration + cross-check" design

The previous design still asked the operator "which battery?" -- just with a safety net catching a wrong answer. The corrected model removes the question entirely: there is no battery-type prompt anywhere in the real execution workflows (Monitor Battery, Monitor Battery Scan, Charge Battery, Discharge Battery). This is a stronger form of "config/devices.py is the single source of truth" than the prior design achieved -- battery type genuinely has exactly one place it can come from, with no runtime input path that could diverge from it, rather than two paths (operator input, group declaration) kept in sync by a validation check.

### Implementation

- **Removed:** `test.py::_select_battery_type()` -- deleted entirely (confirmed unused by any remaining caller before removal, not just unreferenced by convention).
- **`test.py::_select_battery_group()`** is now explicitly documented as the *only* selection prompt for any battery workflow.
- **`_run_monitor_battery()`**: order changed from (select battery type -> select group -> select position) to (select group -> select position -> resolve hardware -> **derive battery type from `dev_cfg.group_test_config(group)["battery_type"]`**, aborting with a clear `[FAIL]` before any hardware activation if the group has none configured).
- **`_run_monitor_battery_scan()`**: same reordering.
- **`_run_charge_or_discharge()`** (Charge/Discharge Battery): battery type is now returned by `validate_group_test_config(group)` itself (see below) rather than resolved separately before calling it.
- **`utils/validators.py::validate_group_test_config(group)`** -- signature changed from `(group, battery_type)` to `(group)`. Stage 1 no longer takes or checks an operator-supplied battery type; it reads `BATTERY_GROUPS[group]["battery_type"]` directly and raises `GroupConfigurationError` only if that's `None` (group not yet configured). Return value changed from `test_setpoints` alone to `{"battery_type": ..., "test_setpoints": ...}`, since callers now need both from this single call. Stages 2 (Battery Limits) and 3 (Hardware Capability) are otherwise unchanged -- they always compared setpoints against `BATTERY_CONFIGS[battery_type]`/the SMU's capability using whatever `battery_type` was in scope, and that value is simply sourced differently now.
- **`_confirm_operation()`**: signature unchanged (still displays `battery_type`/`battery_cfg`) -- only the label changed, to "Battery Type (engineering-configured for this group)", so the operator sees what will run without it implying a choice they made.

### What was deliberately NOT changed

- **`test.py::_select_safety_simulation_battery()`** (used only by the Safety Monitor Simulator, `test_safety_monitor()`) still lets a developer pick a battery type to preview `SafetyMonitor`'s behavior against. This is a hardware-free, database-free walkthrough/exploration tool for comparing behavior across battery types side by side -- not a real execution workflow, and not something a real operator uses to run a test. Changing it to derive from a group would defeat its purpose (seeing how the simulator behaves for HUB vs. SB in the same session). Left untouched, deliberately, not overlooked.
- `BATTERY_CONFIGS` itself -- untouched, exactly as intended.
- The three-stage validation pipeline's Stage 2/3 logic -- unchanged, only Stage 1's source of `battery_type` changed.

### Verification

Direct calls confirm `validate_group_test_config('A')` returns `{"battery_type": "SB", "test_setpoints": {...}}` with no parameter beyond `group`; Group B/an unknown group both still raise `GroupConfigurationError` at the same point as before. Scripted smoke tests (mocked `input()`, declining the confirmation screen before any hardware touch) confirm all three real workflows -- Monitor Battery, Monitor Battery Scan, Charge Battery -- now prompt for Group and Position only, never battery type, and the confirmation screen correctly displays the group-derived battery type and (for Charge/Discharge) the commanded setpoints. `py_compile` clean; no remaining reference to `_select_battery_type` anywhere in `test.py`.

---

## 41. Simulator & Reference-Blueprint Reconciliation + Pre-Hardware-Validation Readiness

Performed immediately before the Real Hardware Validation milestone, on the explicit finding that the Safety Monitor Simulator had drifted from the real implementation across the last several sessions of real architecture work. "Simulator drift from the real architecture is not acceptable" -- this section both fixes the drift found and establishes that this class of check must happen before hardware validation, not be discovered during it.

### Drift found and corrected

**1. Setpoint source.** `test.py::_charge_phase_steps()`/`_discharge_phase_steps()` derived their simulated commanded voltage/current from `battery_cfg["voltage_max_v"]`/`max_charge_current_a`/`max_discharge_current_a` -- **the exact limit-as-setpoint conflation bug that was found and fixed in the real `ChargeSequence`/`DischargeSequence` two sessions ago** (Section 37 Bug context). The simulator had continued modeling the *pre-fix* behavior the whole time, since nothing had gone back to reconcile it. Fixed: both functions now take `test_setpoints` (a `BATTERY_GROUPS[group]["test_setpoints"]` entry) for the commanded value; `battery_cfg` is used only where the real sequences use it -- the discharge safety floor and `max_temp_c` in `_discharge_phase_steps()`, nowhere at all in `_charge_phase_steps()` (which no longer takes `battery_cfg` as a parameter, matching `ChargeSequence.run()` exactly).

**2. Battery-type selection model.** `_select_safety_simulation_battery()` let the operator pick a battery type directly from `BATTERY_CONFIGS` -- but no real workflow does this anymore (Section 40). Replaced with `_select_safety_simulation_group()`, which lists only groups that have both `battery_type` and `test_setpoints` configured (i.e. groups that could actually run a real workflow today) and derives both via `group_test_config()`, exactly mirroring `test.py::_run_charge_or_discharge()`. Group A/SB is the only real candidate today -- by design, the simulator can no longer simulate a configuration that couldn't exist in practice.

**3. Stale status claims.** `_charge_phase_steps()`/`_discharge_phase_steps()`'s docstrings said *"Simulated INTENDED operational sequence for the **not-yet-implemented** ... workflow"* and referenced legacy `ChargeCycle.run(battery_cfg=...)`/`DischargeCycle.run(battery_cfg=...)` -- both wrong (Charge/Discharge are implemented, and the real classes are `ChargeSequence`/`DischargeSequence`). Corrected to describe the real, current implementation. The module-level comment introducing the simulator was corrected the same way (Monitor/Charge/Discharge implemented in software; only Cycle Battery remains a genuine forward-looking blueprint).

**4. A second, separately-discovered instance of the same drift class:** `test.py::test_ui_preview()` (the "UI Test" menu) still lumped "Charge/Discharge/Cycle Battery screens" together as a single "not yet implemented" option. Charge and Discharge have been implemented for two sessions. Added `_demo_charge_battery_frame()`/`_demo_discharge_battery_frame()` (mirroring `_demo_monitor_battery_frame()`'s existing pattern -- real `ExecutionFrame`s with static demo data, rendered through the real `render_execution_frame()`, no hardware/database) and split the menu into separate Charge/Discharge (now implemented) and Cycle (still correctly "not yet implemented," since `CycleSequence` genuinely doesn't exist) entries.

**5. A stale inline comment**, unrelated to the simulator: `test.py::test_configuration()`'s cross-check comment referenced only legacy `DischargeCycle.run()` for the target/floor clamp; corrected to also name `DischargeSequence.run()` (the real implementation) and the simulator's own now-reconciled `_discharge_phase_steps()`.

### Verification

Scripted smoke tests (mocked `input()`, clicking through every step) confirm: Charge Battery walkthrough for Group A displays `Configure PSU limits (V=4.20 V, I_limit=0.050 A)` -- Group A's real `test_setpoints`, not SB's `max_charge_current_a` (0.08 A); Discharge Battery walkthrough displays `I_discharge=0.080 A sink` -- Group A's real discharge setpoint; Cycle Battery walkthrough correctly aborts at the injected overtemperature fault (`50.0 C > 45.0 C`, SB's real `max_temp_c`); the "Skip" fallback still exercises the global `Settings.CHARGE_VOLTAGE_V`/`CHARGE_CURRENT_A` constants unchanged. UI Test's new Charge/Discharge demo screens render correctly via the real `render_execution_frame()`. `py_compile` clean; no remaining reference to `_select_safety_simulation_battery`, `ChargeCycle.run(battery_cfg`, or `DischargeCycle.run(battery_cfg` anywhere in the codebase.

### Architecture consistency review (Phase 2) -- findings

Monitor Battery, Monitor Battery Scan, `ChargeSequence`, `DischargeSequence`, and the (now-reconciled) Simulator all agree on: **group ownership** (group is the only real selection, everywhere); **battery ownership** (derived from the group via `group_test_config()`, everywhere); **setpoint ownership** (`test_setpoints` for commanded values, `BATTERY_CONFIGS` for limits only, everywhere setpoints exist at all -- Monitor/Monitor Scan have none, correctly). Two intentional, documented asymmetries, not inconsistencies: the Simulator does not run the three-stage `validate_group_test_config()` pipeline (it never touches hardware, so hardware-capability validation is moot, and its candidate list is already filtered to configured groups) and does not write any traceability record (by design, stated in its own module comment since Milestone II). **Execution flow order** was already correct before this session (the simulator's step *sequence* matched the real code's operation order); only the *values* flowing through that sequence were wrong.

### Pre-hardware-validation review (Phase 3) -- other findings

No further stale assumptions, incorrect defaults, invalid configuration flows, traceability gaps, or validation gaps were found beyond the drift already documented above. `docs/TODO.md`'s remaining `[MUST]` items before real hardware use (SMU current-capability confirmation for HUB, relay/DAQ channel number confirmation, `BATTERY_CONFIGS` datasheet confirmation, physical rack validation itself) are all inherently hardware-access tasks, not software defects -- correctly out of this session's scope.

### DAQ readiness review (Phase 4)

1. **Can DAQ be integrated later without major refactoring? Yes.** Telemetry acquisition in both `ChargeSequence.run()` and `DischargeSequence.run()` is two lines per sampling iteration (`smu_reading = self.smu.measure()`; `dmm_v = self.dmm.measure_dc_voltage()`) -- swapping to `daq.read_all_batteries()` touches only those lines, not the surrounding control flow, safety checks, traceability, or shutdown logic.
2. **Are Monitor/Charge/Discharge prepared? Yes, with one gap closed this session.** `MonitorBatterySequence` already accepts no `daq` at all (DAQ was never in its constructor -- a pre-existing, separately-tracked gap, unrelated to this fix). `ChargeSequence`/`DischargeSequence`'s constructors did **not** accept a `daq` parameter at all, even though `BatteryOperationSequence` (their own base class) already supports one. Fixed: both constructors now take an optional `daq=None`, forwarded to the base class; `test.py::_run_charge_or_discharge()` now passes `daq=hw_mgr.daq` through (harmless -- neither class reads it yet). This is the minimal correction the task asked for: an interface placeholder, not a DAQ dependency.
3. **Architectural blockers? None found.** The `measurements` table and `ExecutionFrame` already have DAQ-shaped columns/fields (`daq_channel_0_raw`) from earlier work, unused by Charge/Discharge today but already present in the schema.
4. **Other placeholders adjusted?** Only the constructor fix above. Nothing else needed adjustment -- deliberately not touching `hardware/daq.py`'s stubs, not adding any DAQ call, per the explicit instruction not to introduce a DAQ dependency this session.

### Technical debt classification (Phase 5)

| Item | Classification |
|---|---|
| Simulator drift (setpoints, battery-type model, stale status claims) | **Must Fix Before Hardware Validation -- DONE this session** |
| UI Test's stale "Charge/Discharge/Cycle not implemented" claim | **Must Fix Before Hardware Validation -- DONE this session** |
| `ChargeSequence`/`DischargeSequence` missing `daq` constructor parameter | **Must Fix Before Hardware Validation -- DONE this session** (minimal placeholder only) |
| Relay/DAQ channel number confirmation, `BATTERY_CONFIGS` datasheet confirmation, PRIMARY_SMU-vs-HUB reassignment | **Must Fix Before Hardware Validation** -- but inherently requires physical access, not a software task; cannot be closed in this session |
| Physical rack validation of `ChargeSequence`/`DischargeSequence` itself | **Is** the next milestone, not debt |
| `CycleSequence` implementation | **Can Wait Until After Hardware Validation** -- explicitly deferred this session; low complexity but should follow a proven hardware result, not precede it |
| `ProtoTestSequence` migration onto `BatteryOperationSequence` | **Can Wait Until After Hardware Validation** -- already correctly triaged low-priority, unchanged |
| Legacy `ChargeCycle`/`DischargeCycle`/`BatteryTestSequence`/`TestExecutor` retirement | **Can Wait Until After Hardware Validation** -- retire only once the new sequences are hardware-proven |
| `hardware/simulated.py` wiring into `HardwareManager` | **Can Wait Until Production Runtime** |
| DAQ integration, NTC integration | **Can Wait Until Production Runtime** (or later) -- explicit, standing decisions, unaffected by this session |
| `main.py` replacement / continuous runtime | **Can Wait Until Production Runtime** -- explicitly out of scope |

### Milestone readiness decision (Phase 7)

**The software architecture is ready to leave the implementation phase and enter the Real Hardware Validation milestone.** Reasoning: every workflow (`Monitor`, `Monitor Scan`, `Charge`, `Discharge`) and the development reference blueprint (the Simulator) now agree on group ownership, battery ownership, setpoint ownership, validation flow, traceability flow, and execution flow -- verified by direct testing this session, not assumed. The two real defects found in the last review (relay-not-opened-on-success, discharge compliance-voltage) were already fixed and verified before this session began; this session found and fixed the simulator/UI-preview drift and closed the one missing DAQ-readiness interface gap. No further software-only blocker was found.

**Blockers remaining (all hardware-access tasks, not software defects):**
- Confirm `PRIMARY_SMU`'s real current rating against the physical PXIe-4141's datasheet (Group A is currently declared for SB specifically because of this).
- Confirm relay channel numbers and DAQ channel aliases against real NI-MAX wiring.
- Confirm `BATTERY_CONFIGS`' HUB/SB voltage/current/temperature limits against the real BLOSS Hub/SB datasheet (currently `# unconfirmed placeholder`).
- The physical rack validation run itself.

---

## 42. Pre-Hardware-Validation MUST-FIX Closure

Performed in direct response to a pre-hardware-validation architecture FAQ
review (`docs/FAQ.md`, committed separately) that inspected the codebase
question-by-question and flagged several RED (not handled) and YELLOW
(partially handled) findings. This session closed the four highest-priority
items that review identified, all verified via mocked regression tests (no
physical hardware access performed or required for this closure work).

### 1. Reverse Polarity Protection

Closes FAQ Section 10's RED findings ("no reverse-polarity detection or
classification anywhere," "no pre-output-enable voltage sanity check" --
previously the single highest-priority gap the FAQ review identified, given
batteries were about to be connected for real).

- New `Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V = -0.5` V
  (`config/settings.py`, placed immediately after
  `ZERO_CURRENT_THRESHOLD_A`), with a comment explaining why the threshold
  sits below 0.0 V rather than at it: a small negative reading can occur
  from ordinary ADC/DMM offset noise on a near-zero (deeply discharged or
  disconnected) cell, so the threshold must sit safely below that noise
  floor to avoid false-tripping on a merely-discharged-but-intact cell.
- New `ReversePolarityError(SafetyViolationError)` (`utils/errors.py`) --
  deliberately a `SafetyViolationError` subclass so it is caught by
  `BatteryOperationSequence.run_guarded()`'s EXISTING `SafetyViolationError`
  branch and triggers the identical `SafetyMonitor.emergency_stop()`
  shutdown (PMU off + all relays forced open, `StopReason.SAFETY_VIOLATION`
  recorded in `run_summary`/`event_log`) -- no new shutdown path was
  introduced for this.
- New `BatteryOperationSequence._check_battery_polarity(voltage_v, *,
  channel, relay_address)` -- logs an ERROR-level event via
  `self.storage.log_event(...)` and then raises `ReversePolarityError` if
  `voltage_v <= Settings.REVERSE_POLARITY_VOLTAGE_THRESHOLD_V`.
- Both `ChargeSequence.run()` and `DischargeSequence.run()` now call, in
  this exact order immediately after `relay.close()`/
  `record_execution_state(state="ACTIVE")` and strictly BEFORE
  `set_charge_mode()`/`set_discharge_mode()`/`output_enable()`:
  `interruptible_sleep(self.s.STABILIZATION_S, token=token)` ->
  `pre_enable_v = self.dmm.measure_dc_voltage()` ->
  `self._check_battery_polarity(pre_enable_v, channel=channel,
  relay_address=relay_address)`. The SMU output is never enabled if this
  raises.

**Verification:** a mocked regression test asserts a DMM reading of -3.5 V
raises `ReversePolarityError` before `smu.set_charge_mode()`/
`output_enable()` are ever called (`not smu.set_charge_mode.called`, etc.);
a plausible positive reading proceeds normally through to a completed
charge.

**Residual, intentional scope limit (documented as YELLOW in
docs/FAQ.md):** this check answers "is it safe to enable the SMU," not
"what is physically wrong with the battery" -- a reversed cell, a
disconnected lead, a genuinely damaged/over-discharged cell, and a wiring
fault all read identically and all raise the same `ReversePolarityError`.
No attempt to disambiguate these was made this session; deferred pending
real-hardware operational experience.

### 2. Battery-Type Validation

Closes FAQ Section 5's "what happens if a group references a non-existent
battery type" finding (previously a bare, uncaught `KeyError` risk, not
reachable via any operator input today but not defensively guarded either).

- `utils/validators.py::validate_group_test_config()`'s Stage 2 (Battery
  Limits Validation) now has an explicit
  `if battery_type not in dev_cfg.BATTERY_CONFIGS: raise
  ConfigurationError(...)` check, BEFORE the
  `battery_cfg = dev_cfg.BATTERY_CONFIGS[battery_type]` lookup that used to
  be able to raise a bare `KeyError`.
- The two other code paths that read `BATTERY_CONFIGS[battery_type]`
  directly without going through `validate_group_test_config()` --
  `test.py::_run_monitor_battery()` and `_run_monitor_battery_scan()` --
  each got the identical explicit check, printed as a `[FAIL]` message in
  the same style as the pre-existing "has no battery_type configured"
  check immediately above it in both functions.

**Verification:** a mocked regression test monkeypatches a group's
`battery_type` to an unknown string and confirms `validate_group_test_config()`
now raises `ConfigurationError` (not `KeyError`).

**Residual, intentional scope limit (documented as YELLOW in
docs/FAQ.md):** the Safety Monitor Simulator's `_select_safety_simulation_group()`
and its two callers (test.py:2623, test.py:2769) still do a bare
`BATTERY_CONFIGS[cfg["battery_type"]]` lookup with no equivalent guard.
Simulator/demo-only code -- no hardware activation, no real battery, no
safety consequence -- noted, not fixed, this session.

### 3. Timeout Traceability

Closes the "`StopReason.TIMEOUT` defined but never used" finding (FAQ
Sections 3-4's timeout questions, and Section 12's technical-debt/risk
list).

- `BatteryOperationSequence.run_guarded()` now has a dedicated `except
  NIPXITimeoutError` branch, placed after the `RelayError` branch and
  before the generic `except Exception` branch, that records
  `StopReason.TIMEOUT` (not the generic `StopReason.FAILED`) in both
  `record_execution_state()` and `finish_run_summary()`. Shutdown behavior
  (`safety.emergency_stop()`) is unchanged/identical to every other fault
  path -- only the recorded stop_reason differs.

**Verification:** a mocked regression test runs a `ChargeSequence` with
`CHARGE_TIMEOUT_S=0.0` and confirms `StopReason.TIMEOUT` now appears in both
the `record_execution_state` and `finish_run_summary` mock call args
(previously would have been `FAILED`).

**Scope boundary:** applies to `ChargeSequence`/`DischargeSequence` (both
built on `BatteryOperationSequence`) only. Does NOT apply to the legacy
`charge_cycle.py`/`discharge_cycle.py`/`ChargeCycle`/`DischargeCycle`
classes, which are non-`BatteryOperationSequence` code already documented
elsewhere (Section 33) as superseded.

### 4. Database Startup Hardening

Closes FAQ Section 7's "what happens if the database is unavailable"
findings (previously: an uncaught `sqlite3.Error`/`OSError` from
`storage.open()` or `start_run_summary()` would propagate as a raw,
operator-unfriendly traceback, though hardware safety itself was never
compromised since teardown lived in an outer `finally`).

- New `test.py::_open_storage_guarded(hw_mgr=None)` -- wraps
  `DataStorage(settings=Settings)` + `.open()` in `try/except (OSError,
  sqlite3.Error)`, prints a clean `[FAIL] Database unavailable -- could not
  open storage: {e}` message (no raw traceback shown to the operator), and
  disconnects `hw_mgr` if given. Returns `None` on failure; callers check
  `if storage is None: return`. Diagnostic detail is preserved because
  `DataStorage.open()` (`data/storage.py`) already calls
  `self.log.error(...)` with the exception before re-raising -- this
  change only replaces what the OPERATOR sees, not what is logged.
- New `test.py::_start_run_summary_guarded(storage, test_type, **fields)`
  -- wraps `storage.start_run_summary(...)` in `try/except sqlite3.Error`,
  prints an equivalent clean `[FAIL]` message, and returns `True`/`False`;
  callers check `if not _start_run_summary_guarded(...): return`.
- All FOUR real workflow entry points now use these helpers instead of
  calling `storage.open()`/`storage.start_run_summary()` directly:
  `_run_monitor_battery()`, `_run_monitor_battery_scan()`,
  `_run_charge_or_discharge()` (shared by Charge Battery / Discharge
  Battery), and `run_proto_test_execution()`. The read-only
  `_open_real_storage_readonly()` database-viewer tool was deliberately
  left untouched -- it's a read-only inspection tool, not a real test
  workflow, and carries no hardware risk.

**Verification:** mocked regression tests confirm `_open_storage_guarded()`
returns `None` and calls `hw_mgr.disconnect_all()` when `DataStorage.open()`
raises `sqlite3.OperationalError`; `_start_run_summary_guarded()` returns
`False` (no exception propagates) when `start_run_summary()` raises
`sqlite3.Error`, and `True` on success.

**Residual, intentional scope limit (documented as YELLOW in
docs/FAQ.md):** per-write calls made DURING a test --
`record_measurement()`/`record_execution_state()`/`log_event()` -- remain
unwrapped; an in-flight SQLite failure mid-test still propagates as an
unhandled exception (though it is still caught by `run_guarded()`'s
generic `except Exception` branch, so hardware safety shutdown still runs
-- only the clean-`[FAIL]`-messaging benefit is missing for that specific
failure mode). Not addressed this session; startup-time failures were the
priority.

### Documentation updated

`docs/FAQ.md` Sections 3, 4, 5, 6, 7, 10, and 12 were updated to reflect
all of the above -- statuses changed from Not/Partially Implemented to
Implemented (or Partially Implemented, where a residual gap is explicitly
named), evidence re-cited against the real file:line locations in the
current code, and Section 12's GREEN/YELLOW/RED tally and Top-10 lists
re-derived from the entries actually changed (not blindly incremented).
`docs/TODO.md` gained a "Pre-Hardware-Validation MUST-FIX Closure" entry
under Completed (Summary) and a matching "(residual, low priority)"
subsection under Remaining Work for the two deferred items.
`docs/MILESTONES.md` gained Milestone IX recording this closure.

### Final Readiness Assessment for Real Hardware Validation

**Software blockers: none.** All four MUST-FIX items identified by the
pre-hardware-validation FAQ review are closed and verified by mocked
regression test. No new software defect was found or introduced while
closing them.

**Hardware blockers (unchanged, carried forward from Milestone VIII /
Section 41 -- not re-investigated this session, per explicit instruction
that this closure work is software-documentation-only):**
- Confirm `PRIMARY_SMU`'s real current rating against the physical
  PXIe-4141's datasheet (Group A is currently declared for SB specifically
  because of this).
- Confirm relay channel numbers and DAQ channel aliases against real
  NI-MAX wiring.
- Confirm `BATTERY_CONFIGS`' HUB/SB voltage/current/temperature limits
  against the real BLOSS Hub/SB datasheet (currently `# unconfirmed
  placeholder`).
- Confirm the SMU output stage's and Numato relay module's actual
  fail-safe behavior under power loss (docs/FAQ.md Section 9 -- "the
  least-characterized risk in the system," per Section 17).
- Confirm, on the bench, the actual real-hardware voltage/current
  signature of a reversed-polarity connection, and whether the new -0.5 V
  threshold is well-chosen against real readings.
- The physical rack validation run itself.

**Decision: GO for the Real Hardware Validation milestone.** The four
software MUST-FIX items are closed; no software blocker remains. The
following RED items are explicitly, deliberately deferred -- not
blockers for this milestone gate, per the user's explicit instruction that
DAQ/NTC/`CycleSequence`/runtime power-loss-and-incomplete-run recovery work
is out of scope for this gate:
- Power-loss / incomplete-run recovery (docs/FAQ.md Section 9) -- no
  startup check for an incomplete `run_summary` row exists; explicitly and
  accurately documented as deferred, not a silent gap.
- Reverse-polarity / damaged-battery / disconnected-lead / wiring-fault
  disambiguation (docs/FAQ.md Section 10) -- intentionally out of scope
  for the safety-gate check closed this session.
- The Safety Monitor Simulator's unguarded `battery_type` lookup
  (docs/FAQ.md Section 5) -- simulator/demo-only, no hardware risk.

None of these three deferred items block Group A + a real SB battery on
one relay-selected channel, the validation scope Milestone VIII already
recommended and this session does not change.

**Recommendations:** validate `ChargeSequence`/`DischargeSequence` against Group A + a real SB battery first (the only fully-validated software configuration today); do not attempt HUB until a group is reassigned to a higher-current SMU; do not start `CycleSequence` until at least one real charge and one real discharge have completed successfully on real hardware.

## 43. Single Global Relay Settling/Dead-Time Constant

**Driver for this change:** a pre-hardware-validation timing review (Sections
1, 4, 8, 9 and `docs/TIMING_ANALYSIS.md`) found relay settling delay was
inconsistent across workflows -- `MonitorBatteryScanSequence` used
`Settings.RELAY_SETTLE_TIME_S` (`0.2s`), `MonitorBatterySequence` and the
`test.py` relay validation/hardware-validation scans had **no delay at
all** between a relay action and the next step, and `ChargeSequence`/
`DischargeSequence` used the unrelated `Settings.STABILIZATION_S` (`5.0s`,
also serving a second, different purpose: post-output-enable electrical
settling). This meant relay dead-time was not a single, deliberately-chosen
value -- it was zero in some paths and an inherited, undifferentiated
constant in others.

**Decision:** relay settling/dead-time must be `2.0` s, everywhere a relay
is switched, with no exceptions and never `0`.

**Implementation -- enforced structurally in `RelayBase`, not per-caller:**
`hardware/relay.py::RelayBase.open()`/`close()` are now concrete (no longer
abstract) and are the single point where every relay switch happens.
Each calls the driver-specific `_open_impl()`/`_close_impl()` (the renamed
abstract methods `NumatoRelayMatrix`/`SerialRelay`/`SimulatedRelay` must
implement), then unconditionally blocks for `Settings.RELAY_SETTLE_TIME_S`
via a new `_settle()` helper, which raises `ValidationError` if that
constant is ever configured `<= 0`. Because every concrete relay driver
subclasses `RelayBase` and no subclass overrides `open()`/`close()`
themselves anymore, **every relay switch in the application -- in every
workflow, every commissioning/validation test, and any future caller --
automatically waits the same 2.0 s before returning control to the
caller**, without that caller needing to add its own delay. This also
means a subsequent relay action (open-then-close, close-then-open, or one
channel to another) can never begin less than 2.0 s after the previous
one completed, since the previous `open()`/`close()` call does not return
until that wait has elapsed.

**Call sites updated to remove now-duplicate, inconsistent delays:**
- `config/settings.py::RELAY_SETTLE_TIME_S` changed from `0.2` to `2.0`,
  and re-documented as the one global constant for this purpose (not a
  `MonitorBatteryScanSequence`-specific value).
- `MonitorBatteryScanSequence` no longer takes a `settle_s` parameter or
  calls its own `interruptible_sleep(settle_s, ...)` after `relay.open()`/
  `relay.close()` -- the wait already happened inside those calls.
- `ChargeSequence`/`DischargeSequence` no longer sleep
  `Settings.STABILIZATION_S` immediately after `relay.close()` (that
  delay was relay-related and is now redundant); the second
  `STABILIZATION_S` sleep, after `smu.output_enable()`, is unchanged --
  it is genuine electrical/output stabilization, not relay settling, and
  `STABILIZATION_S`'s docstring was updated to make that distinction
  explicit so the two concerns are not re-merged later.
- `MonitorBatterySequence` and every `test.py` relay validation/hardware-
  validation scan (`_run_relay_matrix_scan`, `test_relay_safety_selftest`,
  etc.) needed no code change -- they call `relay.open()`/`relay.close()`
  directly, so they inherit the enforced delay automatically. This also
  closes the "no delay found -- back-to-back" gap the timing review
  identified in `MonitorBatterySequence` and in the 32-channel relay
  matrix scan.

**Non-goals / explicitly unchanged:** `Settings.STABILIZATION_S` (SMU
output electrical settling) and `Settings.PROTO_TEST_DWELL_S` (per-relay
measurement dwell) remain separate constants for separate physical
concerns -- this change only consolidates the relay contact settling/
dead-time value itself, per the explicit requirement that there be a
single constant for relay switching, not that all hardware timing
collapse into one number. `hardware/relay_matrix.py::RelayMatrix` (dead,
unreferenced legacy code, not a `RelayBase` subclass, not reachable via
`RelayFactory`) was left untouched, consistent with Section 24's
precedent of not modifying unreachable scaffolding.

**Verification:** all touched modules byte-compile cleanly
(`hardware/relay.py`, `hardware/relay_eth.py`, `hardware/relay_serial.py`,
`hardware/simulated.py`, `config/settings.py`,
`test_control/monitor_battery_scan_sequence.py`,
`test_control/charge_sequence.py`, `test_control/discharge_sequence.py`).
Real-hardware confirmation that 2.0 s is sufficient for the Numato relay
bank's actual mechanical settling time is still a first-hardware-
validation task (see Section 42 and `docs/TIMING_ANALYSIS.md`'s
Recommendations) -- this change fixes the software's *consistency* and
*floor*, not a hardware-measured value.

## 44. RelayEthernetTest Bypassed the Global Relay Settle Constant -- Root Cause and Fix

**Observed issue:** running `[3] RelayEthernetTest (native 0-based
primitives)` against the 8-relay Numato board showed relay transitions
happening immediately, with none of the expected ~2.0 s
(`Settings.RELAY_SETTLE_TIME_S`) gap between them.

**Root cause:** Section 43's fix enforces the settle delay inside
`RelayBase.open()`/`close()` -- but `test.py::test_relay_ethernet_test()`
was never calling those methods. As documented in Section 24 ("The one
deliberate exception"), this test intentionally exercises
`NumatoRelayMatrix`'s native primitives (`write()`, `write_all()`,
`verify_all()`) directly, bypassing the `open()`/`close()` wrapper, to
validate that command layer independently. Section 43 did not account for
this pre-existing, already-documented bypass path: since the settle delay
lived entirely inside `open()`/`close()`, any code that legitimately
calls the native primitives directly received no settle delay at all --
exactly the immediate-transition behavior observed on the physical rack.
This was a real gap in the Section 43 implementation, not a hardware
issue and not a misconfiguration.

**Fix:** `hardware/relay.py::RelayBase._settle()` was renamed to a public
`settle()` -- the single implementation of the settle delay, still called
automatically by `open()`/`close()`, but now also callable directly by any
path that deliberately operates below that wrapper. `test.py::
test_relay_ethernet_test()` now calls `relay.settle()` after each of its
three state-changing native operations per relay index (`write_all(0)`
force-off, `write(relay_index, True)` energize, `write_all(0)`
de-energize), and once more on the operator-cancellation force-off path.
This reuses `Settings.RELAY_SETTLE_TIME_S` and `RelayBase.settle()`'s
existing `> 0` guard -- no second constant, no duplicated sleep logic.

**Audit performed (per this review's Phase 3/4 scope) -- every relay-state-changing call site in the repo:**

| Call site | Reaches settle? | How |
|---|---|---|
| `NumatoRelayMatrix`/`SerialRelay`/`SimulatedRelay` via `RelayBase.open()`/`close()` | Yes | Automatic |
| `MonitorBatterySequence.run()` | Yes | `relay.close()`/`open_all()` |
| `MonitorBatteryScanSequence._scan_one_position()` | Yes | `relay.open()`/`close()` |
| `ChargeSequence`/`DischargeSequence` | Yes | `relay.close()`/`open()` |
| `ProtoTestSequence.run()` | Yes | `relay.close()`/`open()` |
| `HardwareManager` startup/shutdown/`atexit` | Yes | `relay.open_all()` |
| `SafetyMonitor.emergency_stop()`/`safe_cancel_shutdown()` | Yes | `relay_matrix.open_all()` |
| `test.py` "Relay 1 quick check" / Matrix Scan / Safety Self-Test | Yes | `relay.close(ch)`/`relay.open(ch)` |
| `test.py::test_relay_ethernet_test()` (`RelayEthernetTest`) | **No, until this fix** | Native `write()`/`write_all()` bypassing the wrapper -- now calls `relay.settle()` explicitly |

No other bypass path was found (confirmed by a repo-wide search for direct
`.write(`/`.write_all(` calls on a relay object outside
`hardware/relay_eth.py` itself -- `test_relay_ethernet_test()` was the
only caller).

**Read/write verification audit (Phase 4):** every relay-state-changing
call site above -- including `test_relay_ethernet_test()` -- already
followed Read Current State -> Verify -> Write -> Read -> Verify before
this fix (Section 24's pattern); that part of the architecture was not
violated. The gap was specifically the missing settle delay on the one
documented native-primitive path, not a missing read/verify step. No
read/write/verify correction was needed as part of this fix.

**Verification performed:** `hardware/relay.py` and `test.py`
byte-compile cleanly after the change. `RelayBase.settle()`'s `> 0` guard
means `test_relay_ethernet_test()` cannot silently regress to a 0 s delay
either, for the same reason `open()`/`close()` cannot.
