"""
Phase 2 — Drive SLAC against real hardware.

Opens :class:`PcapL2Transport` on the configured interface and runs
HotWire's :class:`SlacStateMachine` in the requested role. Meanwhile
a parallel pcap capture records everything on the wire so the result
can be post-mortemed.

Two common scenarios:

1. ``--role pev`` against a real CCS charger — the charger is SECC
   and will respond with CM_SLAC_PARAM.CNF. This is the "can we
   actually drive a charger?" test.

2. Two Raspberry Pis each running ``phase2_slac.py`` — one with
   ``--role pev``, one with ``--role evse``. Lets you verify two
   HomePlug modems you have on the bench talk to each other before
   plugging into a real car.

Success criterion: the state machine reaches ``SLAC_PAIRED`` and we
recorded a ``CM_SLAC_MATCH`` exchange in the pcap.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

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

from hotwire.plc.slac import (  # noqa: E402
    ROLE_EVSE,
    ROLE_PEV,
    SLAC_PAIRED,
    SlacStateMachine,
)
from hotwire.plc.l2_transport import PcapL2Transport  # noqa: E402


def phase2_slac(
    ctx: RunContext,
    role: str,
    local_mac: bytes | None = None,
    budget_s: float = 20.0,
    tick_period_s: float = 0.01,
) -> PhaseResult:
    if not ctx.interface:
        return PhaseResult(
            name="phase2_slac",
            status=Status.SKIP,
            summary="no interface configured",
        )

    # Local MAC auto-detection: try ``getmac`` / fallback via netifaces-lite
    if local_mac is None:
        mac = _resolve_local_mac(ctx.interface)
        if mac is None:
            return PhaseResult(
                name="phase2_slac",
                status=Status.FAIL,
                summary=f"could not resolve MAC for {ctx.interface}",
                details=(
                    "Pass --mac AA:BB:CC:DD:EE:FF explicitly, or make sure "
                    "the interface is present on the host."
                ),
            )
        local_mac = mac
    metrics: dict[str, object] = {
        "role": role,
        "interface": ctx.interface,
        "local_mac": local_mac.hex(),
        "budget_s": budget_s,
    }

    # Open the L2 transport
    try:
        transport = PcapL2Transport(ctx.interface)
    except RuntimeError as e:
        return PhaseResult(
            name="phase2_slac",
            status=Status.FAIL,
            summary=f"pcap open failed: {e}",
            details="Install pcap-ct on Windows or run as root/CAP_NET_RAW on Linux.",
            metrics=metrics,
        )

    bpf = "ether proto 0x88E1"
    traces: list[str] = []
    callbacks: list[tuple[bytes, bytes, bytes]] = []

    def _trace(msg: str) -> None:
        traces.append(msg)
        ctx.log.event(kind="phase2.trace", message=msg)

    def _slac_ok(nmk: bytes, nid: bytes, peer_mac: bytes) -> None:
        callbacks.append((nmk, nid, peer_mac))
        ctx.log.event(
            kind="phase2.slac_ok",
            nmk_prefix=nmk[:4].hex(),
            nid=nid.hex(),
            peer_mac=peer_mac.hex(),
        )

    sm = SlacStateMachine(
        role=role,
        transport=transport,
        local_mac=local_mac,
        callback_add_to_trace=_trace,
        callback_slac_ok=_slac_ok,
    )
    sm._total_timeout_s = budget_s                           # noqa: SLF001

    artifacts: list[Path] = []
    with PacketCapture(
        ctx, phase="phase2", interface=ctx.interface, bpf=bpf,
    ) as cap:
        if cap.available and cap.pcap_path:
            artifacts.append(cap.pcap_path)
        ctx.log.event(
            kind="phase2.start", role=role, local_mac=local_mac.hex(),
        )
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            sm.tick()
            if sm.is_paired():
                break
            if sm.has_failed():
                break
            time.sleep(tick_period_s)
        # Drain any final inbound frames that arrived during shutdown.
        for _ in range(5):
            sm.tick()
            time.sleep(0.01)

    transport.close()

    metrics["final_state"] = sm.state
    metrics["run_id"] = sm.run_id.hex()
    metrics["trace_lines"] = len(traces)
    if sm.peer_mac:
        metrics["peer_mac"] = sm.peer_mac.hex()
    if callbacks:
        nmk, nid, peer = callbacks[0]
        metrics["nid"] = nid.hex()
        metrics["nmk_prefix"] = nmk[:4].hex()

    details = "\n".join(traces[-40:])

    if sm.state == SLAC_PAIRED:
        status = Status.PASS
        summary = (
            f"SLAC paired with {sm.peer_mac.hex() if sm.peer_mac else '?'}"
            f" (NID={nid.hex() if callbacks else '?'})"
        )
    elif sm.has_failed():
        status = Status.FAIL
        summary = f"SLAC watchdog fired (state={sm.state})"
    else:
        status = Status.FAIL
        summary = f"SLAC did not complete within {budget_s:.1f}s"

    return PhaseResult(
        name="phase2_slac",
        status=status,
        summary=summary,
        details=details,
        metrics=metrics,
        artifacts=artifacts,
    )


# --- Helpers ----------------------------------------------------------


def _resolve_local_mac(iface: str) -> bytes | None:
    """Cheap cross-platform MAC lookup."""
    # Linux: /sys/class/net/<iface>/address
    sys_path = Path(f"/sys/class/net/{iface}/address")
    if sys_path.exists():
        try:
            txt = sys_path.read_text().strip()
            parts = txt.split(":")
            if len(parts) == 6:
                return bytes(int(p, 16) for p in parts)
        except OSError:
            pass
    # Windows: ``iface`` may be either a friendly NIC name
    # (``"Ethernet 14"``) or a libpcap/Npcap device path
    # (``\Device\NPF_{GUID}``). Resolve via psutil + PowerShell's
    # InterfaceGuid property to translate NPF GUID -> friendly name -> MAC.
    if sys.platform == "win32":
        import subprocess
        # Extract GUID if the caller passed an NPF path.
        iface_lower = iface.lower()
        npf_guid = ""
        if "\\device\\npf_" in iface_lower:
            start = iface_lower.find("{")
            end = iface_lower.find("}", start)
            if start != -1 and end != -1:
                npf_guid = iface_lower[start:end + 1]

        # If we have an NPF GUID, translate it to the friendly NIC name
        # via Get-NetAdapter.
        friendly_name: Optional[str] = None
        if npf_guid:
            try:
                ps_cmd = (
                    f"(Get-NetAdapter | Where-Object "
                    f"{{ $_.InterfaceGuid -eq '{npf_guid.upper()}' }})."
                    f"Name"
                )
                out = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, text=True, timeout=5,
                )
                name = (out.stdout or "").strip()
                if name:
                    friendly_name = name
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            friendly_name = iface

        # psutil is the most robust MAC source (AF_LINK family).
        try:
            import psutil
            import socket
            af_link = getattr(psutil, "AF_LINK", -1)
            if friendly_name and friendly_name in psutil.net_if_addrs():
                for addr in psutil.net_if_addrs()[friendly_name]:
                    fam = getattr(addr, "family", None)
                    if fam == af_link and addr.address:
                        mac_txt = addr.address.replace("-", ":")
                        parts = mac_txt.split(":")
                        if len(parts) == 6:
                            try:
                                return bytes(int(p, 16) for p in parts)
                            except ValueError:
                                pass
        except ImportError:
            pass

        # Fallback: getmac /v (friendly name match only — `Transport Name`
        # field is "N/A" on many NICs, so we can't rely on NPF GUID there).
        try:
            out = subprocess.run(
                ["getmac.exe", "/fo", "csv", "/nh", "/v"],
                capture_output=True, text=True, timeout=5,
                encoding="ansi",
            )
            target_name = (friendly_name or iface).lower()
            for line in (out.stdout or "").splitlines():
                cols = [c.strip('"') for c in line.split(",")]
                if len(cols) >= 3 and target_name in cols[0].lower():
                    mac_txt = cols[2].replace("-", ":")
                    parts = mac_txt.split(":")
                    if len(parts) == 6:
                        try:
                            return bytes(int(p, 16) for p in parts)
                        except ValueError:
                            continue
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _parse_mac(text: str) -> bytes:
    parts = text.replace("-", ":").split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError(
            f"bad MAC format: {text!r} (want AA:BB:CC:DD:EE:FF)"
        )
    try:
        return bytes(int(p, 16) for p in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True)
    parser.add_argument("--role", choices=[ROLE_PEV, ROLE_EVSE],
                        required=True)
    parser.add_argument("--mac", type=_parse_mac, default=None,
                        help="Override the local MAC (default: auto-detect)")
    parser.add_argument("--budget", type=float, default=20.0,
                        help="Total seconds before declaring failure.")
    args = parser.parse_args(argv)
    print_banner(f"Phase 2 — SLAC pairing ({args.role})")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(
        ctx, "phase2_slac", phase2_slac,
        role=args.role, local_mac=args.mac, budget_s=args.budget,
    )
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
