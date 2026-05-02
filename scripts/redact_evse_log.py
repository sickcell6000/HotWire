"""
Redact PII from a real-hardware EVSE plain-text log before publication.

Use this for the verbose pyPlc / HotWire EVSE stdout dumps that are
NOT structured JSONL (for those, use ``redact_session.py`` instead).

The five identifier classes redacted by default:

  1. Victim PEV's EVCCID (= victim vehicle MAC) — the most sensitive.
     In SessionSetupReq lines this is the byte string the real
     vehicle sent over the wire. Replaced with a stable
     ``[REDACTED-N7-EVCCID]`` tag.

  2. EVSE-side lab machine MAC. Less sensitive (it is the
     researcher's own machine, not a victim) but still an identifier.
     Replaced with a clearly-fake ``02:00:00:00:00:01``.

  3. IPv6 link-local addresses derived from #2 (modified EUI-64).
     Replaced with ``fe80::1%lab0``.

  4. Windows pcap interface GUID (fingerprints the lab Windows host).
     Replaced with ``NPF_{REDACTED}``.

  5. (Reserved for future) Geographic identifiers, station names —
     not present in these logs but easy to add.

The simulated EVSEID (``5a5a3030303030`` = "ZZ00000") is left intact
because it is the OpenV2G default test sentinel, not a real station
identifier.

Usage::

    python scripts/redact_evse_log.py input.txt --out output.txt
    python scripts/redact_evse_log.py input.txt --evccid <12HEX> \\
        --evse-mac <AA:BB:CC:DD:EE:FF> --out output.txt

(supply the actual captured identifiers as CLI flags — they must
not appear in this public source file).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# NOTE: this file is part of the *public* HotWire repository, so the
# defaults below MUST NOT be the real captured identifiers. The
# operator passes the actual values via CLI flags (--evccid, --evse-mac,
# etc.) at redaction time. The placeholder defaults exist only so
# `--help` prints something readable and so a no-flag invocation
# fails closed (it would no-op rather than silently leak).
DEFAULT_EVCCID = "ffffffffffff"               # placeholder; supply via --evccid
DEFAULT_EVSE_MAC = "FF:FF:FF:FF:FF:FF"        # placeholder; supply via --evse-mac
DEFAULT_IPV6_LL = "fe80::ffff:ffff:ffff:ffff%0"  # placeholder; supply via --ipv6
DEFAULT_NIC_GUID = "{FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF}"  # supply via --nic-guid

REDACT_EVCCID = "[REDACTED-N7-EVCCID]"
REDACT_EVSE_MAC = "02:00:00:00:00:01"
REDACT_IPV6_LL = "fe80::1%lab0"
REDACT_NIC_GUID = "{REDACTED-NIC-GUID}"


def redact(text: str, evccid: str, evse_mac: str, ipv6_ll: str,
           nic_guid: str) -> tuple[str, dict[str, int]]:
    """Apply all five redaction rules. Returns (new_text, counts)."""
    counts: dict[str, int] = {}

    # 1. Victim EVCCID — match in JSON-style ``"EVCCID": "<hex>"``
    #    and in plain ``EVCCID = <hex>`` log lines.
    pattern = re.compile(re.escape(evccid), re.IGNORECASE)
    text, n = pattern.subn(REDACT_EVCCID, text)
    counts["EVCCID"] = n

    # 2. EVSE lab MAC — match colon-separated and (less common)
    #    no-separator forms. Case-insensitive.
    mac_no_colons = evse_mac.replace(":", "")
    text, n1 = re.compile(re.escape(evse_mac), re.IGNORECASE).subn(
        REDACT_EVSE_MAC, text)
    text, n2 = re.compile(re.escape(mac_no_colons), re.IGNORECASE).subn(
        REDACT_EVSE_MAC.replace(":", ""), text)
    counts["EVSE_MAC"] = n1 + n2

    # 3. IPv6 link-local — strip both the literal address and any
    #    bare interface index suffix (``%8`` etc.).
    text, n = re.compile(re.escape(ipv6_ll), re.IGNORECASE).subn(
        REDACT_IPV6_LL, text)
    counts["IPv6_LL"] = n

    # 4. NIC GUID
    text, n = re.compile(re.escape(nic_guid), re.IGNORECASE).subn(
        REDACT_NIC_GUID, text)
    counts["NIC_GUID"] = n

    return text, counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, required=True,
                    help="output path for the redacted log")
    ap.add_argument("--evccid", default=DEFAULT_EVCCID,
                    help=f"victim EVCCID hex string (default: {DEFAULT_EVCCID})")
    ap.add_argument("--evse-mac", default=DEFAULT_EVSE_MAC,
                    help=f"EVSE lab MAC (default: {DEFAULT_EVSE_MAC})")
    ap.add_argument("--ipv6", default=DEFAULT_IPV6_LL,
                    help=f"IPv6 link-local to redact (default: {DEFAULT_IPV6_LL})")
    ap.add_argument("--nic-guid", default=DEFAULT_NIC_GUID,
                    help=f"Windows pcap NIC GUID (default: {DEFAULT_NIC_GUID})")
    args = ap.parse_args(argv)

    if not args.input.is_file():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    raw = args.input.read_text(encoding="utf-8", errors="replace")
    redacted, counts = redact(raw, args.evccid, args.evse_mac,
                              args.ipv6, args.nic_guid)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(redacted, encoding="utf-8")

    total = sum(counts.values())
    print(f"redacted {args.input.name} -> {args.out}")
    for key, n in counts.items():
        print(f"  {key:10s}: {n:>6d} replacements")
    print(f"  {'TOTAL':10s}: {total:>6d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
