"""
Base class for all hardware interfaces.
All hardware drivers inherit from this.
"""

import logging
from abc import ABC, abstractmethod


class HardwareBase(ABC):
    """Minimal interface every hardware driver must implement."""

    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.log = logging.getLogger(f"nipxi.hw.{name}")

    @abstractmethod
    def connect(self):
        """Open connection to the physical device."""

    @abstractmethod
    def disconnect(self):
        """Close connection cleanly."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    def __repr__(self):
        state = "connected" if self.connected else "disconnected"
        return f"<{self.__class__.__name__} name={self.name} {state}>"
