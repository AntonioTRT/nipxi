"""
PXI rack interface placeholder.
Handles chassis initialization and card discovery via NI-VISA / NI-DAQmx.

TODO: Implement using `nidaqmx` and `pyvisa` libraries.
"""

import logging
from hardware.base import HardwareBase


class PXIRack(HardwareBase):
    """
    Represents the NI PXI chassis.
    Cards present:
        Slot 2 - NI 6363 DAQ
        Slot 3 - NI 4065 DMM
        Slot 4 - NI 4140 / 4139 SMU
        Slot 5 - NI 4130 SMU (optional)
    """

    def __init__(self, simulate: bool = False):
        super().__init__("PXIRack")
        self.simulate = simulate
        self.cards = {}   # populated on connect()

    def connect(self):
        self.log.info("Connecting to PXI rack (simulate=%s)...", self.simulate)
        # TODO: nidaqmx.system.System().devices to enumerate cards
        # TODO: pyvisa.ResourceManager().open_resource(resource_string)
        self.connected = True
        self.log.info("PXI rack connected.")

    def disconnect(self):
        self.log.info("Disconnecting PXI rack...")
        # TODO: close all open VISA/DAQmx sessions
        self.connected = False

    def get_card(self, resource_string: str):
        """Return an already-open card handle or None."""
        return self.cards.get(resource_string)
