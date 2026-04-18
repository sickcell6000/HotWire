"""
SDP server — EVSE-side discovery responder.

Joins the ``ff02::1`` multicast group on a configurable IPv6 interface,
listens for SECC Discovery Requests, and unicasts a response back
advertising the EVSE's own IPv6 address and the TCP port where it's
waiting for V2G traffic.

Runs on its own daemon thread so the FSM's main loop stays reactive.
Thread-safety: the FSM holds a reference but never touches the socket
directly — the only shared state is the "stop" flag.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import threading
from typing import Optional

from .protocol import (
    SDP_MULTICAST_ADDR,
    SDP_PORT,
    SDP_SECURITY_NONE,
    SDP_TRANSPORT_TCP,
    build_sdp_response,
    parse_sdp_request,
)


log = logging.getLogger(__name__)


class SdpServer:
    """Respond to SDP requests on a specific IPv6 scope.

    Parameters
    ----------
    secc_ip
        The IPv6 address the EVSE wants to be reached on. Typically the
        link-local address of the interface SLAC landed on.
    secc_port
        TCP port the FSM is listening on for V2G messages.
    scope_id
        IPv6 interface index. 0 lets the OS pick; on a real charger this
        should be the PLC interface.
    security
        Value advertised back to the PEV in the response.
    """

    def __init__(
        self,
        secc_ip: ipaddress.IPv6Address | str,
        secc_port: int,
        scope_id: int = 0,
        security: int = SDP_SECURITY_NONE,
        transport: int = SDP_TRANSPORT_TCP,
    ) -> None:
        if not isinstance(secc_ip, ipaddress.IPv6Address):
            secc_ip = ipaddress.IPv6Address(secc_ip)
        self.secc_ip = secc_ip
        self.secc_port = secc_port
        self.scope_id = scope_id
        self.security = security
        self.transport = transport

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._sock = self._make_socket()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hotwire-sdp-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout_s: float = 1.0) -> None:
        self._stop.set()
        # Closing the socket unblocks recvfrom.
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)
            self._thread = None
        self._sock = None

    # ------------------------------------------------------------------

    def _make_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Needed on Linux to receive our own multicast on loopback; harmless
        # on Windows.
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, 1)
        except OSError:
            pass

        sock.bind(("::", SDP_PORT, 0, self.scope_id))

        # Join ff02::1 on the requested scope. ``ff02::1`` is the
        # all-nodes link-local group, so technically we don't need to
        # "join" to receive it — but explicitly joining also works on
        # interfaces where the kernel hasn't yet, so do it defensively.
        mreq = socket.inet_pton(socket.AF_INET6, SDP_MULTICAST_ADDR) \
            + struct.pack("I", self.scope_id)
        try:
            sock.setsockopt(
                socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq,
            )
        except OSError as e:
            # Already a member, or loopback-only — log and continue.
            log.debug("[SDP server] IPV6_JOIN_GROUP ignored: %s", e)
        return sock

    def _run(self) -> None:
        assert self._sock is not None
        log.info(
            "[SDP server] listening on [%s]:%d (scope=%d) advertising [%s]:%d",
            SDP_MULTICAST_ADDR, SDP_PORT, self.scope_id,
            self.secc_ip, self.secc_port,
        )
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except OSError:
                # Socket closed during stop().
                return
            req = parse_sdp_request(data)
            if req is None:
                continue
            resp_frame = build_sdp_response(
                ip=self.secc_ip,
                port=self.secc_port,
                security=self.security,
                transport=self.transport,
            )
            try:
                self._sock.sendto(resp_frame, addr)
                log.info(
                    "[SDP server] responded to %s with [%s]:%d",
                    addr, self.secc_ip, self.secc_port,
                )
            except OSError as e:
                log.warning("[SDP server] sendto failed: %s", e)
