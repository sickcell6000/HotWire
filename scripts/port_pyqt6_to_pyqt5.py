#!/usr/bin/env python3
"""Port HotWire's GUI / test code from PyQt6 to PyQt5.

PyQt5 has wider availability on aarch64 Linux (Raspberry Pi) where
PyQt6 6.4 has known layout-engine bugs we couldn't work around.

Replacements applied:
  1. ``from PyQt5.X`` → ``from PyQt5.X``
  2. ``import PyQt5`` → ``import PyQt5``
  3. PyQt6 namespaced enums → PyQt5 flat names (Qt.Vertical
     → Qt.Vertical, Qt.AlignCenter → Qt.AlignCenter,
     etc.)
  4. ``QSizePolicy.X`` → ``QSizePolicy.X``
  5. ``QFrame.X`` → ``QFrame.X``
  6. ``QDialogButtonBox.X`` → ``QDialogButtonBox.X``
  7. ``QDialog.X`` → ``QDialog.X``
  8. ``QMessageBox.X`` → ``QMessageBox.X``
  9. ``QAction`` import: PyQt6.QtGui → PyQt5.QtWidgets
 10. ``.exec_()`` on QApplication / QDialog → ``.exec_()``

Run from repo root::

    python scripts/port_pyqt6_to_pyqt5.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns. Order matters — most specific first.
PATTERNS: list[tuple[str, str]] = [
    # Module imports.
    (r"\bfrom PyQt6\.", "from PyQt5."),
    (r"\bimport PyQt6\b", "import PyQt5"),

    # Namespaced enums — the bulk of the work.
    (r"\bQt\.Orientation\.", "Qt."),
    (r"\bQt\.AlignmentFlag\.", "Qt."),
    (r"\bQt\.CheckState\.", "Qt."),
    (r"\bQt\.ItemFlag\.", "Qt."),
    (r"\bQt\.TextInteractionFlag\.", "Qt."),
    (r"\bQt\.WindowState\.", "Qt."),
    (r"\bQt\.WindowType\.", "Qt."),
    (r"\bQt\.MouseButton\.", "Qt."),
    (r"\bQt\.Key\.", "Qt."),
    (r"\bQt\.ConnectionType\.", "Qt."),
    (r"\bQt\.GlobalColor\.", "Qt."),
    (r"\bQt\.PenStyle\.", "Qt."),
    (r"\bQt\.BrushStyle\.", "Qt."),
    (r"\bQt\.SizeHint\.", "Qt."),
    (r"\bQt\.CursorShape\.", "Qt."),
    (r"\bQt\.ScrollBarPolicy\.", "Qt."),
    (r"\bQt\.FocusPolicy\.", "Qt."),
    (r"\bQt\.ContextMenuPolicy\.", "Qt."),
    (r"\bQt\.SortOrder\.", "Qt."),
    (r"\bQt\.MatchFlag\.", "Qt."),
    (r"\bQt\.DropAction\.", "Qt."),
    (r"\bQt\.TextFormat\.", "Qt."),

    # Widget enums.
    (r"\bQSizePolicy\.Policy\.", "QSizePolicy."),
    (r"\bQFrame\.Shape\.", "QFrame."),
    (r"\bQFrame\.Shadow\.", "QFrame."),
    (r"\bQDialogButtonBox\.ButtonRole\.", "QDialogButtonBox."),
    (r"\bQDialogButtonBox\.StandardButton\.", "QDialogButtonBox."),
    (r"\bQDialog\.DialogCode\.", "QDialog."),
    (r"\bQMessageBox\.StandardButton\.", "QMessageBox."),
    (r"\bQMessageBox\.Icon\.", "QMessageBox."),
    (r"\bQFileDialog\.Option\.", "QFileDialog."),
    (r"\bQFileDialog\.AcceptMode\.", "QFileDialog."),
    (r"\bQFileDialog\.FileMode\.", "QFileDialog."),
    (r"\bQAbstractItemView\.SelectionMode\.", "QAbstractItemView."),
    (r"\bQAbstractItemView\.SelectionBehavior\.", "QAbstractItemView."),
    (r"\bQAbstractItemView\.EditTrigger\.", "QAbstractItemView."),
    (r"\bQAbstractItemView\.ScrollMode\.", "QAbstractItemView."),
    (r"\bQHeaderView\.ResizeMode\.", "QHeaderView."),
    (r"\bQListWidget\.SelectionMode\.", "QListWidget."),
    (r"\bQTreeWidget\.SelectionMode\.", "QTreeWidget."),
    (r"\bQTabWidget\.TabPosition\.", "QTabWidget."),
    (r"\bQLineEdit\.EchoMode\.", "QLineEdit."),
    (r"\bQTextEdit\.LineWrapMode\.", "QTextEdit."),

    # exec → exec_ on QApplication and QDialog (PyQt5 prefers exec_).
    # We add both so PyQt6's exec() also works after backport.
    (r"\.exec\(\)(?=[^_])", ".exec_()"),
]


def _move_qaction(content: str) -> str:
    """Move QAction import from QtGui to QtWidgets (PyQt5 location).

    Handles three patterns:
      from PyQt5.QtGui import QAction       (alone)
      from PyQt5.QtGui import QAction, X    (with siblings)
      from PyQt5.QtGui import (
          QAction,
          X,
      )
    """
    # Easiest: in PyQt5, QAction lives in QtWidgets but importing it
    # from QtGui still works for backward compat in some 5.x versions.
    # We move it to QtWidgets to be portable across all PyQt5 minor
    # versions.
    if "QAction" not in content:
        return content
    # Single-line, alone.
    content = re.sub(
        r"^from PyQt5\.QtGui import QAction$",
        "from PyQt5.QtWidgets import QAction",
        content, flags=re.MULTILINE,
    )
    # Single-line, with siblings — split into two import lines.
    def _split_qaction_line(m: re.Match) -> str:
        rest = m.group(1)
        items = [s.strip() for s in rest.split(",") if s.strip()]
        items_no_qaction = [s for s in items if s != "QAction"]
        if not items_no_qaction:
            return "from PyQt5.QtWidgets import QAction"
        return (
            f"from PyQt5.QtGui import {', '.join(items_no_qaction)}\n"
            f"from PyQt5.QtWidgets import QAction"
        )
    content = re.sub(
        r"^from PyQt5\.QtGui import ([^()\n]*\bQAction\b[^()\n]*)$",
        _split_qaction_line,
        content, flags=re.MULTILINE,
    )
    # Multi-line parenthesized form: handle by extracting QAction.
    def _multiline(m: re.Match) -> str:
        body = m.group(1)
        items = [s.strip().rstrip(",") for s in body.splitlines() if s.strip()]
        items_no_qaction = [s for s in items if s and s != "QAction"]
        new_qtgui = ""
        if items_no_qaction:
            joined = ",\n    ".join(items_no_qaction)
            new_qtgui = f"from PyQt5.QtGui import (\n    {joined},\n)\n"
        return new_qtgui + "from PyQt5.QtWidgets import QAction"
    content = re.sub(
        r"^from PyQt5\.QtGui import \(\n((?:.*\bQAction\b.*\n|.*\n)*?)\)$",
        _multiline,
        content, flags=re.MULTILINE,
    )
    return content


def port_file(path: Path) -> bool:
    """Apply patterns to one file. Returns True if anything changed."""
    original = path.read_text(encoding="utf-8")
    new = original
    for pattern, replacement in PATTERNS:
        new = re.sub(pattern, replacement, new)
    new = _move_qaction(new)
    if new != original:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    files = []
    for ext in ("py",):
        files.extend(repo_root.rglob(f"*.{ext}"))
    # Skip vendor / archive / .venv / __pycache__.
    skip_parts = {"vendor", "archive", ".venv", "__pycache__", ".git"}
    targets = [
        f for f in files
        if not any(part in skip_parts for part in f.parts)
    ]

    changed: list[Path] = []
    for f in targets:
        try:
            if "PyQt6" in f.read_text(encoding="utf-8"):
                if port_file(f):
                    changed.append(f)
                    print(f"  ported: {f.relative_to(repo_root)}")
        except (UnicodeDecodeError, OSError):
            continue

    print(f"\nDone. {len(changed)} file(s) modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
