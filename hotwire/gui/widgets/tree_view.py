"""
Req / Res tree view — every decoded DIN 70121 message that passed
through the FSM, displayed across three tabs:

    +-----------------------------------------------+
    | [Combined]  [Received (rx)]  [Sent (tx)]      |
    +-----------------------------------------------+
    |  →  #1  SessionSetupReq    EVCCID=ab:cd:…     |  ← chronological
    |  ←  #1  SessionSetupRes    EVSEID=ZZDEFLT     |    (Combined tab)
    |  →  #2  ServiceDiscoveryReq …                 |
    |  ←  #2  ServiceDiscoveryRes ResponseCode=OK   |
    +-----------------------------------------------+

Side-by-side rx | tx columns have been replaced by tabs so each tab
gets the full center-column width. The *Combined* tab interleaves
incoming and outgoing messages in time-of-arrival order, which is
how the operator most often wants to read the protocol exchange.
The *Received* and *Sent* tabs are still here for when the operator
wants to focus on one direction at a time.

Each side caps at 200 messages (per direction; the Combined tab
re-derives its display from the underlying trees on every update so
it stays in sync without owning a third copy).
"""
from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_MAX_MESSAGES_PER_SIDE = 200

_RX_HEADING_COLOR = QColor("#1565C0")   # blue for received
_TX_HEADING_COLOR = QColor("#2E7D32")   # green for transmitted
_SKIP_KEYS = {"info", "result", "error", "debug", "schema", "g_errn"}


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


class _MessageTree(QTreeWidget):
    """Single tree for one direction (rx, tx, or interleaved)."""

    def __init__(
        self,
        col0_label: str,
        heading_color: QColor | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._heading_color = heading_color
        # Monotonic counter for the displayed ``#N`` label. Distinct
        # from ``self.topLevelItemCount()`` because that one caps at
        # ``_MAX_MESSAGES_PER_SIDE`` once eviction kicks in — leaving
        # every late-session row labelled "#200" forever, which made
        # the GUI useless for navigating long traces. We bump this on
        # every successful insert and never reset it (except on
        # ``clear_messages``).
        self._total_count: int = 0
        self.setHeaderLabels([col0_label, "Value"])
        self.setColumnWidth(0, 320)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setAnimated(False)  # avoid repaint churn under high message rate

    def append_message(
        self,
        msg_name: str,
        params: dict[str, Any],
        prefix: str = "",
        color: QColor | None = None,
    ) -> None:
        """Insert one message as an expanded top-level item with key/value children.

        ``prefix`` is prepended to the column-0 label (e.g. ``"→"`` /
        ``"←"`` for the Combined tab). ``color`` overrides the per-tree
        default (used by the Combined tab to color rx blue / tx green
        in the same widget).
        """
        # Bound the tree so sessions with thousands of CurrentDemandRes don't
        # blow memory / slow the UI.
        while self.topLevelItemCount() >= _MAX_MESSAGES_PER_SIDE:
            self.takeTopLevelItem(0)

        root = QTreeWidgetItem(self)
        self._total_count += 1
        idx = self._total_count
        label = f"{prefix}  #{idx}  {msg_name}" if prefix else f"#{idx}  {msg_name}"
        root.setText(0, label)
        applied = color or self._heading_color
        if applied is not None:
            root.setForeground(0, QBrush(applied))
        root.setText(1, _summary_for(params))

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
        self._total_count = 0


class ReqResTreeView(QWidget):
    """Tabbed protocol-message viewer.

    Three tabs share the full center-column width:
    Combined (chronological rx + tx), Received-only, Sent-only.

    Usage::

        view = ReqResTreeView()
        signals.msg_decoded.connect(view.on_message)
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # Direction-specific trees keep the per-direction message count
        # bounded and let the operator filter visually by tab. The
        # ``Combined`` tree is updated alongside them so all three views
        # stay coherent without re-deriving on every paint.
        self._rx = _MessageTree("Received (from peer)", _RX_HEADING_COLOR)
        self._tx = _MessageTree("Sent (to peer)", _TX_HEADING_COLOR)
        self._combined = _MessageTree("Direction / Message")

        # Counter that keeps the Combined tab's per-tree #N in sync with
        # the side-specific tabs (otherwise Combined's #N would drift
        # because it accumulates rx + tx but each side still has its own
        # numbering).
        self._rx_count = 0
        self._tx_count = 0

        self._tabs = QTabWidget()
        self._tabs.addTab(self._combined, "Combined")
        self._tabs.addTab(self._rx, "Received (rx)")
        self._tabs.addTab(self._tx, "Sent (tx)")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.addWidget(self._tabs)

    # ---- slot -------------------------------------------------------

    def on_message(self, direction: str, msg_name: str, params: dict) -> None:
        if direction == "rx":
            self._rx.append_message(msg_name, params)
            self._rx_count += 1
            self._append_combined("rx", msg_name, params, self._rx_count)
        elif direction == "tx":
            self._tx.append_message(msg_name, params)
            self._tx_count += 1
            self._append_combined("tx", msg_name, params, self._tx_count)

    def _append_combined(
        self,
        direction: str,
        msg_name: str,
        params: dict,
        side_index: int,
    ) -> None:
        if direction == "rx":
            prefix = "←  rx"
            color = _RX_HEADING_COLOR
        else:
            prefix = "→  tx"
            color = _TX_HEADING_COLOR
        # Bound the combined tab.
        while self._combined.topLevelItemCount() >= _MAX_MESSAGES_PER_SIDE * 2:
            self._combined.takeTopLevelItem(0)
        # We bypass ``_MessageTree.append_message`` because we want the
        # Combined tab's ``#N`` to match the per-direction tree's ``#N``
        # (i.e. the rx-side ``#side_index`` for an rx row, tx-side
        # ``#side_index`` for a tx row). We also bump ``_total_count``
        # on the combined tree so any future direct ``append_message``
        # call to it stays consistent.
        root = QTreeWidgetItem(self._combined)
        self._combined._total_count += 1
        root.setText(0, f"{prefix}  #{side_index}  {msg_name}")
        root.setForeground(0, QBrush(color))
        root.setText(1, _summary_for(params))
        for key, value in params.items():
            if key in _SKIP_KEYS:
                continue
            child = QTreeWidgetItem(root)
            child.setText(0, key)
            child.setText(1, str(value))
        root.setExpanded(True)
        self._combined.scrollToItem(root)

    def clear(self) -> None:
        """Remove all messages from every tab."""
        self._rx.clear_messages()
        self._tx.clear_messages()
        self._combined.clear_messages()
        self._rx_count = 0
        self._tx_count = 0

    def tab_widget(self):
        """Expose the inner :class:`QTabWidget` so the host window can
        graft additional tabs (e.g. the trace log) alongside the three
        message tabs without nesting another tab bar."""
        return self._tabs

    # ---- test helpers -----------------------------------------------

    def rx_count(self) -> int:
        return self._rx.topLevelItemCount()

    def tx_count(self) -> int:
        return self._tx.topLevelItemCount()

    def combined_count(self) -> int:
        return self._combined.topLevelItemCount()
