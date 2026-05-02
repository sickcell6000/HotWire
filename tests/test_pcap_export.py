"""Tests for scripts/export_pcap.py — JSONL → Wireshark pcap."""
from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "export_pcap", ROOT / "scripts" / "export_pcap.py"
)
export_pcap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_pcap)


def test_reconstruct_exi_from_raw_hex():
    rec = {
        "params": {"_raw_exi_hex": "809a020040"},
    }
    got = export_pcap._reconstruct_exi(rec)
    assert got == bytes.fromhex("809a020040")


def test_reconstruct_exi_from_legacy_result():
    """Older logs stored raw hex under ``params['result']``; still supported."""
    rec = {"params": {"result": "deadbeef"}}
    got = export_pcap._reconstruct_exi(rec)
    assert got == bytes.fromhex("deadbeef")


def test_reconstruct_exi_returns_none_when_missing():
    assert export_pcap._reconstruct_exi({"params": {}}) is None
    assert export_pcap._reconstruct_exi({"params": {"EVCCID": "xx"}}) is None


def test_reconstruct_exi_returns_none_on_invalid_hex():
    assert export_pcap._reconstruct_exi({
        "params": {"_raw_exi_hex": "not hex at all"}
    }) is None


def test_build_ipv6_packet_shape():
    tcp = b"X" * 20
    pkt = export_pcap._build_ipv6_packet(
        export_pcap.EVSE_IP, export_pcap.PEV_IP, tcp
    )
    # IPv6 header is 40 bytes + TCP segment.
    assert len(pkt) == 40 + len(tcp)
    # Version field (top 4 bits) should be 6.
    first = struct.unpack("!I", pkt[:4])[0]
    assert (first >> 28) == 6


def test_build_tcp_segment_carries_payload():
    payload = b"hello"
    seg = export_pcap._build_tcp_segment(57122, 49152, 1, 0, payload)
    # TCP header is 20 bytes + payload.
    assert len(seg) == 20 + len(payload)
    assert seg[-5:] == payload


def test_end_to_end_pcap_has_records(tmp_path):
    jsonl = tmp_path / "session.jsonl"
    record = {
        "timestamp": "2026-04-18T10:00:00",
        "direction": "tx",
        "msg_name": "SessionSetupRes",
        "mode": "EVSE",
        "params": {"_raw_exi_hex": "809a020040"},
    }
    jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")

    pcap_out = tmp_path / "out.pcap"

    old_argv = sys.argv[:]
    sys.argv = ["export_pcap.py", str(jsonl), "--out", str(pcap_out)]
    try:
        export_pcap.main()
    finally:
        sys.argv = old_argv

    assert pcap_out.exists()
    data = pcap_out.read_bytes()
    # First 4 bytes are the pcap magic number.
    assert len(data) > 24
    magic = struct.unpack("!I", data[:4])[0]
    assert magic == export_pcap.PCAP_MAGIC
