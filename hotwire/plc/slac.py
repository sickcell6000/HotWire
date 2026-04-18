"""
Simplified SLAC (Signal Level Attenuation Characterization) state machine.

SLAC is how a PEV and EVSE that plug into the same CCS connector decide
they are a pair rather than overhearing traffic from nearby modems. ISO
15118-3 Annex A defines the full dance (CM_SLAC_PARAM.REQ →
CM_START_ATTEN_CHAR.IND → CM_MNBC_SOUND.IND × N → CM_ATTEN_CHAR.IND/RSP
→ CM_VALIDATE.REQ/CNF → CM_SLAC_MATCH.REQ/CNF → both sides CM_SET_KEY
with the newly-negotiated NMK).

**Why a simplified version?** The full specification's attenuation rounds
are meaningful on the real powerline channel but meaningless in a
pcap-based loopback or the pipe-based mock harness. So we implement the
*protocol* — correctly typed REQ/CNF/IND/RSP exchanges for every
message the spec requires — but we skip the signal-level measurements
and hard-code the attenuation results.

The state machine uses :class:`L2Transport` so the same code runs on
real pcap hardware and on the in-process test harness. Pairing success
surfaces as a ``connMgr.ModemFinderOk(2)`` + ``connMgr.SlacOk()`` call.

This is the **minimal viable** SLAC; sufficient for two HotWire
instances to recognise each other and start DIN handshake over the
paired modems. A production implementation would also need to handle
attenuation timeouts, retry budgets, and multi-peer disambiguation —
see ``archive/legacy-evse/pyPlcHomeplug.py`` for the full-weight version.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from .homeplug_frames import (
    HomePlugFrame,
    MAC_BROADCAST,
    MMSUB_CNF,
    MMSUB_REQ,
    build_atten_char_ind,
    build_atten_char_rsp,
    build_mnbc_sound_ind,
    build_set_key_cnf,
    build_set_key_req,
    build_slac_match_cnf,
    build_slac_match_req,
    build_slac_param_cnf,
    build_slac_param_req,
    build_start_atten_char_ind,
    extract_run_id,
)
from .l2_transport import L2Transport


# --- SLAC phases (coarse — more granular than pyPLC's 17 states) ------

SLAC_IDLE = 0
SLAC_WAIT_PARAM_REQ = 1         # EVSE role: waiting for PEV to kick off
SLAC_WAIT_PARAM_CNF = 2         # PEV role: sent REQ, waiting for CNF
SLAC_WAIT_SOUNDS = 6            # EVSE: got PARAM_REQ, collecting sounds
SLAC_WAIT_ATTEN_CHAR = 7        # PEV: sent sounds, waiting for ATTEN_CHAR.IND
SLAC_WAIT_MATCH_REQ = 3         # EVSE: saw ATTEN_CHAR.RSP, waiting for MATCH
SLAC_WAIT_MATCH_CNF = 4         # PEV: sent MATCH_REQ, waiting for CNF
SLAC_PAIRED = 5                 # both sides: SLAC_MATCH exchanged, NMK known
SLAC_FAILED = 9


# Per-spec (ISO 15118-3 §A.7.1) PEV sends 10 sound frames.
_NUM_SOUNDS = 10


# --- Role constants ---------------------------------------------------

ROLE_EVSE = "evse"
ROLE_PEV = "pev"


class SlacStateMachine:
    """Runs the SLAC dance over an L2 transport.

    Parameters
    ----------
    role
        ``"evse"`` or ``"pev"``. Determines who initiates.
    transport
        An :class:`L2Transport` (pcap or pipe-mock).
    local_mac
        This host's layer-2 MAC. Used as source address for outbound
        frames.
    callback_add_to_trace
        Where to write progress messages.
    callback_slac_ok
        Called with ``(nmk, nid, peer_mac)`` the moment pairing succeeds.
        The worker's ``connMgr.SlacOk()`` should be invoked from here.
    run_id
        Optional 8-byte session identifier. Auto-generated if omitted.
    """

    def __init__(
        self,
        role: str,
        transport: L2Transport,
        local_mac: bytes,
        callback_add_to_trace: Callable[[str], None],
        callback_slac_ok: Callable[[bytes, bytes, bytes], None] = None,
        run_id: Optional[bytes] = None,
    ) -> None:
        if role not in (ROLE_EVSE, ROLE_PEV):
            raise ValueError(f"role must be evse|pev; got {role}")
        assert len(local_mac) == 6
        self.role = role
        self.transport = transport
        self.local_mac = bytes(local_mac)
        self.trace = callback_add_to_trace
        self.on_slac_ok = callback_slac_ok
        self.run_id = run_id or os.urandom(8)

        # Negotiated after CM_SLAC_MATCH.CNF lands.
        self.peer_mac: Optional[bytes] = None
        self.nmk = os.urandom(16)           # new-key for this session
        self.nid = os.urandom(7)            # network ID

        # Attenuation-phase bookkeeping. The PEV counts down the 10 sounds
        # it has sent; the EVSE counts up the ones it received.
        self._sounds_remaining = _NUM_SOUNDS
        self._sounds_received = 0
        self._start_atten_sent = False
        self._last_sound_time: Optional[float] = None

        # Coarse state.
        if role == ROLE_EVSE:
            self.state = SLAC_WAIT_PARAM_REQ
        else:
            self.state = SLAC_IDLE          # PEV kicks off in tick()

        # Watchdog — if nothing progresses in this many seconds, fail.
        self._deadline: Optional[float] = None
        self._total_timeout_s = 15.0
        self._start_time = time.monotonic()

    # ---- public API ------------------------------------------------

    def tick(self) -> None:
        """Advance the state machine by one step. Call this at ~30ms.

        Checks for received frames, processes them, may send new frames
        in response.
        """
        # Global timeout.
        if self.state not in (SLAC_PAIRED, SLAC_FAILED):
            if time.monotonic() - self._start_time > self._total_timeout_s:
                self.trace(f"[SLAC {self.role}] total timeout; failing")
                self.state = SLAC_FAILED
                return

        # PEV: kick things off exactly once.
        if self.role == ROLE_PEV and self.state == SLAC_IDLE:
            self._send_slac_param_req()
            self.state = SLAC_WAIT_PARAM_CNF
            return

        # PEV: after PARAM_CNF, drip-send sound frames at ~20 ms intervals
        # so the EVSE has time to accumulate them in its rx queue. We don't
        # block the tick loop; each tick sends at most one sound.
        if (self.role == ROLE_PEV
                and self.state == SLAC_WAIT_ATTEN_CHAR
                and self._sounds_remaining > 0):
            now = time.monotonic()
            if self._last_sound_time is None or now - self._last_sound_time >= 0.02:
                self._send_next_sound()
                self._last_sound_time = now
            # Still allow inbound handling in the same tick, below.

        # Pull any incoming frame and dispatch.
        raw = self.transport.recv()
        if raw is not None:
            frame = HomePlugFrame.from_bytes(raw)
            if frame is not None:
                self._handle(frame)

    def is_paired(self) -> bool:
        return self.state == SLAC_PAIRED

    def has_failed(self) -> bool:
        return self.state == SLAC_FAILED

    # ---- frame handlers --------------------------------------------

    def _handle(self, frame: HomePlugFrame) -> None:
        # Ignore our own frames if a transport loopback echoes them.
        if frame.src_mac == self.local_mac:
            return

        if self.role == ROLE_EVSE:
            self._handle_evse(frame)
        else:
            self._handle_pev(frame)

    def _handle_evse(self, frame: HomePlugFrame) -> None:
        if self.state == SLAC_WAIT_PARAM_REQ and frame.is_slac_param_req():
            run_id = extract_run_id(frame)
            if run_id:
                self.run_id = run_id
            self.peer_mac = frame.src_mac
            self.trace(
                f"[SLAC evse] got CM_SLAC_PARAM.REQ from {self._mac_str(self.peer_mac)}; "
                f"replying CNF"
            )
            self._send_slac_param_cnf()
            # Move straight to waiting for sounds; if the peer skips the
            # attenuation round (simplified SLAC used by the mock harness)
            # we'll fall through to MATCH_REQ below — so this state accepts
            # both paths.
            self.state = SLAC_WAIT_SOUNDS
            return

        # Attenuation phase — EVSE collects sounds, then answers with
        # CM_ATTEN_CHAR.IND after the last one arrives.
        if self.state == SLAC_WAIT_SOUNDS:
            if frame.is_start_atten_char_ind():
                # Just bookkeeping; START_ATTEN_CHAR is informational.
                return
            if frame.is_mnbc_sound_ind():
                self._sounds_received += 1
                self.trace(
                    f"[SLAC evse] sound {self._sounds_received}/{_NUM_SOUNDS}"
                )
                if self._sounds_received >= _NUM_SOUNDS:
                    self._send_atten_char_ind()
                    # Stay in WAIT_SOUNDS until we see RSP; the RSP
                    # handler below flips us to WAIT_MATCH_REQ.
                return
            if frame.is_atten_char_rsp():
                self.trace("[SLAC evse] got CM_ATTEN_CHAR.RSP; awaiting MATCH.REQ")
                self.state = SLAC_WAIT_MATCH_REQ
                return
            # The simplified harness skips attenuation entirely and jumps
            # straight to MATCH_REQ. Accept it for compatibility.
            if frame.is_slac_match_req():
                self.trace(
                    "[SLAC evse] got CM_SLAC_MATCH.REQ (skipped atten); "
                    "replying CNF + paired"
                )
                self._send_slac_match_cnf()
                self._finish_paired()
                return

        if self.state == SLAC_WAIT_MATCH_REQ and frame.is_slac_match_req():
            self.trace("[SLAC evse] got CM_SLAC_MATCH.REQ; replying CNF + paired")
            self._send_slac_match_cnf()
            self._finish_paired()
            return

    def _handle_pev(self, frame: HomePlugFrame) -> None:
        if self.state == SLAC_WAIT_PARAM_CNF and frame.is_slac_param_cnf():
            self.peer_mac = frame.src_mac
            self.trace(
                f"[SLAC pev] got CM_SLAC_PARAM.CNF from {self._mac_str(self.peer_mac)}; "
                f"entering attenuation round"
            )
            # Enter the attenuation round: send START_ATTEN_CHAR and begin
            # dripping sounds on subsequent ticks. When we see ATTEN_CHAR.IND
            # we'll answer with RSP and jump to MATCH.
            self._send_start_atten_char()
            self.state = SLAC_WAIT_ATTEN_CHAR
            return

        if (self.state == SLAC_WAIT_ATTEN_CHAR
                and frame.is_atten_char_ind()):
            self.trace(
                "[SLAC pev] got CM_ATTEN_CHAR.IND; replying RSP and sending MATCH.REQ"
            )
            self._send_atten_char_rsp(frame.src_mac)
            self._send_slac_match_req()
            self.state = SLAC_WAIT_MATCH_CNF
            return

        if self.state == SLAC_WAIT_MATCH_CNF and frame.is_slac_match_cnf():
            run_id = extract_run_id(frame)
            # Pick NMK/NID from the CNF payload per HomePlug spec positions.
            if len(frame.payload) >= 87:
                self.nid = bytes(frame.payload[64:71])
                self.nmk = bytes(frame.payload[71:87])
            self.trace(
                "[SLAC pev] got CM_SLAC_MATCH.CNF; NMK/NID received; paired"
            )
            self._finish_paired()
            return

    # ---- senders ---------------------------------------------------

    def _send_slac_param_req(self) -> None:
        frame = build_slac_param_req(self.local_mac, self.run_id)
        self.transport.send(frame.to_bytes())

    def _send_slac_param_cnf(self) -> None:
        assert self.peer_mac is not None
        frame = build_slac_param_cnf(self.local_mac, self.peer_mac, self.run_id)
        self.transport.send(frame.to_bytes())

    def _send_slac_match_req(self) -> None:
        assert self.peer_mac is not None
        frame = build_slac_match_req(
            src_mac=self.local_mac,
            dst_mac=self.peer_mac,
            run_id=self.run_id,
            pev_mac=self.local_mac,
            evse_mac=self.peer_mac,
        )
        self.transport.send(frame.to_bytes())

    def _send_slac_match_cnf(self) -> None:
        assert self.peer_mac is not None
        frame = build_slac_match_cnf(
            src_mac=self.local_mac,
            dst_mac=self.peer_mac,
            run_id=self.run_id,
            nmk=self.nmk,
            nid=self.nid,
        )
        self.transport.send(frame.to_bytes())

    # ---- attenuation senders (PEV / EVSE) --------------------------

    def _send_start_atten_char(self) -> None:
        """PEV: one-shot CM_START_ATTEN_CHAR.IND before the sound burst."""
        frame = build_start_atten_char_ind(self.local_mac, self.run_id,
                                           num_sounds=_NUM_SOUNDS)
        self.transport.send(frame.to_bytes())
        self._start_atten_sent = True

    def _send_next_sound(self) -> None:
        """PEV: send one of the 10 MNBC_SOUND frames and decrement the
        remaining count. When the count hits zero we stop sending."""
        if self._sounds_remaining <= 0:
            return
        frame = build_mnbc_sound_ind(
            src_mac=self.local_mac,
            run_id=self.run_id,
            remaining_sounds=self._sounds_remaining - 1,
        )
        self.transport.send(frame.to_bytes())
        self._sounds_remaining -= 1

    def _send_atten_char_ind(self) -> None:
        """EVSE: after seeing the last sound, report the attenuation
        profile back to the PEV. Profile bytes are zeroed — real
        modems fill them with per-tone AGC values, but the mock
        transport has no channel to measure."""
        assert self.peer_mac is not None
        frame = build_atten_char_ind(
            src_mac=self.local_mac,
            dst_mac=self.peer_mac,
            run_id=self.run_id,
            pev_mac=self.peer_mac,
            num_sounds=self._sounds_received,
        )
        self.transport.send(frame.to_bytes())

    def _send_atten_char_rsp(self, evse_mac: bytes) -> None:
        """PEV: acknowledge the EVSE's attenuation profile with RESULT=0."""
        frame = build_atten_char_rsp(
            src_mac=self.local_mac,
            dst_mac=evse_mac,
            run_id=self.run_id,
            pev_mac=self.local_mac,
            result=0,
        )
        self.transport.send(frame.to_bytes())

    # ---- pairing finish ------------------------------------------

    def _finish_paired(self) -> None:
        self.state = SLAC_PAIRED
        self.trace(
            f"[SLAC {self.role}] PAIRED; "
            f"nid={self.nid.hex()} nmk={self.nmk[:4].hex()}... "
            f"peer={self._mac_str(self.peer_mac)}"
        )
        if self.on_slac_ok is not None and self.peer_mac is not None:
            try:
                self.on_slac_ok(self.nmk, self.nid, self.peer_mac)
            except Exception as e:                              # noqa: BLE001
                self.trace(f"[SLAC {self.role}] on_slac_ok callback raised: {e}")

    # ---- helpers --------------------------------------------------

    @staticmethod
    def _mac_str(mac: Optional[bytes]) -> str:
        if mac is None:
            return "?"
        return ":".join(f"{b:02x}" for b in mac)
