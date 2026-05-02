"""
Pure-function JSONL → pcap exporter.

Extracted from ``scripts/export_pcap.py`` at Checkpoint 13 so the CLI
and the GUI's :class:`SessionReplayPanel` can share the same byte-level
logic without the Qt layer shelling out to a subprocess.

The output pcap is **classic pcap** (magic ``0xa1b2c3d4``,
LINKTYPE_RAW = 101), containing synthesised IPv6 + TCP headers that
carry the original V2GTP + EXI bytes as payload. Wireshark with the
``dsV2Gshark`` plugin dissects these cleanly.

Call site::

    from hotwire.io import export_session_to_pcap
    n = export_session_to_pcap(Path("sessions/EVSE_20260418.jsonl"),
                               Path("captures/evse.pcap"))
    print(f"wrote {n} packets")

See :func:`export_session_to_pcap` for the full signature.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..exi.connector import addV2GTPHeader

_log = logging.getLogger(__name__)


# --- pcap constants ---------------------------------------------------

PCAP_MAGIC = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
PCAP_LINKTYPE_RAW = 101

# Two fixed link-local addresses for the synthesised IPv6 packets.
# RFC 4291 §2.5.3 forbids ::1 as a source address in an actual packet,
# so we use fe80::1 / fe80::2 instead.
_EVSE_IP = (
    b"\xfe\x80\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x01"
)
_PEV_IP = (
    b"\xfe\x80\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x02"
)
_PEV_EPHEMERAL_PORT = 49152
_DEFAULT_EVSE_PORT = 57122


@dataclass(frozen=True)
class ExportResult:
    """Result of :func:`export_session_to_pcap` — number of packets
    written, number skipped (no raw EXI), and the output path."""
    packets_written: int
    records_skipped: int
    out_path: Path


# --- public API -------------------------------------------------------


def export_session_to_pcap(
    jsonl_path: Path,
    out_path: Path,
    evse_port: Optional[int] = None,
) -> ExportResult:
    """Convert a HotWire JSONL session log into a Wireshark-openable pcap.

    Parameters
    ----------
    jsonl_path
        Input JSONL written by :class:`hotwire.core.session_log.SessionLogger`.
    out_path
        Output pcap path. Parent directories are created.
    evse_port
        TCP port used for the EVSE side of synthesised segments. If
        omitted, :func:`hotwire.plc.tcp_socket._resolve_tcp_port` is
        consulted; if that fails, ``57122`` is used as a final fallback
        (matches the default in ``config/hotwire.ini``).

    Returns
    -------
    ExportResult
        ``packets_written`` counts successful conversions;
        ``records_skipped`` counts lines that were malformed JSON or
        lacked the ``_raw_exi_hex`` / ``result`` field needed to
        reconstruct the EXI payload.
    """
    jsonl_path = Path(jsonl_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_evse_port = evse_port if evse_port is not None else _pick_evse_port()

    seq_a = seq_b = 1
    n_ok = 0
    n_skip = 0

    with out_path.open("wb") as fh:
        _write_pcap_global_header(fh)

        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
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

            src, dst, sport, dport, seq, ack, seq_a, seq_b = _endpoints(
                rec, resolved_evse_port, seq_a, seq_b, len(payload),
            )
            tcp = _build_tcp_segment(sport, dport, seq, ack, payload)
            pkt = _build_ipv6_packet(src, dst, tcp)

            ts = _extract_timestamp(rec)
            _write_pcap_record(fh, ts, pkt)
            n_ok += 1

    return ExportResult(
        packets_written=n_ok,
        records_skipped=n_skip,
        out_path=out_path,
    )


# --- internals --------------------------------------------------------


def _pick_evse_port() -> int:
    """Resolve the configured EVSE TCP port. Never raises."""
    try:
        from ..core.config import load as load_config
        from ..plc.tcp_socket import _resolve_tcp_port
        load_config()
        return _resolve_tcp_port()
    except Exception:                                            # noqa: BLE001
        # Config missing / env unloaded — fall back to the documented default.
        return _DEFAULT_EVSE_PORT


def _endpoints(
    rec: dict,
    evse_port: int,
    seq_a: int,
    seq_b: int,
    payload_len: int,
) -> tuple[bytes, bytes, int, int, int, int, int, int]:
    """Pick src/dst IP + ports + seq numbers for a single record.

    Returns (src, dst, sport, dport, seq, ack, new_seq_a, new_seq_b).
    Sequence counters are updated on the "sender" side so subsequent
    packets in the same direction continue the byte stream.
    """
    mode = str(rec.get("mode", "")).lower()
    direction = str(rec.get("direction", "tx")).lower()

    # "tx from evse" and "rx on pev" mean the same wire direction.
    evse_sends = (mode == "evse" and direction == "tx") or \
                 (mode == "pev" and direction == "rx")

    if evse_sends:
        src, dst = _EVSE_IP, _PEV_IP
        sport, dport = evse_port, _PEV_EPHEMERAL_PORT
        seq, ack = seq_a, seq_b
        seq_a += payload_len
    else:
        src, dst = _PEV_IP, _EVSE_IP
        sport, dport = _PEV_EPHEMERAL_PORT, evse_port
        seq, ack = seq_b, seq_a
        seq_b += payload_len

    return src, dst, sport, dport, seq, ack, seq_a, seq_b


def _reconstruct_exi(rec: dict) -> Optional[bytes]:
    """Pull the raw EXI bytes out of a JSONL record.

    HotWire's FSM observer stashes the wire-level hex under
    ``params["_raw_exi_hex"]`` (Checkpoint 6 onwards). Older logs may
    have it under ``params["result"]`` — we accept both. Anything else
    → ``None`` and the caller skips the record.
    """
    params = rec.get("params", {}) or {}
    hex_str = params.get("_raw_exi_hex") or params.get("result")
    if not isinstance(hex_str, str) or not hex_str:
        return None
    try:
        return bytes.fromhex(hex_str)
    except ValueError:
        return None


def _extract_timestamp(rec: dict) -> _dt.datetime:
    ts_str = rec.get("timestamp")
    if isinstance(ts_str, str) and ts_str:
        try:
            return _dt.datetime.fromisoformat(ts_str)
        except ValueError:
            pass
    return _dt.datetime.now()


def _build_tcp_segment(
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    payload: bytes,
    flags: int = 0x18,       # PSH + ACK
) -> bytes:
    header = struct.pack(
        "!HHIIBBHHH",
        src_port, dst_port,
        seq, ack,
        0x50, flags,         # data offset 5 (no options) + flags
        0xFFFF,              # window
        0,                   # checksum — zero (Wireshark recomputes)
        0,                   # urgent pointer
    )
    return header + payload


def _build_ipv6_packet(src: bytes, dst: bytes, tcp: bytes) -> bytes:
    vtcfl = 6 << 28          # version 6, class 0, flow 0
    return struct.pack(
        "!IHBB16s16s",
        vtcfl,
        len(tcp),
        6,                   # next header = TCP
        64,                  # hop limit
        src,
        dst,
    ) + tcp


def _write_pcap_global_header(fh) -> None:
    fh.write(struct.pack(
        "!IHHiIII",
        PCAP_MAGIC,
        PCAP_VERSION_MAJOR, PCAP_VERSION_MINOR,
        0,                   # thiszone
        0,                   # sigfigs
        65535,               # snaplen
        PCAP_LINKTYPE_RAW,
    ))


def _write_pcap_record(fh, ts: _dt.datetime, pkt: bytes) -> None:
    epoch = ts.timestamp()
    sec = int(epoch)
    usec = int((epoch - sec) * 1_000_000)
    fh.write(struct.pack("!IIII", sec, usec, len(pkt), len(pkt)))
    fh.write(pkt)
