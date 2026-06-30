# TODO — NIPXI Implementation

Ordered by priority. Items marked [MUST] are required before first hardware run.

---

## Hardware Drivers

- [MUST] `hardware/relay_matrix.py` — fill in real serial command protocol
  - Replace placeholder `OPEN {ch}` / `CLOSE {ch}` strings with actual controller commands
  - Test with loopback or real relay box before connecting batteries

- [MUST] `hardware/smu.py` — implement using `nidcpower`
  - `connect()`: open nidcpower.Session
  - `set_charge_mode()` / `set_discharge_mode()`: configure source/sink
  - `output_enable()` / `output_disable()`
  - `measure()`: return real V and I

- [MUST] `hardware/daq.py` — implement using `nidaqmx`
  - `read_all_batteries()`: read all 24 analog channels simultaneously
  - `verify_zero_current()`: use current channel reading

- [ ] `hardware/pxi_rack.py` — enumerate cards at startup via VISA
- [ ] `hardware/temperature.py` — verify NTC Beta value from battery datasheet

---

## Test Control

- [MUST] `test_control/charge_cycle.py` — integrate real DAQ reads into loop
- [MUST] `test_control/discharge_cycle.py` — integrate real DAQ reads into loop
- [ ] `test_control/battery_test.py` — wire up all hardware objects in main.py
- [ ] Add rest period between charge and discharge if needed

---

## Data

- [ ] `data/report.py` — implement summary report
  - Compute capacity (Ah) per channel per cycle
  - Plot V/I vs time
  - Export to text or HTML

---

## Configuration

- [MUST] `config/settings.py`
  - Set `RELAY_COM_PORT` to correct COM port
  - Set `PXI_RESOURCE_*` to correct VISA resource strings
  - Verify `BAT_VOLTAGE_MAX / MIN` against actual battery datasheet

- [MUST] `config/devices.py`
  - Confirm relay channel numbers match physical wiring
  - Confirm DAQ channel numbers match PCB connector layout

---

## Infrastructure

- [ ] Add unit tests in `tests/` folder (mock hardware)
- [ ] Add `--dry-run` mode that exercises logic without real hardware
- [ ] Set up remote Git repository and update README with URL
- [ ] CI: add basic linting (flake8 or ruff)

---

## Optional / Future

- [ ] GUI (PyQt or tkinter) for live monitoring
- [ ] Temperature chamber control (if environmental chamber added)
- [ ] Export results to BLOAST main pipeline format
- [ ] Multi-SMU support (parallel channels)
