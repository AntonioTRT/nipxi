"""
SMU (Source Measure Unit) interface placeholder.
Covers NI 4140, 4139, 4130 cards used for charge and discharge.

TODO: Implement using `nidcpower` (NI-DCPower Python bindings).
"""

from hardware.base import HardwareBase


class SMU(HardwareBase):
    """
    Controls an NI SMU card for CC-CV charge and CC discharge.

    Typical workflow:
        smu.connect()
        smu.set_charge_mode(current_a=0.5, voltage_limit_v=4.2)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.set_discharge_mode(current_a=0.5, voltage_limit_v=3.0)
        smu.output_enable()
        ... measure loop ...
        smu.output_disable()
        smu.disconnect()
    """

    def __init__(self, resource: str):
        super().__init__(f"SMU_{resource}")
        self.resource = resource
        self._session = None

    def connect(self):
        self.log.info("Opening SMU session: %s", self.resource)
        # TODO: import nidcpower; self._session = nidcpower.Session(self.resource)
        self.connected = True

    def disconnect(self):
        # TODO: self._session.close()
        self.connected = False
        self.log.info("SMU session closed: %s", self.resource)

    def set_charge_mode(self, current_a: float, voltage_limit_v: float):
        """Configure CC-CV charge. Call before output_enable()."""
        self.log.debug("SMU charge mode: %.3f A / %.3f V", current_a, voltage_limit_v)
        # TODO: configure nidcpower for CC-CV source

    def set_discharge_mode(self, current_a: float, voltage_limit_v: float):
        """Configure CC discharge (sink). Call before output_enable()."""
        self.log.debug("SMU discharge mode: %.3f A / %.3f V", current_a, voltage_limit_v)
        # TODO: configure nidcpower for current sink

    def output_enable(self):
        """Enable SMU output/sink."""
        # TODO: self._session.initiate()
        self.log.info("SMU output enabled.")

    def output_disable(self):
        """Disable SMU output/sink (safe standby)."""
        # TODO: self._session.abort()
        self.log.info("SMU output disabled.")

    def measure(self) -> dict:
        """Return instantaneous voltage and current reading."""
        # TODO: return self._session.measure(nidcpower.MeasurementTypes.VOLTAGE, CURRENT)
        return {"voltage_v": 0.0, "current_a": 0.0}
