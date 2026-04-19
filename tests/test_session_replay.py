"""pytest-qt tests for SessionReplayPanel."""
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

from hotwire.gui.widgets.session_replay import SessionReplayPanel  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "session_sample.jsonl"


def test_load_session_populates_listbox(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    n = panel.load_session(FIXTURE)
    assert n == 5
    assert panel._listbox.count() == 5                           # noqa: SLF001


def test_load_session_enables_export_button(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    assert not panel._export_btn.isEnabled()                     # noqa: SLF001
    panel.load_session(FIXTURE)
    assert panel._export_btn.isEnabled()                         # noqa: SLF001


def test_selection_emits_event_selected(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    panel.load_session(FIXTURE)

    with qtbot.waitSignal(panel.event_selected, timeout=1000) as blocker:
        panel._listbox.setCurrentRow(0)                          # noqa: SLF001
    direction, msg_name, params = blocker.args
    assert direction == "rx"
    assert msg_name == "supportedAppProtocolReq"
    assert params.get("NameSpace_0") == "urn:din:70121:2012:MsgDef"


def test_selection_second_row_emits_different_event(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    panel.load_session(FIXTURE)

    panel._listbox.setCurrentRow(0)                              # noqa: SLF001
    with qtbot.waitSignal(panel.event_selected, timeout=1000) as blocker:
        panel._listbox.setCurrentRow(2)                          # noqa: SLF001
    direction, msg_name, params = blocker.args
    assert msg_name == "SessionSetupReq"
    assert params.get("EVCCID") == "d83add22f182"


def test_clear_session_empties_listbox(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    panel.load_session(FIXTURE)
    assert panel._listbox.count() > 0                            # noqa: SLF001
    panel.clear_session()
    assert panel._listbox.count() == 0                           # noqa: SLF001
    assert not panel._export_btn.isEnabled()                     # noqa: SLF001


def test_load_nonexistent_raises(qtbot):
    panel = SessionReplayPanel()
    qtbot.addWidget(panel)
    with pytest.raises(FileNotFoundError):
        panel.load_session(Path("/no/such/file.jsonl"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
