"""
Hardware interface — physical power supply, Arduino, serial display, etc.

Two variants:
  - ``HardwareInterface`` (real): talks to Arduino/Dieter board via serial.
    Requires pyserial and a physical PLC testbed. See archive/legacy-evse for
    the full implementation.
  - ``SimulatedHardwareInterface`` (mock): returns plausible values for
    end-to-end simulation without any hardware. Used whenever the worker is
    constructed with ``isSimulationMode=1``.

Adapted from pyPLC's hardwareInterface.py (GPL-3.0, uhi22).
"""
from __future__ import annotations

from typing import Callable, Optional

from .config import getConfigValueBool


class SimulatedHardwareInterface:
    """Returns plausible dummy values so the FSMs run end-to-end without hardware."""

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Callable[[str, str], None] | None = None,
        hp=None,
    ) -> None:
        self.callbackAddToTrace = callbackAddToTrace
        self.callbackShowStatus = callbackShowStatus
        self.hp = hp

        # Virtual battery state.
        self._soc: float = 30.0
        self._accu_voltage: float = 350.0
        self._accu_max_voltage: float = 400.0
        self._accu_max_current: float = 125.0
        self._inlet_voltage: float = 0.0
        self._charger_voltage: float = 0.0
        self._charger_current: float = 0.0
        self._charger_max_voltage: float = 500.0
        self._charger_max_current: float = 200.0

        # Relay / CP state.
        self._power_relay_on: bool = False
        self._relay2_on: bool = False
        self._connector_locked: bool = False

        # Attack-related stop flags.
        self._user_stop_request: bool = False
        self._is_accu_full: bool = False

        self.isSerialInterfaceOk = False
        self.ser = None

        self.addToTrace("initialized (simulation mode, no real hardware)")

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace("[HARDWAREINTERFACE-SIM] " + s)

    # ---- CP (Control Pilot) -----------------------------------------

    def setStateB(self) -> None:
        self.addToTrace("CP -> State B")

    def setStateC(self) -> None:
        self.addToTrace("CP -> State C (charging)")

    def setPowerRelayOn(self) -> None:
        self._power_relay_on = True
        self.addToTrace("Power relay ON")

    def setPowerRelayOff(self) -> None:
        self._power_relay_on = False
        self.addToTrace("Power relay OFF")

    def setRelay2On(self) -> None:
        self._relay2_on = True

    def setRelay2Off(self) -> None:
        self._relay2_on = False

    def getPowerRelayConfirmation(self) -> bool:
        return self._power_relay_on

    def triggerConnectorLocking(self) -> None:
        self._connector_locked = True
        self.addToTrace("Connector locked")

    def triggerConnectorUnlocking(self) -> None:
        self._connector_locked = False
        self.addToTrace("Connector unlocked")

    def isConnectorLocked(self) -> bool:
        return self._connector_locked

    # ---- Battery / charger state ------------------------------------

    def getSoc(self) -> float:
        # Simulate rising SoC over time if the config enables it.
        if getConfigValueBool("soc_simulation") and self._power_relay_on:
            self._soc = min(self._soc + 0.05, 95.0)
        return self._soc

    def getAccuVoltage(self) -> float:
        return self._accu_voltage

    def getAccuMaxVoltage(self) -> float:
        return self._accu_max_voltage

    def getAccuMaxCurrent(self) -> float:
        return self._accu_max_current

    def getInletVoltage(self) -> float:
        return self._inlet_voltage

    def getIsAccuFull(self) -> bool:
        return self._is_accu_full

    def stopRequest(self) -> bool:
        return self._user_stop_request

    def setStopRequest(self, value: bool) -> None:
        self._user_stop_request = value

    def simulatePreCharge(self) -> None:
        # Gradually ramp inlet voltage toward accu voltage to mimic a real precharge.
        if self._inlet_voltage < self._accu_voltage:
            self._inlet_voltage = min(self._inlet_voltage + 10, self._accu_voltage)

    def setChargerParameters(self, maxVoltage: float, maxCurrent: float) -> None:
        self._charger_max_voltage = maxVoltage
        self._charger_max_current = maxCurrent
        self.addToTrace(f"Charger reports max {maxVoltage}V / {maxCurrent}A")

    def setChargerVoltageAndCurrent(self, voltage: float, current: float) -> None:
        self._charger_voltage = voltage
        self._charger_current = current

    # ---- Display / output -------------------------------------------

    def displayStateAndSoc(self, state: str, soc: float = -1) -> None:
        pass  # no real display in simulation

    def showOnDisplay(self, state: str, aux1: str = "", aux2: str = "") -> None:
        pass

    # ---- Lifecycle --------------------------------------------------

    def mainfunction(self) -> None:
        # Real implementation polls the serial port. Nothing to do in simulation.
        pass

    def close(self) -> None:
        pass


def hardwareInterface(
    callbackAddToTrace: Callable[[str], None],
    callbackShowStatus: Callable[[str, str], None] | None = None,
    hp=None,
    isSimulationMode: int = 0,
):
    """Factory — returns simulated or real interface based on mode.

    The real hardware interface lives in archive/legacy-evse/hardwareInterface.py
    and depends on pyserial + physical testbed. Since HotWire v1 ships with
    simulation as the primary tested mode, we default to the mock here.
    """
    if isSimulationMode:
        return SimulatedHardwareInterface(callbackAddToTrace, callbackShowStatus, hp)
    # Lazy import so pyserial is only needed when running with real hardware.
    try:
        from ..plc.real_hardware import RealHardwareInterface  # type: ignore
        return RealHardwareInterface(callbackAddToTrace, callbackShowStatus, hp)
    except ImportError:
        print("Real hardware interface not available; falling back to simulation.")
        return SimulatedHardwareInterface(callbackAddToTrace, callbackShowStatus, hp)
