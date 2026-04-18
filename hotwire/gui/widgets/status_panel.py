"""Live status readout: FSM state, EVCCID, SoC, voltages, currents."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


_STATE_COLORS = {
    "idle": "#607D8B",
    "running": "#4CAF50",
    "paused": "#FFC107",
    "stopped": "#F44336",
    "error": "#D32F2F",
}


class StatusPanel(QWidget):
    """Grid of key/value labels; updated by Signals.status_changed slot."""

    # Map FSM status selection -> (display label, None marker for primary state)
    _FIELDS: tuple[tuple[str, str], ...] = (
        ("evseState", "EVSE State"),
        ("pevState", "PEV State"),
        ("EVCCID", "EVCCID"),
        ("EVSEPresentVoltage", "Present Voltage (V)"),
        ("PowerSupplyUTarget", "Target Voltage (V)"),
        ("mode", "Mode"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)

        # Primary state (colored, bigger font).
        primary_box = QGroupBox("Session State")
        primary_layout = QFormLayout(primary_box)
        self._primary_label = QLabel("Idle")
        primary_font = QFont()
        primary_font.setPointSize(11)
        primary_font.setBold(True)
        self._primary_label.setFont(primary_font)
        self._primary_label.setStyleSheet(f"color: {_STATE_COLORS['idle']};")
        primary_layout.addRow("Status:", self._primary_label)
        root.addWidget(primary_box)

        # Detailed fields.
        detail_box = QGroupBox("Live Parameters")
        detail_layout = QFormLayout(detail_box)
        for key, label in self._FIELDS:
            lbl = QLabel("N/A")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._labels[key] = lbl
            detail_layout.addRow(label + ":", lbl)
        root.addWidget(detail_box)
        root.addStretch(1)

        self.setFrameStyle = QFrame.Shape.NoFrame

    # ---- slot -------------------------------------------------------

    def on_status(self, key: str, value: str) -> None:
        # Primary state tracks evseState or pevState directly.
        if key in ("evseState", "pevState"):
            self._primary_label.setText(value)
            color = self._color_for_state(value)
            self._primary_label.setStyleSheet(f"color: {color};")
        lbl = self._labels.get(key)
        if lbl is not None:
            lbl.setText(value)

    def _color_for_state(self, value: str) -> str:
        v = value.lower()
        if "error" in v or "timeout" in v or "fail" in v:
            return _STATE_COLORS["error"]
        if "stopped" in v:
            return _STATE_COLORS["stopped"]
        if "pause" in v:
            return _STATE_COLORS["paused"]
        if "listen" in v or "idle" in v or v == "n/a":
            return _STATE_COLORS["idle"]
        return _STATE_COLORS["running"]
