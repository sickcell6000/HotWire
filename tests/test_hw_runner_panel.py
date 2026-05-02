"""pytest-qt smoke tests for HwRunnerPanel."""
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

from hotwire.gui.widgets.hw_runner_panel import HwRunnerPanel     # noqa: E402


def test_panel_constructs(qtbot):
    p = HwRunnerPanel()
    qtbot.addWidget(p)
    assert p._phase_combo.count() >= 7                            # noqa: SLF001


def test_all_phases_listed(qtbot):
    p = HwRunnerPanel()
    qtbot.addWidget(p)
    labels = [p._phase_combo.itemText(i)                          # noqa: SLF001
              for i in range(p._phase_combo.count())]             # noqa: SLF001
    assert any("phase0_env" in l for l in labels)
    assert any("phase0_hw" in l for l in labels)
    assert any("phase1" in l for l in labels)
    assert any("phase4" in l for l in labels)
    assert any("run_all" in l for l in labels)


def test_buttons_initial_state(qtbot):
    p = HwRunnerPanel()
    qtbot.addWidget(p)
    assert p._run_btn.isEnabled()                                 # noqa: SLF001
    assert not p._stop_btn.isEnabled()                            # noqa: SLF001


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
