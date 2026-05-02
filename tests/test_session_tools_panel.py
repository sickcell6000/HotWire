"""pytest-qt tests for SessionToolsPanel — redact / pcap / csv."""
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

from hotwire.gui.widgets.session_tools_panel import (            # noqa: E402
    SessionToolsPanel,
    _do_redact,
)


FIXTURE = ROOT / "tests" / "fixtures" / "session_sample.jsonl"


def test_redact_removes_evccid(tmp_path):
    out = tmp_path / "redacted.jsonl"
    n = _do_redact(FIXTURE, out)
    assert n == 5
    text = out.read_text(encoding="utf-8")
    # The fixture's EVCCID 'd83add22f182' must be gone.
    assert "d83add22f182" not in text


def test_pcap_export_via_panel(qtbot, tmp_path):
    panel = SessionToolsPanel()
    qtbot.addWidget(panel)
    panel._pcap_input.setText(str(FIXTURE))                       # noqa: SLF001
    panel._pcap_output.setText(str(tmp_path / "out.pcap"))        # noqa: SLF001
    with qtbot.waitSignal(panel.tool_finished, timeout=2000) as blocker:
        panel._on_pcap()                                          # noqa: SLF001
    tool, msg = blocker.args
    assert tool == "pcap"
    assert (tmp_path / "out.pcap").exists()


def test_csv_export_via_panel(qtbot, tmp_path):
    panel = SessionToolsPanel()
    qtbot.addWidget(panel)
    panel._csv_input.setText(str(FIXTURE))                        # noqa: SLF001
    panel._csv_output.setText(str(tmp_path / "out.csv"))          # noqa: SLF001
    with qtbot.waitSignal(panel.tool_finished, timeout=2000) as blocker:
        panel._on_csv()                                           # noqa: SLF001
    tool, _msg = blocker.args
    assert tool == "csv"
    assert (tmp_path / "out.csv").exists()


def test_redact_via_panel(qtbot, tmp_path, monkeypatch):
    panel = SessionToolsPanel()
    qtbot.addWidget(panel)
    panel._redact_input.setText(str(FIXTURE))                     # noqa: SLF001
    out = tmp_path / "redacted.jsonl"
    panel._redact_output.setText(str(out))                        # noqa: SLF001
    with qtbot.waitSignal(panel.tool_finished, timeout=2000) as blocker:
        panel._on_redact()                                        # noqa: SLF001
    tool, _msg = blocker.args
    assert tool == "redact"
    assert out.exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
