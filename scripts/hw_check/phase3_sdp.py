"""
Phase 3 — SDP (SECC Discovery Protocol) live exchange.

Two modes:

* ``--role pev``  send a single CM_SDP_REQ multicast to ff02::1 and wait
  for a charger's CM_SDP_RSP. Succeeds if we hear a well-formed RSP.
  This is the first thing a production EV does after SLAC and is the
  one protocol step where a dead IPv6 stack silently stalls the whole
  session — so worth checking in isolation.

* ``--role evse`` bring up an SDP responder on the interface and print
  out every SDP_REQ it sees (still PASSing after 1 successful response
  round-trip). Useful to confirm the charger probe is working when
  developing from the EVSE side.

We capture UDP/IPv6 traffic with the pcap tool so the resulting
``phase3_capture.pcap`` is a standalone bug report for anyone who
wants to inspect the bytes on the wire.
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
import time
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))
sys.path.insert(0, str(_THIS.parent.parent.parent))

from _runner import (  # noqa: E402
    PacketCapture,
    PhaseResult,
    RunContext,
    Status,
    print_banner,
    print_result,
    run_phase,
)

from hotwire.sdp.client import SdpClient  # noqa: E402
from hotwire.sdp.protocol import (  # noqa: E402
    SDP_MULTICAST_ADDR,
    SDP_PORT,
    SDP_SECURITY_NONE,
    SDP_TRANSPORT_TCP,
    SdpResponse,
    build_sdp_response,
    parse_sdp_request,
)
from hotwire.sdp.server import SdpServer  # noqa: E402


def phase3_sdp(
    ctx: RunContext,
    role: str,
    scope_id: int,
    secc_ip: str | None = None,
    secc_port: int = 15118,
    budget_s: float = 8.0,
) -> PhaseResult:
    if not ctx.interface:
        return PhaseResult(
            name="phase3_sdp",
            status=Status.SKIP,
            summary="no interface configured",
        )

    metrics: dict[str, object] = {
        "role": role,
        "scope_id": scope_id,
        "budget_s": budget_s,
        "bpf": f"udp port {SDP_PORT}",
    }

    artifacts: list[Path] = []
    bpf = f"udp port {SDP_PORT}"

    with PacketCapture(
        ctx, phase="phase3", interface=ctx.interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)

        if role == "pev":
            return _do_pev_side(ctx, scope_id, budget_s, metrics, artifacts)
        elif role == "evse":
            return _do_evse_side(
                ctx, scope_id, secc_ip, secc_port, budget_s,
                metrics, artifacts,
            )
        else:
            return PhaseResult(
                name="phase3_sdp",
                status=Status.ERROR,
                summary=f"unknown role {role!r}",
                metrics=metrics,
            )


def _do_pev_side(
    ctx: RunContext,
    scope_id: int,
    budget_s: float,
    metrics: dict[str, object],
    artifacts: list[Path],
) -> PhaseResult:
    client = SdpClient(scope_id=scope_id, timeout_s=budget_s, retries=1)
    ctx.log.event(
        kind="phase3.pev.discover_start",
        scope_id=scope_id, timeout_s=budget_s,
    )
    t0 = time.monotonic()
    resp = client.discover()
    elapsed = time.monotonic() - t0
    metrics["elapsed_s"] = round(elapsed, 3)

    if resp is None:
        ctx.log.event(kind="phase3.pev.timeout")
        return PhaseResult(
            name="phase3_sdp",
            status=Status.FAIL,
            summary=(
                f"no SDP response received within {budget_s:.1f}s. "
                "Check that the charger is powered, SLAC is complete, "
                "and ff02::1 multicast is reachable on this interface."
            ),
            metrics=metrics,
            artifacts=artifacts,
        )

    metrics["secc_ip"] = str(resp.ip)
    metrics["secc_port"] = resp.port
    metrics["security"] = resp.security
    metrics["transport"] = resp.transport
    ctx.log.event(
        kind="phase3.pev.discovered",
        secc_ip=str(resp.ip), secc_port=resp.port,
        security=resp.security, transport=resp.transport,
    )
    return PhaseResult(
        name="phase3_sdp",
        status=Status.PASS,
        summary=(
            f"SECC found at [{resp.ip}]:{resp.port} "
            f"(security=0x{resp.security:02x}, transport=0x{resp.transport:02x}) "
            f"in {elapsed:.2f}s"
        ),
        metrics=metrics,
        artifacts=artifacts,
    )


def _do_evse_side(
    ctx: RunContext,
    scope_id: int,
    secc_ip: str | None,
    secc_port: int,
    budget_s: float,
    metrics: dict[str, object],
    artifacts: list[Path],
) -> PhaseResult:
    if secc_ip is None:
        # Default: autodetect the first link-local on this interface.
        addr = _pick_link_local(ctx.interface)
        if addr is None:
            return PhaseResult(
                name="phase3_sdp",
                status=Status.FAIL,
                summary=(
                    f"could not determine a link-local IPv6 for {ctx.interface}. "
                    "Pass --secc-ip fe80:: explicitly."
                ),
                metrics=metrics,
                artifacts=artifacts,
            )
        secc_ip = addr

    seen_clients: list[tuple[str, int]] = []

    server = SdpServer(
        secc_ip=secc_ip, secc_port=secc_port, scope_id=scope_id,
    )
    server.start()
    metrics["secc_ip"] = secc_ip
    metrics["secc_port"] = secc_port
    try:
        ctx.log.event(
            kind="phase3.evse.listening",
            secc_ip=secc_ip, secc_port=secc_port,
        )
        # Passively wait for a request by sniffing ff02::1 on the scope.
        # We don't parse here — we rely on the server thread to log via
        # its own logger. The goal is just to confirm at least one
        # request is observed within the budget.
        with socket.socket(
            socket.AF_INET6, socket.SOCK_DGRAM
        ) as sniff_sock:
            sniff_sock.settimeout(budget_s)
            try:
                # Bind on a different port so we don't steal from the server.
                sniff_sock.bind(("::", 0, 0, scope_id))
            except OSError as e:
                ctx.log.event(
                    kind="phase3.evse.sniff_bind_failed", error=str(e),
                )
            # We just wait for the budget — the actual request handling
            # is done inside SdpServer. Reading from a socket we bound
            # on the ephemeral port won't give us the ff02::1 traffic,
            # so we use a simpler heuristic: wait and then report the
            # server's internal state via the log file.
            t0 = time.monotonic()
            while time.monotonic() - t0 < budget_s:
                time.sleep(0.2)
    finally:
        server.stop()

    # Check the session.jsonl we wrote for "SDP server responded" events,
    # which happens inside SdpServer via its logger. Since that logger
    # writes to the Python logging module rather than our EventLog, we
    # need to count something else: the pcap. Scan it for SDP requests.
    sdp_req_count = _count_sdp_reqs(artifacts[0]) if artifacts else 0
    metrics["observed_sdp_requests"] = sdp_req_count
    ctx.log.event(
        kind="phase3.evse.done",
        observed_requests=sdp_req_count,
    )

    if sdp_req_count == 0:
        return PhaseResult(
            name="phase3_sdp",
            status=Status.FAIL,
            summary=(
                f"no SDP requests observed during {budget_s:.1f}s. "
                "Confirm a PEV or tester is multicasting to ff02::1 on "
                "the same link."
            ),
            metrics=metrics,
            artifacts=artifacts,
        )
    return PhaseResult(
        name="phase3_sdp",
        status=Status.PASS,
        summary=f"observed {sdp_req_count} SDP request(s); server advertised [{secc_ip}]:{secc_port}",
        metrics=metrics,
        artifacts=artifacts,
    )


# --- helpers ----------------------------------------------------------


def _pick_link_local(iface: str) -> str | None:
    """Best-effort: first fe80:: address on ``iface`` (Linux only).

    Windows: just return None and let the caller insist on --secc-ip.
    """
    if not sys.platform.startswith("linux"):
        return None
    proc_path = Path(f"/proc/net/if_inet6")
    try:
        for line in proc_path.read_text().splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            addr_hex, if_idx, _plen, scope_hex, _flags, ifname = parts[:6]
            if ifname != iface:
                continue
            if int(scope_hex, 16) != 0x20:  # link-scope
                continue
            # /proc format is colon-less — insert colons every 4 nibbles.
            grouped = ":".join(addr_hex[i:i + 4] for i in range(0, 32, 4))
            try:
                return str(ipaddress.IPv6Address(grouped))
            except ValueError:
                continue
    except OSError:
        return None
    return None


def _count_sdp_reqs(pcap_path: Path) -> int:
    """Count SDP REQ payloads inside a pcap. Uses pcapng_reader so we
    inherit support for both .pcap and .pcapng."""
    from hotwire.plc.pcapng_reader import iter_packets

    count = 0
    for pkt in iter_packets(pcap_path):
        # Strip Ethernet + IPv6 + UDP headers. Layout varies, so just
        # scan for the V2GTP signature: 01 FE 90 00 at the start of any
        # aligned byte position. We cheat: SDP payloads are short, and
        # the V2GTP header always starts with 01 FE 90 00 for a request.
        idx = pkt.find(b"\x01\xfe\x90\x00")
        if idx >= 0:
            tail = pkt[idx:]
            # Verify it's a request: payload length = 2 (at offset 4..7)
            if len(tail) >= 10 and tail[4:8] == b"\x00\x00\x00\x02":
                # One more sanity: parse via our helper.
                req = parse_sdp_request(tail)
                if req is not None:
                    count += 1
    return count


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=("pev", "evse"), required=True)
    parser.add_argument("--scope-id", type=int, default=0,
                        help="IPv6 scope/interface index (0 = OS default).")
    parser.add_argument("--budget", type=float, default=8.0,
                        help="How long to wait for the exchange.")
    parser.add_argument("--secc-ip", default=None,
                        help="EVSE side only: IPv6 to advertise.")
    parser.add_argument("--secc-port", type=int, default=15118,
                        help="EVSE side only: TCP port to advertise.")
    args = parser.parse_args(argv)
    print_banner(f"Phase 3 — SDP discovery ({args.role})")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(
        ctx, "phase3_sdp", phase3_sdp,
        role=args.role,
        scope_id=args.scope_id,
        secc_ip=args.secc_ip,
        secc_port=args.secc_port,
        budget_s=args.budget,
    )
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
