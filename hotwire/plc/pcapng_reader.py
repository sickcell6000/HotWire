"""
Minimal pcap / pcapng reader — just enough to replay real HomePlug
captures into the SLAC state machine for offline validation.

Using the Python stdlib so there's no new dependency. We support:

* classic pcap (magic ``0xA1B2C3D4`` / ``0xD4C3B2A1``)
* pcapng (block-based; only the Enhanced Packet Block type is walked)

Both formats yield the same shape: an iterable of raw ethernet frames
(bytes) with any file-level headers stripped off. Callers then pass
each frame to :meth:`HomePlugFrame.from_bytes` just like pcap's live
``dispatch`` callback would.

This sits next to :class:`PcapL2Transport` but is completely
independent — one reads live, the other reads from disk.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator, Union


_PCAPNG_MAGIC = 0x1A2B3C4D
_PCAP_MAGIC_BE = 0xA1B2C3D4
_PCAP_MAGIC_LE = 0xD4C3B2A1

_BLOCK_TYPE_SECTION_HEADER = 0x0A0D0D0A
_BLOCK_TYPE_INTERFACE_DESCR = 0x00000001
_BLOCK_TYPE_ENHANCED_PACKET = 0x00000006
_BLOCK_TYPE_SIMPLE_PACKET = 0x00000003


def iter_packets(path: Union[str, Path]) -> Iterator[bytes]:
    """Yield raw ethernet frames from a ``.pcap`` or ``.pcapng`` file.

    Raises :class:`ValueError` if the file isn't a recognised capture
    format.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 8:
        raise ValueError(f"{path}: too short to be a pcap")

    # Detect file type by looking at the first 4 bytes.
    leading = struct.unpack("<I", data[:4])[0]

    if leading == _BLOCK_TYPE_SECTION_HEADER:
        yield from _iter_pcapng(data)
    elif leading in (_PCAP_MAGIC_BE, _PCAP_MAGIC_LE):
        yield from _iter_pcap(data, leading == _PCAP_MAGIC_LE)
    else:
        raise ValueError(
            f"{path}: unrecognised pcap magic 0x{leading:08x}"
        )


def _iter_pcap(data: bytes, little_endian: bool) -> Iterator[bytes]:
    # Global header is 24 bytes; records are [ts_sec, ts_usec, cap, orig] + data.
    endian = "<" if little_endian else ">"
    offset = 24
    while offset + 16 <= len(data):
        _ts_sec, _ts_us, cap_len, _orig_len = struct.unpack(
            f"{endian}IIII", data[offset:offset + 16]
        )
        offset += 16
        if cap_len == 0 or offset + cap_len > len(data):
            break
        yield bytes(data[offset:offset + cap_len])
        offset += cap_len


def _iter_pcapng(data: bytes) -> Iterator[bytes]:
    offset = 0
    while offset + 8 <= len(data):
        block_type, block_len = struct.unpack("<II", data[offset:offset + 8])
        if block_len == 0 or offset + block_len > len(data):
            break
        if block_type == _BLOCK_TYPE_ENHANCED_PACKET:
            # EPB: block_type(4) block_len(4) iface(4) ts_hi(4) ts_lo(4)
            #      cap_len(4) orig_len(4) data[cap_len, padded to 4]
            cap_len = struct.unpack(
                "<I", data[offset + 20:offset + 24]
            )[0]
            pkt_start = offset + 28
            yield bytes(data[pkt_start:pkt_start + cap_len])
        elif block_type == _BLOCK_TYPE_SIMPLE_PACKET:
            cap_len = struct.unpack("<I", data[offset + 8:offset + 12])[0]
            yield bytes(data[offset + 12:offset + 12 + cap_len])
        offset += block_len


def iter_homeplug_frames(
    path: Union[str, Path],
) -> Iterator[bytes]:
    """Convenience: filter ``iter_packets`` down to 0x88E1 ethertype."""
    for pkt in iter_packets(path):
        if len(pkt) >= 14 and pkt[12] == 0x88 and pkt[13] == 0xE1:
            yield pkt
