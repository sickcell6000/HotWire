"""
HotWire worker — orchestrates address manager, connection manager, TCP
transport, hardware interface, and the appropriate FSM for the selected
operating mode.

Modeled on pyPLC's ``pyPlcWorker`` (GPL-3.0, uhi22) but slimmed down to
the dependencies that HotWire's clean package structure needs.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..fsm import PauseController, fsmEvse, fsmPev
from ..fsm.message_observer import MessageObserver
from ..plc.homeplug import build_homeplug
from ..sdp.client import SdpClient
from ..sdp.server import SdpServer
from .address_manager import addressManager
from .conn_mgr import connMgr
from .hardware_interface import hardwareInterface
from .modes import C_EVSE_MODE, C_LISTEN_MODE, C_PEV_MODE


_log = logging.getLogger(__name__)


class HotWireWorker:
    """Owns and pumps every subsystem on a 30 ms cadence.

    Construct once, then call :meth:`mainfunction` in a loop (typically every
    30 ms). The worker chooses between the EVSE and PEV FSM based on
    ``mode``; in simulation mode the SLAC/SDP layer is replaced by
    :class:`SimulatedHomePlug`.
    """

    def __init__(
        self,
        callbackAddToTrace: Callable[[str], None],
        callbackShowStatus: Optional[Callable[..., None]] = None,
        mode: int = C_EVSE_MODE,
        isSimulationMode: int = 0,
        pause_controller: Optional[PauseController] = None,
        message_observer: Optional[MessageObserver] = None,
        preferred_protocol: str = "din",
    ) -> None:
        self.mode = mode
        self.isSimulationMode = isSimulationMode
        self.callbackAddToTrace = callbackAddToTrace
        self.callbackShowStatus = callbackShowStatus or (lambda *a, **kw: None)
        self.pause_controller = pause_controller or PauseController()
        self.message_observer = message_observer
        self.preferred_protocol = preferred_protocol

        self.nMainFunctionCalls = 0

        self.addressManager = addressManager(isSimulationMode=isSimulationMode)
        self.connMgr = connMgr(self._worker_trace, self._show_status_wrapper)

        # Picks the real pcap driver in hardware mode (falling back to the
        # simulation driver if pypcap / the ethernet interface aren't
        # available). See hotwire/plc/homeplug.py.
        self.hp = build_homeplug(
            self._worker_trace,
            self._show_status_wrapper,
            mode,
            self.addressManager,
            self.connMgr,
            isSimulationMode=isSimulationMode,
        )

        self.hardwareInterface = hardwareInterface(
            self._worker_trace,
            self._show_status_wrapper,
            hp=self.hp,
            isSimulationMode=isSimulationMode,
        )

        self._worker_trace("[WORKER] initialized")

        self.evse: Optional[fsmEvse] = None
        self.pev: Optional[fsmPev] = None
        self._build_fsm()

        self.oldAvlnStatus = 0

        # SDP is only meaningful on real hardware. In simulation mode the
        # addressManager hardwires ``::1`` for both sides, so the PEV
        # already knows where the SECC is. On a real PLC link the two
        # sides just met via SLAC and neither knows the other's IP —
        # that's exactly what SDP is for.
        self.sdp_server: Optional[SdpServer] = None
        self._sdp_attempted = False
        try:
            self._sdp_scope_id = self.addressManager.getScopeId()
        except Exception:                                          # noqa: BLE001
            self._sdp_scope_id = 0

    # ---- logging helpers -------------------------------------------

    def _worker_trace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    def _show_status_wrapper(self, s: str, selection: str = "", *_rest) -> None:
        self.callbackShowStatus(s, selection)

    # ---- FSM wiring ------------------------------------------------

    def _build_fsm(self) -> None:
        if self.mode == C_EVSE_MODE:
            self.evse = fsmEvse(
                addressManager=self.addressManager,
                callbackAddToTrace=self._worker_trace,
                hardwareInterface=self.hardwareInterface,
                callbackShowStatus=self._show_status_wrapper,
                pause_controller=self.pause_controller,
                message_observer=self.message_observer,
            )
        elif self.mode == C_PEV_MODE:
            self.pev = fsmPev(
                addressManager=self.addressManager,
                connMgr=self.connMgr,
                callbackAddToTrace=self._worker_trace,
                hardwareInterface=self.hardwareInterface,
                callbackShowStatus=self._show_status_wrapper,
                pause_controller=self.pause_controller,
                message_observer=self.message_observer,
                preferred_protocol=self.preferred_protocol,
            )

    # ---- main loop -------------------------------------------------

    def _handle_tcp_connection_trigger(self) -> None:
        """When the sim reports SDP done, move the PEV FSM into Connecting."""
        if self.mode != C_PEV_MODE or self.pev is None:
            return
        level = self.connMgr.getConnectionLevel()
        if level >= 50 and self.oldAvlnStatus == 0:
            self._worker_trace("[WORKER] Network established — starting PEV FSM")
            self.oldAvlnStatus = 1
            self.pev.reInit()
        elif level < 50:
            self.oldAvlnStatus = 0

    # ---- SDP plumbing ---------------------------------------------

    def _start_sdp_server_if_needed(self) -> None:
        """EVSE side, real hardware: once the local modem is up, start
        the SDP responder so the PEV can find us. Idempotent — the server
        is only created on the first call."""
        if self.sdp_server is not None or self.isSimulationMode:
            return
        # When we are the EVSE, ``getSeccIp()`` is empty (that field is
        # for the PEV to learn the charger address via SDP). Advertise
        # our own link-local address instead so the PEV can actually
        # reach us.
        secc_ip = self.addressManager.getSeccIp()
        if not secc_ip:
            try:
                secc_ip = self.addressManager.getLinkLocalIpv6Address(
                    resulttype="string"
                )
            except Exception:                                       # noqa: BLE001
                secc_ip = ""
        if not secc_ip:
            secc_ip = "::"
        secc_port = self.addressManager.SeccTcpPort
        try:
            self.sdp_server = SdpServer(
                secc_ip=secc_ip,
                secc_port=secc_port,
                scope_id=self._sdp_scope_id,
            )
            self.sdp_server.start()
            self._worker_trace(
                f"[WORKER] SDP responder started on [{secc_ip}]:{secc_port}"
            )
        except Exception as e:                                      # noqa: BLE001
            self._worker_trace(f"[WORKER] SDP responder failed to start: {e}")
            self.sdp_server = None

    def _run_sdp_client_if_needed(self) -> None:
        """PEV side, real hardware: once SLAC reports Ok, multicast a
        discovery request so we learn the SECC's IPv6 address. Called
        once per session; guarded by ``_sdp_attempted``."""
        if self._sdp_attempted or self.isSimulationMode:
            return
        # Only attempt discovery after both modems paired (SLAC done).
        if self.connMgr.getConnectionLevel() < 20:
            return
        self._sdp_attempted = True
        self._worker_trace("[WORKER] starting SDP discovery...")
        try:
            resp = SdpClient(scope_id=self._sdp_scope_id).discover()
        except Exception as e:                                      # noqa: BLE001
            self._worker_trace(f"[WORKER] SDP client error: {e}")
            return
        if resp is None:
            self._worker_trace("[WORKER] SDP discovery timed out")
            return
        self._worker_trace(
            f"[WORKER] SDP discovered SECC at [{resp.ip}]:{resp.port}"
        )
        self.addressManager.setSeccIp(str(resp.ip))
        self.addressManager.SeccTcpPort = resp.port
        self.connMgr.SdpOk()

    def mainfunction(self) -> None:
        self.nMainFunctionCalls += 1
        if self.mode == C_PEV_MODE:
            self.connMgr.mainfunction()
            self._run_sdp_client_if_needed()
        elif self.mode == C_EVSE_MODE:
            self._start_sdp_server_if_needed()
        self._handle_tcp_connection_trigger()
        self.hp.mainfunction()
        self.hardwareInterface.mainfunction()

        if self.mode == C_EVSE_MODE and self.evse is not None:
            # Wait for the simulated modem layer to settle on its first cycles.
            if self.nMainFunctionCalls > 8:
                self.evse.mainfunction()
        elif self.mode == C_PEV_MODE and self.pev is not None:
            self.pev.mainfunction()

    def shutdown(self) -> None:
        """Graceful teardown — stops the SDP server thread if running."""
        if self.sdp_server is not None:
            self.sdp_server.stop()
            self.sdp_server = None
