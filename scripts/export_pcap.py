"""
CLI wrapper around :func:`hotwire.io.pcap_export.export_session_to_pcap`.

The paper's methodology section cites dsV2Gshark for post-hoc packet
analysis. HotWire's native log is JSON, which is more ergonomic for
post-processing but loses Wireshark compatibility. This script bridges
the gap by synthesising minimal IPv6 + TCP frames that carry the
V2GTP + EXI bytes the FSM observed.

The actual conversion logic lives in ``hotwire/io/pcap_export.py`` so
it can be called from both the CLI (this file) and the GUI's
:class:`SessionReplayPanel`. Keep this script small — any new feature
belongs in the module.

Usage::

    python scripts/export_pcap.py sessions/EVSE_20260418.jsonl \\
        --out captures/evse.pcap
    # Then open in Wireshark:
    #     wireshark captures/evse.pcap
    # Install dsV2Gshark for DIN 70121 / ISO 15118 dissection:
    #     https://github.com/dSPACE-group/dsV2Gshark
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.io.pcap_export import export_session_to_pcap  # noqa: E402

# --- Backwards-compat re-exports (used by tests/test_pcap_export.py) ---
# The Checkpoint 13 refactor moved the implementation into
# ``hotwire.io.pcap_export``; keep the old symbol names here so the
# existing unit tests that poke the CLI module's internals stay green.
from hotwire.io.pcap_export import (  # noqa: E402,F401
    PCAP_MAGIC,
    _EVSE_IP as EVSE_IP,
    _PEV_IP as PEV_IP,
    _build_ipv6_packet,
    _build_tcp_segment,
    _reconstruct_exi,
)


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

    result = export_session_to_pcap(
        jsonl_path=args.input,
        out_path=args.out,
        evse_port=args.evse_port,
    )

    print(
        f"[ok] {args.input} -> {result.out_path}: "
        f"{result.packets_written} packets, "
        f"{result.records_skipped} skipped"
    )
    if result.records_skipped:
        print(
            "[hint] Skipped records lacked raw EXI bytes. Re-run the "
            "session with an updated SessionLogger that preserves "
            "`params['_raw_exi_hex']` or `params['result']` in tx records."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
