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
from typing import Any, Callable, Optional

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

        # Real SLAC state machine — instantiated lazily in ``mainfunction``
        # once the interface is quiet and we know our local MAC. Until
        # then the CONNMGR sees us at level 5 (eth link present).
        self._slac: Any = None
        self._slac_started: bool = False
        self._slac_reported_modems: bool = False
        self._slac_reported_ok: bool = False

        self.addToTrace("[RealHomePlug] pcap driver initialised")

    # ---- logging ---------------------------------------------------

    def addToTrace(self, s: str) -> None:
        self.callbackAddToTrace(s)

    # ---- pcap setup ------------------------------------------------

    def _setup_pcap(self) -> None:
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

        # Use PcapL2Transport — same wrapper phase2_slac uses — so the
        # SLAC state machine can plug in without any special case. The
        # wrapper already applies ``setfilter("ether proto 0x88E1")`` and
        # ``setnonblock(True)`` so SLAC tick loops respect their budget.
        from .l2_transport import PcapL2Transport
        try:
            self._transport = PcapL2Transport(iface)
        except RuntimeError as e:
            raise RuntimeError(
                f"could not open pcap L2 transport on {iface!r}: {e}"
            ) from e

        self.addToTrace(f"[RealHomePlug] opened interface {iface!r}")
        self._iface = iface

    # ---- main loop -------------------------------------------------

    def mainfunction(self) -> None:
        """Called by :class:`HotWireWorker` every ~30 ms.

        Drives the SLAC state machine against the real pcap interface
        and, on pairing success, reports progress to the
        :class:`ConnectionManager` so the rest of the worker (SDP
        client, TCP dialler, V2G FSM) unblocks.
        """
        self._ensure_slac_started()
        if self._slac is None:
            return

        self._slac.tick()

        # Promote CONNMGR whenever SLAC crosses a milestone. The first
        # promotion (ModemFinderOk(2)) tells the connection manager two
        # modems are present; the second (SlacOk) unblocks SDP. We hold
        # the booleans so we don't re-report every tick.
        if self._slac.is_paired() and not self._slac_reported_ok:
            self.nmk = self._slac.nmk
            self.nid = self._slac.nid
            if not self._slac_reported_modems:
                self.connMgr.ModemFinderOk(2)
                self._slac_reported_modems = True
            self.connMgr.SlacOk()
            self._slac_reported_ok = True
            self.addToTrace(
                "[RealHomePlug] SLAC paired — reported ModemFinderOk(2) "
                "+ SlacOk() to connection manager"
            )
        elif self._slac.has_failed() and not self._slac_reported_ok:
            # Surface the failure once so the operator sees it in the
            # trace, but don't keep blasting the log.
            self.addToTrace(
                f"[RealHomePlug] SLAC failed in role={self.mode}; "
                "CONNMGR will remain at eth-link level"
            )
            # Flip the flag to avoid re-log spam; keep _slac_reported_modems
            # false so a later retry can still promote.
            self._slac_reported_ok = True

    def _ensure_slac_started(self) -> None:
        """Instantiate and start the SLAC state machine once we have
        all the prerequisites (local MAC known, role is not listener).
        Idempotent — subsequent calls are no-ops.
        """
        if self._slac_started:
            return

        if self.mode == C_LISTEN_MODE:
            # Listen mode sniffs but does not participate — leave SLAC
            # unstarted so the state machine doesn't emit any frames.
            self._slac_started = True
            return

        # Resolve our local MAC from addressManager (already filled in
        # at worker startup for both platforms).
        try:
            raw = self.addressManager.getLocalMacAddress()
        except Exception as e:                                  # noqa: BLE001
            self.addToTrace(
                f"[RealHomePlug] address manager has no local MAC yet: {e}"
            )
            return
        mac_bytes = bytes(raw) if raw is not None else b""
        if len(mac_bytes) != 6:
            return  # not ready yet

        from .slac import SlacStateMachine, ROLE_EVSE, ROLE_PEV
        role = ROLE_PEV if self.mode == C_PEV_MODE else ROLE_EVSE

        # Pull a stable NMK/NID from config when available so the
        # modem doesn't need to re-pair its AVLN every time the worker
        # restarts. If the operator didn't set them we fall through to
        # per-session randoms. The EVSE-side value is authoritative
        # (MATCH.CNF delivers it to the PEV), but PEV is also given
        # the same values as a hint in case a future revision honours
        # client-proposed keys.
        from ..core.config import getConfigValue
        nmk_bytes: Optional[bytes] = None
        nid_bytes: Optional[bytes] = None
        try:
            nmk_hex = getConfigValue("plc_nmk_hex")
            if nmk_hex and len(nmk_hex) == 32:
                nmk_bytes = bytes.fromhex(nmk_hex)
        except SystemExit:
            pass
        except (ValueError, TypeError):
            pass
        try:
            nid_hex = getConfigValue("plc_nid_hex")
            if nid_hex and len(nid_hex) == 14:
                nid_bytes = bytes.fromhex(nid_hex)
        except SystemExit:
            pass
        except (ValueError, TypeError):
            pass

        self._slac = SlacStateMachine(
            role=role,
            transport=self._transport,
            local_mac=mac_bytes,
            callback_add_to_trace=self.addToTrace,
            nmk=nmk_bytes,
            nid=nid_bytes,
        )
        # Leave SLAC's internal total-timeout as ``None`` (default) so
        # the EVSE / PEV state machines wait indefinitely for a peer —
        # this matches pyPLC and real charging-station behaviour: an
        # EVSE with nothing plugged in stays idle, a PEV with no
        # charger keeps re-broadcasting CM_SLAC_PARAM.REQ. Phase scripts
        # (hw_check/phase2_slac.py, phase4_v2g.py) impose their own
        # bounded budgets by overriding ``_total_timeout_s`` on the
        # SLAC instance they construct themselves.
        self._slac_started = True
        self.addToTrace(
            f"[RealHomePlug] SLAC started (role={role}, "
            f"local_mac={':'.join(f'{b:02x}' for b in mac_bytes)})"
        )

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

    # ---- Lifecycle -------------------------------------------------

    def close(self) -> None:
        """Release the pcap interface so a subsequent ``HotWireWorker``
        can re-open it. Without this the interface stays bound to the
        dead worker's pcap handle and the GUI's stop→start cycle fails
        with "interface busy" or yields a half-initialised SLAC machine.
        """
        # Drop the SLAC reference first — it holds a transport pointer.
        self._slac = None
        self._slac_started = False
        transport = getattr(self, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except Exception as e:                                  # noqa: BLE001
                self.addToTrace(f"[RealHomePlug] transport.close raised: {e}")
            self._transport = None


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
