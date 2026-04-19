"""Tests for hotwire.io.csv_export — pure-function JSONL → CSV."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

from hotwire.io.csv_export import (                              # noqa: E402
    CsvExportResult,
    export_session_to_csv,
    FIXED_COLUMNS,
)


FIXTURE = ROOT / "tests" / "fixtures" / "session_sample.jsonl"


def test_export_returns_dataclass_with_counts(tmp_path):
    out = tmp_path / "out.csv"
    result = export_session_to_csv(FIXTURE, out)
    assert isinstance(result, CsvExportResult)
    assert result.rows_written == 5
    assert result.out_path == out


def test_header_row_contains_fixed_columns(tmp_path):
    out = tmp_path / "out.csv"
    export_session_to_csv(FIXTURE, out)
    with out.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
    for col in FIXED_COLUMNS:
        assert col in header


def test_params_are_flattened_into_columns(tmp_path):
    out = tmp_path / "out.csv"
    export_session_to_csv(FIXTURE, out)
    with out.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    # At least one row has params.EVCCID set.
    assert any(r.get("params.EVCCID") == "d83add22f182" for r in rows)


def test_raw_exi_hex_dropped_by_default(tmp_path):
    out = tmp_path / "out.csv"
    export_session_to_csv(FIXTURE, out)
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "params._raw_exi_hex" not in header


def test_raw_exi_hex_kept_when_requested(tmp_path):
    out = tmp_path / "out.csv"
    export_session_to_csv(FIXTURE, out, drop_raw_hex=False)
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "params._raw_exi_hex" in header


def test_missing_params_field_leaves_empty_cell(tmp_path):
    jsonl = tmp_path / "sparse.jsonl"
    jsonl.write_text(
        '{"timestamp":"t1","direction":"tx","msg_name":"A","mode":"EVSE",'
        '"params":{"X":1}}\n'
        '{"timestamp":"t2","direction":"tx","msg_name":"A","mode":"EVSE",'
        '"params":{"Y":2}}\n',
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    result = export_session_to_csv(jsonl, out)
    assert result.rows_written == 2
    with out.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    # First row has X=1, Y=""; second has X="", Y=2.
    assert rows[0].get("params.X") == "1"
    assert rows[0].get("params.Y") == ""
    assert rows[1].get("params.X") == ""
    assert rows[1].get("params.Y") == "2"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
