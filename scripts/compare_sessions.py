"""
Diff two HotWire session JSONL streams, message by message.

Typical use: compare a "baseline" session against an "attack" session and
show exactly which fields the attack mutated — perfect fodder for a paper
figure or a reviewer demo.

Two alignment strategies:

  * ``sequence`` (default) — pair messages by their position in the log.
    Good when both sessions went through the same handshake phases and
    you want to see per-field drift.
  * ``name`` — pair messages by the first ``msg_name`` match. Robust if
    one side has extra / retry messages.

Output formats:

  * ``text`` (default) — colour-coded side-by-side terminal diff.
  * ``markdown`` — GitHub-flavoured diff table.
  * ``json`` — machine-readable report.

Usage::

    python scripts/compare_sessions.py baseline.jsonl attack.jsonl
    python scripts/compare_sessions.py a.jsonl b.jsonl --format markdown > diff.md
    python scripts/compare_sessions.py a.jsonl b.jsonl --align name
    python scripts/compare_sessions.py a.jsonl b.jsonl --only-differences
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # skip corrupt lines silently (the redactor already warned)
    return records


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into ``{dot.path: value}``."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _align_by_sequence(a: list[dict], b: list[dict]) -> list[tuple[dict | None, dict | None]]:
    n = max(len(a), len(b))
    pairs: list[tuple[dict | None, dict | None]] = []
    for i in range(n):
        pairs.append((a[i] if i < len(a) else None, b[i] if i < len(b) else None))
    return pairs


def _align_by_name(a: list[dict], b: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Greedy: walk through ``a`` and try to find the next unmatched ``b`` with
    the same msg_name. Anything left over in either list becomes a lone pair.
    """
    pairs: list[tuple[dict | None, dict | None]] = []
    bi = 0
    for rec_a in a:
        name_a = rec_a.get("msg_name")
        # Find the next b-record with matching msg_name starting at bi.
        match_j = None
        for j in range(bi, len(b)):
            if b[j].get("msg_name") == name_a:
                match_j = j
                break
        if match_j is None:
            pairs.append((rec_a, None))
            continue
        # Everything in b between bi and match_j was skipped — emit as orphans.
        for j in range(bi, match_j):
            pairs.append((None, b[j]))
        pairs.append((rec_a, b[match_j]))
        bi = match_j + 1
    # Trailing unmatched b records.
    for j in range(bi, len(b)):
        pairs.append((None, b[j]))
    return pairs


def _diff_params(a: dict, b: dict) -> list[tuple[str, Any, Any]]:
    fa, fb = _flatten(a), _flatten(b)
    keys = sorted(set(fa) | set(fb))
    diffs: list[tuple[str, Any, Any]] = []
    for k in keys:
        va, vb = fa.get(k, "<missing>"), fb.get(k, "<missing>")
        if va != vb:
            diffs.append((k, va, vb))
    return diffs


def _format_text(
    pairs: list[tuple[dict | None, dict | None]],
    only_diffs: bool,
    colour: bool,
) -> str:
    def cc(s: str, code: str) -> str:
        return f"\033[{code}m{s}\033[0m" if colour else s

    out: list[str] = []
    for idx, (ra, rb) in enumerate(pairs, 1):
        name_a = ra.get("msg_name", "—") if ra else "∅"
        name_b = rb.get("msg_name", "—") if rb else "∅"
        dir_a = ra.get("direction", "") if ra else ""
        dir_b = rb.get("direction", "") if rb else ""

        header = f"[{idx:03d}] A:{dir_a:<2} {name_a:<30} | B:{dir_b:<2} {name_b}"
        if ra is None or rb is None:
            out.append(cc(header + "   [MISSING]", "33"))
            continue

        diffs = _diff_params(ra.get("params", {}), rb.get("params", {}))
        if only_diffs and not diffs and name_a == name_b:
            continue
        if name_a != name_b:
            out.append(cc(header + "   [TYPE MISMATCH]", "31"))
        elif not diffs:
            out.append(cc(header + "   (identical)", "32"))
        else:
            out.append(cc(header, "36"))
            for key, va, vb in diffs:
                out.append(f"     {key}: {cc(str(va), '31')} → {cc(str(vb), '32')}")
    return "\n".join(out)


def _format_markdown(pairs, only_diffs: bool) -> str:
    lines = ["| # | Dir A | Msg A | Dir B | Msg B | Differences |",
             "|---|-------|-------|-------|-------|-------------|"]
    for idx, (ra, rb) in enumerate(pairs, 1):
        name_a = ra.get("msg_name", "—") if ra else "∅"
        name_b = rb.get("msg_name", "—") if rb else "∅"
        dir_a = ra.get("direction", "") if ra else ""
        dir_b = rb.get("direction", "") if rb else ""
        diff_cell = ""
        if ra and rb:
            diffs = _diff_params(ra.get("params", {}), rb.get("params", {}))
            if only_diffs and not diffs and name_a == name_b:
                continue
            if diffs:
                diff_cell = "<br>".join(f"`{k}`: `{a}` → `{b}`" for k, a, b in diffs)
            elif name_a != name_b:
                diff_cell = "**type mismatch**"
        else:
            diff_cell = "**missing**"
        lines.append(
            f"| {idx} | {dir_a} | {name_a} | {dir_b} | {name_b} | {diff_cell} |"
        )
    return "\n".join(lines)


def _format_json(pairs) -> str:
    out = []
    for idx, (ra, rb) in enumerate(pairs, 1):
        entry = {
            "idx": idx,
            "a": None if ra is None else {
                "direction": ra.get("direction"),
                "msg_name": ra.get("msg_name"),
            },
            "b": None if rb is None else {
                "direction": rb.get("direction"),
                "msg_name": rb.get("msg_name"),
            },
            "diffs": [],
        }
        if ra and rb:
            for k, va, vb in _diff_params(ra.get("params", {}), rb.get("params", {})):
                entry["diffs"].append({"field": k, "a": va, "b": vb})
        out.append(entry)
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diff two HotWire session JSONL streams."
    )
    parser.add_argument("a", type=Path, help="First session JSONL (baseline).")
    parser.add_argument("b", type=Path, help="Second session JSONL (attack).")
    parser.add_argument(
        "--align", choices=("sequence", "name"), default="sequence",
        help="Alignment strategy (default: sequence).",
    )
    parser.add_argument(
        "--format", choices=("text", "markdown", "json"), default="text",
    )
    parser.add_argument(
        "--only-differences", action="store_true",
        help="Hide messages that are byte-identical between the two sessions.",
    )
    parser.add_argument(
        "--no-colour", action="store_true",
        help="Disable ANSI colour in text output.",
    )
    args = parser.parse_args()

    a_recs = _load(args.a)
    b_recs = _load(args.b)
    pairs = (_align_by_name if args.align == "name" else _align_by_sequence)(
        a_recs, b_recs
    )

    if args.format == "markdown":
        print(_format_markdown(pairs, args.only_differences))
    elif args.format == "json":
        print(_format_json(pairs))
    else:
        print(_format_text(pairs, args.only_differences, colour=not args.no_colour))
    return 0


if __name__ == "__main__":
    sys.exit(main())
