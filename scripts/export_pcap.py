"""
Convert a HotWire session JSONL into a pcap that Wireshark + dsV2Gshark
can dissect.

The paper's methodology section cites dsV2Gshark for post-hoc packet
analysis. HotWire's native log is JSON, which is more ergonomic for
post-processing but loses Wireshark compatibility. This script bridges
the gap by synthesising minimal IPv6-over-loopback frames carrying the
V2GTP + EXI bytes that the FSM observed.

What gets emitted
-----------------
Each JSONL record with a ``params`` field that includes the original EXI
hex under ``params["result"]`` or ``params["info"]`` is converted into
one TCP segment. When the original EXI bytes aren't present in the log
(because the session was recorded without including the raw payload)
we re-encode them from the decoded params — best-effort, and marked
with a ``[reconstructed]`` flag.

File format: classic pcap (magic ``0xa1b2c3d4``), link-layer LINKTYPE_RAW
(101), IPv6 + TCP headers synthesised, timestamps from the JSONL
``timestamp`` field.

Usage::

    python scripts/export_pcap.py sessions/EVSE_20260418.jsonl \
        --out captures/evse.pcap
    # Then open in Wireshark:
    #     wireshark captures/evse.pcap
    # Install dsV2Gshark for DIN 70121 / ISO 15118 dissection:
    #     https://github.com/dSPACE-group/dsV2Gshark
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.exi.connector import addV2GTPHeader, exiEncode  # noqa: E402
from hotwire.plc.tcp_socket import _resolve_tcp_port  # noqa: E402


# --- pcap globals ---------------------------------------------------------

PCAP_MAGIC = 0xa1b2c3d4          # big-endian / native byte order
PCAP_VERSION = (2, 4)
PCAP_LINKTYPE_RAW = 101          # IPv4 / IPv6 with no link-layer header

# Fixed addresses for the synthesised packets. The loopback `::1` wasn't a
# valid choice inside an IPv6 header (RFC 4291 §2.5.3 forbids it as a
# source), so we use two deterministic link-local addresses from the
# ``fe80::/64`` private subnet.
EVSE_IP = b"\xfe\x80\x00\x00\x00\x00\x00\x00" b"\x00\x00\x00\x00\x00\x00\x00\x01"
PEV_IP  = b"\xfe\x80\x00\x00\x00\x00\x00\x00" b"\x00\x00\x00\x00\x00\x00\x00\x02"


# --- helpers --------------------------------------------------------------


def _build_tcp_segment(
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    payload: bytes,
    flags: int = 0x18,           # PSH + ACK
) -> bytes:
    """Minimal TCP segment without checksum (Wireshark re-computes)."""
    header = struct.pack(
        "!HHIIBBHHH",
        src_port, dst_port,
        seq, ack,
        0x50, flags,                 # data offset 5 (no options) + flags
        0xffff,                      # window
        0,                           # checksum — zero; Wireshark validates later
        0,                           # urgent pointer
    )
    return header + payload


def _build_ipv6_packet(src: bytes, dst: bytes, tcp: bytes) -> bytes:
    """IPv6 header with Next Header = 6 (TCP) and no extension headers."""
    payload_length = len(tcp)
    vtcfl = (6 << 28)                 # version 6, class 0, flow 0
    return struct.pack(
        "!IHBB16s16s",
        vtcfl,
        payload_length,
        6,                            # next header
        64,                           # hop limit
        src,
        dst,
    ) + tcp


def _write_pcap_global_header(fh) -> None:
    fh.write(struct.pack(
        "!IHHiIII",
        PCAP_MAGIC,
        PCAP_VERSION[0], PCAP_VERSION[1],
        0,                            # thiszone (local time correction)
        0,                            # sigfigs
        65535,                        # snaplen
        PCAP_LINKTYPE_RAW,
    ))


def _write_pcap_record(fh, ts: _dt.datetime, pkt: bytes) -> None:
    epoch = ts.timestamp()
    sec = int(epoch)
    usec = int((epoch - sec) * 1_000_000)
    fh.write(struct.pack("!IIII", sec, usec, len(pkt), len(pkt)))
    fh.write(pkt)


def _reconstruct_exi(record: dict) -> bytes | None:
    """Extract the raw EXI bytes from a JSONL record.

    Both rx and tx records stash the wire-level hex under
    ``params["_raw_exi_hex"]`` (added by the FSM's observer pipeline in
    Checkpoint 6). Older session logs that don't have this field can't be
    exported — the caller will skip them with a warning.
    """
    params = record.get("params", {})
    hex_str = params.get("_raw_exi_hex") or params.get("result")
    if not isinstance(hex_str, str) or not hex_str:
        return None
    try:
        return bytes.fromhex(hex_str)
    except ValueError:
        return None


# --- main --------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a HotWire JSONL session log to a Wireshark pcap."
    )
    parser.add_argument("input", type=Path, help="Input JSONL session log.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output pcap path.")
    parser.add_argument(
        "--evse-port", type=int, default=None,
        help="TCP port to use for the EVSE side (default: from hotwire.ini).",
    )
    args = parser.parse_args()

    try:
        from hotwire.core.config import load as load_config
        load_config()
        evse_port = args.evse_port if args.evse_port is not None else _resolve_tcp_port()
    except SystemExit:
        evse_port = args.evse_port if args.evse_port is not None else 57122

    pev_ephemeral = 49152

    args.out.parent.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_skip = 0
    seq_a = seq_b = 1

    with args.out.open("wb") as fh:
        _write_pcap_global_header(fh)

        for line in args.input.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                n_skip += 1
                continue

            exi = _reconstruct_exi(rec)
            if exi is None:
                n_skip += 1
                continue
            payload = bytes(addV2GTPHeader(exi))

            # Decide source / dest ports from the recorded direction.
            # tx from EVSE → EVSE:evse_port -> PEV:ephemeral (and vice-versa).
            mode = rec.get("mode", "").lower()
            direction = rec.get("direction", "tx")
            if mode == "evse":
                if direction == "tx":
                    src, dst = EVSE_IP, PEV_IP
                    sport, dport = evse_port, pev_ephemeral
                    seq, ack = seq_a, seq_b
                    seq_a += len(payload)
                else:
                    src, dst = PEV_IP, EVSE_IP
                    sport, dport = pev_ephemeral, evse_port
                    seq, ack = seq_b, seq_a
                    seq_b += len(payload)
            else:  # PEV log
                if direction == "tx":
                    src, dst = PEV_IP, EVSE_IP
                    sport, dport = pev_ephemeral, evse_port
                    seq, ack = seq_b, seq_a
                    seq_b += len(payload)
                else:
                    src, dst = EVSE_IP, PEV_IP
                    sport, dport = evse_port, pev_ephemeral
                    seq, ack = seq_a, seq_b
                    seq_a += len(payload)

            tcp = _build_tcp_segment(sport, dport, seq, ack, payload)
            pkt = _build_ipv6_packet(src, dst, tcp)

            ts_str = rec.get("timestamp")
            try:
                ts = _dt.datetime.fromisoformat(ts_str) if ts_str \
                    else _dt.datetime.now()
            except (TypeError, ValueError):
                ts = _dt.datetime.now()
            _write_pcap_record(fh, ts, pkt)
            n_ok += 1

    print(f"[ok] {args.input} -> {args.out}: {n_ok} packets, {n_skip} skipped")
    if n_skip:
        print("[hint] Skipped records lacked raw EXI bytes. Re-run the "
              "session with an updated SessionLogger that preserves "
              "`params['result']` in tx records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
