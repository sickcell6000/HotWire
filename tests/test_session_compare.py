"""Tests for scripts/compare_sessions.py — session diff tool."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "compare_sessions", ROOT / "scripts" / "compare_sessions.py"
)
compare_sessions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare_sessions)


def _rec(direction="tx", name="SessionSetupRes", params=None):
    return {
        "timestamp": "2026-04-18T10:00:00",
        "direction": direction,
        "msg_name": name,
        "mode": "EVSE",
        "params": params or {},
    }


def test_flatten_handles_nesting():
    out = compare_sessions._flatten({
        "a": 1,
        "b": {"c": 2, "d": {"e": 3}},
    })
    assert out == {"a": 1, "b.c": 2, "b.d.e": 3}


def test_align_by_sequence_matches_positions():
    a = [_rec(name="X"), _rec(name="Y")]
    b = [_rec(name="X"), _rec(name="Z")]
    pairs = compare_sessions._align_by_sequence(a, b)
    assert len(pairs) == 2
    assert pairs[0][0]["msg_name"] == "X"
    assert pairs[0][1]["msg_name"] == "X"
    assert pairs[1][0]["msg_name"] == "Y"
    assert pairs[1][1]["msg_name"] == "Z"


def test_align_by_sequence_handles_uneven_lengths():
    a = [_rec(name="X")]
    b = [_rec(name="X"), _rec(name="Y"), _rec(name="Z")]
    pairs = compare_sessions._align_by_sequence(a, b)
    assert len(pairs) == 3
    assert pairs[0] == (a[0], b[0])
    assert pairs[1][0] is None and pairs[1][1] is b[1]
    assert pairs[2][0] is None and pairs[2][1] is b[2]


def test_align_by_name_pairs_across_gaps():
    a = [_rec(name="A"), _rec(name="B"), _rec(name="C")]
    b = [_rec(name="X"), _rec(name="A"), _rec(name="B"), _rec(name="C")]
    pairs = compare_sessions._align_by_name(a, b)
    # B has one unmatched X prefix then three matching messages.
    matched_pairs = [(p[0]["msg_name"] if p[0] else None,
                      p[1]["msg_name"] if p[1] else None) for p in pairs]
    assert (None, "X") in matched_pairs
    assert ("A", "A") in matched_pairs
    assert ("B", "B") in matched_pairs
    assert ("C", "C") in matched_pairs


def test_diff_params_flags_changes():
    diffs = compare_sessions._diff_params(
        {"EVSEID": "ZZDEFLT", "ResponseCode": "OK"},
        {"EVSEID": "HACKED", "ResponseCode": "OK"},
    )
    assert len(diffs) == 1
    key, a, b = diffs[0]
    assert key == "EVSEID"
    assert a == "ZZDEFLT"
    assert b == "HACKED"


def test_diff_params_handles_missing_fields():
    diffs = compare_sessions._diff_params(
        {"a": 1, "b": 2},
        {"a": 1, "c": 3},
    )
    keys = {d[0] for d in diffs}
    assert keys == {"b", "c"}


def test_format_text_marks_identical_messages():
    a = [_rec(name="X", params={"k": 1})]
    b = [_rec(name="X", params={"k": 1})]
    pairs = compare_sessions._align_by_sequence(a, b)
    out = compare_sessions._format_text(pairs, only_diffs=False, colour=False)
    assert "identical" in out


def test_format_text_shows_diffs():
    a = [_rec(name="X", params={"EVSEPresentVoltage": 350})]
    b = [_rec(name="X", params={"EVSEPresentVoltage": 999})]
    pairs = compare_sessions._align_by_sequence(a, b)
    out = compare_sessions._format_text(pairs, only_diffs=False, colour=False)
    assert "350" in out
    assert "999" in out


def test_format_json_returns_parseable(tmp_path):
    a = [_rec(name="X", params={"a": 1})]
    b = [_rec(name="X", params={"a": 2})]
    pairs = compare_sessions._align_by_sequence(a, b)
    out = compare_sessions._format_json(pairs)
    parsed = json.loads(out)
    assert parsed[0]["a"]["msg_name"] == "X"
    assert parsed[0]["diffs"][0]["field"] == "a"
