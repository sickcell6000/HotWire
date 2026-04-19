"""
Reusable network-interface picker combo box.

Replaces every ``QLineEdit`` that used to prompt the operator for a NIC
name. Backed by :func:`hotwire.net.list_interfaces`, so every place
the picker is dropped in immediately gains:

* Best-first ranking (PLC-modem-like NICs at the top)
* Windows NPF-GUID pain handled — we show psutil names which pcap also
  accepts
* Tooltip per item with MAC / MTU / IPv4 / carrier / score reasons
* Optional Refresh button so hot-plugging a cable can be picked up
  without restarting HotWire

The widget emits ``interface_changed(str)`` whenever the selection
changes (including an initial emission after construction).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from ...net import NetInterface, list_interfaces


_PLACEHOLDER = "— No interfaces detected —"


class InterfacePickerCombo(QWidget):
    """QComboBox + optional Refresh button, pre-populated with NICs."""

    interface_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        show_refresh: bool = True,
        initial: Optional[str] = None,
    ) -> None:
        super().__init__(parent)

        self._combo = QComboBox()
        self._combo.setMinimumWidth(280)
        self._refresh_btn: Optional[QPushButton] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo, 1)

        if show_refresh:
            self._refresh_btn = QPushButton("↻")
            self._refresh_btn.setToolTip("Re-enumerate network interfaces")
            self._refresh_btn.setFixedWidth(32)
            self._refresh_btn.clicked.connect(self.refresh)
            layout.addWidget(self._refresh_btn)

        self._combo.currentIndexChanged.connect(self._on_changed)
        self.refresh(preferred=initial)

    # ---- public API ------------------------------------------------

    def current_interface(self) -> str:
        """Return the raw NIC name the operator picked, or ``""``."""
        data = self._combo.currentData()
        if isinstance(data, str):
            return data
        return ""

    def set_current_interface(self, name: str) -> None:
        """Programmatically select ``name`` if present."""
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == name:
                self._combo.setCurrentIndex(i)
                return

    def refresh(self, *, preferred: Optional[str] = None) -> None:
        """Re-enumerate and repopulate the combo.

        Preserves the current (or ``preferred``) selection if it's still
        present; otherwise falls back to the highest-scored NIC.
        """
        to_preserve = preferred if preferred is not None else self.current_interface()

        interfaces = list_interfaces()

        self._combo.blockSignals(True)
        self._combo.clear()

        if not interfaces:
            self._combo.addItem(_PLACEHOLDER, userData="")
            self._combo.model().item(0).setEnabled(False)
            self._combo.blockSignals(False)
            self.interface_changed.emit("")
            return

        for ni in interfaces:
            self._combo.addItem(ni.short_label(), userData=ni.name)
            self._combo.setItemData(
                self._combo.count() - 1, ni.tooltip(), role=3    # Qt.ToolTipRole
            )

        # Try to restore previous selection; else pick the best-scored.
        picked_index = 0
        if to_preserve:
            for i in range(self._combo.count()):
                if self._combo.itemData(i) == to_preserve:
                    picked_index = i
                    break
        self._combo.setCurrentIndex(picked_index)
        self._combo.blockSignals(False)

        # Now emit — we want exactly one signal for the new state.
        self.interface_changed.emit(self.current_interface())

    def interface_count(self) -> int:
        """Test helper — number of non-placeholder entries."""
        if self._combo.count() == 1 and self._combo.itemData(0) == "":
            return 0
        return self._combo.count()

    # ---- internals --------------------------------------------------

    def _on_changed(self, _index: int) -> None:
        self.interface_changed.emit(self.current_interface())
