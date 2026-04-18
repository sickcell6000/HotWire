"""
Real HomePlug PLC driver — SLAC / SDP / pcap integration.

This is a **ported skeleton** of the legacy ``pyPlcHomeplug.py`` (1,279
lines). It exposes the same interface as
:class:`hotwire.plc.simulation.SimulatedHomePlug` so a :class:`HotWireWorker`
can plug one in place of the other based on the ``isSimulationMode``
flag.

Status: **alpha**. The class scaffolding, pcap interface discovery,
MAC frame constants, and per-mode bootstrap (EVSE listen / PEV dispatch)
are in place. The full SLAC state machine (CM_SLAC_PARAM, CM_MNBC_SOUND,
CM_ATTEN_CHAR, CM_VALIDATE, CM_SLAC_MATCH, CM_SET_KEY) is intentionally
delegated to the legacy ``archive/legacy-evse/pyPlcHomeplug.py`` until
hardware-in-the-loop testing confirms the port works on a physical
QCA7005 modem.

To use the real driver:

    export HOTWIRE_HARDWARE=1
    python scripts/run_evse.py --hw
    # Falls back to SimulatedHomePlug if pypcap or the interface is missing.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from ..core.modes import C_EVSE_MODE, C_LISTEN_MODE, C_PEV_MODE
from .tcp_socket import _resolve_tcp_port


# HomePlug Green PHY management message types (subset — see legacy for full list).
CM_SET_KEY = 0x6008
CM_GET_KEY = 0x600C
CM_SLAC_PARAM = 0x6064
CM_START_ATTEN_CHAR = 0x6068
CM_ATTEN_CHAR = 0x606C
CM_MNBC_SOUND = 0x6074
CM_VALIDATE = 0x6078
CM_SLAC_MATCH = 0x607C
CM_GET_SW = 0xA000

MMTYPE_REQ = 0x0000
MMTYPE_CNF = 0x0001
MMTYPE_IND = 0x0002
MMTYPE_RSP = 0x0003

# HomePlug AV Ethertype.
HOMEPLUG_ETHERTYPE = 0x88E1

# Broadcast MAC.
MAC_BROADCAST = b"\xff\xff\xff\xff\xff\xff"


class RealHomePlug:
    """pcap-backed HomePlug driver.

    Parameters match :class:`SimulatedHomePlug` so the worker can treat
    the two interchangeably. Key differences:

    * pcap interface is opened at construction time and used for both
      sniffing (to pick up SLAC traffic) and transmission (to send
      CM_SET_KEY / CM_SLAC_MATCH / CM_VALIDATE frames).
    * ``mainfunction`` drives a real SLAC state machine in place of the
      simulation's one-shot bootstrap.
    * SDP (SECC Discovery Protocol) is performed via UDP multicast to
      ``ff02::1`` on the PLC interface instead of pretending the EVSE is
      at ``::1``.

    When pcap is unavailable or the ethernet interface is missing, the
    constructor raises :class:`RuntimeError` — the caller should catch
    that and fall back to the simulation driver.
    """

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Callable[[str, str], None],
        mode: int,
        addrMan: Any,
        connMgr: Any,
        isSimulationMode: int = 0,
    ) -> None:
        # Refuse to operate in simulation mode — that's the other driver's job.
        if isSimulationMode:
            raise RuntimeError(
                "RealHomePlug cannot run in simulation mode — use SimulatedHomePlug"
            )

        self.callbackAddToTrace = callbackAddToTrace
        self.callbackShowStatus = callbackShowStatus
        self.mode = mode
        self.addressManager = addrMan
        self.connMgr = connMgr
        self.isSimulationMode = 0

        # Mode flags exposed to the FSMs.
        self.iAmEvse = 1 if mode == C_EVSE_MODE else 0
        self.iAmPev = 1 if mode == C_PEV_MODE else 0
        self.iAmListener = 1 if mode == C_LISTEN_MODE else 0

        self._setup_pcap()

        # SLAC state machine (minimal port — see archive/ for the full one).
        self.slac_state = 0
        self.nmk = bytes(16)
        self.nid = bytes(7)

        self.addToTrace("[RealHomePlug] pcap driver initialised")

    # ---- logging ---------------------------------------------------

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    # ---- pcap setup ------------------------------------------------

    def _setup_pcap(self) -> None:
        try:
            import pcap                                        # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "pypcap is not importable. pip install pcap-ct (note: the "
                "'pcap-ct' package, NOT 'pypcap' or 'libpcap')."
            ) from e
        self._pcap = pcap

        # The interface name comes from hotwire.ini: `eth_interface` on
        # Linux/macOS or `eth_windows_interface_name` on Windows.
        try:
            from ..core.config import getConfigValue
            if sys.platform.startswith("win"):
                iface = getConfigValue("eth_windows_interface_name")
            else:
                iface = getConfigValue("eth_interface")
        except (SystemExit, Exception) as e:
            raise RuntimeError(f"missing PLC interface config: {e}") from e

        try:
            self._sock = pcap.pcap(
                name=iface,
                promisc=True,
                immediate=True,
                timeout_ms=50,
            )
        except Exception as e:                                  # noqa: BLE001
            raise RuntimeError(
                f"could not open pcap on {iface!r}: {e}"
            ) from e

        self.addToTrace(f"[RealHomePlug] opened interface {iface!r}")
        self._iface = iface

    # ---- main loop -------------------------------------------------

    def mainfunction(self) -> None:
        """Called by :class:`HotWireWorker` every ~30 ms.

        TODO — port the legacy SLAC state machine here. The sketch:

        1. Read one (at most) raw frame from ``self._sock`` via a short
           non-blocking poll.
        2. If we're the PEV and we see CM_SLAC_PARAM.CNF, progress the
           SLAC state. Drive CM_START_ATTEN_CHAR, CM_MNBC_SOUND,
           CM_ATTEN_CHAR.IND, CM_VALIDATE.REQ, CM_SLAC_MATCH.REQ in
           sequence.
        3. If we're the EVSE, respond to CM_SLAC_PARAM.REQ and sound.
        4. On SLAC completion, program the modem NMK via CM_SET_KEY.REQ
           and report connMgr.ModemFinderOk(2) + connMgr.SlacOk().
        5. Kick off SDP (UDP multicast) once SLAC is done, report
           SdpOk() when the EVSE is reachable.

        Until that's done, this driver is a **scaffold**: it opens the
        pcap interface but doesn't drive the state machine. Use the
        simulation driver (``isSimulationMode=1``) for anything other
        than bench-top hardware experiments.
        """
        pass  # see TODO above

    # ---- mode transitions (match SimulatedHomePlug interface) -----

    def enterPevMode(self) -> None:
        self.iAmEvse, self.iAmPev, self.iAmListener = 0, 1, 0
        self.mode = C_PEV_MODE
        self.callbackShowStatus("PEV mode", "mode")

    def enterEvseMode(self) -> None:
        self.iAmEvse, self.iAmPev, self.iAmListener = 1, 0, 0
        self.mode = C_EVSE_MODE
        self.callbackShowStatus("EVSE mode", "mode")

    def enterListenMode(self) -> None:
        self.iAmEvse, self.iAmPev, self.iAmListener = 0, 0, 1
        self.mode = C_LISTEN_MODE
        self.callbackShowStatus("LISTEN mode", "mode")

    def sendTestFrame(self, strAction: str) -> None:
        # Legacy test hook; low priority to port.
        self.addToTrace(f"[RealHomePlug] sendTestFrame({strAction}) ignored")

    def printToUdp(self, s: str) -> None:
        # TODO: port udplog.py when we care about remote syslog output.
        pass

    def sendSpecialMessageToControlThePowerSupply(
        self, targetVoltage: Any, targetCurrent: Any
    ) -> None:
        # Real implementation sends a proprietary frame to the Arduino
        # power supply board. Until the hardware interface is wired up
        # (see hotwire/core/hardware_interface.py) this is a log-only stub.
        self.addToTrace(
            f"[RealHomePlug] PS target {targetVoltage}V / {targetCurrent}A"
        )
        self.callbackShowStatus(str(targetVoltage), "PowerSupplyUTarget")


def build_homeplug(
    callbackAddToTrace: Callable[[str], None],
    callbackShowStatus: Callable[[str, str], None],
    mode: int,
    addrMan: Any,
    connMgr: Any,
    isSimulationMode: int = 0,
):
    """Factory — returns :class:`RealHomePlug` if hardware mode is
    requested and the driver can open its pcap interface, otherwise
    falls back to :class:`SimulatedHomePlug` with a log warning.
    """
    if isSimulationMode:
        from .simulation import SimulatedHomePlug
        return SimulatedHomePlug(
            callbackAddToTrace, callbackShowStatus, mode, addrMan, connMgr,
            isSimulationMode=isSimulationMode,
        )
    try:
        return RealHomePlug(
            callbackAddToTrace, callbackShowStatus, mode, addrMan, connMgr,
            isSimulationMode=0,
        )
    except RuntimeError as e:
        callbackAddToTrace(f"[RealHomePlug] unavailable: {e}")
        callbackAddToTrace("[RealHomePlug] falling back to SimulatedHomePlug")
        from .simulation import SimulatedHomePlug
        return SimulatedHomePlug(
            callbackAddToTrace, callbackShowStatus, mode, addrMan, connMgr,
            isSimulationMode=1,
        )
