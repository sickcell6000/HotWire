"""
Bare-metal pcap smoke test for HotWire.

Purpose: isolate the pcap-open / send / receive layer from the SLAC
state machine. Run this on each side of a two-modem bench and we can
tell immediately whether the HomePlug AV carrier is propagating.

Modes:
  --mode tx       send N broadcast 0x88E1 frames then exit
  --mode rx       listen for DURATION seconds, print every 0x88E1 frame
                  we see (src mac, MMTYPE-like summary, length)
  --mode loopback open, send one frame to ff:ff:ff:ff:ff:ff, read back
                  anything for 2 seconds. Confirms the NIC is genuinely
                  in promisc mode and our own frames come back via the
                  modem's own echo path (or not — which is still useful
                  info).

All three modes use HotWire's own ``hotwire.plc.l2_transport.PcapL2Transport``
so we validate the exact code path phase2_slac.py uses.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))


def _build_probe_frame(src_mac: bytes, marker: bytes = b"HOTWIRE_PROBE") -> bytes:
    """Minimal well-formed 0x88E1 frame: broadcast dst + MMV + fake MMTYPE + marker."""
    dst = b"\xff\xff\xff\xff\xff\xff"
    ethertype = b"\x88\xe1"
    # MMV(1) + MMTYPE(2, vendor-ish 0xA0F8) + OUI(3) + payload
    hpav_header = bytes([0x00]) + b"\xA0\xF8" + b"\x00\xB0\x52"
    payload = marker + b"\x00" * max(0, 46 - len(hpav_header) - len(marker))
    return dst + src_mac + ethertype + hpav_header + payload


def _short_mmtype(pkt: bytes) -> str:
    if len(pkt) < 17:
        return "short"
    if pkt[12] != 0x88 or pkt[13] != 0xE1:
        return f"non-88E1 ethertype {pkt[12]:02x}{pkt[13]:02x}"
    # MMV at 14, MMTYPE at 15..16
    if len(pkt) >= 17:
        mmtype = int.from_bytes(pkt[15:17], "little")
        return f"mmtype=0x{mmtype:04x}"
    return "truncated"


def _src_mac(pkt: bytes) -> str:
    if len(pkt) < 12:
        return "?"
    return ":".join(f"{b:02x}" for b in pkt[6:12])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interface", "-i", required=True,
                   help="Ethernet interface name or NPF path")
    p.add_argument("--mode", choices=("tx", "rx", "loopback"), required=True)
    p.add_argument("--count", type=int, default=5,
                   help="(tx/loopback) number of frames to send")
    p.add_argument("--duration", type=float, default=10.0,
                   help="(rx) listen duration in seconds")
    p.add_argument("--mac", default=None,
                   help="source MAC to put in our frames (default: auto-detect)")
    args = p.parse_args()

    # Auto-detect MAC if not specified.
    if args.mac:
        src_mac = bytes.fromhex(args.mac.replace(":", "").replace("-", ""))
    else:
        # Try Linux /sys
        try:
            if sys.platform.startswith("linux"):
                mac_txt = Path(f"/sys/class/net/{args.interface}/address").read_text().strip()
                src_mac = bytes.fromhex(mac_txt.replace(":", ""))
            else:
                # Windows — ask psutil
                import psutil
                af_link = getattr(psutil, "AF_LINK", -1)
                # Try resolving NPF path -> friendly name
                friendly = args.interface
                if "NPF_" in args.interface.upper():
                    import subprocess
                    guid_start = args.interface.find("{")
                    guid_end = args.interface.find("}", guid_start)
                    if guid_start != -1 and guid_end != -1:
                        guid = args.interface[guid_start:guid_end + 1]
                        ps = subprocess.run(
                            ["powershell.exe", "-NoProfile", "-Command",
                             f"(Get-NetAdapter | Where-Object InterfaceGuid -eq '{guid.upper()}').Name"],
                            capture_output=True, text=True, timeout=5,
                        )
                        friendly = (ps.stdout or "").strip() or friendly
                src_mac = None
                for addr in psutil.net_if_addrs().get(friendly, []):
                    if getattr(addr, "family", None) == af_link and addr.address:
                        src_mac = bytes.fromhex(addr.address.replace("-", "").replace(":", ""))
                        break
                if src_mac is None:
                    print(f"[fatal] can't auto-detect MAC for {args.interface}; pass --mac")
                    return 2
        except Exception as e:
            print(f"[fatal] MAC detection failed: {e}")
            return 2

    print(f"[setup] interface = {args.interface!r}")
    print(f"[setup] src_mac   = {':'.join(f'{b:02x}' for b in src_mac)}")
    print(f"[setup] mode      = {args.mode}")

    # Open via HotWire's transport so we validate the exact code path.
    from hotwire.plc.l2_transport import PcapL2Transport
    try:
        t = PcapL2Transport(args.interface)
    except Exception as e:
        print(f"[fatal] PcapL2Transport open failed: {e}")
        return 3
    print(f"[ok] opened PcapL2Transport on {args.interface!r}")

    if args.mode == "tx":
        frame = _build_probe_frame(src_mac)
        print(f"[tx] sending {args.count} frames of {len(frame)} bytes each ...")
        for i in range(args.count):
            t.send(frame)
            print(f"  sent #{i + 1}")
            time.sleep(0.2)
        return 0

    if args.mode == "rx":
        print(f"[rx] listening for {args.duration:.1f}s on ether proto 0x88E1 ...")
        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < args.duration:
            frame = t.recv()
            if frame is None:
                time.sleep(0.01)
                continue
            n += 1
            print(f"  [{time.monotonic() - t0:6.2f}s] rx {len(frame)}B from {_src_mac(frame)} {_short_mmtype(frame)}")
        print(f"[rx] done — {n} frames in {args.duration:.1f}s")
        return 0 if n > 0 else 1

    if args.mode == "loopback":
        frame = _build_probe_frame(src_mac)
        print(f"[loopback] send {args.count} + listen 2s to see our own frames back")
        for i in range(args.count):
            t.send(frame)
            time.sleep(0.05)
        print("[loopback] sent, now listening...")
        t0 = time.monotonic()
        n = 0
        own = 0
        while time.monotonic() - t0 < 2.0:
            f = t.recv()
            if f is None:
                time.sleep(0.01)
                continue
            n += 1
            if b"HOTWIRE_PROBE" in f:
                own += 1
                print(f"  [{time.monotonic()-t0:5.2f}s] GOT OUR FRAME BACK: {_src_mac(f)}")
            else:
                print(f"  [{time.monotonic()-t0:5.2f}s] other frame {len(f)}B from {_src_mac(f)} {_short_mmtype(f)}")
        print(f"[loopback] total {n} frames seen, {own} were ours")
        return 0 if n > 0 else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
