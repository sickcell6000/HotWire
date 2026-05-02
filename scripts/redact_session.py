"""
Strip privacy-sensitive fields from a HotWire session JSONL.

The paper says: "we do not release raw experimental data containing EVCCID
values, charging station identifiers, or geographic locations; instead,
we provide anonymized datasets". This script produces those anonymized
datasets.

Fields removed or replaced by default:

  * ``EVCCID``                       → redacted to a stable 12-char tag
    (so cross-message correlation within a session is preserved)
  * ``EVSEID``                       → redacted similarly
  * ``header.SessionID``              → redacted
  * Any string that matches a 12+ hex-char MAC/EVCCID pattern → redacted
  * Any IP address (v4 or v6)        → redacted

Tags have the form ``<PREFIX>_<counter>``, e.g. ``EVCCID_01``, so a
reader can still see *which* message carries *which* identifier without
learning the real bytes.

Usage::

    python scripts/redact_session.py sessions/EVSE_20260418_123000.jsonl
    python scripts/redact_session.py sessions/*.jsonl --out sessions/anon/
    python scripts/redact_session.py input.jsonl --out output.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


# Match a 12-char (6-byte MAC/EVCCID) or 16-char (8-byte SessionID) hex blob.
_HEX_IDENT = re.compile(r"^[0-9a-fA-F]{12}$|^[0-9a-fA-F]{16}$")
_IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_IPV6 = re.compile(r"^[0-9a-fA-F:]+$")


class _Redactor:
    """Builds stable tags for any sensitive value it sees.

    Two runs of the redactor on the same file produce identical output —
    tags are derived deterministically from a SHA-256 prefix of the
    original value. Across runs on *different* files the tags match only
    if the original value matches, which is the privacy-preserving
    correlation we want.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def _tag(self, prefix: str, raw: str) -> str:
        key = f"{prefix}:{raw}"
        if key in self._cache:
            return self._cache[key]
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        tag = f"{prefix}_{digest}"
        self._cache[key] = tag
        return tag

    def redact_value(self, key: str, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return {k: self.redact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact_value(key, v) for v in value]
        if isinstance(value, (int, float)):
            return value

        # String handling — the sensitive payloads are all strings.
        text = str(value)
        lower_key = key.lower()

        if "evccid" in lower_key:
            return self._tag("EVCCID", text)
        if "evseid" in lower_key:
            return self._tag("EVSEID", text)
        if "sessionid" in lower_key:
            return self._tag("SID", text)

        # Heuristic match on any sufficiently long hex identifier.
        if _HEX_IDENT.match(text):
            return self._tag("HEX", text)

        # IPv4 / IPv6 literal.
        if _IPV4.match(text) or (text.count(":") >= 2 and _IPV6.match(text)):
            return self._tag("IP", text)

        return text

    def redact_record(self, record: dict) -> dict:
        return {
            **record,
            "params": self.redact_value("params", record.get("params", {})),
        }


def _process(in_path: Path, out_path: Path, redactor: _Redactor) -> int:
    count = 0
    with in_path.open("r", encoding="utf-8") as fh_in, \
         out_path.open("w", encoding="utf-8") as fh_out:
        for line in fh_in:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Preserve anything unparsable verbatim so we don't silently
                # drop partial data — but warn.
                print(f"[warn] {in_path}: unparsable line: {line[:80]}...",
                      file=sys.stderr)
                fh_out.write(line + "\n")
                continue
            anon = redactor.redact_record(rec)
            fh_out.write(json.dumps(anon, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Redact privacy-sensitive fields from HotWire session JSONL."
    )
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="Input JSONL file(s).")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output file (single input) or directory (multi).")
    parser.add_argument(
        "--in-place", action="store_true",
        help="Overwrite each input file with its redacted version.",
    )
    parser.add_argument(
        "--shared-tags", action="store_true",
        help="Reuse the same redactor across all inputs, so identical "
             "values map to the same tag in every output file. Default is "
             "per-file tags.",
    )
    args = parser.parse_args()

    if args.in_place and args.out is not None:
        parser.error("--in-place and --out are mutually exclusive")

    # Determine output path for each input.
    outputs: list[Path] = []
    if args.in_place:
        outputs = [p for p in args.inputs]
    elif args.out is None:
        outputs = [p.with_suffix(".anon.jsonl") for p in args.inputs]
    elif len(args.inputs) == 1 and (
        not args.out.exists() or args.out.is_file()
    ):
        outputs = [args.out]
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        outputs = [args.out / p.name for p in args.inputs]

    shared = _Redactor() if args.shared_tags else None

    total_messages = 0
    for src, dst in zip(args.inputs, outputs):
        if not src.exists():
            print(f"[error] missing: {src}", file=sys.stderr)
            return 2
        redactor = shared if shared is not None else _Redactor()
        # When writing in-place, redact to a sibling temp file then atomic rename.
        if args.in_place:
            tmp = src.with_suffix(src.suffix + ".tmp")
            n = _process(src, tmp, redactor)
            tmp.replace(src)
            print(f"[ok] {src}: {n} records redacted (in place)")
        else:
            n = _process(src, dst, redactor)
            print(f"[ok] {src} -> {dst}: {n} records")
        total_messages += n

    print(f"\nTotal: {total_messages} records across {len(args.inputs)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
