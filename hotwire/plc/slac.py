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
SLAC_WAIT_SET_KEY_CNF = 8       # both sides: sent CM_SET_KEY.REQ to own modem
SLAC_PAIRED = 5                 # both sides: SLAC_MATCH exchanged, NMK known
SLAC_FAILED = 9

# Budget for the local modem to acknowledge our CM_SET_KEY.REQ. If it
# expires we still declare ``SLAC_PAIRED`` — the protocol exchange with
# the peer is already complete. The only reason to hold up pairing on
# the modem's ACK would be to guarantee AVLN membership, and upstream
# pyPLC operates the same way (sends SET_KEY, continues regardless).
_SET_KEY_CNF_TIMEOUT_S = 0.8


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
        nmk: Optional[bytes] = None,
        nid: Optional[bytes] = None,
        total_timeout_s: Optional[float] = None,
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
        # Caller may pin NMK/NID so repeated SLAC sessions program the
        # modem with the same key — otherwise the modems silently leave
        # the AVLN after each session and the next PEV kick-off frame
        # is unreachable over the powerline. When left unset we fall
        # back to per-session randoms (matches original pyPLC behaviour
        # for the very first pairing).
        if nmk is not None:
            assert len(nmk) == 16
            self.nmk = bytes(nmk)
        else:
            self.nmk = os.urandom(16)
        if nid is not None:
            assert len(nid) == 7
            self.nid = bytes(nid)
        else:
            self.nid = os.urandom(7)

        # Attenuation-phase bookkeeping. The PEV counts down the 10 sounds
        # it has sent; the EVSE counts up the ones it received.
        self._sounds_remaining = _NUM_SOUNDS
        self._sounds_received = 0
        self._start_atten_sent = False
        self._last_sound_time: Optional[float] = None

        # SET_KEY.REQ → local modem deadline (set once we move into
        # SLAC_WAIT_SET_KEY_CNF). None while the earlier SLAC phases run.
        self._set_key_deadline: Optional[float] = None

        # PEV-side PARAM.REQ retry bookkeeping (see tick()).
        self._last_param_req_time: Optional[float] = None

        # Coarse state.
        if role == ROLE_EVSE:
            self.state = SLAC_WAIT_PARAM_REQ
        else:
            self.state = SLAC_IDLE          # PEV kicks off in tick()

        # Watchdog — if nothing progresses in this many seconds, fail.
        # ``None`` (default) = no internal timeout; the SLAC machine
        # retries PARAM.REQ forever, matching pyPLC's behaviour where
        # opening either side and waiting for the peer is normal use.
        # Phase scripts that want a hard ceiling pass an explicit value
        # via ``total_timeout_s``.
        self._deadline: Optional[float] = None
        self._total_timeout_s = total_timeout_s
        self._start_time = time.monotonic()

    # ---- public API ------------------------------------------------

    def tick(self) -> None:
        """Advance the state machine by one step. Call this at ~30ms.

        Checks for received frames, processes them, may send new frames
        in response.
        """
        # Global timeout. SLAC_WAIT_SET_KEY_CNF has its own short budget
        # handled below — treat it as "already done" here so the full
        # budget timer doesn't kill a session that's literally one
        # modem-ACK away from PASSing. ``None`` total_timeout = no
        # ceiling, retry until either the operator stops the worker or
        # the outer phase budget expires.
        if (self._total_timeout_s is not None
                and self.state not in (
                    SLAC_PAIRED, SLAC_FAILED, SLAC_WAIT_SET_KEY_CNF
                )
                and time.monotonic() - self._start_time > self._total_timeout_s):
            self.trace(f"[SLAC {self.role}] total timeout; failing")
            self.state = SLAC_FAILED
            return

        # PEV: kick things off and periodically retry until we see a
        # PARAM_CNF. ``phase2_slac`` used to pass because the operator
        # happened to start the two sides in the right order, but
        # ``phase4_v2g`` spins up a full worker on each side and the
        # EVSE's pcap listener isn't ready for the first few hundred ms
        # — so a single kick-off frame is easily missed. One REQ every
        # 2 s matches pyPLC's retry cadence.
        if self.role == ROLE_PEV and self.state == SLAC_IDLE:
            self._send_slac_param_req()
            self._last_param_req_time = time.monotonic()
            self.state = SLAC_WAIT_PARAM_CNF
            return
        if (self.role == ROLE_PEV
                and self.state == SLAC_WAIT_PARAM_CNF):
            now = time.monotonic()
            if (self._last_param_req_time is not None
                    and now - self._last_param_req_time >= 2.0):
                self._send_slac_param_req()
                self._last_param_req_time = now

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

        # SET_KEY.CNF budget: if the local modem never replies, declare
        # paired anyway. The peer protocol handshake already succeeded,
        # and downstream SDP/V2G traffic will either work (modem joined
        # AVLN silently) or surface its own timeout.
        if (self.state == SLAC_WAIT_SET_KEY_CNF
                and self._set_key_deadline is not None
                and time.monotonic() >= self._set_key_deadline):
            self._mark_paired_and_notify(
                "no SET_KEY.CNF from local modem before budget; "
                "continuing on peer-handshake success"
            )

    def is_paired(self) -> bool:
        return self.state == SLAC_PAIRED

    def has_failed(self) -> bool:
        return self.state == SLAC_FAILED

    # ---- frame handlers --------------------------------------------

    def _handle(self, frame: HomePlugFrame) -> None:
        # Ignore our own frames if a transport loopback echoes them.
        if frame.src_mac == self.local_mac:
            return

        # Local modem's reply to our CM_SET_KEY.REQ is role-independent
        # (both PEV and EVSE side do the same programming step), so
        # handle it here before the role dispatch.
        if (self.state == SLAC_WAIT_SET_KEY_CNF
                and frame.is_set_key_cnf()):
            self.trace(
                f"[SLAC {self.role}] got CM_SET_KEY.CNF from "
                f"{self._mac_str(frame.src_mac)}"
            )
            self._mark_paired_and_notify("local modem ACKed SET_KEY")
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
            # Payload starts at wire byte 19, so:
            #   NID  @ wire 85..91  -> payload[66..73]
            #   NMK  @ wire 93..108 -> payload[74..90]
            if len(frame.payload) >= 90:
                self.nid = bytes(frame.payload[66:73])
                self.nmk = bytes(frame.payload[74:90])
            self.trace(
                "[SLAC pev] got CM_SLAC_MATCH.CNF; NMK/NID received; paired"
            )
            self._finish_paired()
            return

    # ---- senders ---------------------------------------------------

    def _send_slac_param_req(self) -> None:
        frame = build_slac_param_req(self.local_mac, self.run_id)
        self.transport.send(frame.to_bytes())
        self.trace(
            f"[SLAC {self.role}] sent CM_SLAC_PARAM.REQ "
            f"(run_id={self.run_id.hex()})"
        )

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
        """Protocol exchange with the peer is complete — now program
        the local modem with the negotiated NMK so it actually joins
        the AVLN. We enter ``SLAC_WAIT_SET_KEY_CNF`` and only move to
        ``SLAC_PAIRED`` once the modem ACKs (or the short budget expires;
        see below)."""
        self.trace(
            f"[SLAC {self.role}] peer handshake done; "
            f"programming local modem with NID={self.nid.hex()} "
            f"NMK={self.nmk[:4].hex()}..."
        )
        self._send_set_key_req_raw()
        self.state = SLAC_WAIT_SET_KEY_CNF
        self._set_key_deadline = time.monotonic() + _SET_KEY_CNF_TIMEOUT_S

    def _mark_paired_and_notify(self, reason: str) -> None:
        """Final state transition. Called once either the modem ACKed
        our SET_KEY (ideal path) or the short budget expired (degraded
        path — protocol still OK, modem just didn't reply)."""
        self.state = SLAC_PAIRED
        self.trace(
            f"[SLAC {self.role}] PAIRED ({reason}); "
            f"nid={self.nid.hex()} nmk={self.nmk[:4].hex()}... "
            f"peer={self._mac_str(self.peer_mac)}"
        )
        if self.on_slac_ok is not None and self.peer_mac is not None:
            try:
                self.on_slac_ok(self.nmk, self.nid, self.peer_mac)
            except Exception as e:                              # noqa: BLE001
                self.trace(f"[SLAC {self.role}] on_slac_ok callback raised: {e}")

    def _send_set_key_req_raw(self) -> None:
        """Emit a CM_SET_KEY.REQ whose wire layout exactly matches
        pyPLC's ``composeSetKey`` (60 bytes, FMI at offsets 17–18,
        key_info_type at 19, my_nonce at 20–23, PID at 28, NID at 33–39,
        peks at 40, NMK at 41–56).

        We intentionally don't route through ``build_set_key_req`` /
        ``HomePlugFrame.to_bytes`` here because that helper treats the
        FMI bytes as part of ``payload``, and QCA7420 / TP-Link modems
        reject SET_KEY frames whose payload doesn't start at offset 19.
        pyPLC's field-proven layout is the source of truth.
        """
        # Broadcast DA makes the local modem pick it up without us
        # needing to know its MAC (it only recognises SET_KEY addressed
        # to itself or broadcast — pyPLC comment verifies this).
        buf = bytearray(60)
        # Dst MAC = broadcast
        for i in range(6):
            buf[i] = 0xFF
        # Src MAC = host
        buf[6:12] = self.local_mac
        # Ethertype
        buf[12] = 0x88
        buf[13] = 0xE1
        buf[14] = 0x01                 # MMV
        buf[15] = 0x08                 # MMTYPE LSB (CM_SET_KEY.REQ = 0x6008)
        buf[16] = 0x60                 # MMTYPE MSB
        buf[17] = 0x00                 # FMI frag_index
        buf[18] = 0x00                 # FMI frag_seqnum
        buf[19] = 0x01                 # key_info_type = 1 (NMK)
        # my_nonce (4B) — any non-zero 32-bit value; pyPLC uses 0xAAAAAAAA.
        buf[20] = 0xAA
        buf[21] = 0xAA
        buf[22] = 0xAA
        buf[23] = 0xAA
        # your_nonce (4B) — zero for a first-time SET_KEY.
        # offset 28 = PID = 0x04 (HLE, per ISO 15118-3 §A.7.2)
        buf[28] = 0x04
        # PRN, PMN, CCo cap — zero
        # NID (7B) at offset 33
        for i in range(7):
            buf[33 + i] = self.nid[i]
        # peks (payload encryption key select) = 0x01 (NMK)
        buf[40] = 0x01
        # NMK (16B) at offset 41
        for i in range(16):
            buf[41 + i] = self.nmk[i]
        try:
            self.transport.send(bytes(buf))
        except Exception as e:                                  # noqa: BLE001
            self.trace(f"[SLAC {self.role}] SET_KEY.REQ send failed: {e}")

    # ---- helpers --------------------------------------------------

    @staticmethod
    def _mac_str(mac: Optional[bytes]) -> str:
        if mac is None:
            return "?"
        return ":".join(f"{b:02x}" for b in mac)
