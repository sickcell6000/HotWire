"""
SDP wire-format encode / decode.

ISO 15118-2 Annex A §A.1 defines two messages:

    SECC Discovery Request (0x9000):
        Security       uint8   0x00 = TLS, 0x10 = none (must be TLS-or-none)
        TransportProto uint8   0x00 = TCP,  0x10 = UDP (must be TCP)
        ------------
        2 bytes payload, wrapped in the standard 8-byte V2GTP header.

    SECC Discovery Response (0x9001):
        SECC_IP         16B    the SECC's IPv6 address (link-local or ULA)
        SECC_TCP_Port   uint16 big-endian, port the SECC listens on
        Security        uint8  matches the selected security from the req
        TransportProto  uint8  0x00 for TCP
        ------------
        20 bytes payload, again with the V2GTP header.

V2GTP header (shared with EXI messages):

    Offset  Size  Field
    0       1     ProtocolVersion       = 0x01
    1       1     ProtocolVersionInv    = 0xFE
    2       2     PayloadType           big-endian, 0x9000 or 0x9001
    4       4     PayloadLength         big-endian, bytes after header

Keeping all of this in one module means the UDP client, the UDP server,
the pcap replay tests and the future fuzzer can all import the exact
same wire format — no hidden divergence.
"""
from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass
from typing import Optional


# --- Wire constants ----------------------------------------------------

SDP_PORT = 15118
SDP_MULTICAST_ADDR = "ff02::1"

V2GTP_VERSION = 0x01
V2GTP_VERSION_INV = 0xFE
V2GTP_PAYLOAD_SDP_REQ = 0x9000
V2GTP_PAYLOAD_SDP_RSP = 0x9001

SDP_SECURITY_TLS = 0x00
SDP_SECURITY_NONE = 0x10

SDP_TRANSPORT_TCP = 0x00
SDP_TRANSPORT_UDP = 0x10


_V2GTP_HEADER_SIZE = 8
_SDP_REQ_PAYLOAD_SIZE = 2
_SDP_RSP_PAYLOAD_SIZE = 20


# --- Typed message views ----------------------------------------------


@dataclass(frozen=True)
class SdpRequest:
    security: int = SDP_SECURITY_NONE
    transport: int = SDP_TRANSPORT_TCP


@dataclass(frozen=True)
class SdpResponse:
    """SECC's reply telling the EVCC where to open TCP.

    ``ip`` is stored as :class:`ipaddress.IPv6Address` so callers don't
    have to juggle raw bytes when they pass it into ``socket.connect``.
    """

    ip: ipaddress.IPv6Address
    port: int
    security: int = SDP_SECURITY_NONE
    transport: int = SDP_TRANSPORT_TCP


# --- Encoders ---------------------------------------------------------


def _v2gtp_header(payload_type: int, payload_len: int) -> bytes:
    return struct.pack(">BBHI",
                       V2GTP_VERSION, V2GTP_VERSION_INV,
                       payload_type, payload_len)


def build_sdp_request(
    security: int = SDP_SECURITY_NONE,
    transport: int = SDP_TRANSPORT_TCP,
) -> bytes:
    """Encode a 10-byte SDP request frame (V2GTP + 2-byte body)."""
    payload = bytes([security & 0xFF, transport & 0xFF])
    return _v2gtp_header(V2GTP_PAYLOAD_SDP_REQ, _SDP_REQ_PAYLOAD_SIZE) + payload


def build_sdp_response(
    ip: ipaddress.IPv6Address | str,
    port: int,
    security: int = SDP_SECURITY_NONE,
    transport: int = SDP_TRANSPORT_TCP,
) -> bytes:
    """Encode a 28-byte SDP response frame."""
    if not isinstance(ip, ipaddress.IPv6Address):
        ip = ipaddress.IPv6Address(ip)
    if not (0 < port <= 0xFFFF):
        raise ValueError(f"port out of range: {port}")
    payload = ip.packed + struct.pack(">H", port) + bytes(
        [security & 0xFF, transport & 0xFF]
    )
    if len(payload) != _SDP_RSP_PAYLOAD_SIZE:
        raise AssertionError(
            f"SDP response payload must be {_SDP_RSP_PAYLOAD_SIZE} bytes, "
            f"got {len(payload)}"
        )
    return _v2gtp_header(V2GTP_PAYLOAD_SDP_RSP, _SDP_RSP_PAYLOAD_SIZE) + payload


# --- Decoders ---------------------------------------------------------


def _parse_v2gtp_header(frame: bytes) -> tuple[int, int]:
    """Validate the 8-byte V2GTP header and return ``(payload_type, length)``.

    Raises :class:`ValueError` on malformed input — callers that want a
    soft-fail semantics should wrap this with ``try / except``.
    """
    if len(frame) < _V2GTP_HEADER_SIZE:
        raise ValueError(f"V2GTP frame too short: {len(frame)} bytes")
    ver, ver_inv, payload_type, payload_len = struct.unpack_from(
        ">BBHI", frame, 0
    )
    if ver != V2GTP_VERSION or ver_inv != V2GTP_VERSION_INV:
        raise ValueError(
            f"Bad V2GTP version: {ver:02x}/{ver_inv:02x} "
            f"(expected {V2GTP_VERSION:02x}/{V2GTP_VERSION_INV:02x})"
        )
    if len(frame) < _V2GTP_HEADER_SIZE + payload_len:
        raise ValueError(
            f"V2GTP frame truncated: header says {payload_len} bytes, "
            f"got {len(frame) - _V2GTP_HEADER_SIZE}"
        )
    return payload_type, payload_len


def parse_sdp_request(frame: bytes) -> Optional[SdpRequest]:
    """Decode a SDP request. Returns ``None`` if ``frame`` isn't a valid
    SDP request — we don't raise because the UDP receiver sees all
    sorts of traffic on ``ff02::1`` and noise shouldn't crash the SECC.
    """
    try:
        payload_type, payload_len = _parse_v2gtp_header(frame)
    except ValueError:
        return None
    if payload_type != V2GTP_PAYLOAD_SDP_REQ:
        return None
    if payload_len != _SDP_REQ_PAYLOAD_SIZE:
        return None
    body = frame[_V2GTP_HEADER_SIZE:_V2GTP_HEADER_SIZE + _SDP_REQ_PAYLOAD_SIZE]
    return SdpRequest(security=body[0], transport=body[1])


def parse_sdp_response(frame: bytes) -> Optional[SdpResponse]:
    """Decode a SDP response, or ``None`` if it isn't one."""
    try:
        payload_type, payload_len = _parse_v2gtp_header(frame)
    except ValueError:
        return None
    if payload_type != V2GTP_PAYLOAD_SDP_RSP:
        return None
    if payload_len != _SDP_RSP_PAYLOAD_SIZE:
        return None
    body = frame[_V2GTP_HEADER_SIZE:_V2GTP_HEADER_SIZE + _SDP_RSP_PAYLOAD_SIZE]
    ip = ipaddress.IPv6Address(bytes(body[0:16]))
    port = struct.unpack_from(">H", body, 16)[0]
    return SdpResponse(
        ip=ip,
        port=port,
        security=body[18],
        transport=body[19],
    )
