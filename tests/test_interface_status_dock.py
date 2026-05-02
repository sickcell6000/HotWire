"""pytest-qt tests for NetworkInterfacesDock."""
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

from hotwire.gui.widgets.interface_status_dock import (          # noqa: E402
    NetworkInterfacesDock,
)


def test_table_populates_on_construct(qtbot):
    dock = NetworkInterfacesDock()
    qtbot.addWidget(dock)
    # Host has >= 1 NIC (if only loopback).
    assert dock._table.rowCount() >= 1                            # noqa: SLF001


def test_current_best_returns_top_scored(qtbot):
    dock = NetworkInterfacesDock()
    qtbot.addWidget(dock)
    dock.refresh()
    best = dock.current_best()
    # Either None (no NICs) or a string matching row 0.
    if best is not None:
        assert dock._table.item(0, 0).text() == best              # noqa: SLF001


def test_timer_is_active_then_stops_on_close(qtbot):
    dock = NetworkInterfacesDock()
    qtbot.addWidget(dock)
    assert dock._timer.isActive()                                 # noqa: SLF001
    dock.close()
    assert not dock._timer.isActive()                             # noqa: SLF001


def test_best_changed_signal_on_refresh(qtbot):
    dock = NetworkInterfacesDock()
    qtbot.addWidget(dock)
    # The signal fires once during the initial refresh() inside __init__.
    # Subsequent refresh() with unchanged best should NOT emit.
    # So we test that calling refresh() again doesn't emit when the top
    # NIC is unchanged.
    initial_best = dock.current_best()
    qtbot.wait(50)
    if initial_best is None:
        pytest.skip("no NICs to test against")
    with qtbot.assertNotEmitted(dock.best_changed, wait=50):
        dock.refresh()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
