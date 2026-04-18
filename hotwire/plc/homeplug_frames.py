"""
HomePlug AV / Green PHY management message (MME) frame codec.

A HomePlug MME is a raw Ethernet frame carrying the HomePlug AV protocol
on Ethertype 0x88E1. The payload layout is::

    Octets  0..5   Destination MAC
    Octets  6..11  Source MAC
    Octets 12..13  Ethertype (0x88E1)
    Octets 14      MMV (management message version, usually 0x00)
    Octets 15..16  MMTYPE (little-endian — Table 11-2 of HomePlug spec)
    Octets 17..19  Vendor OUI or FMI depending on frame type
    Octets 20..    MME payload (SLAC_PARAM, SET_KEY, etc)

This module gives us a typed, symmetric view of the frames we send and
receive. The real pcap driver and the in-memory mock harness use the
same codec, so SLAC logic doesn't care whether it's running over a real
modem or a Python queue.

Reference: HomePlug AV 2.1 spec; ISO 15118-3 Annex A; legacy pyPLC
``pyPlcHomeplug.py`` for the specific byte positions we need.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --- Ethertype / MAC constants ------------------------------------------

ETHERTYPE_HOMEPLUG_AV = 0x88E1
MAC_BROADCAST = b"\xff\xff\xff\xff\xff\xff"


# --- Management message types (MMTYPE base values) ---------------------

MMTYPE_CM_SLAC_PARAM = 0x6064
MMTYPE_CM_START_ATTEN_CHAR = 0x6068
MMTYPE_CM_ATTEN_CHAR = 0x606C
MMTYPE_CM_MNBC_SOUND = 0x6074
MMTYPE_CM_VALIDATE = 0x6078
MMTYPE_CM_SLAC_MATCH = 0x607C
MMTYPE_CM_SET_KEY = 0x6008
MMTYPE_CM_AMP_MAP = 0x601C
MMTYPE_CM_GET_SW = 0xA000

# MMTYPE sub-value (bottom 2 bits): REQ / CNF / IND / RSP.
MMSUB_REQ = 0x00
MMSUB_CNF = 0x01
MMSUB_IND = 0x02
MMSUB_RSP = 0x03


# --- Frame model --------------------------------------------------------


@dataclass
class HomePlugFrame:
    """Typed view of one HomePlug MME frame.

    Fields mirror the wire format 1:1 so encode/decode is a pure data
    transform — no semantic interpretation happens here, that's the job
    of the SLAC state machine.
    """

    dst_mac: bytes = MAC_BROADCAST
    src_mac: bytes = b"\x00\x00\x00\x00\x00\x00"
    mmv: int = 0x00                 # management message version
    mmtype_base: int = 0            # top 14 bits of MMTYPE
    mmsub: int = MMSUB_REQ          # bottom 2 bits — REQ/CNF/IND/RSP
    payload: bytes = b""            # everything after byte 17 (MME-specific)

    @property
    def mmtype(self) -> int:
        """Composite MMTYPE = base | sub."""
        return self.mmtype_base | self.mmsub

    def to_bytes(self, min_length: int = 60) -> bytes:
        """Serialise to a raw ethernet frame (padded to ``min_length``)."""
        buf = bytearray(max(min_length, 20 + len(self.payload)))
        buf[0:6] = self.dst_mac
        buf[6:12] = self.src_mac
        buf[12] = ETHERTYPE_HOMEPLUG_AV >> 8
        buf[13] = ETHERTYPE_HOMEPLUG_AV & 0xFF
        buf[14] = self.mmv
        # MMTYPE stored little-endian per HomePlug spec.
        mmt = self.mmtype
        buf[15] = mmt & 0xFF
        buf[16] = (mmt >> 8) & 0xFF
        # 3 bytes of Vendor OUI / FMI go into positions 17..19 as part of
        # the per-message payload. We include them in ``payload`` so this
        # codec stays agnostic.
        for i, b in enumerate(self.payload):
            pos = 17 + i
            if pos < len(buf):
                buf[pos] = b
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["HomePlugFrame"]:
        """Parse a raw ethernet frame. Returns None if it isn't a
        HomePlug AV MME (wrong ethertype / too short)."""
        if len(data) < 17:
            return None
        ethertype = (data[12] << 8) | data[13]
        if ethertype != ETHERTYPE_HOMEPLUG_AV:
            return None
        mmv = data[14]
        mmtype = data[15] | (data[16] << 8)
        mmtype_base = mmtype & ~0x03
        mmsub = mmtype & 0x03
        payload = bytes(data[17:])
        return cls(
            dst_mac=bytes(data[0:6]),
            src_mac=bytes(data[6:12]),
            mmv=mmv,
            mmtype_base=mmtype_base,
            mmsub=mmsub,
            payload=payload,
        )

    # ---- Convenience names -----------------------------------------

    def is_slac_param_req(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SLAC_PARAM
                and self.mmsub == MMSUB_REQ)

    def is_slac_param_cnf(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SLAC_PARAM
                and self.mmsub == MMSUB_CNF)

    def is_set_key_req(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SET_KEY
                and self.mmsub == MMSUB_REQ)

    def is_set_key_cnf(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SET_KEY
                and self.mmsub == MMSUB_CNF)

    def is_slac_match_req(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SLAC_MATCH
                and self.mmsub == MMSUB_REQ)

    def is_slac_match_cnf(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_SLAC_MATCH
                and self.mmsub == MMSUB_CNF)


# --- Frame builders (thin wrappers — semantics remain in SLAC state machine) --


def build_slac_param_req(src_mac: bytes, run_id: bytes) -> HomePlugFrame:
    """PEV -> (broadcast): kicks off the SLAC matching process."""
    assert len(src_mac) == 6 and len(run_id) == 8
    # HomePlug AV spec Table 11-69: APPLICATION_TYPE(1) + SECURITY_TYPE(1)
    # + RunID(8), plus some reserved bytes — total 22 bytes.
    payload = bytearray(22)
    # Vendor OUI (3 bytes) for CCS — using pyPLC's default.
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    # APPLICATION_TYPE = 0x00 (PEV-EVSE matching)
    payload[3] = 0x00
    # SECURITY_TYPE = 0x00 (no security)
    payload[4] = 0x00
    # RunID
    for i in range(8):
        payload[5 + i] = run_id[i]
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_PARAM,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


def build_slac_param_cnf(src_mac: bytes, dst_mac: bytes,
                         run_id: bytes) -> HomePlugFrame:
    """EVSE -> PEV: confirms SLAC params."""
    assert len(src_mac) == 6 and len(dst_mac) == 6 and len(run_id) == 8
    payload = bytearray(28)
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    # M_SOUND_TARGET = FF:FF:FF:FF:FF:FF (broadcast sounds)
    for i in range(6):
        payload[3 + i] = 0xFF
    # NUM_SOUNDS = 10 (typical pyPLC value)
    payload[9] = 0x0A
    # TIME_OUT = 0x06 * 100ms = 600ms
    payload[10] = 0x06
    # RESP_TYPE = 0x01
    payload[11] = 0x01
    # FORWARDING_STA = 00:00:00:00:00:00 (unused for PEV direct)
    # RunID at offset 18
    for i in range(8):
        payload[18 + i] = run_id[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_PARAM,
        mmsub=MMSUB_CNF,
        payload=bytes(payload),
    )


def build_set_key_req(src_mac: bytes, nmk: bytes, nid: bytes) -> HomePlugFrame:
    """Host -> local modem: program NMK (Network Membership Key)."""
    assert len(src_mac) == 6 and len(nmk) == 16 and len(nid) == 7
    # pyPlcHomeplug.composeSetKey layout (simplified):
    #   00 B0 52 + KEY_TYPE(1) + MY_NONCE(4) + YOUR_NONCE(4)
    #   + PID(1) + PRN(2) + PMN(1) + CCo_CAPABILITY(1) + NID(7)
    #   + NEW_EKS(1) + NEW_KEY(16)
    payload = bytearray(3 + 1 + 4 + 4 + 1 + 2 + 1 + 1 + 7 + 1 + 16)
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    payload[3] = 0x01          # KEY_TYPE = NMK
    # MY_NONCE + YOUR_NONCE: both zero is fine for our purpose
    payload[12] = 0x04         # PID = 0x04 (HLE)
    # PRN, PMN, CCo_CAPABILITY — zero
    # NID
    nid_off = 17
    for i in range(7):
        payload[nid_off + i] = nid[i]
    # NEW_EKS
    payload[24] = 0x01
    # NEW_KEY
    for i in range(16):
        payload[25 + i] = nmk[i]
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,           # local modem listens broadcast
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SET_KEY,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


def build_set_key_cnf(src_mac: bytes, dst_mac: bytes,
                      success: bool = True) -> HomePlugFrame:
    """Modem -> host: confirms NMK programming."""
    payload = bytearray(16)
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    # RESULT: 0 = success, 1 = failure (our interpretation; real modems vary)
    payload[3] = 0x00 if success else 0x01
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SET_KEY,
        mmsub=MMSUB_CNF,
        payload=bytes(payload),
    )


def build_slac_match_req(src_mac: bytes, dst_mac: bytes,
                         run_id: bytes, pev_mac: bytes,
                         evse_mac: bytes) -> HomePlugFrame:
    """PEV -> EVSE (after attenuation rounds): commit to pairing."""
    assert all(len(x) == 6 for x in (src_mac, dst_mac, pev_mac, evse_mac))
    assert len(run_id) == 8
    # Layout mirrors HomePlug AV Table 11-87, minimal fields:
    #   [0..2]  OUI
    #   [3]     APPLICATION_TYPE = 0
    #   [4]     SECURITY_TYPE = 0
    #   [5..6]  MVFLength (little-endian 0x003E)
    #   [7..22] PEV_ID (17 bytes placeholder; MAC at +11)
    #   [24..29] PEV_MAC
    #   [30..46] EVSE_ID (17 bytes placeholder; MAC at +11)
    #   [47..52] EVSE_MAC
    #   [53..60] RunID
    payload = bytearray(85)
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    payload[5] = 0x3E
    for i in range(6):
        payload[18 + i] = pev_mac[i]
    for i in range(6):
        payload[24 + i] = pev_mac[i]
    for i in range(6):
        payload[41 + i] = evse_mac[i]
    for i in range(6):
        payload[47 + i] = evse_mac[i]
    for i in range(8):
        payload[53 + i] = run_id[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_MATCH,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


def build_slac_match_cnf(src_mac: bytes, dst_mac: bytes,
                         run_id: bytes, nmk: bytes,
                         nid: bytes) -> HomePlugFrame:
    """EVSE -> PEV: "matched, use this NMK/NID"."""
    assert all(len(x) == 6 for x in (src_mac, dst_mac))
    assert len(run_id) == 8 and len(nmk) == 16 and len(nid) == 7
    payload = bytearray(87)
    payload[0] = 0x00
    payload[1] = 0xB0
    payload[2] = 0x52
    # RunID
    for i in range(8):
        payload[56 + i] = run_id[i]
    # NID
    for i in range(7):
        payload[64 + i] = nid[i]
    # NMK
    for i in range(16):
        payload[71 + i] = nmk[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_MATCH,
        mmsub=MMSUB_CNF,
        payload=bytes(payload),
    )


def extract_run_id(frame: HomePlugFrame) -> Optional[bytes]:
    """Best-effort RunID extraction from SLAC-family frames.

    The RunID position differs by frame type, so this is a heuristic —
    but good enough for the mock harness to correlate REQ→CNF pairs."""
    if frame.is_slac_param_req() and len(frame.payload) >= 13:
        return frame.payload[5:13]
    if frame.is_slac_param_cnf() and len(frame.payload) >= 26:
        return frame.payload[18:26]
    if frame.is_slac_match_req() and len(frame.payload) >= 62:
        # Match request RunID is near the tail; our builder puts it at +63.
        # Allow ±4 drift when parsing other vendors' frames.
        for offset in (63, 59, 55, 52):
            if offset + 8 <= len(frame.payload):
                candidate = frame.payload[offset:offset + 8]
                if any(candidate):
                    return candidate
    if frame.is_slac_match_cnf() and len(frame.payload) >= 64:
        return frame.payload[56:64]
    return None
