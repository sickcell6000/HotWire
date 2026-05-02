"""
Side-by-side comparator for two HotWire JSONL sessions.

Loads A + B, runs :func:`hotwire.io.build_diff`, renders the result
as a QTreeWidget with per-row diff children. Highlight colours:

* green — identical row
* yellow — missing in A or B (alignment gap)
* red — same row, different field values
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...io.session_diff import build_diff, load_session


_COLOR_SAME = QColor("#E8F5E9")       # pale green
_COLOR_MISSING = QColor("#FFF8E1")    # pale yellow
_COLOR_DIFF = QColor("#FFEBEE")       # pale red


class SessionComparePanel(QWidget):
    """Dockable two-session diff viewer."""

    diff_loaded = pyqtSignal(int)              # number of rows

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._path_a: Optional[Path] = None
        self._path_b: Optional[Path] = None
        self._build_layout()
        self._wire()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        ctrl_row = QHBoxLayout()
        self._label_a = QLabel("<i>A: not loaded</i>")
        self._label_b = QLabel("<i>B: not loaded</i>")
        self._open_a_btn = QPushButton("Open A…")
        self._open_b_btn = QPushButton("Open B…")
        self._strategy = QComboBox()
        self._strategy.addItems(["sequence", "name"])
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setEnabled(False)
        ctrl_row.addWidget(self._open_a_btn)
        ctrl_row.addWidget(self._label_a, 1)
        ctrl_row.addWidget(self._open_b_btn)
        ctrl_row.addWidget(self._label_b, 1)
        ctrl_row.addWidget(QLabel("Strategy:"))
        ctrl_row.addWidget(self._strategy)
        ctrl_row.addWidget(self._compare_btn)
        root.addLayout(ctrl_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["#", "msg_name (A)", "msg_name (B)", "Diff"])
        self._tree.setColumnWidth(0, 60)
        self._tree.setColumnWidth(1, 240)
        self._tree.setColumnWidth(2, 240)
        root.addWidget(self._tree, 1)

        self._summary = QLabel("<i>No comparison yet</i>")
        root.addWidget(self._summary)

    def _wire(self) -> None:
        self._open_a_btn.clicked.connect(lambda: self._open_side("A"))
        self._open_b_btn.clicked.connect(lambda: self._open_side("B"))
        self._compare_btn.clicked.connect(self._on_compare)

    # ---- slots ----------------------------------------------------

    def _open_side(self, side: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Open session {side}",
            "sessions",
            "HotWire sessions (*.jsonl);;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        if side == "A":
            self._path_a = p
            self._label_a.setText(f"<b>A:</b> {p.name}")
        else:
            self._path_b = p
            self._label_b.setText(f"<b>B:</b> {p.name}")
        self._compare_btn.setEnabled(
            self._path_a is not None and self._path_b is not None
        )

    def _on_compare(self) -> None:
        if self._path_a is None or self._path_b is None:
            return
        try:
            a = load_session(self._path_a)
            b = load_session(self._path_b)
        except OSError as e:
            QMessageBox.warning(self, "Load failed", str(e))
            return
        self.compare(a, b)

    # ---- public API (also used by tests) --------------------------

    def compare(self, a: list[dict], b: list[dict]) -> int:
        """Run the diff and populate the tree. Returns number of rows."""
        strategy = self._strategy.currentText()
        pairs = build_diff(a, b, strategy=strategy)
        self._tree.clear()

        n_same = n_diff = n_missing = 0
        for p in pairs:
            name_a = (p.a or {}).get("msg_name", "∅")
            name_b = (p.b or {}).get("msg_name", "∅")
            diff_label = (
                "—" if p.a is None or p.b is None
                else f"{len(p.field_diffs)} field(s)" if p.field_diffs
                else "identical"
            )
            row = QTreeWidgetItem(self._tree)
            row.setText(0, str(p.index))
            row.setText(1, name_a)
            row.setText(2, name_b)
            row.setText(3, diff_label)

            if p.a is None or p.b is None:
                color = _COLOR_MISSING
                n_missing += 1
            elif p.field_diffs:
                color = _COLOR_DIFF
                n_diff += 1
            else:
                color = _COLOR_SAME
                n_same += 1
            for col in range(4):
                row.setBackground(col, QBrush(color))

            # Add one child per field diff so the reviewer can expand to see details.
            for key, va, vb in p.field_diffs:
                child = QTreeWidgetItem(row)
                child.setText(0, "")
                child.setText(1, key)
                child.setText(2, str(va))
                child.setText(3, str(vb))

        self._summary.setText(
            f"<b>{len(pairs)} rows:</b> "
            f"<span style='color:#2E7D32;'>{n_same} identical</span> · "
            f"<span style='color:#C62828;'>{n_diff} differ</span> · "
            f"<span style='color:#EF6C00;'>{n_missing} missing</span>"
        )
        self.diff_loaded.emit(len(pairs))
        return len(pairs)
