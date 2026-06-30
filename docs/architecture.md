# NIPXI Architecture

## Overview

```
                  ┌─────────────────────────────────────────────────┐
                  │                   PC / Embedded                  │
                  │                                                   │
                  │   main.py                                        │
                  │     └── BatteryTestSequence                      │
                  │           ├── ChargeCycle                        │
                  │           ├── DischargeCycle                     │
                  │           ├── SafetyMonitor                      │
                  │           └── DataStorage                        │
                  └──────────────┬────────────────────────────────────┘
                                 │ NI-VISA / nidaqmx / nidcpower
         ┌───────────────────────▼──────────────────────────────┐
         │                   PXI Chassis                        │
         │   Slot 2: NI 6363 DAQ  (analog in: V/I/NTC)         │
         │   Slot 3: NI 4065 DMM  (precision voltage)          │
         │   Slot 4: NI 4140 SMU  (charge/discharge)           │
         │   Slot 5: NI 4130 SMU  (optional extra channel)     │
         └────────────────┬─────────────────────────────────────┘
                          │ pyserial (COM port)
         ┌────────────────▼──────────┐
         │   Relay Matrix (NI 2569)  │
         │   8 channels, multiplexed │
         └────────────────┬──────────┘
                          │ wire connections
         ┌────────────────▼────────────────────────────────────┐
         │            BLOSS Hub PCB (Rev A)                    │
         │   8x Li-ion battery connectors                      │
         │   8x 2A fuses                                       │
         │   8x NTC thermistors (10k @ 25 degC)               │
         │   8x Kelvin sense outputs                           │
         └─────────────────────────────────────────────────────┘
```

## Control Flow

Based on the VI flowchart (`flowcharts/vi flowchart.md`):

1. Initialize hardware (PXI, DAQ, SMU, relay)
2. For each active channel:
   a. Read current — must be ~0 A before switching relay
   b. Close relay channel N
   c. Run charge cycle (CC-CV):
      - Set SMU to charge mode
      - Wait stabilization
      - Loop: measure V/I/T → check safety → log → check EOC
   d. Disable SMU, wait, verify I=0
   e. Run discharge cycle (CC):
      - Set SMU to discharge mode
      - Wait stabilization
      - Loop: measure V/I/T → check safety → log → check EOD
   f. Disable SMU
   g. Open relay channel N
3. Save final data, generate report

## Safety Rules

- Never switch relay while |I| > 0.01 A
- Emergency stop if: V > 4.7 V, V < 3.5 V, |I| > 1.0 A, T > 45 degC
- Emergency stop sequence: SMU output disable → relay open all

## Data Flow

```
DAQ analog inputs ──► DataStorage.record() ──► SQLite (nipxi.db)
                                           └──► CSV (one file per channel per run)
                                                     │
                                                     ▼
                                           ReportGenerator ──► reports/
```
