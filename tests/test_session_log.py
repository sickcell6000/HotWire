"""Tests for the JSONL session logger."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hotwire.core.session_log import SessionLogger, TeeObserver  # noqa: E402


def test_session_logger_writes_jsonl(tmp_path):
    path = tmp_path / "test.jsonl"
    logger = SessionLogger(path, mode="EVSE")
    logger.on_message("rx", "SessionSetupReq", {"EVCCID": "d83add22f182"})
    logger.on_message("tx", "SessionSetupRes", {"ResponseCode": "OK", "EVSEID": "5a5a4445464c54"})
    logger.close()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    rec1 = json.loads(lines[0])
    assert rec1["direction"] == "rx"
    assert rec1["msg_name"] == "SessionSetupReq"
    assert rec1["mode"] == "EVSE"
    assert rec1["params"] == {"EVCCID": "d83add22f182"}
    assert "timestamp" in rec1

    rec2 = json.loads(lines[1])
    assert rec2["direction"] == "tx"
    assert rec2["msg_name"] == "SessionSetupRes"


def test_session_logger_appends_on_reopen(tmp_path):
    path = tmp_path / "test.jsonl"
    SessionLogger(path, mode="PEV").on_message("tx", "A", {"x": 1})
    SessionLogger(path, mode="PEV").on_message("tx", "B", {"x": 2})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    msgs = [json.loads(l)["msg_name"] for l in lines]
    assert msgs == ["A", "B"]


def test_session_logger_survives_non_serializable_value(tmp_path):
    """Non-JSON values (e.g. bytes) should not crash the logger — the
    ``default=str`` fallback in SessionLogger converts them to strings."""
    path = tmp_path / "test.jsonl"
    logger = SessionLogger(path, mode="EVSE")
    logger.on_message("rx", "Mystery", {"blob": b"\x00\x01\x02"})
    logger.close()
    line = path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    # The bytes should have been stringified (not silently dropped).
    assert "blob" in parsed["params"]


def test_session_logger_context_manager(tmp_path):
    path = tmp_path / "test.jsonl"
    with SessionLogger(path, mode="EVSE") as logger:
        logger.on_message("rx", "X", {})
    # File should be closed after context exit; calling close() again is
    # a no-op.
    assert path.exists()


def test_tee_observer_fans_out():
    """TeeObserver should deliver each message to every wrapped observer."""
    events_a: list[tuple] = []
    events_b: list[tuple] = []

    class _Capture:
        def __init__(self, out): self._out = out
        def on_message(self, d, n, p): self._out.append((d, n, p))

    tee = TeeObserver(_Capture(events_a), _Capture(events_b))
    tee.on_message("rx", "Foo", {"x": 1})
    tee.on_message("tx", "Bar", {"y": 2})

    assert events_a == [("rx", "Foo", {"x": 1}), ("tx", "Bar", {"y": 2})]
    assert events_b == events_a


def test_tee_observer_survives_one_observer_raising():
    """A broken observer must not break the others."""
    events: list[tuple] = []

    class _Broken:
        def on_message(self, d, n, p):
            raise RuntimeError("boom")

    class _Good:
        def on_message(self, d, n, p):
            events.append((d, n, p))

    tee = TeeObserver(_Broken(), _Good())
    tee.on_message("rx", "X", {})
    assert events == [("rx", "X", {})]


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            import inspect
            sig = inspect.signature(t)
            if "tmp_path" in sig.parameters:
                with tempfile.TemporaryDirectory() as d:
                    t(Path(d))
            else:
                t()
            print(f"[PASS] {t.__name__}")
        except Exception:                                       # noqa: BLE001
            fails += 1
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    sys.exit(0 if fails == 0 else 1)
