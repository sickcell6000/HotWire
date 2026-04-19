"""
Live-updating network-interface status table.

A dockable companion to :class:`InterfacePickerCombo`: shows every
NIC on the host with its carrier state + rolling packet counters, so
the operator can watch a modem drop offline mid-session without
opening a shell.

Refreshes every 2 seconds via QTimer. The timer is stopped on
``close()`` so no stray callbacks fire after the dock is gone.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...net import NetInterface, list_interfaces

_GREEN = QColor("#2E7D32")
_YELLOW = QColor("#EF6C00")
_GRAY = QColor("#9E9E9E")


class NetworkInterfacesDock(QWidget):
    """QTableWidget showing NICs live. 2-second QTimer refresh cycle."""

    best_changed = pyqtSignal(str)                   # top-scored name

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._last_rx: dict[str, int] = {}
        self._last_tx: dict[str, int] = {}
        self._last_best: Optional[str] = None
        self._build_layout()
        self._wire()
        self.refresh()

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    # ---- layout ----------------------------------------------------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        self._best_label = QLabel("")
        top.addWidget(self._best_label, 1)
        self._refresh_btn = QPushButton("Refresh now")
        self._refresh_btn.setFixedWidth(120)
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "Interface", "MAC", "MTU", "Carrier",
            "Speed", "RX pkts/s", "TX pkts/s", "Score",
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table, 1)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #555;")
        root.addWidget(self._summary_label)

    def _wire(self) -> None:
        self._refresh_btn.clicked.connect(self.refresh)

    # ---- public API ------------------------------------------------

    def refresh(self) -> None:
        nics = list_interfaces()
        rx_rates, tx_rates = self._compute_rates()

        self._table.setRowCount(len(nics))
        for row, ni in enumerate(nics):
            self._table.setItem(row, 0, QTableWidgetItem(ni.name))
            self._table.setItem(row, 1, QTableWidgetItem(ni.mac or "—"))
            self._table.setItem(row, 2, QTableWidgetItem(str(ni.mtu) if ni.mtu else "—"))

            carrier_item = QTableWidgetItem(self._carrier_text(ni))
            carrier_item.setForeground(QBrush(self._carrier_color(ni)))
            self._table.setItem(row, 3, carrier_item)

            self._table.setItem(
                row, 4,
                QTableWidgetItem(f"{ni.speed_mbps} Mbps" if ni.speed_mbps else "—"),
            )
            self._table.setItem(
                row, 5,
                QTableWidgetItem(f"{rx_rates.get(ni.name, 0):.0f}"),
            )
            self._table.setItem(
                row, 6,
                QTableWidgetItem(f"{tx_rates.get(ni.name, 0):.0f}"),
            )

            score_item = QTableWidgetItem(str(ni.score))
            score_color = (
                _GREEN if ni.score >= 15
                else _YELLOW if ni.score >= 0
                else _GRAY
            )
            score_item.setForeground(QBrush(score_color))
            self._table.setItem(row, 7, score_item)

        best = nics[0].name if nics else None
        self._best_label.setText(
            f"<b>Best candidate:</b> {best}  (score {nics[0].score})"
            if nics else "<i>No interfaces detected.</i>"
        )
        self._summary_label.setText(
            f"{len(nics)} interface(s) · auto-refresh every 2s"
        )

        if best and best != self._last_best:
            self._last_best = best
            self.best_changed.emit(best)

    def current_best(self) -> Optional[str]:
        return self._last_best

    def close(self) -> bool:                          # noqa: A003
        """Stop the timer before the Qt widget is torn down."""
        if self._timer.isActive():
            self._timer.stop()
        return super().close()

    # ---- internals --------------------------------------------------

    def _compute_rates(self) -> tuple[dict[str, float], dict[str, float]]:
        """Difference packet counters since last sample to get pkts/s.

        Returns two dicts: rx_rate_by_name, tx_rate_by_name. First call
        returns zeros (no previous sample to diff against).
        """
        try:
            import psutil
            io = psutil.net_io_counters(pernic=True)
        except Exception:                                        # noqa: BLE001
            return {}, {}

        rx_rates: dict[str, float] = {}
        tx_rates: dict[str, float] = {}
        for name, counters in io.items():
            prev_rx = self._last_rx.get(name)
            prev_tx = self._last_tx.get(name)
            cur_rx = int(getattr(counters, "packets_recv", 0))
            cur_tx = int(getattr(counters, "packets_sent", 0))
            if prev_rx is not None and prev_tx is not None:
                # Sample interval = 2 s, so rate = delta / 2.
                rx_rates[name] = max(0.0, (cur_rx - prev_rx) / 2.0)
                tx_rates[name] = max(0.0, (cur_tx - prev_tx) / 2.0)
            self._last_rx[name] = cur_rx
            self._last_tx[name] = cur_tx
        return rx_rates, tx_rates

    @staticmethod
    def _carrier_text(ni: NetInterface) -> str:
        if ni.has_carrier is True:
            return "● up"
        if ni.has_carrier is False and ni.is_up:
            return "● no link"
        if ni.is_up:
            return "● up (unknown carrier)"
        return "● down"

    @staticmethod
    def _carrier_color(ni: NetInterface) -> QColor:
        if ni.has_carrier is True:
            return _GREEN
        if ni.is_up:
            return _YELLOW
        return _GRAY
