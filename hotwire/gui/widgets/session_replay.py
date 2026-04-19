"""
Session replay — load a previously-recorded JSONL session and step
through the protocol messages post-hoc.

Wraps :class:`hotwire.core.session_log.SessionLogger`'s on-disk format.
Every line of the JSONL file is one decoded Req/Res with timestamp,
direction, msg_name, mode, params; this widget lists them, lets the
operator click one, and emits a signal carrying the full decoded
params — the main window routes that signal into the existing
:class:`ReqResTreeView` so reviewers see the same tree for live runs
and replays.

Also exposes an "Export pcap…" button that calls
:func:`hotwire.io.pcap_export.export_session_to_pcap` on the loaded
file — handy for re-dissecting an existing session in Wireshark /
dsV2Gshark without leaving the GUI.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...io.pcap_export import export_session_to_pcap

_log = logging.getLogger(__name__)


class SessionReplayPanel(QWidget):
    """Dockable panel for stepping through a JSONL session log."""

    # (direction, msg_name, params) — matches ReqResTreeView.on_message.
    event_selected = pyqtSignal(str, str, dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._path: Optional[Path] = None
        self._records: list[dict] = []

        self._build_layout()
        self._wire()

    # ---- layout ----------------------------------------------------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # Header row — file label + Open button.
        top = QHBoxLayout()
        self._file_label = QLabel("<i>No session loaded</i>")
        self._file_label.setMinimumWidth(200)
        self._open_btn = QPushButton("Open session…")
        self._export_btn = QPushButton("Export pcap…")
        self._export_btn.setEnabled(False)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setEnabled(False)
        top.addWidget(self._file_label, 1)
        top.addWidget(self._open_btn)
        top.addWidget(self._export_btn)
        top.addWidget(self._clear_btn)
        root.addLayout(top)

        # Info line — shows record count.
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #777;")
        root.addWidget(self._info_label)

        # Timeline.
        self._listbox = QListWidget()
        root.addWidget(self._listbox, 1)

    def _wire(self) -> None:
        self._open_btn.clicked.connect(self._on_open_clicked)
        self._export_btn.clicked.connect(self._on_export_clicked)
        self._clear_btn.clicked.connect(self.clear_session)
        self._listbox.currentRowChanged.connect(self._on_row_changed)

    # ---- public API ------------------------------------------------

    def load_session(self, path: Path) -> int:
        """Load a JSONL file from disk. Returns the number of records
        successfully parsed."""
        path = Path(path)
        records: list[dict] = []
        if not path.exists():
            raise FileNotFoundError(str(path))
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(rec)

        self._path = path
        self._records = records
        self._file_label.setText(f"<b>{path.name}</b>")
        self._file_label.setToolTip(str(path))
        self._info_label.setText(f"{len(records)} records loaded")
        self._export_btn.setEnabled(bool(records))
        self._clear_btn.setEnabled(bool(records))

        self._listbox.clear()
        for i, rec in enumerate(records):
            label = _format_record(i, rec)
            QListWidgetItem(label, self._listbox)
        return len(records)

    def clear_session(self) -> None:
        self._path = None
        self._records = []
        self._listbox.clear()
        self._file_label.setText("<i>No session loaded</i>")
        self._file_label.setToolTip("")
        self._info_label.setText("")
        self._export_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

    # ---- slots -----------------------------------------------------

    def _on_open_clicked(self) -> None:
        path_str, _filter = QFileDialog.getOpenFileName(
            self,
            "Open HotWire session",
            "sessions",
            "HotWire sessions (*.jsonl);;All files (*)",
        )
        if not path_str:
            return
        try:
            n = self.load_session(Path(path_str))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Failed to load session", f"{type(exc).__name__}: {exc}"
            )
            return
        if n == 0:
            QMessageBox.information(
                self, "Empty session",
                "No valid records found in the selected file.",
            )

    def _on_export_clicked(self) -> None:
        if self._path is None:
            return
        default_out = self._path.with_suffix(".pcap")
        out_str, _filter = QFileDialog.getSaveFileName(
            self,
            "Export session to pcap",
            str(default_out),
            "pcap (*.pcap);;All files (*)",
        )
        if not out_str:
            return
        try:
            result = export_session_to_pcap(self._path, Path(out_str))
        except Exception as exc:                                 # noqa: BLE001
            QMessageBox.critical(
                self, "Export failed", f"{type(exc).__name__}: {exc}"
            )
            _log.exception("pcap export failed")
            return
        QMessageBox.information(
            self, "Export complete",
            f"Wrote {result.packets_written} packets "
            f"({result.records_skipped} records skipped)\n\n"
            f"→ {result.out_path}",
        )

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._records):
            return
        rec = self._records[row]
        direction = str(rec.get("direction", ""))
        msg_name = str(rec.get("msg_name", ""))
        params = rec.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        self.event_selected.emit(direction, msg_name, params)


# --- helpers ---------------------------------------------------------


def _format_record(index: int, rec: dict) -> str:
    ts = str(rec.get("timestamp", ""))
    direction = str(rec.get("direction", "?")).lower()
    msg = str(rec.get("msg_name", ""))
    arrow = "←" if direction == "rx" else "→" if direction == "tx" else "·"
    short_ts = ts.split("T", 1)[-1][:12] if "T" in ts else ts[:12]
    return f"#{index + 1:04d}  {short_ts}  {arrow}  {msg}"
