"""
SDP (SECC Discovery Protocol) round-trip tests.

These cover both the pure wire-format codec and the live UDP
client/server running back-to-back over ``::1`` loopback. Loopback is
deliberate — the goal is to validate *our* logic, not the host's
IPv6 multicast routing. Once hardware is available a separate
integration test can point the client at a real modem.
"""
from __future__ import annotations

import ipaddress
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.sdp.protocol import (  # noqa: E402
    SDP_MULTICAST_ADDR,
    SDP_PORT,
    SDP_SECURITY_NONE,
    SDP_SECURITY_TLS,
    SDP_TRANSPORT_TCP,
    SdpRequest,
    SdpResponse,
    V2GTP_PAYLOAD_SDP_REQ,
    V2GTP_PAYLOAD_SDP_RSP,
    build_sdp_request,
    build_sdp_response,
    parse_sdp_request,
    parse_sdp_response,
)
from hotwire.sdp.client import SdpClient  # noqa: E402
from hotwire.sdp.server import SdpServer  # noqa: E402


# --- Wire-format codec ------------------------------------------------


def test_sdp_request_roundtrip() -> None:
    frame = build_sdp_request()
    assert len(frame) == 10  # 8-byte V2GTP header + 2-byte payload
    assert frame[:2] == b"\x01\xfe"  # V2GTP version + inverted
    assert frame[2:4] == b"\x90\x00"
    assert frame[4:8] == b"\x00\x00\x00\x02"
    req = parse_sdp_request(frame)
    assert req == SdpRequest(
        security=SDP_SECURITY_NONE, transport=SDP_TRANSPORT_TCP
    )


def test_sdp_request_tls_override() -> None:
    frame = build_sdp_request(security=SDP_SECURITY_TLS)
    req = parse_sdp_request(frame)
    assert req is not None
    assert req.security == SDP_SECURITY_TLS


def test_sdp_response_roundtrip() -> None:
    ip = ipaddress.IPv6Address("fe80::1234")
    frame = build_sdp_response(ip=ip, port=15118)
    assert len(frame) == 28
    resp = parse_sdp_response(frame)
    assert resp is not None
    assert resp.ip == ip
    assert resp.port == 15118
    assert resp.security == SDP_SECURITY_NONE
    assert resp.transport == SDP_TRANSPORT_TCP


def test_parse_sdp_request_rejects_garbage() -> None:
    # Random bytes shouldn't crash the SECC — parser must return None.
    assert parse_sdp_request(b"") is None
    assert parse_sdp_request(b"\x00" * 8) is None
    assert parse_sdp_request(b"\x01\xfe\x90\x00") is None  # truncated


def test_parse_sdp_response_rejects_wrong_type() -> None:
    # Request payload type piped into response parser must return None.
    assert parse_sdp_response(build_sdp_request()) is None


def test_parse_sdp_rejects_bad_v2gtp_version() -> None:
    frame = bytearray(build_sdp_request())
    frame[0] = 0x02  # wrong version
    assert parse_sdp_request(bytes(frame)) is None


# --- Live UDP loopback ------------------------------------------------


@pytest.mark.integration
def test_sdp_loopback_discovery() -> None:
    """Spin up a server on ``::1`` and verify the client finds it."""
    # Bind the server to loopback. scope_id=0 on loopback because ::1
    # has no scope, and lets the test run without knowing the host's
    # interface names.
    secc_ip = ipaddress.IPv6Address("::1")
    secc_port = 57199

    # Ephemeral UDP socket for the test client. Instead of using the real
    # SdpClient (which sends to ff02::1 — not reachable on every CI runner
    # with multicast disabled), we unicast directly to ::1:15118 and check
    # the response. This isolates the server logic from the OS multicast
    # routing table.
    server = SdpServer(
        secc_ip=secc_ip, secc_port=secc_port, scope_id=0,
    )
    server.start()
    try:
        time.sleep(0.05)  # let the server thread settle
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.sendto(build_sdp_request(), ("::1", SDP_PORT))
            data, _addr = sock.recvfrom(2048)
        resp = parse_sdp_response(data)
        assert resp is not None
        assert resp.ip == secc_ip
        assert resp.port == secc_port
        assert resp.security == SDP_SECURITY_NONE
    finally:
        server.stop()


@pytest.mark.integration
def test_sdp_server_ignores_non_sdp_traffic() -> None:
    """Garbage UDP on port 15118 must not crash the server."""
    secc_ip = ipaddress.IPv6Address("::1")
    server = SdpServer(secc_ip=secc_ip, secc_port=60000, scope_id=0)
    server.start()
    try:
        time.sleep(0.05)
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.3)
            sock.sendto(b"hello", ("::1", SDP_PORT))
            # Real request afterwards — server must still answer.
            sock.sendto(build_sdp_request(), ("::1", SDP_PORT))
            data, _ = sock.recvfrom(2048)
        resp = parse_sdp_response(data)
        assert resp is not None
        assert resp.ip == secc_ip
    finally:
        server.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
