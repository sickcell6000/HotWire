"""Tests for scripts/redact_session.py — the JSONL privacy scrubber."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load the script as a module. It's not a package, so we use importlib.
_spec = importlib.util.spec_from_file_location(
    "redact_session", ROOT / "scripts" / "redact_session.py"
)
redact_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redact_session)


def test_redactor_replaces_evccid():
    r = redact_session._Redactor()
    out = r.redact_value("EVCCID", "d83add22f182")
    assert out.startswith("EVCCID_")
    assert "d83add22f182" not in out


def test_redactor_is_deterministic():
    r1 = redact_session._Redactor()
    r2 = redact_session._Redactor()
    a = r1.redact_value("EVCCID", "aabbccddeeff")
    b = r2.redact_value("EVCCID", "aabbccddeeff")
    assert a == b


def test_redactor_same_value_same_tag_within_session():
    r = redact_session._Redactor()
    a = r.redact_value("EVCCID", "abc123abc123")
    b = r.redact_value("EVCCID", "abc123abc123")
    assert a == b


def test_redactor_distinct_prefixes_for_distinct_keys():
    r = redact_session._Redactor()
    a = r.redact_value("EVCCID", "aabbccddeeff")
    b = r.redact_value("EVSEID", "aabbccddeeff")
    # Same underlying value, different fields — different tags.
    assert a != b
    assert a.startswith("EVCCID_") and b.startswith("EVSEID_")


def test_redactor_passes_through_non_sensitive():
    r = redact_session._Redactor()
    assert r.redact_value("ResponseCode", "OK") == "OK"
    assert r.redact_value("ResponseCode", 0) == 0
    assert r.redact_value("EVSEProcessing", "Finished") == "Finished"


def test_redactor_handles_nested_dicts():
    r = redact_session._Redactor()
    out = r.redact_value("params", {
        "EVCCID": "aabbccddeeff",
        "nested": {
            "header.SessionID": "0102030405060708",
            "ResponseCode": "OK",
        },
    })
    assert out["EVCCID"].startswith("EVCCID_")
    assert out["nested"]["header.SessionID"].startswith("SID_")
    assert out["nested"]["ResponseCode"] == "OK"


def test_redactor_detects_standalone_hex_ident():
    r = redact_session._Redactor()
    # A 12-hex string in a field that isn't named EVCCID should still be
    # redacted by the heuristic match.
    assert r.redact_value("arbitrary_field", "abcdef123456").startswith("HEX_")


def test_redactor_preserves_booleans_and_ints():
    r = redact_session._Redactor()
    assert r.redact_value("flag", True) is True
    assert r.redact_value("count", 42) == 42


def test_process_end_to_end(tmp_path):
    src = tmp_path / "input.jsonl"
    src.write_text(
        json.dumps({
            "timestamp": "2026-04-18T10:00:00",
            "direction": "rx",
            "msg_name": "SessionSetupReq",
            "mode": "EVSE",
            "params": {"EVCCID": "d83add22f182"},
        }) + "\n" +
        json.dumps({
            "timestamp": "2026-04-18T10:00:01",
            "direction": "tx",
            "msg_name": "SessionSetupRes",
            "mode": "EVSE",
            "params": {"EVSEID": "5a5a4445464c54", "ResponseCode": "OK"},
        }) + "\n",
        encoding="utf-8",
    )
    dst = tmp_path / "output.jsonl"
    r = redact_session._Redactor()
    n = redact_session._process(src, dst, r)
    assert n == 2
    lines = dst.read_text(encoding="utf-8").strip().splitlines()
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["params"]["EVCCID"].startswith("EVCCID_")
    assert "d83add22f182" not in lines[0]
    assert rec2["params"]["EVSEID"].startswith("EVSEID_")
    assert "5a5a4445464c54" not in lines[1]
    # ResponseCode should NOT be redacted — it's not sensitive.
    assert rec2["params"]["ResponseCode"] == "OK"
