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

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
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
from .preset_combo import PresetCombo


class StageConfigPanel(QWidget):
    """Form that binds one stage's ``FieldSpec`` list to Qt widgets."""

    apply_clicked = pyqtSignal(str, dict)   # (stage, values)
    clear_clicked = pyqtSignal(str)         # stage

    def __init__(
        self,
        mode: int,
        pause_controller=None,
        parent=None,
    ) -> None:
        """``pause_controller`` (optional) — when provided, ``set_stage``
        will auto-prefill widgets from any installed override for the
        selected stage, so external mutations (Attack Launcher → Apply,
        scripted ``set_override`` calls, etc.) are immediately visible
        in the editor without the operator having to manually re-type.
        """
        super().__init__(parent)
        self._mode = mode
        self._pause_controller = pause_controller
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

        # If a controller installed an override for this stage (Attack
        # Launcher, scripted set_override, etc.) reflect it in the just-
        # rebuilt widgets. Without this the editor shows schema defaults
        # while the FSM happily transmits the spoofed values — a confusing
        # UX gap that wasted ~10 minutes during paper-validation testing.
        if self._pause_controller is not None:
            try:
                override = self._pause_controller.get_override(stage)
            except Exception:                                       # noqa: BLE001
                override = None
            if override:
                self.load_values(override)

    def refresh_overrides(self) -> None:
        """Re-pull the current override map from the pause controller
        and update the visible widgets. Call this from the main window
        whenever an external action (Attack Launcher, attack playbook,
        etc.) might have just installed/changed overrides for the stage
        currently being edited."""
        if self._current_stage is None or self._pause_controller is None:
            return
        try:
            override = self._pause_controller.get_override(self._current_stage)
        except Exception:                                           # noqa: BLE001
            override = None
        if override:
            self.load_values(override)

    def get_values(self) -> dict[str, Any]:
        """Collect current widget values, applying each field's ``to_wire``
        converter so the dict is ready for the FSM's command builder.

        Fields without ``to_wire`` pass through unchanged.

        **Empty-string filter**: protocol-internal fields like
        ``PreChargeReq.SessionID`` ship with a schema ``default=""`` — the
        operator never touches them because the FSM injects its own value
        (the SessionID it learned from ``SessionSetupRes``). If the
        editor's empty default landed in the resulting override dict,
        :meth:`PauseController.intercept` would clobber the FSM-supplied
        SessionID with empty, OpenV2G would EXI-encode garbage at the
        wrong byte offsets, and the EVSE would decode nonsense
        (``EVTargetVoltage`` parsed as the SessionID byte, etc.). So we
        omit any string-typed value that came back empty — those are
        "operator did not provide a value, please use the FSM default".
        Numeric / boolean fields pass through unchanged because their
        widgets always produce a definite value (0, False, etc. are
        intentional, not absences).
        """
        out: dict[str, Any] = {}
        fields = self._schemas.get(self._current_stage or "", ())
        for spec in fields:
            w = self._widgets.get(spec.key)
            if w is None:
                continue
            raw = self._read_widget_value(w, spec)
            if isinstance(raw, str) and raw == "":
                # Don't poison the override with an empty placeholder.
                continue
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
        # Per-stage scope so PresetCombo can persist + reload presets
        # keyed by ``<stage>.<field>`` (matches the convention used by
        # the attack launcher's ``<AttackClass>.<field>`` scopes).
        scope = f"{self._current_stage}.{spec.key}" if self._current_stage else ""

        if spec.widget == "combo":
            w = QComboBox()
            # ``options`` is either a tuple of display strings
            # (``("OK", "OK_DC", ...)``) or a tuple of ``(display, wire_value)``
            # pairs (``(("Contract", 0), ("ExternalPayment", 1))``). We
            # accept both so stage_schema authors can pick whichever maps
            # cleanest to the wire. Tuple form stores the wire value as
            # the QComboBox item's userData; bare-string form leaves it
            # unset and the wire value is derived elsewhere.
            for opt in spec.options:
                if isinstance(opt, tuple):
                    display, wire = opt
                    w.addItem(str(display), wire)
                else:
                    w.addItem(str(opt))
            # Match default by display string against whichever form was used.
            if spec.default is not None:
                idx = w.findText(str(spec.default))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            return w
        if spec.widget == "int":
            # PresetCombo with int kind: dropdown of saved presets +
            # editable numeric entry + 💾 / ⚙ for save / manage.
            initial = str(spec.default) if spec.default is not None else ""
            return PresetCombo(scope=scope, value_kind="int", initial=initial)
        if spec.widget == "bool":
            w = QCheckBox()
            w.setChecked(bool(spec.default))
            return w
        # "hex" and "str" both use PresetCombo so the operator can save
        # frequently-replayed EVCCIDs / EVSEIDs and pick them by label.
        kind = "hex" if spec.widget == "hex" else "str"
        placeholder = (
            "hex (uppercase, even length)" if spec.widget == "hex" else ""
        )
        initial = str(spec.default) if spec.default is not None else ""
        return PresetCombo(
            scope=scope, value_kind=kind,
            initial=initial, placeholder=placeholder,
        )

    def _read_widget_value(self, w: QWidget, spec: FieldSpec) -> Any:
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, PresetCombo):
            try:
                return w.current_value()
            except ValueError:
                return w.text()
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
        if isinstance(w, PresetCombo):
            w.set_text("" if value is None else str(value))
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
