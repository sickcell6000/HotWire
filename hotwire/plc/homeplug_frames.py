"""
HomePlug AV / Green PHY management message (MME) frame codec.

A HomePlug MME is a raw Ethernet frame carrying the HomePlug AV protocol
on Ethertype 0x88E1. The wire layout is::

    Octets  0..5    Destination MAC
    Octets  6..11   Source MAC
    Octets 12..13   Ethertype (0x88E1)
    Octets 14       MMV (management message version, 0x01 for HomePlug AV 1.1+)
    Octets 15..16   MMTYPE (little-endian — Table 11-2 of HomePlug spec)
    Octets 17..18   FMI (Fragmentation Management Info; 0x0000 = unfragmented)
    Octets 19..     MME-specific payload (SLAC_PARAM, SET_KEY, etc.)

Earlier revisions of this codec mistakenly treated bytes 17..19 as a
Vendor OUI (``00:B0:52``) inside the payload. That is **wrong** for
ISO 15118-3 / HomePlug AV SLAC frames: byte 17–18 is FMI, byte 19 onwards
is the MME-specific payload (no OUI prefix). QCA7420 / TP-Link modems
silently drop frames that don't follow this layout — see the comment on
``slac.SlacStateMachine._send_set_key_req_raw`` for the historic
workaround that this rewrite renders unnecessary.

Reference: HomePlug AV 2.1 spec; ISO 15118-3 Annex A; legacy pyPLC
``archive/legacy-evse/pyPlcHomeplug.py`` for the field-proven byte
positions copied here.
"""
from __future__ import annotations

from dataclasses import dataclass
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

    ``payload`` is the MME-specific payload that begins at wire byte 19,
    immediately after the FMI bytes. It does **not** include any vendor
    OUI prefix — ISO 15118-3 SLAC frames don't have one.
    """

    dst_mac: bytes = MAC_BROADCAST
    src_mac: bytes = b"\x00\x00\x00\x00\x00\x00"
    mmv: int = 0x01                 # MMV — 0x01 for HomePlug AV 1.1+ / pyPLC
    mmtype_base: int = 0            # top 14 bits of MMTYPE
    mmsub: int = MMSUB_REQ          # bottom 2 bits — REQ/CNF/IND/RSP
    fmi: bytes = b"\x00\x00"        # Fragmentation Management Info (bytes 17-18)
    payload: bytes = b""            # everything from byte 19 onwards

    @property
    def mmtype(self) -> int:
        """Composite MMTYPE = base | sub."""
        return self.mmtype_base | self.mmsub

    def to_bytes(self, min_length: int = 60) -> bytes:
        """Serialise to a raw ethernet frame (padded to ``min_length``)."""
        buf = bytearray(max(min_length, 19 + len(self.payload)))
        buf[0:6] = self.dst_mac
        buf[6:12] = self.src_mac
        buf[12] = ETHERTYPE_HOMEPLUG_AV >> 8
        buf[13] = ETHERTYPE_HOMEPLUG_AV & 0xFF
        buf[14] = self.mmv
        # MMTYPE stored little-endian per HomePlug spec.
        mmt = self.mmtype
        buf[15] = mmt & 0xFF
        buf[16] = (mmt >> 8) & 0xFF
        # FMI at offsets 17-18 (defaulting to 0x0000 = unfragmented).
        fmi = (self.fmi + b"\x00\x00")[:2]
        buf[17] = fmi[0]
        buf[18] = fmi[1]
        # MME-specific payload starts at byte 19.
        for i, b in enumerate(self.payload):
            pos = 19 + i
            if pos < len(buf):
                buf[pos] = b
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional["HomePlugFrame"]:
        """Parse a raw ethernet frame. Returns None if it isn't a
        HomePlug AV MME (wrong ethertype / too short)."""
        if len(data) < 19:
            return None
        ethertype = (data[12] << 8) | data[13]
        if ethertype != ETHERTYPE_HOMEPLUG_AV:
            return None
        mmv = data[14]
        mmtype = data[15] | (data[16] << 8)
        mmtype_base = mmtype & ~0x03
        mmsub = mmtype & 0x03
        fmi = bytes(data[17:19])
        payload = bytes(data[19:])
        return cls(
            dst_mac=bytes(data[0:6]),
            src_mac=bytes(data[6:12]),
            mmv=mmv,
            mmtype_base=mmtype_base,
            mmsub=mmsub,
            fmi=fmi,
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

    def is_start_atten_char_ind(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_START_ATTEN_CHAR
                and self.mmsub == MMSUB_IND)

    def is_mnbc_sound_ind(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_MNBC_SOUND
                and self.mmsub == MMSUB_IND)

    def is_atten_char_ind(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_ATTEN_CHAR
                and self.mmsub == MMSUB_IND)

    def is_atten_char_rsp(self) -> bool:
        return (self.mmtype_base == MMTYPE_CM_ATTEN_CHAR
                and self.mmsub == MMSUB_RSP)


# --- Frame builders -----------------------------------------------------
#
# Every builder returns a ``HomePlugFrame`` whose ``payload`` field is
# populated starting at wire byte 19 (NOT byte 17). Field offsets within
# each ``payload`` mirror pyPLC's ``compose*`` methods byte-for-byte —
# any divergence is a bug.


def build_slac_param_req(src_mac: bytes, run_id: bytes) -> HomePlugFrame:
    """PEV -> (broadcast): kicks off SLAC matching.

    Wire layout (matches pyPLC ``composeSlacParamReq``)::

        19      APPLICATION_TYPE = 0
        20      SECURITY_TYPE = 0
        21..28  RunID (8 bytes)
    """
    assert len(src_mac) == 6 and len(run_id) == 8
    # Total frame is 60 bytes on the wire; payload from byte 19 onwards
    # is therefore at most 41 bytes. We size to match pyPLC.
    payload = bytearray(60 - 19)
    payload[0] = 0x00                   # APPLICATION_TYPE
    payload[1] = 0x00                   # SECURITY_TYPE
    for i in range(8):
        payload[2 + i] = run_id[i]      # bytes 21..28 = RunID
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_PARAM,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


def build_slac_param_cnf(src_mac: bytes, dst_mac: bytes,
                         run_id: bytes) -> HomePlugFrame:
    """EVSE -> PEV: confirms SLAC params.

    Wire layout (matches pyPLC ``composeSlacParamCnf``)::

        19..24  M_SOUND_TARGET (FF×6, broadcast)
        25      NUM_SOUNDS (0x0A = 10)
        26      TIME_OUT (0x06 — 600 ms)
        27      RESP_TYPE (0x01)
        28..33  FORWARDING_STA = PEV MAC
        34..35  reserved (0x00 0x00)
        36..43  RunID
    """
    assert len(src_mac) == 6 and len(dst_mac) == 6 and len(run_id) == 8
    payload = bytearray(60 - 19)
    # 19..24 (offsets 0..5): M_SOUND_TARGET
    for i in range(6):
        payload[i] = 0xFF
    payload[6] = 0x0A                   # NUM_SOUNDS
    payload[7] = 0x06                   # TIME_OUT (×100 ms)
    payload[8] = 0x01                   # RESP_TYPE
    # 28..33 (offsets 9..14): FORWARDING_STA = PEV MAC (the dst_mac).
    for i in range(6):
        payload[9 + i] = dst_mac[i]
    # 34..35 (offsets 15..16): reserved 0x0000
    # 36..43 (offsets 17..24): RunID
    for i in range(8):
        payload[17 + i] = run_id[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_PARAM,
        mmsub=MMSUB_CNF,
        payload=bytes(payload),
    )


def build_set_key_req(src_mac: bytes, nmk: bytes, nid: bytes) -> HomePlugFrame:
    """Host -> local modem: program NMK (Network Membership Key).

    Wire layout (matches pyPLC ``composeSetKey``)::

        19      KEY_INFO_TYPE = 0x01 (NMK)
        20..23  MY_NONCE = 0xAA × 4
        24..27  YOUR_NONCE = 0
        28      NW_INFO_PID = 0x04 (HLE per ISO 15118-3 §A.7.2)
        29..30  PRN
        31      PMN
        32      CCo cap
        33..39  NID (7 bytes)
        40      PEKS = 0x01 (NMK)
        41..56  NMK (16 bytes)
    """
    assert len(src_mac) == 6 and len(nmk) == 16 and len(nid) == 7
    payload = bytearray(60 - 19)
    payload[0] = 0x01                   # KEY_INFO_TYPE = NMK
    # MY_NONCE (offsets 1..4)
    payload[1] = 0xAA
    payload[2] = 0xAA
    payload[3] = 0xAA
    payload[4] = 0xAA
    # YOUR_NONCE (offsets 5..8) — zero
    payload[9] = 0x04                   # NW_INFO_PID = HLE
    # PRN, PMN, CCo cap (offsets 10..13) — zero
    # NID at offset 14 (= byte 33)
    for i in range(7):
        payload[14 + i] = nid[i]
    payload[21] = 0x01                  # PEKS = NMK
    # NMK at offset 22 (= byte 41)
    for i in range(16):
        payload[22 + i] = nmk[i]
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,           # local modem listens broadcast
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SET_KEY,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


def build_set_key_cnf(src_mac: bytes, dst_mac: bytes,
                      success: bool = True) -> HomePlugFrame:
    """Modem -> host: confirms NMK programming.

    Layout follows HomePlug AV 11.5.5: byte 19 = RESULT (0=success).
    """
    payload = bytearray(60 - 19)
    payload[0] = 0x00 if success else 0x01
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
    """PEV -> EVSE: commit to pairing.

    Wire layout (matches pyPLC ``composeSlacMatchReq``, total 85 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21..22  MVF Length (little-endian 0x003E)
        23..39  PEV_ID (17 bytes, all 0)
        40..45  PEV_MAC
        46..62  EVSE_ID (17 bytes, all 0)
        63..68  EVSE_MAC
        69..76  RunID
        77..84  reserved (all 0)
    """
    assert all(len(x) == 6 for x in (src_mac, dst_mac, pev_mac, evse_mac))
    assert len(run_id) == 8
    payload = bytearray(85 - 19)        # 66 bytes
    payload[0] = 0x00                   # APP_TYPE
    payload[1] = 0x00                   # SEC_TYPE
    payload[2] = 0x3E                   # MVF length (LE)
    payload[3] = 0x00
    # 23..39 PEV_ID — zero (offsets 4..20)
    # 40..45 PEV_MAC at offset 21
    for i in range(6):
        payload[21 + i] = pev_mac[i]
    # 46..62 EVSE_ID — zero (offsets 27..43)
    # 63..68 EVSE_MAC at offset 44
    for i in range(6):
        payload[44 + i] = evse_mac[i]
    # 69..76 RunID at offset 50
    for i in range(8):
        payload[50 + i] = run_id[i]
    # 77..84 reserved (offsets 58..65) — zero
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_MATCH,
        mmsub=MMSUB_REQ,
        payload=bytes(payload),
    )


# --- Attenuation-round builders (ISO 15118-3 §A.7.1 / HomePlug AV 11.2.11) ----


def build_start_atten_char_ind(
    src_mac: bytes,
    run_id: bytes,
    num_sounds: int = 10,
    timeout_100ms: int = 6,
) -> HomePlugFrame:
    """PEV -> (broadcast): kicks off the sounding phase.

    Wire layout (matches pyPLC ``composeStartAttenCharInd``, 60 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21      NUM_SOUNDS (0x0A)
        22      TIME_OUT (×100 ms)
        23      RESP_TYPE = 1
        24..29  FORWARDING_STA = PEV MAC
        30..37  RunID
    """
    assert len(src_mac) == 6 and len(run_id) == 8
    payload = bytearray(60 - 19)
    payload[0] = 0x00                       # APP_TYPE
    payload[1] = 0x00                       # SEC_TYPE
    payload[2] = num_sounds & 0xFF          # NUM_SOUNDS
    payload[3] = timeout_100ms & 0xFF       # TIME_OUT
    payload[4] = 0x01                       # RESP_TYPE
    # 24..29 FORWARDING_STA = PEV MAC (offset 5..10)
    for i in range(6):
        payload[5 + i] = src_mac[i]
    # 30..37 RunID (offset 11..18)
    for i in range(8):
        payload[11 + i] = run_id[i]
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_START_ATTEN_CHAR,
        mmsub=MMSUB_IND,
        payload=bytes(payload),
    )


def build_mnbc_sound_ind(
    src_mac: bytes,
    run_id: bytes,
    remaining_sounds: int,
    rnd_nonce: Optional[bytes] = None,
) -> HomePlugFrame:
    """PEV -> (broadcast): one of ``num_sounds`` training frames.

    Wire layout (matches pyPLC ``composeNmbcSoundInd``, 71 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21..37  SENDER_ID (17 bytes, all 0)
        38      COUNTDOWN (remaining sounds)
        39..46  RunID
        47..54  reserved (all 0)
        55..70  random nonce (16 bytes)
    """
    import os as _os
    assert len(src_mac) == 6 and len(run_id) == 8
    if rnd_nonce is None:
        rnd_nonce = _os.urandom(16)
    payload = bytearray(71 - 19)            # 52 bytes
    payload[0] = 0x00                       # APP_TYPE
    payload[1] = 0x00                       # SEC_TYPE
    # 21..37 SENDER_ID — zero (offsets 2..18)
    payload[19] = remaining_sounds & 0xFF   # COUNTDOWN @ byte 38
    # 39..46 RunID (offsets 20..27)
    for i in range(8):
        payload[20 + i] = run_id[i]
    # 47..54 reserved — zero (offsets 28..35)
    # 55..70 random nonce (offsets 36..51)
    for i in range(16):
        payload[36 + i] = rnd_nonce[i]
    return HomePlugFrame(
        dst_mac=MAC_BROADCAST,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_MNBC_SOUND,
        mmsub=MMSUB_IND,
        payload=bytes(payload),
    )


def build_atten_char_ind(
    src_mac: bytes,
    dst_mac: bytes,
    run_id: bytes,
    pev_mac: bytes,
    num_sounds: int,
    attenuation_profile: bytes = b"",
) -> HomePlugFrame:
    """EVSE -> PEV: attenuation profile after sounds collected.

    Wire layout (matches pyPLC ``composeAttenCharInd``, 129 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21..26  SOURCE_MAC (PEV MAC, per Alpitronic convention)
        27..34  RunID
        35..51  SOURCE_ID (17 bytes, 0)
        52..68  RESP_ID (17 bytes, 0)
        69      NUM_SOUNDS (0x0A)
        70      NUM_GROUPS (0x3A = 58)
        71..128 ATTEN_PROFILE (58 bytes)
    """
    assert len(src_mac) == 6 and len(dst_mac) == 6
    assert len(pev_mac) == 6 and len(run_id) == 8
    profile = bytearray(58)
    if attenuation_profile:
        n = min(len(attenuation_profile), 58)
        profile[:n] = attenuation_profile[:n]
    else:
        # pyPLC fills with 9 (typical 1..0x19 range; 0 = "defective" to IONIQ).
        for i in range(58):
            profile[i] = 9
        # Higher attenuation for top frequencies, copied from pyPLC.
        profile[55] = 0x0F
        profile[56] = 0x13
        profile[57] = 0x19
    payload = bytearray(129 - 19)           # 110 bytes
    payload[0] = 0x00                       # APP_TYPE
    payload[1] = 0x00                       # SEC_TYPE
    # 21..26 SOURCE_MAC = PEV MAC (offsets 2..7)
    for i in range(6):
        payload[2 + i] = pev_mac[i]
    # 27..34 RunID (offsets 8..15)
    for i in range(8):
        payload[8 + i] = run_id[i]
    # 35..51 SOURCE_ID — zero (offsets 16..32)
    # 52..68 RESP_ID — zero (offsets 33..49)
    payload[50] = num_sounds & 0xFF         # NUM_SOUNDS @ byte 69
    payload[51] = 0x3A                      # NUM_GROUPS = 58 @ byte 70
    # 71..128 ATTEN_PROFILE (offsets 52..109)
    for i in range(58):
        payload[52 + i] = profile[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_ATTEN_CHAR,
        mmsub=MMSUB_IND,
        payload=bytes(payload),
    )


def build_atten_char_rsp(
    src_mac: bytes,
    dst_mac: bytes,
    run_id: bytes,
    pev_mac: bytes,
    result: int = 0,
) -> HomePlugFrame:
    """PEV -> EVSE: acknowledges the attenuation profile.

    Wire layout (matches pyPLC ``composeAttenCharRsp``, 70 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21..26  SOURCE_MAC = PEV MAC
        27..34  RunID
        35..51  SOURCE_ID (17 bytes, 0)
        52..68  RESP_ID (17 bytes, 0)
        69      RESULT (0 = ok)
    """
    assert len(src_mac) == 6 and len(dst_mac) == 6
    assert len(pev_mac) == 6 and len(run_id) == 8
    payload = bytearray(70 - 19)            # 51 bytes
    payload[0] = 0x00                       # APP_TYPE
    payload[1] = 0x00                       # SEC_TYPE
    # 21..26 SOURCE_MAC = PEV MAC (offsets 2..7)
    for i in range(6):
        payload[2 + i] = pev_mac[i]
    # 27..34 RunID (offsets 8..15)
    for i in range(8):
        payload[8 + i] = run_id[i]
    # 35..68 SOURCE_ID + RESP_ID — zero (offsets 16..49)
    payload[50] = result & 0xFF             # RESULT @ byte 69
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_ATTEN_CHAR,
        mmsub=MMSUB_RSP,
        payload=bytes(payload),
    )


def build_slac_match_cnf(src_mac: bytes, dst_mac: bytes,
                         run_id: bytes, nmk: bytes,
                         nid: bytes) -> HomePlugFrame:
    """EVSE -> PEV: "matched, use this NMK/NID".

    Wire layout (matches pyPLC ``composeSlacMatchCnf``, 109 bytes)::

        19      APP_TYPE = 0
        20      SEC_TYPE = 0
        21..22  MVF Length (little-endian 0x0056)
        23..39  PEV_ID (17 bytes, 0)
        40..45  PEV_MAC
        46..62  EVSE_ID (17 bytes, 0)
        63..68  EVSE_MAC
        69..76  RunID
        77..84  reserved (0)
        85..91  NID (7 bytes)
        92      reserved (0)
        93..108 NMK (16 bytes)
    """
    assert all(len(x) == 6 for x in (src_mac, dst_mac))
    assert len(run_id) == 8 and len(nmk) == 16 and len(nid) == 7
    payload = bytearray(109 - 19)           # 90 bytes
    payload[0] = 0x00                       # APP_TYPE
    payload[1] = 0x00                       # SEC_TYPE
    payload[2] = 0x56                       # length LE
    payload[3] = 0x00
    # 23..39 PEV_ID — zero (offsets 4..20)
    # 40..45 PEV_MAC = dst_mac (offsets 21..26)
    for i in range(6):
        payload[21 + i] = dst_mac[i]
    # 46..62 EVSE_ID — zero (offsets 27..43)
    # 63..68 EVSE_MAC = src_mac (offsets 44..49)
    for i in range(6):
        payload[44 + i] = src_mac[i]
    # 69..76 RunID (offsets 50..57)
    for i in range(8):
        payload[50 + i] = run_id[i]
    # 77..84 reserved — zero (offsets 58..65)
    # 85..91 NID (offsets 66..72)
    for i in range(7):
        payload[66 + i] = nid[i]
    # 92 reserved — zero (offset 73)
    # 93..108 NMK (offsets 74..89)
    for i in range(16):
        payload[74 + i] = nmk[i]
    return HomePlugFrame(
        dst_mac=dst_mac,
        src_mac=src_mac,
        mmtype_base=MMTYPE_CM_SLAC_MATCH,
        mmsub=MMSUB_CNF,
        payload=bytes(payload),
    )


def extract_run_id(frame: HomePlugFrame) -> Optional[bytes]:
    """Best-effort RunID extraction from SLAC-family frames.

    Offsets are relative to the new payload (which starts at wire byte 19),
    matching pyPLC's compose layouts byte-for-byte.
    """
    # SLAC_PARAM.REQ: payload[2..9] = RunID (wire bytes 21..28)
    if frame.is_slac_param_req() and len(frame.payload) >= 10:
        return frame.payload[2:10]
    # SLAC_PARAM.CNF: payload[17..24] = RunID (wire bytes 36..43)
    if frame.is_slac_param_cnf() and len(frame.payload) >= 25:
        return frame.payload[17:25]
    # SLAC_MATCH.REQ: payload[50..57] = RunID (wire bytes 69..76)
    if frame.is_slac_match_req() and len(frame.payload) >= 58:
        return frame.payload[50:58]
    # SLAC_MATCH.CNF: payload[50..57] = RunID (wire bytes 69..76)
    if frame.is_slac_match_cnf() and len(frame.payload) >= 58:
        return frame.payload[50:58]
    # MNBC_SOUND.IND: payload[20..27] = RunID (wire bytes 39..46)
    if frame.is_mnbc_sound_ind() and len(frame.payload) >= 28:
        return frame.payload[20:28]
    # ATTEN_CHAR.IND / RSP: payload[8..15] = RunID (wire bytes 27..34)
    if (frame.is_atten_char_ind() or frame.is_atten_char_rsp()) \
            and len(frame.payload) >= 16:
        return frame.payload[8:16]
    # START_ATTEN_CHAR.IND: payload[11..18] = RunID (wire bytes 30..37)
    if frame.is_start_atten_char_ind() and len(frame.payload) >= 19:
        return frame.payload[11:19]
    return None
