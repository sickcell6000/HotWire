"""
Session tools — redact, export to pcap, export to CSV.

Three collapsible group boxes in one dockable panel. Each reuses the
existing CLI-level implementation:

* Redact → :class:`scripts.redact_session._Redactor`
* Pcap    → :func:`hotwire.io.pcap_export.export_session_to_pcap`
* CSV     → :func:`hotwire.io.csv_export.export_session_to_csv`

No subprocess — the logic runs inline in the GUI process; file
operations are fast enough (<1 s for typical sessions) to not warrant
a worker thread. If a session blows up that assumption we can move to
QThread later.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...io.csv_export import export_session_to_csv
from ...io.pcap_export import export_session_to_pcap


# Load the redact_session module via importlib — it isn't a package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_REDACT_SCRIPT = _REPO_ROOT / "scripts" / "redact_session.py"


def _load_redactor_class():
    spec = importlib.util.spec_from_file_location(
        "redact_session_shim", _REDACT_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._Redactor


class SessionToolsPanel(QWidget):
    """Three in-one: redact / pcap / csv."""

    tool_finished = pyqtSignal(str, str)             # (tool_name, result_text)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_layout()
        self._wire()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        root.addWidget(self._build_redact_box())
        root.addWidget(self._build_pcap_box())
        root.addWidget(self._build_csv_box())
        root.addStretch(1)

    def _build_redact_box(self) -> QGroupBox:
        box = QGroupBox("Redact session (anonymize EVCCID / EVSEID / IP / SessionID)")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self._redact_input = QLineEdit()
        self._redact_input.setPlaceholderText("sessions/EVSE_*.jsonl")
        self._redact_browse = QPushButton("Browse…")
        row.addWidget(QLabel("Input:"))
        row.addWidget(self._redact_input, 1)
        row.addWidget(self._redact_browse)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self._redact_output = QLineEdit()
        self._redact_output.setPlaceholderText("(auto: *.redacted.jsonl)")
        self._redact_out_browse = QPushButton("Save as…")
        row2.addWidget(QLabel("Output:"))
        row2.addWidget(self._redact_output, 1)
        row2.addWidget(self._redact_out_browse)
        lay.addLayout(row2)

        self._redact_run = QPushButton("Run redaction")
        lay.addWidget(self._redact_run)

        self._redact_status = QLabel("")
        self._redact_status.setWordWrap(True)
        lay.addWidget(self._redact_status)
        return box

    def _build_pcap_box(self) -> QGroupBox:
        box = QGroupBox("Export to pcap (for Wireshark / dsV2Gshark)")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self._pcap_input = QLineEdit()
        self._pcap_input.setPlaceholderText("sessions/EVSE_*.jsonl")
        self._pcap_browse = QPushButton("Browse…")
        row.addWidget(QLabel("Input:"))
        row.addWidget(self._pcap_input, 1)
        row.addWidget(self._pcap_browse)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self._pcap_output = QLineEdit()
        self._pcap_output.setPlaceholderText("(auto: *.pcap)")
        self._pcap_out_browse = QPushButton("Save as…")
        row2.addWidget(QLabel("Output:"))
        row2.addWidget(self._pcap_output, 1)
        row2.addWidget(self._pcap_out_browse)
        lay.addLayout(row2)

        self._pcap_run = QPushButton("Export to pcap")
        lay.addWidget(self._pcap_run)

        self._pcap_status = QLabel("")
        self._pcap_status.setWordWrap(True)
        lay.addWidget(self._pcap_status)
        return box

    def _build_csv_box(self) -> QGroupBox:
        box = QGroupBox("Export to CSV (for pandas / Excel)")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self._csv_input = QLineEdit()
        self._csv_input.setPlaceholderText("sessions/EVSE_*.jsonl")
        self._csv_browse = QPushButton("Browse…")
        row.addWidget(QLabel("Input:"))
        row.addWidget(self._csv_input, 1)
        row.addWidget(self._csv_browse)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self._csv_output = QLineEdit()
        self._csv_output.setPlaceholderText("(auto: *.csv)")
        self._csv_out_browse = QPushButton("Save as…")
        row2.addWidget(QLabel("Output:"))
        row2.addWidget(self._csv_output, 1)
        row2.addWidget(self._csv_out_browse)
        lay.addLayout(row2)

        self._csv_run = QPushButton("Export to CSV")
        lay.addWidget(self._csv_run)

        self._csv_status = QLabel("")
        self._csv_status.setWordWrap(True)
        lay.addWidget(self._csv_status)
        return box

    def _wire(self) -> None:
        self._redact_browse.clicked.connect(
            lambda: self._pick_input(self._redact_input)
        )
        self._redact_out_browse.clicked.connect(
            lambda: self._pick_output(self._redact_output, "jsonl")
        )
        self._redact_run.clicked.connect(self._on_redact)

        self._pcap_browse.clicked.connect(
            lambda: self._pick_input(self._pcap_input)
        )
        self._pcap_out_browse.clicked.connect(
            lambda: self._pick_output(self._pcap_output, "pcap")
        )
        self._pcap_run.clicked.connect(self._on_pcap)

        self._csv_browse.clicked.connect(
            lambda: self._pick_input(self._csv_input)
        )
        self._csv_out_browse.clicked.connect(
            lambda: self._pick_output(self._csv_output, "csv")
        )
        self._csv_run.clicked.connect(self._on_csv)

    # ---- helpers ---------------------------------------------------

    def _pick_input(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open JSONL", "sessions",
            "HotWire sessions (*.jsonl);;All files (*)",
        )
        if path:
            edit.setText(path)

    def _pick_output(self, edit: QLineEdit, ext: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save as", edit.text() or "",
            f"{ext.upper()} files (*.{ext});;All files (*)",
        )
        if path:
            edit.setText(path)

    # ---- actions --------------------------------------------------

    def _on_redact(self) -> None:
        inp = self._redact_input.text().strip()
        if not inp:
            QMessageBox.warning(self, "Missing input", "Pick an input JSONL.")
            return
        out = self._redact_output.text().strip()
        if not out:
            out = str(Path(inp).with_suffix(".redacted.jsonl"))
        try:
            count = _do_redact(Path(inp), Path(out))
        except Exception as exc:                                  # noqa: BLE001
            QMessageBox.critical(self, "Redact failed", str(exc))
            return
        msg = f"Wrote {count} redacted records → {out}"
        self._redact_status.setText(msg)
        self.tool_finished.emit("redact", msg)

    def _on_pcap(self) -> None:
        inp = self._pcap_input.text().strip()
        if not inp:
            QMessageBox.warning(self, "Missing input", "Pick an input JSONL.")
            return
        out = self._pcap_output.text().strip()
        if not out:
            out = str(Path(inp).with_suffix(".pcap"))
        try:
            result = export_session_to_pcap(Path(inp), Path(out))
        except Exception as exc:                                  # noqa: BLE001
            QMessageBox.critical(self, "pcap export failed", str(exc))
            return
        msg = (
            f"Wrote {result.packets_written} packets "
            f"({result.records_skipped} skipped) → {result.out_path}"
        )
        self._pcap_status.setText(msg)
        self.tool_finished.emit("pcap", msg)

    def _on_csv(self) -> None:
        inp = self._csv_input.text().strip()
        if not inp:
            QMessageBox.warning(self, "Missing input", "Pick an input JSONL.")
            return
        out = self._csv_output.text().strip()
        if not out:
            out = str(Path(inp).with_suffix(".csv"))
        try:
            result = export_session_to_csv(Path(inp), Path(out))
        except Exception as exc:                                  # noqa: BLE001
            QMessageBox.critical(self, "CSV export failed", str(exc))
            return
        msg = (
            f"Wrote {result.rows_written} rows, "
            f"{len(result.columns)} columns → {result.out_path}"
        )
        self._csv_status.setText(msg)
        self.tool_finished.emit("csv", msg)


# --- redact helper -----------------------------------------------------


def _do_redact(jsonl_path: Path, out_path: Path) -> int:
    Redactor = _load_redactor_class()
    r = Redactor()
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            redacted = r.redact_record(rec)
            fh.write(json.dumps(redacted) + "\n")
            n += 1
    return n
