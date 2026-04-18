"""
SDP client — PEV-side SECC discovery.

Once SLAC has paired the PEV and EVSE modems, both sides are on the
same HomePlug AVLN and can talk IPv6 over it. The PEV still doesn't
know the EVSE's IPv6 address though, so it multicasts a SECC Discovery
Request to ``ff02::1%<iface>`` port 15118. Whichever EVSE is on the
link replies (unicast, to the PEV's source port) with its own
``(IPv6, TCP port, security, transport)`` tuple. The PEV then opens
TCP to that endpoint and begins V2G.

This is the minimum discovery step that turns HotWire from "works on
loopback" into "works with a real charger".

The client lives in its own module (rather than inline in the FSM) so
it can be used standalone from the CLI for debugging: feeding the
discovery response back into ``hotwire/core/addressManager.py`` lets an
operator probe a charger in the field without spinning up the full FSM.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional

from .protocol import (
    SDP_MULTICAST_ADDR,
    SDP_PORT,
    SDP_SECURITY_NONE,
    SDP_TRANSPORT_TCP,
    SdpResponse,
    build_sdp_request,
    parse_sdp_response,
)


log = logging.getLogger(__name__)


class SdpClient:
    """Block until an SECC answers on a given IPv6 interface.

    Parameters
    ----------
    scope_id
        Interface index (``socket.if_nametoindex("eth0")``) for the IPv6
        link-local scope. Pass 0 to let the OS pick the default interface.
    timeout_s
        How long one discovery attempt waits before giving up.
    retries
        Number of full retry cycles. A real charger tends to answer on
        the first multicast; we retry for lossy links.
    security
        Propagated into the request. Only ``SDP_SECURITY_NONE`` and
        ``SDP_SECURITY_TLS`` are defined by the spec.
    """

    def __init__(
        self,
        scope_id: int = 0,
        timeout_s: float = 2.0,
        retries: int = 3,
        security: int = SDP_SECURITY_NONE,
        transport: int = SDP_TRANSPORT_TCP,
    ) -> None:
        self.scope_id = scope_id
        self.timeout_s = timeout_s
        self.retries = retries
        self.security = security
        self.transport = transport

    def discover(self) -> Optional[SdpResponse]:
        """Send discovery requests and return the first SECC response
        received, or ``None`` if everything timed out."""
        req_frame = build_sdp_request(
            security=self.security, transport=self.transport
        )

        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 1)
            # Bind to an ephemeral source port — the SECC answers unicast
            # back here. We don't need to bind to any specific address.
            sock.bind(("::", 0, 0, self.scope_id))
            sock.settimeout(self.timeout_s)

            target = (SDP_MULTICAST_ADDR, SDP_PORT, 0, self.scope_id)
            for attempt in range(1, self.retries + 1):
                log.debug(
                    "[SDP client] attempt %d/%d -> %s",
                    attempt, self.retries, target,
                )
                sock.sendto(req_frame, target)
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                resp = parse_sdp_response(data)
                if resp is None:
                    # Non-SDP traffic on 15118; keep waiting on this attempt.
                    log.debug(
                        "[SDP client] got %d bytes from %s — not an SDP "
                        "response, ignoring", len(data), addr,
                    )
                    continue
                log.info(
                    "[SDP client] discovered SECC at [%s]:%d security=0x%02x",
                    resp.ip, resp.port, resp.security,
                )
                return resp
        return None


def discover_secc(
    scope_id: int = 0,
    timeout_s: float = 2.0,
    retries: int = 3,
) -> Optional[SdpResponse]:
    """Convenience wrapper when the caller doesn't need the class instance."""
    return SdpClient(
        scope_id=scope_id, timeout_s=timeout_s, retries=retries
    ).discover()
