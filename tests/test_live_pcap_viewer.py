"""pytest-qt tests for LivePcapViewer.

These tests don't spawn tcpdump — they call the pure
``update_from_counts`` API and assert the tables fill correctly.
"""
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

from hotwire.gui.widgets.live_pcap_viewer import LivePcapViewer   # noqa: E402


def test_initial_tables_are_empty(qtbot):
    v = LivePcapViewer()
    qtbot.addWidget(v)
    assert v._mmtype_table.rowCount() == 0                        # noqa: SLF001
    assert v._mac_table.rowCount() == 0                           # noqa: SLF001


def test_update_from_counts_populates_tables(qtbot):
    v = LivePcapViewer()
    qtbot.addWidget(v)
    with qtbot.waitSignal(v.update_applied, timeout=500) as blocker:
        v.update_from_counts(
            {"CM_SLAC_PARAM.REQ": 3, "CM_SLAC_MATCH.REQ": 2},
            {"aabbccddeeff": 4, "001122334455": 1},
        )
    total = blocker.args[0]
    assert total == 5
    assert v._mmtype_table.rowCount() == 2                        # noqa: SLF001
    assert v._mac_table.rowCount() == 2                           # noqa: SLF001


def test_clear_empties_tables(qtbot):
    v = LivePcapViewer()
    qtbot.addWidget(v)
    v.update_from_counts({"A": 1}, {"aa": 1})
    v.clear()
    assert v._mmtype_table.rowCount() == 0                        # noqa: SLF001
    assert v._mac_table.rowCount() == 0                           # noqa: SLF001


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
