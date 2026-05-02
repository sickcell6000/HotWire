"""Tests for the refactored :mod:`hotwire.io.pcap_export` module.

Checkpoint 13 moved the JSONL → pcap logic out of
``scripts/export_pcap.py`` and into an importable module so the GUI
can call it without shelling out. These tests pin the public function
signature and the output file shape.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.core.config import load as load_config             # noqa: E402

load_config()

from hotwire.io.pcap_export import (                             # noqa: E402
    PCAP_LINKTYPE_RAW,
    PCAP_MAGIC,
    ExportResult,
    export_session_to_pcap,
)


FIXTURE = ROOT / "tests" / "fixtures" / "session_sample.jsonl"


def test_export_result_is_dataclass(tmp_path):
    out = tmp_path / "out.pcap"
    result = export_session_to_pcap(FIXTURE, out)
    assert isinstance(result, ExportResult)
    assert result.out_path == out
    assert result.packets_written > 0
    assert result.records_skipped == 0  # fixture was crafted to be valid


def test_export_creates_valid_pcap_header(tmp_path):
    out = tmp_path / "out.pcap"
    export_session_to_pcap(FIXTURE, out)
    data = out.read_bytes()
    # Classic pcap global header is 24 bytes.
    assert len(data) >= 24
    magic, v_major, v_minor, _tz, _sig, snaplen, linktype = struct.unpack(
        "!IHHiIII", data[:24]
    )
    assert magic == PCAP_MAGIC
    assert (v_major, v_minor) == (2, 4)
    assert snaplen == 65535
    assert linktype == PCAP_LINKTYPE_RAW


def test_export_writes_expected_packet_count(tmp_path):
    out = tmp_path / "out.pcap"
    result = export_session_to_pcap(FIXTURE, out)
    # The fixture has 5 events, all with _raw_exi_hex.
    assert result.packets_written == 5


def test_export_skips_records_without_raw_exi(tmp_path):
    jsonl = tmp_path / "partial.jsonl"
    jsonl.write_text(
        '{"timestamp": "2026-04-19T10:00:00", "direction": "tx", '
        '"msg_name": "Foo", "mode": "EVSE", "params": {}}\n'
        '{"timestamp": "2026-04-19T10:00:01", "direction": "tx", '
        '"msg_name": "Bar", "mode": "EVSE", '
        '"params": {"_raw_exi_hex": "deadbeef"}}\n',
        encoding="utf-8",
    )
    out = tmp_path / "out.pcap"
    result = export_session_to_pcap(jsonl, out)
    assert result.packets_written == 1
    assert result.records_skipped == 1


def test_export_honors_explicit_evse_port(tmp_path):
    """Explicit evse_port ends up in the synthesised TCP header."""
    out = tmp_path / "out.pcap"
    export_session_to_pcap(FIXTURE, out, evse_port=12345)
    data = out.read_bytes()
    # Skip global pcap header (24B) and first record header (16B).
    # First packet is 40B IPv6 + 20B TCP + V2GTP payload.
    pkt_start = 24 + 16
    ipv6_header = 40
    tcp_header_offset = pkt_start + ipv6_header
    src_port = struct.unpack("!H", data[tcp_header_offset:tcp_header_offset + 2])[0]
    dst_port = struct.unpack("!H", data[tcp_header_offset + 2:tcp_header_offset + 4])[0]
    # First record is rx on EVSE → source is PEV ephemeral, dest is EVSE port.
    assert dst_port == 12345
    assert src_port == 49152


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
