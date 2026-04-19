"""Unit tests for StageNavPanel's public pause-state API.

Checkpoint 13 added ``set_pause_state(stage, enabled)`` so the main
window stops poking ``stage_nav._items[s]`` (private attribute). These
tests pin that contract.
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

from PyQt6.QtCore import Qt                                      # noqa: E402

from hotwire.core.config import load as load_config              # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE           # noqa: E402
from hotwire.gui.widgets.stage_nav import StageNavPanel          # noqa: E402


def test_set_pause_state_toggles_checkbox(qtbot):
    nav = StageNavPanel(C_EVSE_MODE)
    qtbot.addWidget(nav)
    # Pick a stage we know exists for EVSE.
    stage = "PreChargeRes"
    assert nav.has_stage(stage)

    # Collect pause_toggled emissions so we can assert state changed.
    received: list[tuple[str, bool]] = []
    nav.pause_toggled.connect(lambda s, e: received.append((s, e)))

    nav.set_pause_state(stage, True)
    nav.set_pause_state(stage, False)
    nav.set_pause_state(stage, True)

    # Each setCheckState fires itemChanged → pause_toggled — so we
    # should see three events in order.
    assert received == [(stage, True), (stage, False), (stage, True)]


def test_set_pause_state_ignores_unknown_stage(qtbot):
    """Unknown stages must be no-ops, not raise."""
    nav = StageNavPanel(C_PEV_MODE)
    qtbot.addWidget(nav)
    nav.set_pause_state("NotAStage", True)                       # must not raise


def test_set_pause_state_matches_check_widget_state(qtbot):
    """The underlying QTreeWidgetItem actually reflects the call."""
    nav = StageNavPanel(C_EVSE_MODE)
    qtbot.addWidget(nav)
    stage = "SessionSetupRes"
    nav.set_pause_state(stage, True)
    # Use has_stage + the widget's own API; do NOT reach into _items.
    # We access Qt's top-level children instead — deterministic order.
    found = False
    for i in range(nav.topLevelItemCount()):
        item = nav.topLevelItem(i)
        if item.text(0) == stage:
            assert item.checkState(1) == Qt.CheckState.Checked
            found = True
            break
    assert found, f"{stage} not found in top-level items"


if __name__ == "__main__":
    # Allow ``python tests/test_stage_nav_api.py`` to run via pytest so
    # the plain-Python runner in scripts/run_all_tests.py honours
    # pytest-qt's qtbot fixture. Exit with pytest's result code so
    # failures are visible.
    sys.exit(pytest.main([__file__, "-q"]))
