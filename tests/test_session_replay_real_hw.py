"""
SessionReplayPanel + curated real-hardware trace integration test.

Confirms a reviewer can take any of the bundled
``datasets/real_hw_traces/`` ``session.jsonl`` files, drop it into
:class:`hotwire.gui.widgets.session_replay.SessionReplayPanel`, and
get a populated message timeline. This is the most common reviewer
flow when no hardware is available.

Run headless via ``QT_QPA_PLATFORM=offscreen``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from PyQt5.QtWidgets import QApplication                       # noqa: E402

from hotwire.gui.widgets.session_replay import SessionReplayPanel  # noqa: E402


_TRACE_ROOT = Path(__file__).resolve().parent.parent / "datasets" / "real_hw_traces"


_BUNDLED_SESSIONS = [
    _TRACE_ROOT / "phase4_clean_pass" / "pev" / "session.jsonl",
    _TRACE_ROOT / "phase4_clean_pass" / "evse" / "session.jsonl",
    _TRACE_ROOT / "phase4_a2_attack" / "pev" / "session.jsonl",
    _TRACE_ROOT / "phase5_pause_send" / "evse" / "session.jsonl",
    _TRACE_ROOT / "comprehensive_matrix" / "test1_a1_evccid" / "pev" / "session.jsonl",
    _TRACE_ROOT / "comprehensive_matrix" / "test10_stress_postfix" / "pev" / "session.jsonl",
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.mark.parametrize("session_path", _BUNDLED_SESSIONS)
def test_replay_loads_bundled_session(qapp, session_path: Path):
    if not session_path.exists():
        pytest.skip(f"bundle missing: {session_path}")

    panel = SessionReplayPanel()
    n = panel.load_session(session_path)
    assert n > 0, f"loaded {n} records from {session_path.name}"
    assert panel._listbox.count() == n  # noqa: SLF001 — test introspection
    panel.clear_session()
    assert panel._listbox.count() == 0  # noqa: SLF001


def test_replay_handles_malformed_jsonl(qapp, tmp_path):
    """Real-hardware logs sometimes get truncated mid-write. Replay must
    skip un-parseable lines instead of crashing."""
    p = tmp_path / "broken.jsonl"
    p.write_text(
        '{"kind": "trace", "msg": "ok"}\n'
        'not json at all\n'
        '{"kind": "phase4.start"}\n'
        '\n'
        '{"kind": "trun',  # truncated mid-line
        encoding="utf-8",
    )
    panel = SessionReplayPanel()
    n = panel.load_session(p)
    # The two well-formed lines survive; malformed and empty are skipped.
    assert n == 2


def test_replay_picks_up_phase4_sequence(qapp):
    """Walks through a known-good phase4 PASS trace and checks the
    9-stage V2G chain is represented in the loaded records."""
    p = _TRACE_ROOT / "phase4_clean_pass" / "pev" / "session.jsonl"
    if not p.exists():
        pytest.skip(f"missing {p}")

    panel = SessionReplayPanel()
    n = panel.load_session(p)
    assert n > 0
    records = panel._records  # noqa: SLF001
    stages_observed = {
        r.get("stage")
        for r in records
        if r.get("kind") == "phase4.message"
    }
    expected_stages = {
        "supportedAppProtocolReq", "SessionSetupReq",
        "ServiceDiscoveryReq", "ServicePaymentSelectionReq",
        "ContractAuthenticationReq", "ChargeParameterDiscoveryReq",
        "CableCheckReq", "PreChargeReq", "PowerDeliveryReq",
        "CurrentDemandReq",
    }
    missing = expected_stages - stages_observed
    assert not missing, (
        f"phase4 PASS trace should cover all 10 PEV-side stages, "
        f"missing: {sorted(missing)}; got: {sorted(stages_observed)}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
