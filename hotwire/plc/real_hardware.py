"""Real-hardware ``HardwareInterface`` for HotWire.

Drop-in replacement for :class:`hotwire.core.hardware_interface.SimulatedHardwareInterface`
when a physical tester board (Arduino / ESP32 / 'celeron55' style) is
wired up to the host's serial port. Presents the exact same method
surface the worker and FSMs rely on.

Adapted from pyPLC's ``hardwareInterface.py`` (GPL-3.0, uhi22),
trimmed to the serial / celeron55 backend only. MQTT, BeagleBone GPIO,
and CHAdeMO CAN paths are intentionally dropped: they're not needed
for HotWire's V2G bench workflow, and keeping them would drag in
``paho-mqtt`` / ``Adafruit_BBIO`` / ``python-can`` as hard deps.

Serial wire format (celeron55 dialect):

* Outbound commands (HotWire → tester):
  ``cp=0\n`` / ``cp=1\n`` — set CP line to state B / C
  ``contactor=0\n`` / ``contactor=1\n`` — open / close the power relay
  ``lock\n`` / ``unlock\n`` — connector lock motor
  ``disp0=<text>\n`` … — LCD line updates (optional)

* Inbound telemetry (tester → HotWire), one ``key=value`` per line:
  ``inlet_v=<int>`` — inlet voltage in volts (PEV only)
  ``dc_link_v=<int>`` — DC-link voltage (EVSE only, post-contactor)
  ``cp_pwm=<int>`` — measured CP duty cycle (0–100)
  ``cp_output_state=<0|1>`` — echo of the last ``cp=`` command
  ``ccs_contactor_wanted_closed=<0|1>`` — echo of last ``contactor=``
  ``max_charge_a=<int>`` — BMS reported max-charge current (PEV)
  ``soc_percent=<int>`` — BMS state of charge (PEV)
  ``contactor_confirmed=<0|1>`` — hardware contactor feedback
  ``plugged_in=<0|1>`` — proximity pilot detect

If the serial port can't be opened the interface degrades gracefully:
logs a warning, flips ``isSerialInterfaceOk = False``, and returns
plausible defaults for the getters so the V2G state machine still
makes forward progress. This is deliberate — it lets the operator
bring up HotWire against real PLC modems *without* the full tester
board while they wire up the analog front end.
"""
from __future__ import annotations

from time import time
from typing import Callable, Optional

try:
    import serial  # type: ignore
    from serial.tools.list_ports import comports  # type: ignore
    _HAS_PYSERIAL = True
except ImportError:  # pragma: no cover — pyserial is a soft dep
    serial = None  # type: ignore
    comports = None  # type: ignore
    _HAS_PYSERIAL = False

from ..core.config import getConfigValue, getConfigValueBool, load


# Output-bit layout, matches pyPLC's ``outvalue`` contract so any
# tester firmware written for pyPLC also works with HotWire:
#   bit 0 — CP state (0 = B, 1 = C)
#   bit 1 — power relay (0 = open, 1 = closed)
#   bit 2 — relay 2 (optional)
_OUT_BIT_CP = 1 << 0
_OUT_BIT_POWER_RELAY = 1 << 1
_OUT_BIT_RELAY2 = 1 << 2


def _cfg(key: str, default: str) -> str:
    """Read a [general] key with a fallback — doesn't sys.exit like
    ``getConfigValue`` does on missing keys, since most RealHardwareInterface
    config is optional (the board may simply not be present)."""
    try:
        cfg = load()
    except SystemExit:
        return default
    try:
        if cfg.has_option("general", key):
            return cfg.get("general", key)
    except Exception:  # noqa: BLE001
        pass
    return default


def _cfg_bool(key: str, default: bool) -> bool:
    raw = _cfg(key, "true" if default else "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


class RealHardwareInterface:
    """Real serial-backed tester board driver.

    Same public API as :class:`SimulatedHardwareInterface`. Constructor
    never raises — if the serial port is missing the object goes into
    "stub real" mode (``isSerialInterfaceOk=False``) and uses stored
    defaults for every getter.
    """

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Optional[Callable[[str, str], None]] = None,
        hp=None,
    ) -> None:
        self.callbackAddToTrace = callbackAddToTrace
        self.callbackShowStatus = callbackShowStatus
        self.homeplughandler = hp
        self.hp = hp  # alias kept for symmetry with sim interface

        # Output-latch byte; every setState* / setRelay* call flips a bit.
        self.outvalue: int = 0

        # Internal battery / charger state. Getters return these values
        # directly; the serial main-loop updates them from inbound lines.
        # accuVoltage seeds PEV.EVTargetVoltage in PreChargeReq — leaving
        # it at 0 makes the EVSE answer with its 350V fallback every tick
        # and the PEV never satisfies its precharge-end condition. Default
        # to ``charge_target_voltage`` so the synthetic battery presents a
        # voltage matching what the EVSE will report back, closing the
        # PreCharge → CurrentDemand transition on the first response.
        self.inletVoltage: float = 0.0
        self.accuVoltage: float = float(_cfg("charge_target_voltage", "400"))
        self.accuMaxCurrent: float = 9.0
        self.accuMaxVoltage: float = float(_cfg("charge_target_voltage", "400"))
        self.chargerVoltage: float = 0.0
        self.chargerCurrent: float = 0.0
        self.maxChargerVoltage: float = 0.0
        self.maxChargerCurrent: float = 10.0
        self.cp_pwm: float = 0.0
        self.soc_percent: float = float(_cfg("initial_soc_percent", "30"))
        self.simulatedSoc: float = self.soc_percent
        self.IsAccuFull: bool = False
        self.contactor_confirmed: bool = False
        self.plugged_in: Optional[bool] = None
        self.lock_confirmed: bool = False
        self.enabled: bool = True  # flip False to request an orderly stop
        self.demoAuthenticationCounter: int = 0

        self.rxbuffer: str = ""
        self.lastReceptionTime: float = 0.0

        # Tracks last-logged values so mainfunction() only emits traces
        # when telemetry changes — keeps the log readable on a steady
        # session.
        self._logged_inlet_v: Optional[float] = None
        self._logged_dc_link_v: Optional[float] = None
        self._logged_cp_pwm: Optional[float] = None
        self._logged_max_charge_a: Optional[float] = None
        self._logged_soc_percent: Optional[float] = None
        self._logged_contactor_confirmed: Optional[bool] = None
        self._logged_plugged_in: Optional[bool] = None

        # Serial port — may stay None if not available. Never crashes.
        self.ser: Optional["serial.Serial"] = None
        self.isSerialInterfaceOk: bool = False
        self._open_serial_port()

        self.addToTrace(
            f"initialized (serial_ok={self.isSerialInterfaceOk}, "
            f"mode=celeron55-compat)"
        )

    # ---- logging ----------------------------------------------------

    def addToTrace(self, s: str) -> None:
        msg = "[HARDWAREINTERFACE-REAL] " + s
        if self.callbackAddToTrace is not None:
            self.callbackAddToTrace(msg)
        else:  # fallback so standalone unit tests still see output
            print(msg)

    # ---- serial bring-up --------------------------------------------

    def _open_serial_port(self) -> None:
        """Open the configured port or auto-pick the first USB-serial.
        Swallows every error: if anything goes wrong we just run in
        stub-real mode with ``isSerialInterfaceOk = False``."""
        if not _HAS_PYSERIAL:
            self.addToTrace(
                "pyserial not installed; running in stub-real mode"
            )
            return

        baud_str = _cfg("serial_baud", "115200")
        try:
            baud = int(baud_str)
        except ValueError:
            baud = 115200

        configured_port = _cfg("serial_port", "auto")

        if configured_port != "auto":
            self._try_open(configured_port, baud)
            return

        # Auto-detect. Skip the Pi's built-in UART (ttyAMA0) — that's
        # only useful for headless console, never a tester board.
        if comports is None:
            return
        candidates: list[str] = []
        for port, desc, _hwid in sorted(comports()):
            if port == "/dev/ttyAMA0":
                self.addToTrace(f"skipping {port} (Pi built-in UART)")
                continue
            candidates.append(port)
            self.addToTrace(f"candidate serial port: {port} ({desc})")

        if not candidates:
            self.addToTrace(
                "no USB-serial ports detected; stub-real mode"
            )
            return

        self._try_open(candidates[0], baud)

    def _try_open(self, port: str, baud: int) -> None:
        try:
            self.ser = serial.Serial(port, baud, timeout=0)
            self.isSerialInterfaceOk = True
            self.addToTrace(f"serial open: {port} @ {baud} baud")
        except Exception as e:  # noqa: BLE001
            self.addToTrace(f"serial open failed ({port}): {e}")
            self.ser = None
            self.isSerialInterfaceOk = False

    def _write(self, command: str) -> None:
        """Send a line to the tester board. No-op in stub-real mode."""
        if not self.isSerialInterfaceOk or self.ser is None:
            return
        try:
            self.ser.write(command.encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.addToTrace(f"serial write failed: {e}")
            self.isSerialInterfaceOk = False

    # ---- CP (Control Pilot) -----------------------------------------

    def setStateB(self) -> None:
        self.addToTrace("CP -> State B")
        self._write("cp=0\n")
        self.outvalue &= ~_OUT_BIT_CP

    def setStateC(self) -> None:
        self.addToTrace("CP -> State C (charging)")
        self._write("cp=1\n")
        self.outvalue |= _OUT_BIT_CP

    def setPowerRelayOn(self) -> None:
        self.addToTrace("Power relay ON")
        self._write("contactor=1\n")
        self.outvalue |= _OUT_BIT_POWER_RELAY

    def setPowerRelayOff(self) -> None:
        self.addToTrace("Power relay OFF")
        self._write("contactor=0\n")
        self.outvalue &= ~_OUT_BIT_POWER_RELAY

    def setRelay2On(self) -> None:
        self.outvalue |= _OUT_BIT_RELAY2

    def setRelay2Off(self) -> None:
        self.outvalue &= ~_OUT_BIT_RELAY2

    def getPowerRelayConfirmation(self) -> bool:
        # Firmware echoes ``contactor_confirmed=1`` once the coil settles.
        if self.isSerialInterfaceOk:
            return self.contactor_confirmed
        # Stub-real: report "closed" whenever the output bit is set so
        # the charger FSM can still advance.
        return bool(self.outvalue & _OUT_BIT_POWER_RELAY)

    def triggerConnectorLocking(self) -> None:
        self.addToTrace("Connector lock")
        self._write("lock\n")

    def triggerConnectorUnlocking(self) -> None:
        self.addToTrace("Connector unlock")
        self._write("unlock\n")

    def isConnectorLocked(self) -> bool:
        return True  # placeholder until the tester reports ``lock=``

    # ---- Battery / charger state ------------------------------------

    def getInletVoltage(self) -> float:
        return float(self.inletVoltage)

    def getAccuVoltage(self) -> float:
        return float(self.accuVoltage)

    def getAccuMaxCurrent(self) -> float:
        # Production EV firmwares usually clamp at the inlet rating; keep
        # a conservative default when nothing has been reported yet.
        ev_max = 250.0
        if self.accuMaxCurrent >= ev_max:
            return ev_max
        return float(self.accuMaxCurrent)

    def getAccuMaxVoltage(self) -> float:
        return float(self.accuMaxVoltage)

    def getIsAccuFull(self) -> bool:
        # Prefer live telemetry; fall back to the simulated SOC ramp
        # that ``mainfunction`` runs when soc_simulation is enabled.
        if self.isSerialInterfaceOk:
            self.IsAccuFull = (self.soc_percent >= 98)
        else:
            self.IsAccuFull = (self.simulatedSoc >= 98)
        return self.IsAccuFull

    def getSoc(self) -> float:
        if self.callbackShowStatus is not None:
            self.callbackShowStatus(format(self.soc_percent, ".1f"), "soc")
        if self.isSerialInterfaceOk:
            return float(self.soc_percent)
        return float(self.simulatedSoc)

    def stopRequest(self) -> bool:
        return not self.enabled

    def setStopRequest(self, value: bool) -> None:
        self.enabled = not value

    def isUserAuthenticated(self) -> bool:
        # Two-step demo: first call returns False ("pending"), next True.
        # pyPLC upstream discussion: uhi22/pyPLC#28.
        if self.demoAuthenticationCounter < 1:
            self.demoAuthenticationCounter += 1
            return False
        return True

    def simulatePreCharge(self) -> None:
        """Ramp the simulated inlet voltage toward the simulated battery
        voltage during PreCharge.

        Same gotcha as ``getSoc``: when ``isSerialInterfaceOk`` is True
        but no Arduino telemetry is actually arriving (the common case
        on a Pi PEV with a CP2102 USB-serial bridge plugged in but no
        real BMS firmware behind it), ``inletVoltage`` only advances if
        the tester sends ``inlet_voltage=<int>`` lines. Without them
        ``inletVoltage`` stays pinned at 0 V, and the PEV's PreCharge
        completion check
        (``use_evsepresentvoltage_for_precharge_end = no`` path) sees
        0 V vs ``accu_v`` 220 V and never exits PreCharge.

        Same fix pattern as commit ddc557f: ramp ``inletVoltage``
        unconditionally when sim is requested, then let any later
        Arduino telemetry overwrite it.
        """
        if self.inletVoltage < self.accuVoltage:
            self.inletVoltage = min(self.inletVoltage + 10.0, self.accuVoltage)

    def setChargerParameters(
        self, maxVoltage: float, maxCurrent: float
    ) -> None:
        self.addToTrace(
            f"Charger reports max {int(maxVoltage)}V / {int(maxCurrent)}A"
        )
        self.maxChargerVoltage = int(maxVoltage)
        self.maxChargerCurrent = int(maxCurrent)

    def setChargerVoltageAndCurrent(
        self, voltageNow: float, currentNow: float
    ) -> None:
        self.chargerVoltage = int(voltageNow)
        self.chargerCurrent = int(currentNow)

    def setPowerSupplyVoltageAndCurrent(
        self, targetVoltage: float, targetCurrent: float
    ) -> None:
        # If we've been wired to a modem handler, forward the set-point
        # over the PLC link so the charger's real PSU follows. Silent
        # no-op when running SDP/TCP-only smoke tests.
        if self.homeplughandler is not None and hasattr(
            self.homeplughandler,
            "sendSpecialMessageToControlThePowerSupply",
        ):
            try:
                self.homeplughandler.sendSpecialMessageToControlThePowerSupply(
                    targetVoltage, targetCurrent
                )
            except Exception as e:  # noqa: BLE001
                self.addToTrace(f"PSU set-point forward failed: {e}")

    # ---- Display / output -------------------------------------------

    def displayStateAndSoc(self, state: str, soc: float = -1) -> None:
        pass  # TODO: route to MQTT or LCD when a real display is wired.

    def showOnDisplay(
        self, state: str, aux1: str = "", aux2: str = ""
    ) -> None:
        if not self.isSerialInterfaceOk:
            return
        if not _cfg_bool("display_via_serial", False):
            return
        self._write(f"disp0={state}\n")
        self._write(f"disp1={aux1}\n")
        self._write(f"disp2={aux2}\n")

    # ---- Lifecycle --------------------------------------------------

    def mainfunction(self) -> None:
        """Called every worker tick.

        Soft-simulates a rising SOC when requested, then drains the
        serial RX buffer line-by-line and updates internal state.
        """
        # SOC simulation — useful for bench tests where the tester
        # board isn't reporting BMS telemetry yet.
        if _cfg_bool("soc_simulation", False):
            if self.outvalue & _OUT_BIT_POWER_RELAY:
                # Same rate as pyPLC: 0.01 %/tick ≈ slow ramp for bulb
                # tests; bump this in config for automated regression.
                if self.simulatedSoc < 100:
                    self.simulatedSoc += 0.01
                # Also bump ``soc_percent`` (the value ``getSoc`` returns
                # in real-Arduino mode). Without this, PEVs running with
                # ``isSerialInterfaceOk = True`` but no actual BMS
                # telemetry on the bus stay pinned at ``initial_soc_percent``
                # forever — the GUI's SoC field never moves and the
                # transmitted CurrentDemandReq.EVRESSSOC stays at 30%.
                # Any incoming ``soc_percent=<int>`` line from the
                # Arduino later in this same tick will overwrite this
                # increment (see the ``soc_percent=`` branch in the
                # serial-line dispatch below), so real-BMS telemetry
                # still wins when present.
                if self.soc_percent < 100:
                    self.soc_percent += 0.01

        if self.isSerialInterfaceOk and self.ser is not None:
            try:
                data = self.ser.read(256)
            except Exception as e:  # noqa: BLE001
                self.addToTrace(f"serial read failed: {e}")
                self.isSerialInterfaceOk = False
                return
            if data:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                self._consume_serial(text)

    def _consume_serial(self, chunk: str) -> None:
        """Line-based parser for celeron55-style telemetry.

        Unknown keys are logged once and ignored so a newer firmware
        doesn't spam the trace just because it added fields.
        """
        self.rxbuffer += chunk
        while True:
            nl = self.rxbuffer.find("\n")
            if nl < 0:
                break
            line = self.rxbuffer[:nl].strip()
            self.rxbuffer = self.rxbuffer[nl + 1:]
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        if line.startswith("inlet_v="):
            try:
                v = int(line[len("inlet_v="):])
            except ValueError:
                return
            self.inletVoltage = v
            if self._logged_inlet_v != v:
                self._logged_inlet_v = v
                self.addToTrace(f"<< inlet_voltage={v}")
            if self.callbackShowStatus is not None:
                self.callbackShowStatus(format(v, ".1f"), "uInlet")
        elif line.startswith("dc_link_v="):
            try:
                v = int(line[len("dc_link_v="):])
            except ValueError:
                return
            self.accuVoltage = v
            if self._logged_dc_link_v != v:
                self._logged_dc_link_v = v
                self.addToTrace(f"<< dc_link_voltage={v}")
        elif line.startswith("cp_pwm="):
            try:
                v = int(line[len("cp_pwm="):])
            except ValueError:
                return
            self.cp_pwm = v
            if self._logged_cp_pwm != v:
                self._logged_cp_pwm = v
                self.addToTrace(f"<< cp_pwm={v}")
        elif line.startswith("cp_output_state="):
            try:
                state = int(line[len("cp_output_state="):])
            except ValueError:
                return
            if bool(state) == bool(self.outvalue & _OUT_BIT_CP):
                self.addToTrace("<< CP state confirmed")
            else:
                self.addToTrace("<< CP state MISMATCH")
        elif line.startswith("ccs_contactor_wanted_closed="):
            try:
                state = int(line[len("ccs_contactor_wanted_closed="):])
            except ValueError:
                return
            if bool(state) == bool(self.outvalue & _OUT_BIT_POWER_RELAY):
                self.addToTrace("<< contactor request confirmed")
            else:
                self.addToTrace("<< contactor request MISMATCH")
        elif line.startswith("max_charge_a="):
            try:
                v = int(line[len("max_charge_a="):])
            except ValueError:
                return
            self.accuMaxCurrent = v
            if self._logged_max_charge_a != v:
                self._logged_max_charge_a = v
                self.addToTrace(f"<< max_charge_a={v}")
        elif line.startswith("soc_percent="):
            try:
                v = int(line[len("soc_percent="):])
            except ValueError:
                return
            self.soc_percent = v
            if self._logged_soc_percent != v:
                self._logged_soc_percent = v
                self.addToTrace(f"<< soc_percent={v}")
        elif line.startswith("contactor_confirmed="):
            try:
                v = bool(int(line[len("contactor_confirmed="):]))
            except ValueError:
                return
            self.contactor_confirmed = v
            if self._logged_contactor_confirmed != v:
                self._logged_contactor_confirmed = v
                self.addToTrace(f"<< contactor_confirmed={int(v)}")
        elif line.startswith("plugged_in="):
            try:
                v = bool(int(line[len("plugged_in="):]))
            except ValueError:
                return
            self.plugged_in = v
            if self._logged_plugged_in != v:
                self._logged_plugged_in = v
                self.addToTrace(f"<< plugged_in={int(v)}")
        else:
            # One-shot warn per unrecognized prefix; avoid trace flood
            # if the firmware blasts an unknown field every tick.
            prefix = line.split("=", 1)[0] if "=" in line else line[:32]
            self.addToTrace(f"<< unknown telemetry: {prefix}=...")
        self.lastReceptionTime = time()

    def close(self) -> None:
        if self.isSerialInterfaceOk and self.ser is not None:
            try:
                self.ser.close()
            except Exception:  # noqa: BLE001
                pass
        self.ser = None
        self.isSerialInterfaceOk = False
