"""Project-wide constants. Do not put user-editable parameters here (use config/settings.py)."""

# Project identity
PROJECT_NAME    = "NIPXI"
PROJECT_VERSION = "0.1.0"

# PXI card models present in this setup -- informational only, not read by
# any code (config/devices.py::PXI_SLOTS is the actual single source of
# truth for resource/model strings). Kept in sync with the real rack
# inventory confirmed via NI-MAX detection.
CARD_DAQ_MAIN       = "PXIe-6363"   # PXI1Slot2  -- MAIN_DAQ
CARD_DAQ_EXPANSION  = "PXIe-6368"   # PXI1Slot17 -- EXPANSION_DAQ
CARD_DAQ_PRECISION  = "PXIe-6365"   # PXI1Slot18 -- PRECISION_DAQ
CARD_DMM            = "PXI-4065"    # PXI1Slot3  -- MAIN_DMM
CARD_SMU_PRIMARY    = "PXIe-4141"   # PXI1Slot5  -- PRIMARY_SMU
CARD_SMU_HIGH_POWER = "PXIe-4139"   # PXI1Slot6  -- HIGH_POWER_SMU
CARD_SMU_AUX_1      = "PXI-4130"    # PXI1Slot7  -- AUX_SMU_1
CARD_SMU_AUX_2      = "PXI-4130"    # PXI1Slot8  -- AUX_SMU_2
CARD_CHASSIS_RELAY  = "PXIe-2569"   # PXI1Slot11 -- CHASSIS_RELAY_MATRIX (not the active relay driver)
CARD_TEMP_MODULE    = "PXIe-4353"   # PXI1Slot15 -- TEMP_MODULE (TB-4353 connector 0)

# Battery channel count (BLOSS Hub PCB has 8 slots)
MAX_CHANNELS = 8

# Physical units
UNIT_VOLT   = "V"
UNIT_AMP    = "A"
UNIT_OHM    = "Ohm"
UNIT_CELSIUS = "degC"
UNIT_SECOND = "s"
UNIT_AH     = "Ah"

# Phases
PHASE_CHARGE    = "charge"
PHASE_DISCHARGE = "discharge"
PHASE_REST      = "rest"
