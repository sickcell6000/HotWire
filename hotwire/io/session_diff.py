"""
Pure-function session alignment + diffing (used by both CLI and GUI).

Extracted from ``scripts/compare_sessions.py`` at Checkpoint 14 so the
GUI's :class:`SessionComparePanel` can call it directly. No Qt, no CLI
dependencies — returns plain dataclasses the caller renders however
it wants.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Optional


@dataclasses.dataclass(frozen=True)
class DiffPair:
    """One aligned row from two session streams.

    ``index`` is 1-based so CLI / GUI can display it directly.
    ``a`` / ``b`` are the raw JSONL records or None if one side missed
    this row. ``field_diffs`` lists ``(dotted_key, value_a, value_b)``
    tuples for every params field that differs. Empty list = identical.
    """
    index: int
    a: Optional[dict]
    b: Optional[dict]
    field_diffs: list[tuple[str, Any, Any]]


def load_session(path: Path) -> list[dict]:
    """Read a JSONL session log, skipping malformed lines."""
    records: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def align_by_sequence(a: list[dict], b: list[dict]) -> list[tuple[Optional[dict], Optional[dict]]]:
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else None,
             b[i] if i < len(b) else None) for i in range(n)]


def align_by_name(a: list[dict], b: list[dict]) -> list[tuple[Optional[dict], Optional[dict]]]:
    pairs: list[tuple[Optional[dict], Optional[dict]]] = []
    bi = 0
    for rec_a in a:
        name_a = rec_a.get("msg_name")
        match_j = None
        for j in range(bi, len(b)):
            if b[j].get("msg_name") == name_a:
                match_j = j
                break
        if match_j is None:
            pairs.append((rec_a, None))
            continue
        for j in range(bi, match_j):
            pairs.append((None, b[j]))
        pairs.append((rec_a, b[match_j]))
        bi = match_j + 1
    for j in range(bi, len(b)):
        pairs.append((None, b[j]))
    return pairs


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def diff_params(a: dict, b: dict) -> list[tuple[str, Any, Any]]:
    fa = _flatten(a.get("params") or {})
    fb = _flatten(b.get("params") or {})
    keys = sorted(set(fa) | set(fb))
    out: list[tuple[str, Any, Any]] = []
    for k in keys:
        va, vb = fa.get(k, "<missing>"), fb.get(k, "<missing>")
        if va != vb:
            out.append((k, va, vb))
    return out


def build_diff(
    a_records: list[dict],
    b_records: list[dict],
    *,
    strategy: str = "sequence",
) -> list[DiffPair]:
    """Align two sessions and return per-row diff records.

    strategy: "sequence" (default, pair by position) or "name"
    (pair by next matching msg_name).
    """
    if strategy == "name":
        pairs = align_by_name(a_records, b_records)
    else:
        pairs = align_by_sequence(a_records, b_records)

    out: list[DiffPair] = []
    for i, (ra, rb) in enumerate(pairs, start=1):
        if ra is None or rb is None:
            out.append(DiffPair(index=i, a=ra, b=rb, field_diffs=[]))
            continue
        out.append(DiffPair(
            index=i, a=ra, b=rb, field_diffs=diff_params(ra, rb),
        ))
    return out
