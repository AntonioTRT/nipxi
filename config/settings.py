"""
Global configuration for NIPXI Battery Test System.
Edit this file before running tests.
"""

import os


class Settings:
    # -------------------------------------------------------------------------
    # Project
    # -------------------------------------------------------------------------
    PROJECT_NAME = "NIPXI Battery Test System"
    VERSION = "0.1.0"

    # -------------------------------------------------------------------------
    # Channels
    # Number of battery channels available on the relay matrix / BLOSS Hub PCB
    # -------------------------------------------------------------------------
    NUM_CHANNELS = 8
    ACTIVE_CHANNELS = list(range(1, 9))  # [1, 2, 3, 4, 5, 6, 7, 8]

    # -------------------------------------------------------------------------
    # Battery limits (Li-ion, per BLOSS Hub spec)
    # -------------------------------------------------------------------------
    BAT_VOLTAGE_MAX = 4.7       # V  - absolute upper limit
    BAT_VOLTAGE_MIN = 3.5       # V  - absolute lower limit (do not discharge below)
    BAT_CURRENT_MAX = 1.0       # A  - max charge/discharge current
    BAT_TEMP_MAX_C  = 45.0      # °C - safety cutoff temperature
    BAT_TEMP_MIN_C  = 20.0      # °C - minimum operating temperature

    # -------------------------------------------------------------------------
    # Charge parameters (CC-CV)
    # -------------------------------------------------------------------------
    CHARGE_CURRENT_A   = 0.5    # A  - constant current during CC phase
    CHARGE_VOLTAGE_V   = 4.2    # V  - target CV voltage
    CHARGE_CUTOFF_A    = 0.05   # A  - end-of-charge current threshold (CV taper)
    CHARGE_TIMEOUT_S   = 7200   # s  - max charge time (2 h)

    # -------------------------------------------------------------------------
    # Discharge parameters (CC)
    # -------------------------------------------------------------------------
    DISCHARGE_CURRENT_A = 0.5   # A  - constant discharge current
    DISCHARGE_CUTOFF_V  = 3.0   # V  - end-of-discharge voltage
    DISCHARGE_TIMEOUT_S = 7200  # s  - max discharge time

    # -------------------------------------------------------------------------
    # Stabilization and sampling
    # -------------------------------------------------------------------------
    STABILIZATION_S     = 5.0   # s  - wait after relay switch before measuring
    SAMPLE_RATE_HZ      = 1.0   # Hz - DAQ acquisition rate during cycle

    # -------------------------------------------------------------------------
    # Safety
    # -------------------------------------------------------------------------
    ZERO_CURRENT_THRESHOLD_A = 0.01  # A - "current is zero" threshold for relay safety

    # -------------------------------------------------------------------------
    # PXI rack -- simulation mode only. VISA resource strings (slot numbers)
    # live in config/devices.py (SMU_ASSIGNMENTS / DAQ_CONFIG / DMM_CONFIG)
    # ONLY -- that is their single source of truth. They used to be
    # duplicated here as PXI_RESOURCE_DAQ/DMM/SMU1/SMU2; those were removed
    # because HardwareManager was reading them instead of config/devices.py,
    # silently diverging from it. Edit config/devices.py to change a slot.
    # -------------------------------------------------------------------------
    PXI_SIMULATE       = False          # True = NI VISA simulation mode (no hardware)

    # -------------------------------------------------------------------------
    # Numato Relay Matrix (Numato Lab 32 Channel Ethernet Relay Module) -- PRODUCTION
    # Single source of truth for relay count. config/devices.py
    # NUMATO_RELAY_MATRIX_CONFIG reads this value (never hardcode 32
    # elsewhere -- e.g. in RelayEthernetTest).
    # -------------------------------------------------------------------------
    RELAY_COUNT = 32

    # -------------------------------------------------------------------------
    # Relay matrix  (COM port - not NI, custom controller) -- diagnostic only,
    # NOT the production relay path (see RELAY_COUNT / NUMATO_RELAY_MATRIX_CONFIG above).
    # -------------------------------------------------------------------------
    RELAY_COM_PORT    = "COM3"          # Serial port of relay matrix controller
    RELAY_BAUD_RATE   = 9600
    RELAY_TIMEOUT_S   = 2.0
    RELAY_NUM_CHANNELS = 8

    # -------------------------------------------------------------------------
    # DAQ channels  (NI 6363 physical channel names)
    # -------------------------------------------------------------------------
    DAQ_VOLTAGE_CHANNELS = [f"Dev1/ai{i}" for i in range(8)]   # ai0..ai7
    DAQ_CURRENT_CHANNELS = [f"Dev1/ai{i}" for i in range(8, 16)]  # ai8..ai15
    DAQ_NTC_CHANNELS     = [f"Dev1/ai{i}" for i in range(16, 24)] # ai16..ai23

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
    LOG_FILE  = os.path.join("logs", "nipxi.log")

    # -------------------------------------------------------------------------
    # Data storage
    # -------------------------------------------------------------------------
    DATA_DIR         = "data_output"
    DATABASE_FILE    = os.path.join(DATA_DIR, "nipxi.db")
    CSV_DIR          = os.path.join(DATA_DIR, "csv")
    REPORT_DIR       = os.path.join(DATA_DIR, "reports")
