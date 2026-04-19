"""
CLI wrapper around :func:`hotwire.io.csv_export.export_session_to_csv`.

Usage::

    python scripts/export_csv.py sessions/EVSE_20260418.jsonl --out out.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.io.csv_export import export_session_to_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a HotWire JSONL session log to CSV."
    )
    parser.add_argument("input", type=Path, help="Input JSONL session log.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output CSV path.")
    parser.add_argument("--keep-raw-hex", action="store_true",
                        help="Include the bulky _raw_exi_hex column.")
    args = parser.parse_args()

    result = export_session_to_csv(
        jsonl_path=args.input,
        out_path=args.out,
        drop_raw_hex=not args.keep_raw_hex,
    )
    print(
        f"[ok] {args.input} -> {result.out_path}: "
        f"{result.rows_written} rows, {len(result.columns)} columns"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
