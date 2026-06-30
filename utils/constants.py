"""Project-wide constants. Do not put user-editable parameters here (use config/settings.py)."""

# Project identity
PROJECT_NAME    = "NIPXI"
PROJECT_VERSION = "0.1.0"

# PXI card models present in this setup
CARD_DAQ  = "NI-6363"
CARD_DMM  = "NI-4065"
CARD_SMU1 = "NI-4140"
CARD_SMU2 = "NI-4139"
CARD_SMU3 = "NI-4130"
CARD_RELAY = "NI-2569"

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
