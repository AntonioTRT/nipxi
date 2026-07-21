# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

---

## Hardware Drivers

- [DONE] `hardware/smu.py` — `connect()`/`disconnect()`/`identify()` implemented for
  real (opens a real `nidcpower.Session(resource_name=..., options=...)`, returns
  `session.instrument_model`). Constructed from a `config/devices.py`
  `SMU_ASSIGNMENTS[...]` dict, matching the relay drivers' pattern.
  - [MUST] `set_charge_mode()` / `set_discharge_mode()` / `output_enable()` /
    `output_disable()` / `measure()` — still placeholders, deliberately out of
    scope for the connectivity/discovery work done so far (see docs/architecture.md
    Section 8)

- [DONE] `hardware/daq.py` — `connect()`/`disconnect()`/`identify()` implemented for
  real (NI-DAQmx device enumeration + `self_test_device()`). Constructed from a
  `config/devices.py` `DAQ_CONFIG`-shaped dict.
  - [MUST] `read_channel()` / `read_all_batteries()` / `verify_zero_current()` —
    still placeholders; `test_daq()`'s "deep channel read" step uses `nidaqmx`
    directly until these are implemented (see `test.py::test_daq()` Step 3)

- [DONE] `hardware/dmm.py` — created (did not exist before). `connect()`/
  `disconnect()`/`identify()` implemented for real (opens a real
  `nidmm.Session`). No `measure()` yet -- nothing in the battery workflow calls
  one; add it when DMM-based independent voltage verification is implemented.

- [ ] Fill in relay serial command protocol in `config/devices.py RELAY_CONFIG`
  - Only needed if serial is ever promoted beyond bench diagnostics -- production
    is the Numato Ethernet relay (see below), so this is no longer a [MUST]
  - Replace `"OPEN {ch}\r\n"` / `"CLOSE {ch}\r\n"` / `"QUERY {ch}\r\n"`
    with your controller's actual ASCII commands
  - Test with loopback or relay box before connecting batteries
  - Both `relay_serial.py` (new driver) and `relay_matrix.py` (legacy, unused/dead code) read from this config

- [DONE] `NUMATO_RELAY_MATRIX_CONFIG["ip"]` validated: Numato Lab 32-Channel Ethernet Relay
  Module confirmed reachable at `169.254.1.1:23`, Telnet login `admin`/`admin`
  confirmed working, relay commands and state readback confirmed correct.
  Production path: `main.py -> HardwareManager -> RelayFactory -> NumatoRelayMatrix`.

- [DONE] Mandatory relay safety sequence implemented in `hardware/relay_eth.py`:
  `close()`/`open()` force all relays off, verify, then (for `close()`) activate
  and verify only the requested relay -- raising `RelayStateVerificationError`
  and stopping on any mismatch. See `docs/architecture.md` section 6a.

- [DONE] Relay driver rebuilt around Numato's native command set -- no custom
  protocol. `hardware/relay_eth.py::NumatoRelayMatrix` now exposes two layers:
  native 0-based primitives (`write`, `read_relay`, `write_all`, `read_all`,
  `verify_single`, `verify_all`, `reset`) built directly on
  `relay on/off/read/readall/writeall/reset`, and the existing public 1-based
  API (`open`/`close`/`query`/`open_all`/`close_all`) which is implemented
  entirely on top of the native layer. `close()` now verifies both
  individually (`relay read N`) and in bulk (`relay readall`). Telnet layer
  adds command-rejection detection ("invalid" response) and one bounded
  automatic reconnect-and-retry on comms failure. `RELAY_COUNT` (in
  `config/settings.py`) is the single source of truth for relay count.

- [DONE] `relay readall` hex-bitmask parsing (`hardware/relay_eth.py::_parse_readall_response()`)
  confirmed correct against the physical Numato unit: a live run of
  `test_relay_matrix_scan()` (all 32 channels, ON -> READ -> OFF) and
  `test_hardware_discovery()` both passed end-to-end, decoded ACTIVE channel
  lists matched the physically observed relay state at every step.
  `test_relay_ethernet_test()` (native primitives) and
  `test_relay_safety_selftest()` were not additionally run live in this same
  session (to avoid unnecessary extra relay cycling beyond what was needed
  to confirm the fix) -- both share the identical `connect()`/`_login()`
  code path already proven working, so they are expected to pass too; run
  them for full confirmation when convenient.

- [DONE] Authentication root cause found and fixed: the Numato firmware
  sends a Telnet IAC option-negotiation request ("IAC DO 45") mid-handshake
  that the previous implementation never answered (a real Telnet client
  always does) -- this is why manual Telnet login succeeded while the
  framework reported "Authentication failed". Fixed by
  `hardware/relay_eth.py::NumatoRelayMatrix._handle_iac()` (RFC 854 option
  negotiation, decline-by-default) plus tolerant, case-insensitive prompt
  matching (`_read_until_any()`) since the real login prompt is "User Name: "
  (not "login:"). Confirmed by a live run against the physical unit -- see
  `docs/architecture.md` section 6c.

- [ ] `hardware/pxi_rack.py` — enumerate PXI cards at startup via VISA
  - Use `nidaqmx.system.System.local()` and NI-VISA to confirm expected cards
  - Report missing cards at startup rather than failing silently mid-test

- [ ] `hardware/temperature.py` — verify NTC Beta from the battery pack datasheet
  - Current default: Beta = 3950 K, R25 = 10 kOhm
  - Incorrect Beta shifts the displayed temperature; check at 0 degC, 25 degC, 45 degC

---

## Test Control

- [MUST] `test_control/charge_cycle.py` — wire in real NTC temperature read
  - Replace `t_c = None` with `temperature_sensor.read_celsius(daq.read_ntc(ch))`

- [MUST] `test_control/discharge_cycle.py` — same NTC wire-in as charge_cycle

- [ ] `test_control/battery_test.py` — wire up all hardware objects in `main.py`
  - Instantiate `SMU`, `DAQ`, `RelayFactory.create(cfg)`, `DataStorage`
  - Pass them to `BatteryTestSequence`

- [ ] `test_control/state_machine.py` — review and integrate if state tracking is needed

- [ ] Add rest period between charge and discharge if required by battery spec
  - Typical: 30 min OCV rest at room temperature

---

## Data

- [ ] `data/report.py` — implement test summary report
  - Compute capacity (Ah) per channel per cycle: integrate I over time
  - Generate V/I vs time plot (matplotlib or ASCII table)
  - Export to text or HTML in `data_output/reports/`

---

## Configuration

- [DONE] Removed `PXI_RESOURCE_DAQ`/`PXI_RESOURCE_DMM`/`PXI_RESOURCE_SMU1`/
  `PXI_RESOURCE_SMU2` from `config/settings.py` -- they duplicated the same
  values already in `config/devices.py`'s `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/
  `DMM_CONFIG`, and `HardwareManager` was reading `config/settings.py` instead
  of `config/devices.py`, silently able to diverge from it. Fixed:
  `HardwareManager.__init__()` now defaults `smu_cfg`/`daq_cfg` from
  `config/devices.py` directly. `config/devices.py` is the single source of
  truth for every device's resource string / address.

- [MUST] `config/settings.py`
  - Set `RELAY_COM_PORT` to your COM port (diagnostic serial path only)
  - Confirm `BAT_VOLTAGE_MAX / MIN` against battery datasheet
  - Decide: `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN` (3.5 V) -- which is correct?

- [MUST] `config/devices.py`
  - Confirm relay channel numbers match physical wiring on BLOSS Hub PCB
  - Confirm DAQ channel names match PCB-to-connector layout
  - Set `NUMATO_RELAY_MATRIX_CONFIG["ip"]` to actual relay IP address
  - Set `SMU_ASSIGNMENTS`/`DAQ_CONFIG`/`DMM_CONFIG` `"resource"` fields to match NI-MAX

- [DONE] `utils/device_validator.py` — startup validation of `config/devices.py`:
  every device instantiable, required fields present, no duplicate names/VISA
  resources/IPs/COM ports/relay identifiers, relay count consistency
  (`num_channels == channel_count == Settings.RELAY_COUNT`), every relay
  `"type"` registered in `RelayFactory`. Wired into `main.py` (right after
  `validate_settings()`, before `HardwareManager`) and `test.py`'s
  `preflight_check()` (before the menu). Construction-only -- never `connect()`.

---

## Infrastructure

- [DONE] `test.py` — modular test framework (Startup Device Validation/Hardware Discovery/
  SMU/DMM/DAQ/Relay Serial/Relay Ethernet/Relay Matrix Scan/RelayEthernetTest/
  Relay Safety Self-Test/E-Load/Sensors/Safety/Config/Database/MiniSQL/Run All)
- [DONE] `test_hardware_discovery()` — config-driven connectivity + identification
  test for every device type (SMU/PSU, DMM, DAQ, Relay Ethernet, Relay Serial).
  Uses the SAME production driver classes as `HardwareManager` (no duplicated
  connection logic, no instrument-specific code of its own, no bypass of the
  hardware abstraction layer) -- see docs/architecture.md Section 8.
- [DONE] Relay driver architecture (`relay.py`, `relay_serial.py`, `relay_eth.py`, `relay_factory.py`)
- [DONE] Mandatory relay safety sequence + audit logging in `relay_eth.py` (all-off ->
  verify -> activate -> verify, `RelayStateVerificationError` on any mismatch)
- [DONE] Confirmed `hardware/relay_matrix.py` (legacy) and `utils/ethernet_relay_python.py`
  (Numato reference script) are dead/reference-only code -- neither is imported or
  wired into `RelayFactory`; `NumatoRelayMatrix` is the single enforcement point for
  Numato relay state changes
- [DONE] `data/storage.py` — StorageBackend ABC, DataStorage (SQLite + CSV), query()
- [DONE] `utils/validators.py` — validate_settings() with proper if/raise
- [DONE] `utils/errors.py` — NIPXITimeoutError, full exception hierarchy
- [DONE] `test_control/safety_monitor.py` — all limits + relay guard
- [DONE] `test_control/hardware_manager.py` — HardwareManager: connect_all/disconnect_all/health_check
- [DONE] `test_control/test_executor.py` — TestExecutor + TestRunResult / ChannelResult
- [DONE] `test_control/result_manager.py` — ResultManager: DataStorage lifecycle + MiniSQL hook
- [DONE] `main.py` — thin orchestration: no business logic, delegates to three managers
- [DONE] Documentation pass (README, architecture.md, CONFIGURATION.md, TODO.md)
- [ ] Add `--dry-run` mode: `PXI_SIMULATE = True` + relay mock, exercises logic without hardware
- [ ] Create `flowcharts/vi_flowchart.md` (referenced in architecture.md)
- [ ] Set up remote Git repository and update README with URL
- [ ] CI: add basic linting (ruff or flake8) as a pre-commit hook

---

## Optional / Future

- [ ] MiniSQL storage backend: create `data/storage_minisql.py` implementing `StorageBackend`
  - See README section 14 and architecture.md section 7 for the integration path
- [ ] GUI (PyQt or tkinter) for live voltage/current/temperature monitoring
- [ ] Temperature chamber control (if environmental chamber is added)
- [ ] Export results to BLOAST main pipeline format
- [ ] Multi-SMU support: parallel channel testing (requires separate SMU per channel)
- [ ] `data/report.py`: matplotlib plots of V/I/T vs time per channel
