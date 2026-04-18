"""
HotWire worker — orchestrates address manager, connection manager, TCP
transport, hardware interface, and the appropriate FSM for the selected
operating mode.

Modeled on pyPLC's ``pyPlcWorker`` (GPL-3.0, uhi22) but slimmed down to
the dependencies that HotWire's clean package structure needs.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..fsm import PauseController, fsmEvse, fsmPev
from ..fsm.message_observer import MessageObserver
from ..plc.homeplug import build_homeplug
from .address_manager import addressManager
from .conn_mgr import connMgr
from .hardware_interface import hardwareInterface
from .modes import C_EVSE_MODE, C_LISTEN_MODE, C_PEV_MODE


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

    def mainfunction(self) -> None:
        self.nMainFunctionCalls += 1
        if self.mode == C_PEV_MODE:
            self.connMgr.mainfunction()
        self._handle_tcp_connection_trigger()
        self.hp.mainfunction()
        self.hardwareInterface.mainfunction()

        if self.mode == C_EVSE_MODE and self.evse is not None:
            # Wait for the simulated modem layer to settle on its first cycles.
            if self.nMainFunctionCalls > 8:
                self.evse.mainfunction()
        elif self.mode == C_PEV_MODE and self.pev is not None:
            self.pev.mainfunction()
