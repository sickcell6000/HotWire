"""
Pure-software simulation layer for HotWire.

In real deployments, HotWire relies on HomePlug PLC modems for SLAC /
Layer-2 pairing, and on IPv6 multicast for SDP (SECC Discovery Protocol).
Both require raw packet capture (pcap) and physical PLC hardware.

For AEC reviewers, CI tests, and developer-loop work, we need two
HotWire processes on one host to be able to handshake over TCP loopback
alone. This module provides a **drop-in replacement** for the heavyweight
``pyPlcHomeplug`` layer that:

  1. Fakes SLAC/modem discovery (immediately reports success)
  2. Skips SDP over IPv6 multicast — both sides agree on ``::1`` + a
     pre-agreed port via config
  3. Does not touch ``pcap`` or raw sockets

The state machine code (``fsmEvse``, ``fsmPev``) is **unchanged**: from
its perspective, the connection manager simply reports that all lower
layers are healthy almost instantly.
"""
from __future__ import annotations

from typing import Callable

from ..core.modes import C_EVSE_MODE, C_LISTEN_MODE, C_PEV_MODE
from .tcp_socket import _resolve_tcp_port


class SimulatedHomePlug:
    """Pretend PLC/SLAC/SDP layer for in-process end-to-end testing.

    Drop-in replacement for ``pyPlcHomeplug.pyPlcHomeplug`` that skips
    all modem / SLAC / SDP traffic and immediately reports healthy
    connection levels to the ``connMgr``. The FSMs then proceed to the
    TCP stage, which runs over ``::1`` loopback.
    """

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Callable[[str, str], None],
        mode: int,
        addrMan,
        connMgr,
        isSimulationMode: int = 1,
    ) -> None:
        self.callbackAddToTrace = callbackAddToTrace
        self.callbackShowStatus = callbackShowStatus
        self.mode = mode
        self.addressManager = addrMan
        self.connMgr = connMgr
        self.isSimulationMode = isSimulationMode

        # SLAC state — FSM inspects these in some places.
        self.iAmEvse = 1 if mode == C_EVSE_MODE else 0
        self.iAmPev = 1 if mode == C_PEV_MODE else 0
        self.iAmListener = 1 if mode == C_LISTEN_MODE else 0

        # Port used by the simulated peer (EVSE listens, PEV connects).
        self._simulated_port = _resolve_tcp_port()
        self._bootstrap_done = False

        self.addToTrace("[SimulatedHomePlug] pure-software mode — skipping SLAC/SDP")

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    # ---- Bootstrap handshake ----------------------------------------

    def _bootstrap(self) -> None:
        """One-shot: tell connMgr that every lower layer is healthy."""
        if self._bootstrap_done:
            return
        # Pretend both modems present.
        self.connMgr.ModemFinderOk(2)
        # Pretend SLAC completed.
        self.connMgr.SlacOk()

        if self.mode == C_PEV_MODE:
            # PEV: pretend SDP located the EVSE at loopback.
            self.addressManager.setSeccIp("::1")
            self.addressManager.setSeccTcpPort(self._simulated_port)
            self.connMgr.SdpOk()
            self.addToTrace(
                f"[SimulatedHomePlug] PEV: pretending SDP found EVSE at ::1:{self._simulated_port}"
            )
        else:
            # EVSE side — nothing special, it will listen on ::1 via pyPlcTcpServerSocket.
            self.addToTrace(
                f"[SimulatedHomePlug] EVSE: ready to accept TCP on ::1:{self._simulated_port}"
            )

        self._bootstrap_done = True

    # ---- pyPlcHomeplug-compatible API ------------------------------

    def mainfunction(self) -> None:
        """Called every ~30 ms by the worker. Does the bootstrap once."""
        self._bootstrap()

    def enterPevMode(self) -> None:
        self.iAmEvse = 0
        self.iAmPev = 1
        self.iAmListener = 0
        self.mode = C_PEV_MODE
        self._bootstrap_done = False
        self.callbackShowStatus("PEV mode", "mode")

    def enterEvseMode(self) -> None:
        self.iAmEvse = 1
        self.iAmPev = 0
        self.iAmListener = 0
        self.mode = C_EVSE_MODE
        self._bootstrap_done = False
        self.callbackShowStatus("EVSE mode", "mode")

    def enterListenMode(self) -> None:
        self.iAmEvse = 0
        self.iAmPev = 0
        self.iAmListener = 1
        self.mode = C_LISTEN_MODE
        self.callbackShowStatus("LISTEN mode", "mode")

    def sendTestFrame(self, strAction: str) -> None:
        """No-op in simulation (real implementation sends raw HomePlug frames)."""
        self.addToTrace(f"[SimulatedHomePlug] sendTestFrame({strAction}) ignored")

    def printToUdp(self, s: str) -> None:
        # UDP syslog is disabled in simulation.
        pass

    def sendSpecialMessageToControlThePowerSupply(self, targetVoltage, targetCurrent):
        # The real implementation talks to the Arduino-controlled power supply.
        # In simulation, just log the command.
        self.addToTrace(
            f"[SimulatedHomePlug] power supply would be set to {targetVoltage}V / {targetCurrent}A"
        )
        self.callbackShowStatus(str(targetVoltage), "PowerSupplyUTarget")
