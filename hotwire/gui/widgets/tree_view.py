"""
Req / Res tree view — shows every decoded DIN 70121 message that passed
through the FSM, split into two columns:

    +---------------------+---------------------+
    |     Received (rx)   |    Sent (tx)        |
    |  SessionSetupReq ▸  |  SessionSetupRes ▸  |
    |    header.Session…  |    ResponseCode: 1  |
    |    EVCCID: ab:cd:…  |    EVSEID: ZZDEFLT  |
    +---------------------+---------------------+

Updates on the ``msg_decoded`` queued signal emitted by the FSM observer.
Each message is appended as a new top-level item; expanded-by-default so
users see the payload immediately.

The newest message is auto-scrolled into view. Each side caps at 200
messages to keep memory bounded during long sessions.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_MAX_MESSAGES_PER_SIDE = 200

_RX_HEADING_COLOR = QColor("#1565C0")   # blue for received
_TX_HEADING_COLOR = QColor("#2E7D32")   # green for transmitted
_SKIP_KEYS = {"info", "result", "error", "debug", "schema", "g_errn"}


class _MessageTree(QTreeWidget):
    """One-sided tree: either received (rx) or transmitted (tx)."""

    def __init__(self, title: str, heading_color: QColor, parent=None) -> None:
        super().__init__(parent)
        self._heading_color = heading_color
        self.setHeaderLabels([title, "Value"])
        self.setColumnWidth(0, 240)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setAnimated(False)  # avoid repaint churn under high message rate

    def append_message(self, msg_name: str, params: dict[str, Any]) -> None:
        """Insert one message as an expanded top-level item with key/value children."""
        # Bound the tree so sessions with thousands of CurrentDemandRes don't
        # blow memory / slow the UI.
        while self.topLevelItemCount() >= _MAX_MESSAGES_PER_SIDE:
            self.takeTopLevelItem(0)

        # Row count + message name at column 0; brief summary at column 1.
        root = QTreeWidgetItem(self)
        idx = self.topLevelItemCount()
        root.setText(0, f"#{idx}  {msg_name}")
        root.setForeground(0, QBrush(self._heading_color))
        root.setText(1, self._summary_for(params))

        for key, value in params.items():
            if key in _SKIP_KEYS:
                continue
            child = QTreeWidgetItem(root)
            child.setText(0, key)
            child.setText(1, str(value))

        root.setExpanded(True)
        self.scrollToItem(root)

    def clear_messages(self) -> None:
        self.clear()

    @staticmethod
    def _summary_for(params: dict[str, Any]) -> str:
        """Pick one or two headline fields for the one-line summary."""
        if not params:
            return ""
        # Prefer a ResponseCode if present; otherwise first interesting field.
        for key in ("ResponseCode", "EVSEProcessing", "EVCCID", "EVSEID",
                    "SchemaID", "EVSEPresentVoltage.Value", "EVTargetVoltage",
                    "ReadyToChargeState"):
            if key in params:
                return f"{key}={params[key]}"
        # Fall back to the first non-boilerplate key.
        for key, value in params.items():
            if key not in _SKIP_KEYS and not key.startswith("header."):
                return f"{key}={value}"
        return ""


class ReqResTreeView(QWidget):
    """Side-by-side received/transmitted message trees.

    Usage::

        view = ReqResTreeView()
        signals.msg_decoded.connect(view.on_message)
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._rx = _MessageTree("Received (from peer)", _RX_HEADING_COLOR)
        self._tx = _MessageTree("Sent (to peer)", _TX_HEADING_COLOR)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.addWidget(QLabel("<b>Protocol message history</b>"))

        row = QHBoxLayout()
        row.addWidget(self._rx)
        row.addWidget(self._tx)
        layout.addLayout(row, 1)

    # ---- slot -------------------------------------------------------

    def on_message(self, direction: str, msg_name: str, params: dict) -> None:
        if direction == "rx":
            self._rx.append_message(msg_name, params)
        elif direction == "tx":
            self._tx.append_message(msg_name, params)

    def clear(self) -> None:
        """Remove all messages from both trees."""
        self._rx.clear_messages()
        self._tx.clear_messages()

    # ---- test helpers -----------------------------------------------

    def rx_count(self) -> int:
        return self._rx.topLevelItemCount()

    def tx_count(self) -> int:
        return self._tx.topLevelItemCount()
