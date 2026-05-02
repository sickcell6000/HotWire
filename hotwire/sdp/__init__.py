"""
SECC Discovery Protocol (SDP) — ISO 15118-2 Annex A.

An EVCC (PEV) multicasts a tiny UDP packet to ``ff02::1`` port 15118
right after SLAC pairing, asking "who's my SECC?". The SECC (EVSE)
replies with its own IPv6 address + TCP port so the PEV knows where
to open the TLS/TCP socket for the rest of the V2G session.

Two bytes of wire payload per direction, plus a V2GTP header — but
this discovery step is the one place the PEV cannot hard-code the
SECC's IP, and skipping it means HotWire can only ever talk to
``::1`` loopback. Implementing SDP is what turns this into a real
charging-equipment-compatible tool.

Submodule layout:

* :mod:`hotwire.sdp.protocol`  wire-format helpers (encode / decode)
* :mod:`hotwire.sdp.client`    PEV side — discover the SECC
* :mod:`hotwire.sdp.server`    EVSE side — respond to discovery

The implementation leans on Python's stdlib IPv6 UDP socket rather
than re-implementing the IP header like upstream pyPLC does — the
embedded-platform driver that required manual header crafting is
not our use case.
"""
from .protocol import (
    SDP_PORT,
    SDP_MULTICAST_ADDR,
    SDP_SECURITY_NONE,
    SDP_SECURITY_TLS,
    SDP_TRANSPORT_TCP,
    V2GTP_PAYLOAD_SDP_REQ,
    V2GTP_PAYLOAD_SDP_RSP,
    build_sdp_request,
    build_sdp_response,
    parse_sdp_request,
    parse_sdp_response,
    SdpRequest,
    SdpResponse,
)

__all__ = [
    "SDP_PORT",
    "SDP_MULTICAST_ADDR",
    "SDP_SECURITY_NONE",
    "SDP_SECURITY_TLS",
    "SDP_TRANSPORT_TCP",
    "V2GTP_PAYLOAD_SDP_REQ",
    "V2GTP_PAYLOAD_SDP_RSP",
    "build_sdp_request",
    "build_sdp_response",
    "parse_sdp_request",
    "parse_sdp_response",
    "SdpRequest",
    "SdpResponse",
]
