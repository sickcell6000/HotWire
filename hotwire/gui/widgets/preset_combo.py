"""
Reusable composite widget — editable combo + preset save / manage.

Used by the Attack Launcher (``attack_launcher.py``) and the per-stage
Override editor (``stage_config.py``). Each instance is bound to a
``scope`` string like ``"AutochargeImpersonation.evccid"`` so it loads
and saves to the right slice of the shared :class:`PresetStore`.

UI shape::

    ┌──────────────────────────────────────┬───────┬───────┐
    │ ▼ Pi PEV self-MAC — d83add22f182     │  💾   │   ⚙   │
    └──────────────────────────────────────┴───────┴───────┘
      Note: Sim/dev only — never replay against a live station.

Picking a preset from the dropdown fills the editable line with just
the value (so downstream code reads the bare value via
``current_value()``). Typing a custom value works too — the save
button stores it as a new preset along with a label and note. The
gear button opens a manager dialog to delete entries.

The widget is type-aware via the ``value_kind`` parameter:

  * ``"str"``  — text input, returned as-is
  * ``"int"``  — text input that parses the typed text as int on
                  ``current_value()``; raises ``ValueError`` on bad input
  * ``"hex"``  — same as ``"str"`` but with a placeholder hint and
                  monospace font

Other types (bool / float) fall back to the regular widget builder in
the host dialog.
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..presets import Preset, PresetStore, get_store


_VALUE_KINDS = ("str", "int", "hex")


class PresetCombo(QWidget):
    """Editable combo + preset save / manage buttons.

    Signals
    -------
    value_changed(str)
        Emitted whenever the editable text changes (manual typing or
        preset selection). Always carries the raw text — host code
        coerces it to int / bool / etc. as needed.
    """

    value_changed = pyqtSignal(str)

    def __init__(
        self,
        scope: str,
        *,
        value_kind: str = "str",
        initial: Optional[str] = None,
        placeholder: str = "",
        store: Optional[PresetStore] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if value_kind not in _VALUE_KINDS:
            raise ValueError(
                f"value_kind must be one of {_VALUE_KINDS}; got {value_kind!r}"
            )
        self._scope = scope
        self._value_kind = value_kind
        self._store = store or get_store()

        self._build_layout(placeholder)
        self.refresh_presets()
        if initial is not None:
            self.set_text(initial)
        self._wire()

    # ---- layout ----------------------------------------------------

    def _build_layout(self, placeholder: str) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        if placeholder:
            self._combo.lineEdit().setPlaceholderText(placeholder)
        if self._value_kind == "hex":
            mono = QFont("Consolas")
            mono.setStyleHint(QFont.Monospace)
            self._combo.setFont(mono)
            self._combo.lineEdit().setFont(mono)
        # Stretch the combo to fill the row.
        self._combo.setSizePolicy(self._combo.sizePolicy().Expanding,
                                  self._combo.sizePolicy().Fixed)
        row.addWidget(self._combo, 1)

        self._save_btn = QPushButton("💾")
        self._save_btn.setToolTip("Save the current value as a new preset")
        self._save_btn.setFixedWidth(28)
        row.addWidget(self._save_btn)

        self._manage_btn = QPushButton("⚙")
        self._manage_btn.setToolTip("Manage saved presets for this field")
        self._manage_btn.setFixedWidth(28)
        row.addWidget(self._manage_btn)

    def _wire(self) -> None:
        self._combo.currentIndexChanged.connect(self._on_combo_index_changed)
        self._combo.editTextChanged.connect(self._on_edit_text_changed)
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._manage_btn.clicked.connect(self._on_manage_clicked)

    # ---- public API ------------------------------------------------

    def text(self) -> str:
        """Raw text in the editable area."""
        return self._combo.currentText().strip()

    def set_text(self, value: str) -> None:
        # Don't trigger NoInsert insertion — just set the line edit.
        self._combo.setEditText(str(value))

    def current_value(self) -> Any:
        """Return the value coerced to the declared ``value_kind``."""
        raw = self.text()
        if self._value_kind == "int":
            if not raw:
                return 0
            return int(raw)
        return raw

    def refresh_presets(self) -> None:
        """Reload presets for this scope from the store."""
        # Preserve current text so refresh doesn't wipe what the user is typing.
        current = self._combo.currentText()
        self._combo.blockSignals(True)
        try:
            self._combo.clear()
            self._combo.addItem("", userData=None)        # blank slot
            for preset in self._store.for_scope(self._scope):
                self._combo.addItem(preset.display(), userData=preset)
            self._combo.setEditText(current)
        finally:
            self._combo.blockSignals(False)
        # Update tooltip for the currently selected item if any.
        self._update_tooltip()

    # ---- slots -----------------------------------------------------

    def _on_combo_index_changed(self, index: int) -> None:
        preset = self._combo.itemData(index)
        if isinstance(preset, Preset):
            # Replace the editable text with just the bare value so
            # downstream `text()` / `current_value()` is clean.
            self._combo.setEditText(str(preset.value))
        self._update_tooltip()

    def _on_edit_text_changed(self, text: str) -> None:
        self.value_changed.emit(text)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        """If the editable text matches a preset's value, surface the
        preset's note as the widget's tooltip so the operator can see
        provenance without having to open the combo."""
        raw = self._combo.currentText().strip()
        for preset in self._store.for_scope(self._scope):
            if str(preset.value) == raw and preset.note:
                self.setToolTip(preset.note)
                return
        self.setToolTip("")

    def _on_save_clicked(self) -> None:
        raw = self.text()
        if not raw:
            QMessageBox.information(
                self, "Nothing to save",
                "Type a value first, then click 💾 to save it as a preset.",
            )
            return
        dlg = _SavePresetDialog(default_value=raw, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        label, note = dlg.result_values()
        # Coerce the saved value to the declared kind so an int field's
        # presets aren't stored as strings.
        try:
            stored_value = self._coerce(raw)
        except ValueError as exc:
            QMessageBox.warning(self, "Bad value", str(exc))
            return
        self._store.add(self._scope, stored_value, label, note)
        self.refresh_presets()

    def _on_manage_clicked(self) -> None:
        dlg = _ManagePresetsDialog(self._scope, self._store, parent=self)
        dlg.exec_()
        self.refresh_presets()

    # ---- helpers ---------------------------------------------------

    def _coerce(self, raw: str) -> Any:
        if self._value_kind == "int":
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"'{raw}' is not a valid integer"
                ) from exc
        return raw


# --- Save preset dialog ----------------------------------------------


class _SavePresetDialog(QDialog):
    """Tiny modal asking for the new preset's label + note."""

    def __init__(self, default_value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save preset")
        self.setModal(True)
        self.resize(420, 220)

        layout = QFormLayout(self)
        layout.addRow("Value:", QLabel(f"<code>{default_value}</code>"))
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText(
            "Short name, e.g. 'IONIQ 6 (paper Table 4)'"
        )
        layout.addRow("Label:", self._label_edit)
        self._note_edit = QTextEdit()
        self._note_edit.setPlaceholderText(
            "Free-form note — capture date, target vehicle, charging "
            "station, anything you'd want to know months from now."
        )
        self._note_edit.setMinimumHeight(60)
        layout.addRow("Note:", self._note_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._label_edit.setFocus(Qt.OtherFocusReason)

    def result_values(self) -> tuple[str, str]:
        return (self._label_edit.text().strip(),
                self._note_edit.toPlainText().strip())


# --- Manage presets dialog -------------------------------------------


class _ManagePresetsDialog(QDialog):
    """Lists every preset for ``scope``; lets the operator delete entries."""

    def __init__(
        self,
        scope: str,
        store: PresetStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scope = scope
        self._store = store
        self.setWindowTitle(f"Manage presets — {scope}")
        self.setModal(True)
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Pick a preset to delete it. To edit a preset, delete it then "
            "save the new version from the main field."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        layout.addWidget(info)

        self._list = QListWidget()
        self._populate()
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._delete_btn = QPushButton("Delete selected")
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch(1)
        self._close_btn = QPushButton("Close")
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._delete_btn.clicked.connect(self._on_delete)
        self._close_btn.clicked.connect(self.accept)

    def _populate(self) -> None:
        self._list.clear()
        for preset in self._store.for_scope(self._scope):
            text = f"{preset.label or '(no label)'} — {preset.value!s}"
            if preset.note:
                text += f"\n    {preset.note}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, preset)
            self._list.addItem(item)

    def _on_selection_changed(self) -> None:
        self._delete_btn.setEnabled(bool(self._list.selectedItems()))

    def _on_delete(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        preset: Preset = items[0].data(Qt.UserRole)
        confirm = QMessageBox.question(
            self,
            "Delete preset?",
            f"Permanently delete '{preset.label}' = {preset.value}?",
        )
        if confirm != QMessageBox.Yes:
            return
        self._store.remove(preset.scope, preset.value, preset.label)
        self._populate()
        self._delete_btn.setEnabled(False)
