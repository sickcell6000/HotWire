"""
Phase 1 — Passive link check.

Sit on the PLC interface for a configurable window and count every
HomePlug AV frame (ethertype 0x88E1) the host sees. This is the
cheapest diagnostic for an intermittent hardware problem:

* lots of frames from a single MAC    → our local modem is alive
* lots of frames from two distinct MACs → both modems are on the same AVLN
* zero frames                          → cable / coupler / modem issue

The phase uses :class:`PacketCapture` to save the raw traffic and then
walks the resulting pcap offline with our pure-Python reader so the
verdict is deterministic regardless of which capture tool was used.

Skipped in simulation mode or when no interface is configured.
"""
from __future__ import annotations

import argparse
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

from hotwire.plc.homeplug_frames import (  # noqa: E402
    HomePlugFrame,
    MMTYPE_CM_SLAC_PARAM,
    MMTYPE_CM_START_ATTEN_CHAR,
    MMTYPE_CM_ATTEN_CHAR,
    MMTYPE_CM_MNBC_SOUND,
    MMTYPE_CM_SLAC_MATCH,
    MMTYPE_CM_SET_KEY,
)
from hotwire.plc.pcapng_reader import iter_packets  # noqa: E402


_MMTYPE_NAMES = {
    MMTYPE_CM_SLAC_PARAM: "CM_SLAC_PARAM",
    MMTYPE_CM_START_ATTEN_CHAR: "CM_START_ATTEN_CHAR",
    MMTYPE_CM_ATTEN_CHAR: "CM_ATTEN_CHAR",
    MMTYPE_CM_MNBC_SOUND: "CM_MNBC_SOUND",
    MMTYPE_CM_SLAC_MATCH: "CM_SLAC_MATCH",
    MMTYPE_CM_SET_KEY: "CM_SET_KEY",
}


def phase1_link(
    ctx: RunContext,
    duration_s: float = 10.0,
    min_frames: int = 1,
) -> PhaseResult:
    if not ctx.interface:
        return PhaseResult(
            name="phase1_link",
            status=Status.SKIP,
            summary="no interface configured (simulation-only run)",
        )

    bpf = "ether proto 0x88E1"
    artifacts: list[Path] = []

    with PacketCapture(
        ctx, phase="phase1", interface=ctx.interface, bpf=bpf,
    ) as cap:
        if not cap.available:
            return PhaseResult(
                name="phase1_link",
                status=Status.SKIP,
                summary="capture tool not available — cannot sniff",
                details="install tcpdump or dumpcap and re-run",
            )
        ctx.log.event(
            kind="phase1.sniff_start",
            interface=ctx.interface, bpf=bpf, duration_s=duration_s,
        )
        time.sleep(duration_s)
        ctx.log.event(kind="phase1.sniff_stop")

    if cap.pcap_path is None or not cap.pcap_path.exists():
        return PhaseResult(
            name="phase1_link",
            status=Status.FAIL,
            summary="no pcap file was produced by the capture subprocess",
        )

    artifacts.append(cap.pcap_path)
    counts, macs = _analyse_pcap(cap.pcap_path)
    total = sum(counts.values())

    metrics: dict[str, object] = {
        "pcap_path": str(cap.pcap_path),
        "duration_s": duration_s,
        "total_0x88E1_frames": total,
        "distinct_src_macs": len(macs),
    }
    for name, n in counts.items():
        metrics[f"count.{name}"] = n

    mac_list = ", ".join(sorted(macs)) if macs else "(none)"
    details = [
        f"observed {total} HomePlug AV frames in {duration_s:.1f}s",
        f"distinct src MACs: {mac_list}",
    ]
    for name, n in counts.items():
        details.append(f"  {name}: {n}")

    if total < min_frames:
        status = Status.FAIL
        summary = (
            f"only {total} 0x88E1 frames in {duration_s:.1f}s "
            f"(need >= {min_frames}); check modem power / cable / coupler"
        )
    elif len(macs) < 2:
        status = Status.PASS       # local modem visible but no peer yet
        summary = (
            f"{total} frames from {len(macs)} MAC — local modem alive, "
            "no peer modem seen (that's expected before SLAC)"
        )
    else:
        status = Status.PASS
        summary = (
            f"{total} frames from {len(macs)} MACs — link looks healthy"
        )

    ctx.log.event(
        kind="phase1.result", total=total, macs=sorted(macs),
        counts=counts,
    )
    return PhaseResult(
        name="phase1_link",
        status=status,
        summary=summary,
        details="\n".join(details),
        metrics=metrics,
        artifacts=artifacts,
    )


# --- pcap offline analysis --------------------------------------------


def _analyse_pcap(path: Path) -> tuple[dict[str, int], set[str]]:
    counts: dict[str, int] = {}
    macs: set[str] = set()
    for pkt in iter_packets(path):
        if len(pkt) < 17:
            continue
        if pkt[12] != 0x88 or pkt[13] != 0xE1:
            continue
        fr = HomePlugFrame.from_bytes(pkt)
        if fr is None:
            continue
        name = _MMTYPE_NAMES.get(fr.mmtype_base, f"0x{fr.mmtype_base:04X}")
        subs = {0: "REQ", 1: "CNF", 2: "IND", 3: "RSP"}
        label = f"{name}.{subs.get(fr.mmsub, '?')}"
        counts[label] = counts.get(label, 0) + 1
        macs.add(fr.src_mac.hex())
    return counts, macs


# --- CLI entrypoint ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", "-i", required=True,
                        help="Ethernet interface to sniff (e.g. eth0).")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Sniff window in seconds (default 10).")
    parser.add_argument("--min-frames", type=int, default=1,
                        help="Minimum 0x88E1 frames to consider PASS.")
    args = parser.parse_args(argv)
    print_banner("Phase 1 — passive HomePlug AV sniff")
    ctx = RunContext.create_standalone(interface=args.interface)
    result = run_phase(
        ctx, "phase1_link", phase1_link,
        duration_s=args.duration, min_frames=args.min_frames,
    )
    print_result(result)
    print(f"\nArtifacts: {ctx.run_dir}")
    ctx.close()
    return 0 if result.status in (Status.PASS, Status.SKIP) else 1


if __name__ == "__main__":
    sys.exit(main())
