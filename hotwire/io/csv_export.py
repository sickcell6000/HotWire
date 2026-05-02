"""
Pure-function JSONL → CSV exporter.

HotWire's session logs live as JSONL (one decoded DIN 70121 message
per line; see :mod:`hotwire.core.session_log`). CSV is nicer for
spreadsheet import, pandas, or quick ad-hoc filtering. This module
flattens nested ``params`` dicts into dotted-path columns so the CSV
is both wide (all keys ever seen in the session) and ragged-friendly
(missing cells stay empty).

Usage::

    from hotwire.io.csv_export import export_session_to_csv
    r = export_session_to_csv(Path("sessions/evse.jsonl"),
                              Path("out.csv"))
    print(r.rows_written)
"""
from __future__ import annotations

import csv
import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Iterable

_log = logging.getLogger(__name__)


# Top-level JSONL keys we always emit (in this order) before the
# flattened params columns.
FIXED_COLUMNS: tuple[str, ...] = (
    "timestamp", "direction", "msg_name", "mode",
)


@dataclasses.dataclass(frozen=True)
class CsvExportResult:
    rows_written: int
    out_path: Path
    columns: tuple[str, ...]


def export_session_to_csv(
    jsonl_path: Path,
    out_path: Path,
    *,
    drop_raw_hex: bool = True,
) -> CsvExportResult:
    """Convert a HotWire JSONL session log into a CSV.

    Parameters
    ----------
    jsonl_path
        Input JSONL as written by :class:`hotwire.core.session_log.SessionLogger`.
    out_path
        Output CSV path. Parent directories are created.
    drop_raw_hex
        If True (default), omit the bulky ``_raw_exi_hex`` column. The
        full bytes live in the pcap export instead.
    """
    jsonl_path = Path(jsonl_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = list(_load_jsonl(jsonl_path))
    columns = _discover_columns(records, drop_raw_hex=drop_raw_hex)

    rows_written = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for rec in records:
            writer.writerow(_row_for(rec, columns))
            rows_written += 1

    return CsvExportResult(
        rows_written=rows_written,
        out_path=out_path,
        columns=tuple(columns),
    )


# --- internals --------------------------------------------------------


def _load_jsonl(path: Path) -> Iterable[dict]:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            _log.warning("skipping malformed JSONL line in %s", path)


def _discover_columns(
    records: list[dict], *, drop_raw_hex: bool,
) -> list[str]:
    """Two-pass: fixed columns first, then every params.* key ever seen."""
    param_keys: list[str] = []
    seen: set[str] = set()
    for rec in records:
        params = rec.get("params") or {}
        if not isinstance(params, dict):
            continue
        for k in _flatten(params):
            if drop_raw_hex and k == "_raw_exi_hex":
                continue
            if k not in seen:
                seen.add(k)
                param_keys.append(k)
    return list(FIXED_COLUMNS) + [f"params.{k}" for k in param_keys]


def _flatten(
    d: dict[str, Any], prefix: str = "",
) -> Iterable[str]:
    """Yield dotted-path keys for a possibly nested dict."""
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            yield from _flatten(v, key)
        else:
            yield key


def _row_for(rec: dict, columns: list[str]) -> list[str]:
    row: list[str] = []
    params = rec.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    for col in columns:
        if col in FIXED_COLUMNS:
            row.append(str(rec.get(col, "")))
        elif col.startswith("params."):
            path = col[len("params."):]
            row.append(str(_get_nested(params, path)))
        else:
            row.append("")
    return row


def _get_nested(d: dict, dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return cur
