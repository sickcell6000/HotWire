"""
Data-driven stage parameter editor.

Given a stage name, build one widget per ``FieldSpec`` from ``stage_schema``
and expose:

  * ``set_stage(stage)``  — rebuild the form for a different stage
  * ``get_values()``      — collect current widget values into a dict
  * ``load_values(dict)`` — prefill from an external dict (for pause dialog)
  * ``apply_clicked``     — pyqtSignal emitted when user clicks Apply
  * ``clear_clicked``     — pyqtSignal emitted when user clicks Clear
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..stage_schema import FieldSpec, schema_for


class StageConfigPanel(QWidget):
    """Form that binds one stage's ``FieldSpec`` list to Qt widgets."""

    apply_clicked = pyqtSignal(str, dict)   # (stage, values)
    clear_clicked = pyqtSignal(str)         # stage

    def __init__(self, mode: int, parent=None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._schemas = schema_for(mode)
        self._current_stage: str | None = None
        self._widgets: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)

        self._title = QLabel("<b>No stage selected</b>")
        root.addWidget(self._title)

        self._form_box = QGroupBox("Parameters")
        self._form_layout = QFormLayout(self._form_box)
        root.addWidget(self._form_box, 1)

        # Buttons.
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("Apply Override")
        self._clear_btn = QPushButton("Clear Override")
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._apply_btn.clicked.connect(self._on_apply)
        self._clear_btn.clicked.connect(self._on_clear)

        self._set_buttons_enabled(False)

    # ---- public API -------------------------------------------------

    def set_stage(self, stage: str) -> None:
        self._current_stage = stage
        self._clear_form()
        self._title.setText(f"<b>{stage}</b>")
        fields = self._schemas.get(stage, ())
        if not fields:
            lbl = QLabel("No configurable parameters for this stage.")
            lbl.setStyleSheet("color: #888;")
            self._form_layout.addRow(lbl)
            self._set_buttons_enabled(False)
            return

        for spec in fields:
            widget = self._build_widget(spec)
            self._widgets[spec.key] = widget
            label_text = spec.label
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
            self._form_layout.addRow(label_text, widget)
        self._set_buttons_enabled(True)

    def get_values(self) -> dict[str, Any]:
        """Collect current widget values, applying each field's ``to_wire``
        converter so the dict is ready for the FSM's command builder.

        Fields without ``to_wire`` pass through unchanged.
        """
        out: dict[str, Any] = {}
        fields = self._schemas.get(self._current_stage or "", ())
        for spec in fields:
            w = self._widgets.get(spec.key)
            if w is None:
                continue
            raw = self._read_widget_value(w, spec)
            out[spec.key] = spec.to_wire(raw) if spec.to_wire else raw
        return out

    def get_display_values(self) -> dict[str, Any]:
        """Like :meth:`get_values` but skips ``to_wire`` — for round-tripping
        through :meth:`load_values` inside the pause-intercept dialog."""
        out: dict[str, Any] = {}
        fields = self._schemas.get(self._current_stage or "", ())
        for spec in fields:
            w = self._widgets.get(spec.key)
            if w is not None:
                out[spec.key] = self._read_widget_value(w, spec)
        return out

    def load_values(self, values: dict[str, Any]) -> None:
        """Pre-populate widgets from a dict (e.g. the pending pause params)."""
        fields = self._schemas.get(self._current_stage or "", ())
        for spec in fields:
            if spec.key not in values:
                continue
            w = self._widgets.get(spec.key)
            if w is None:
                continue
            self._write_widget_value(w, spec, values[spec.key])

    # ---- internal helpers -------------------------------------------

    def _clear_form(self) -> None:
        while self._form_layout.rowCount() > 0:
            self._form_layout.removeRow(0)
        self._widgets.clear()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._apply_btn.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)

    def _build_widget(self, spec: FieldSpec) -> QWidget:
        if spec.widget == "combo":
            w = QComboBox()
            for opt in spec.options:
                w.addItem(opt)
            if spec.default is not None and str(spec.default) in spec.options:
                w.setCurrentIndex(spec.options.index(str(spec.default)))
            return w
        if spec.widget == "int":
            w = QSpinBox()
            w.setRange(-2**31, 2**31 - 1)
            if spec.default is not None:
                try:
                    w.setValue(int(spec.default))
                except (TypeError, ValueError):
                    pass
            return w
        if spec.widget == "bool":
            w = QCheckBox()
            w.setChecked(bool(spec.default))
            return w
        # "hex" and "str" both use QLineEdit.
        w = QLineEdit()
        if spec.default is not None:
            w.setText(str(spec.default))
        if spec.widget == "hex":
            w.setPlaceholderText("hex (uppercase, even length)")
        return w

    def _read_widget_value(self, w: QWidget, spec: FieldSpec) -> Any:
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    def _write_widget_value(self, w: QWidget, spec: FieldSpec, value: Any) -> None:
        if isinstance(w, QComboBox):
            idx = w.findText(str(value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            return
        if isinstance(w, QSpinBox):
            try:
                w.setValue(int(value))
            except (TypeError, ValueError):
                pass
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
            return
        if isinstance(w, QLineEdit):
            w.setText("" if value is None else str(value))

    # ---- button handlers --------------------------------------------

    def _on_apply(self) -> None:
        if self._current_stage:
            self.apply_clicked.emit(self._current_stage, self.get_values())

    def _on_clear(self) -> None:
        if self._current_stage:
            self.clear_clicked.emit(self._current_stage)
