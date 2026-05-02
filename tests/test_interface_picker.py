"""pytest-qt smoke tests for InterfacePickerCombo."""
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

from hotwire.gui.widgets.interface_picker import (                # noqa: E402
    InterfacePickerCombo,
    _PLACEHOLDER,
)


def test_widget_populates_with_at_least_one_interface(qtbot):
    """Any host worth running HotWire on has >= 1 NIC."""
    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    # The host has NICs (even if loopback); expect at least 1 entry.
    assert w.interface_count() >= 1


def test_current_interface_returns_string(qtbot):
    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    iface = w.current_interface()
    assert isinstance(iface, str)


def test_set_current_interface_selects_matching_item(qtbot):
    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    if w.interface_count() == 0:
        pytest.skip("no NICs on this host")
    # Pick whatever's in the second slot (if any) to switch.
    target = w._combo.itemData(                                   # noqa: SLF001
        w._combo.count() - 1                                      # noqa: SLF001
    )
    w.set_current_interface(target)
    assert w.current_interface() == target


def test_interface_changed_signal_fires(qtbot):
    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    if w._combo.count() < 2:                                      # noqa: SLF001
        pytest.skip("need >= 2 NICs to test combo change")
    with qtbot.waitSignal(w.interface_changed, timeout=500) as blocker:
        w._combo.setCurrentIndex(                                 # noqa: SLF001
            (w._combo.currentIndex() + 1)                         # noqa: SLF001
            % w._combo.count()                                    # noqa: SLF001
        )
    assert isinstance(blocker.args[0], str)


def test_empty_enumeration_shows_placeholder(qtbot, monkeypatch):
    # Mock the enumerator so it returns nothing.
    import hotwire.gui.widgets.interface_picker as mod
    monkeypatch.setattr(mod, "list_interfaces", lambda: [])

    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    assert w.interface_count() == 0
    assert w._combo.itemText(0) == _PLACEHOLDER                   # noqa: SLF001
    assert w.current_interface() == ""


def test_refresh_preserves_selection_when_possible(qtbot):
    w = InterfacePickerCombo()
    qtbot.addWidget(w)
    if w.interface_count() < 2:
        pytest.skip("need >= 2 NICs")
    target = w._combo.itemData(1)                                 # noqa: SLF001
    w.set_current_interface(target)
    assert w.current_interface() == target
    w.refresh()
    # After a refresh (same host), the selection should survive.
    assert w.current_interface() == target


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
