# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

---

## Hardware Drivers

- [MUST] `hardware/smu.py` — implement using `nidcpower`
  - `connect()`: `self._session = nidcpower.Session(self.resource)`
  - `set_charge_mode()`: configure CC-CV source (nidcpower voltage/current limit)
  - `set_discharge_mode()`: configure current sink
  - `output_enable()` / `output_disable()`
  - `measure()`: return `{"voltage_v": ..., "current_a": ...}` from real measurements

- [MUST] `hardware/daq.py` — implement using `nidaqmx`
  - `connect()`: create persistent `nidaqmx.Task()` for multi-channel reads
  - `read_all_batteries()`: read ai0..ai23 simultaneously, return dict keyed 1-8
  - `verify_zero_current()`: read current channel, compare to `ZERO_CURRENT_THRESHOLD_A`

- [MUST] Fill in relay serial command protocol in `config/devices.py RELAY_CONFIG`
  - Replace `"OPEN {ch}\r\n"` / `"CLOSE {ch}\r\n"` / `"QUERY {ch}\r\n"`
    with your controller's actual ASCII commands
  - Test with loopback or relay box before connecting batteries
  - Both `relay_serial.py` (new driver) and `relay_matrix.py` (legacy) read from this config

- [MUST] Set `RELAY_ETH_CONFIG["ip"]` in `config/devices.py` to the actual relay IP

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

- [MUST] `config/settings.py`
  - Set `RELAY_COM_PORT` to your COM port
  - Set `PXI_RESOURCE_*` to match NI-MAX resource strings
  - Confirm `BAT_VOLTAGE_MAX / MIN` against battery datasheet
  - Decide: `DISCHARGE_CUTOFF_V` (3.0 V) vs `BAT_VOLTAGE_MIN` (3.5 V) -- which is correct?

- [MUST] `config/devices.py`
  - Confirm relay channel numbers match physical wiring on BLOSS Hub PCB
  - Confirm DAQ channel names match PCB-to-connector layout
  - Set `RELAY_ETH_CONFIG["ip"]` to actual relay IP address

---

## Infrastructure

- [DONE] `test.py` — modular test framework (12 sections: SMU/DMM/DAQ/Relay Serial/
  Relay Ethernet/E-Load/Sensors/Safety/Config/Database/MiniSQL/Run All)
- [DONE] Relay driver architecture (`relay.py`, `relay_serial.py`, `relay_eth.py`, `relay_factory.py`)
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
