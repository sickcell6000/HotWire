"""pytest-qt tests for SessionComparePanel + io/session_diff."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOTWIRE_CONFIG", str(ROOT / "config" / "hotwire.ini"))

pytest.importorskip("PyQt6")

from hotwire.core.config import load as load_config              # noqa: E402

load_config()

from hotwire.gui.widgets.session_compare_panel import SessionComparePanel  # noqa: E402
from hotwire.io.session_diff import (                            # noqa: E402
    build_diff, load_session,
)


FIXTURE = ROOT / "tests" / "fixtures" / "session_sample.jsonl"


def test_identical_sessions_compare_clean(qtbot):
    panel = SessionComparePanel()
    qtbot.addWidget(panel)
    a = load_session(FIXTURE)
    n = panel.compare(a, a)
    assert n == len(a)


def test_build_diff_sequence_strategy():
    a = load_session(FIXTURE)
    b = load_session(FIXTURE)
    pairs = build_diff(a, b, strategy="sequence")
    assert len(pairs) == len(a)
    for p in pairs:
        assert p.field_diffs == []


def test_build_diff_detects_one_field_change():
    a = load_session(FIXTURE)
    # Mutate one field in b.
    b = load_session(FIXTURE)
    b[0]["params"]["SchemaID_0"] = "999"

    pairs = build_diff(a, b, strategy="sequence")
    first = pairs[0]
    keys = [k for k, _va, _vb in first.field_diffs]
    assert "SchemaID_0" in keys


def test_build_diff_name_strategy_resyncs_after_reordering():
    """Name strategy is greedy: when B has the same messages in a
    different order, we expect correct A↔B pairing where both sides
    have matching names, and orphans for the ones that got reordered
    out of alignment. Contract: every pair that has both sides has
    matching msg_name."""
    a = load_session(FIXTURE)
    b = load_session(FIXTURE)
    b[-2], b[-1] = b[-1], b[-2]                       # flip last two

    pairs = build_diff(a, b, strategy="name")
    # Every (A, B) pair with both sides set must have matching msg_name.
    for p in pairs:
        if p.a is not None and p.b is not None:
            assert p.a["msg_name"] == p.b["msg_name"], (
                f"pair at index {p.index} mismatched: "
                f"{p.a['msg_name']} vs {p.b['msg_name']}"
            )


def test_panel_handles_mismatched_lengths(qtbot):
    panel = SessionComparePanel()
    qtbot.addWidget(panel)
    a = load_session(FIXTURE)
    b = a[:2]
    n = panel.compare(a, b)
    assert n == len(a)                                            # longest wins


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
