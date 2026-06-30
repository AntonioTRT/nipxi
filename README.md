# NIPXI — Battery Test System

Python-based control software for automated lithium-ion battery charge/discharge testing using a National Instruments PXI rack, a custom relay matrix (COM port), and the BLOSS Hub PCB.

This sub-repository is independent from the main BLOAST project repo.

---

## Purpose

Automate capacity and calendar aging tests on up to 8 Li-ion battery channels simultaneously:

- Charge each battery to a known SOC using CC-CV (SMU)
- Discharge each battery at a constant current (SMU)
- Measure voltage, current, and temperature per channel (DAQ + NTC)
- Log all data to SQLite and CSV for post-processing in the BLOAST ML pipeline

---

## Hardware

| Equipment | Model | Interface |
|---|---|---|
| PXI Chassis | NI PXI | NI-VISA |
| DAQ | NI 6363 (Slot 2) | nidaqmx |
| DMM | NI 4065 (Slot 3) | nidmm |
| SMU | NI 4140 / 4139 / 4130 (Slot 4-5) | nidcpower |
| Relay Matrix | NI 2569 (COM port controlled) | pyserial |
| Battery Hub PCB | BLOSS Hub Rev A | — |

The BLOSS Hub PCB connects up to 8 Li-ion batteries (3.5 V – 4.7 V, max 1 A per channel). Each channel has a 2 A fuse and an NTC thermistor for temperature monitoring.

---

## Project Structure

```
nipxi/
├── main.py                   Entry point
├── config/
│   ├── settings.py           Edit here: voltages, currents, ports, paths
│   └── devices.py            Edit here: channel/card assignments
├── hardware/
│   ├── base.py               Abstract hardware driver
│   ├── pxi_rack.py           PXI chassis (NI-VISA)
│   ├── smu.py                SMU charge/discharge (nidcpower)
│   ├── daq.py                DAQ acquisition (nidaqmx)
│   ├── relay_matrix.py       Relay matrix (pyserial)
│   └── temperature.py        NTC to degC conversion
├── test_control/
│   ├── charge_cycle.py       CC-CV charge sequence
│   ├── discharge_cycle.py    CC discharge sequence
│   ├── battery_test.py       Main orchestrator (all channels)
│   ├── safety_monitor.py     Limit checks + emergency stop
│   └── state_machine.py      Optional state tracker
├── data/
│   ├── logger.py             Logging setup
│   ├── storage.py            SQLite + CSV writer
│   └── report.py             Report generation (placeholder)
├── utils/
│   ├── constants.py          Project constants
│   ├── errors.py             Exception hierarchy
│   ├── helpers.py            Utilities
│   └── validators.py         Config/input validation
├── docs/
│   ├── architecture.md       System design overview
│   └── TODO.md               What still needs implementing
├── requirements.txt
└── .gitignore
```

---

## Quick Start (after implementation)

```bash
# 1. Clone or navigate to this repository
cd nipxi

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Edit configuration
#    - config/settings.py : voltages, COM port, PXI slot numbers
#    - config/devices.py  : channel mapping

# 5. Run
python main.py
python main.py --channels 1 2 3   # test only channels 1, 2, 3
python main.py --dry-run           # no hardware connection
```

---

## Configuring Devices

Before running, set these values in `config/settings.py`:

| Parameter | Default | Description |
|---|---|---|
| `RELAY_COM_PORT` | `"COM3"` | Serial port of relay matrix controller |
| `PXI_RESOURCE_DAQ` | `"PXI1Slot2"` | NI 6363 VISA resource |
| `PXI_RESOURCE_SMU1` | `"PXI1Slot4"` | NI SMU VISA resource |
| `CHARGE_CURRENT_A` | `0.5` | CC charge current |
| `DISCHARGE_CURRENT_A` | `0.5` | CC discharge current |
| `ACTIVE_CHANNELS` | `[1..8]` | Which channels to test |

---

## Remote Repository

> **TODO:** Set up remote Git repository and update this URL.
>
> ```bash
> git remote add origin <YOUR_REMOTE_URL_HERE>
> git push -u origin main
> ```

---

## Related Project

This software controls the hardware described in the main BLOAST repository:
- Battery type and specs: `hw/kicad/docs/COMPONENT_SPECIFICATIONS.md`
- PCB design: `hw/kicad/`
- Test protocol: `flowcharts/vi flowchart.md`
- Project roadmap: `roadmap.md`
