"""
Trace log widget — high-throughput, level-colored, batched appends.

Design notes
------------
CurrentDemand sustains ~17 msg/s per side; each emits 2-3 trace lines plus
state updates. Naively calling ``appendPlainText`` per signal forces a
layout/paint cycle per line. Instead, we buffer incoming lines in a deque
and flush the batch every 50 ms with one ``appendPlainText(chunk)`` call,
which costs one layout pass regardless of batch size.

Line coloring is applied *after* the batched append by walking the new
blocks and setting a QTextCharFormat per block based on the level tag we
stripped off the line. ``setMaximumBlockCount(5000)`` bounds memory so
long runs don't leak.
"""
from __future__ import annotations

import datetime
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QPlainTextEdit

LEVEL_COLORS: dict[str, QColor] = {
    "INFO": QColor("#000000"),
    "DEBUG": QColor("#7F7F7F"),
    "WARNING": QColor("#B07000"),
    "ERROR": QColor("#C62828"),
    "SUCCESS": QColor("#2E7D32"),
}

FLUSH_INTERVAL_MS = 50
MAX_LINES = 5000


class TraceLogWidget(QPlainTextEdit):
    """Scrolled, colored log view with batched appends."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_LINES)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        font = self.font()
        font.setFamily("Consolas")
        font.setPointSize(9)
        self.setFont(font)

        self._buffer: deque[tuple[str, str]] = deque()
        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_INTERVAL_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    # ---- slot --------------------------------------------------------

    def on_trace(self, level: str, text: str) -> None:
        """Slot: called from main thread when a trace signal arrives."""
        self._buffer.append((level, text))

    # ---- flush -------------------------------------------------------

    def _flush(self) -> None:
        if not self._buffer:
            return
        # Snapshot and clear under no lock (main thread only writes here).
        batch = list(self._buffer)
        self._buffer.clear()

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        for level, text in batch:
            fmt = QTextCharFormat()
            fmt.setForeground(LEVEL_COLORS.get(level, LEVEL_COLORS["INFO"]))
            cursor.setCharFormat(fmt)
            cursor.insertText(text + "\n")

        # Auto-scroll to bottom.
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    # ---- save --------------------------------------------------------

    def save_to_file(self, parent_widget=None) -> None:
        """Prompt the user for a filename and write the log contents."""
        default = (
            f"hotwire_log_"
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        path_str, _ = QFileDialog.getSaveFileName(
            parent_widget or self, "Save trace log", default, "Text files (*.txt)"
        )
        if not path_str:
            return
        try:
            Path(path_str).write_text(self.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(parent_widget or self, "Save failed", str(e))
