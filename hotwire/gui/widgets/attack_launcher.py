"""
Attack launcher — modal dialog to install a HotWire ``Attack`` playbook
into the live :class:`PauseController`.

The dialog auto-discovers every :class:`Attack` subclass exported from
:mod:`hotwire.attacks`, filters to those whose ``.mode`` matches the
running window, and builds a parameter form by introspecting the
subclass's :func:`dataclasses.fields`. That way a new playbook picked
up by the :mod:`hotwire.attacks` ``__all__`` list appears in the
dropdown automatically with no GUI code changes.

Typical flow:

1. Operator opens ``Attacks → Launch attack…``
2. Dialog populates dropdown from `AVAILABLE_ATTACKS`
3. Picks an attack → form rebuilds for its dataclass fields
4. Fills in values → clicks **Apply**
5. Dialog instantiates the Attack (catching ``ValueError`` from
   dataclass ``__post_init__`` validators → shows QMessageBox)
6. ``attack.apply(pause_controller)`` installs the overrides
7. Dialog closes; main window reflects the new state in trace log +
   stage-nav override indicators
"""
from __future__ import annotations

import dataclasses
from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...attacks import AutochargeImpersonation, ForcedDischarge
from ...attacks.base import Attack
from ...core.modes import C_EVSE_MODE, C_PEV_MODE
from ...fsm.pause_controller import PauseController
from .preset_combo import PresetCombo


# Registry of playbooks the launcher knows about. New Attack subclasses
# added to ``hotwire.attacks.__init__.__all__`` should also be added
# here — the one-line import at the top of this file makes them
# importable, and the tuple below makes them selectable.
#
# (class, mode) — we carry the mode separately so the launcher never
# has to default-construct an Attack (some validators raise on empty
# default fields, e.g. Autocharge rejects an empty EVCCID).
ATTACK_REGISTRY: tuple[tuple[type[Attack], int], ...] = (
    (AutochargeImpersonation, C_PEV_MODE),
    (ForcedDischarge, C_EVSE_MODE),
)

# Back-compat alias — some tests + old call sites use the flat tuple.
AVAILABLE_ATTACKS: tuple[type[Attack], ...] = tuple(
    cls for cls, _mode in ATTACK_REGISTRY
)

_MODE_LABEL = {C_EVSE_MODE: "EVSE", C_PEV_MODE: "PEV"}

# Dataclass fields common to every Attack subclass — skipped when
# building the operator form (they're populated by ``__post_init__``).
_BASE_FIELDS = {"name", "mode", "description", "overrides"}


class AttackLauncherDialog(QDialog):
    """Modal picker + form for installing an ``Attack`` playbook."""

    # Emitted AFTER the attack is installed into pause_controller.
    attack_launched = pyqtSignal(str)          # attack name

    def __init__(
        self,
        mode: int,
        pause_controller: PauseController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("HotWire — Launch attack")
        self.setModal(True)
        self.resize(520, 420)

        self._mode = mode
        self._pause_controller = pause_controller

        # Only show attacks compatible with the current window mode.
        self._candidates: list[type[Attack]] = [
            cls for cls, cls_mode in ATTACK_REGISTRY
            if cls_mode == mode
        ]

        self._build_layout()
        self._wire()
        if self._candidates:
            self._on_attack_changed(0)

    # ---- layout ----------------------------------------------------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)

        mode_label = _MODE_LABEL.get(self._mode, "?")
        header = QLabel(
            f"Current mode: <b>{mode_label}</b>. "
            "Only playbooks compatible with this mode are shown below. "
            "Launching an attack installs its <i>Overrides</i> on the "
            "live PauseController — every subsequent outbound message "
            "from the FSM carries the attacker-shaped fields."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        self._combo = QComboBox()
        if self._candidates:
            for cls in self._candidates:
                self._combo.addItem(cls.__name__, userData=cls)
        else:
            self._combo.addItem(
                f"No attacks available for {mode_label} mode", userData=None
            )
            self._combo.setEnabled(False)
        root.addWidget(self._combo)

        self._description = QLabel("")
        self._description.setWordWrap(True)
        self._description.setStyleSheet("color: #555; padding: 4px;")
        root.addWidget(self._description)

        self._form_host = QWidget()
        self._form_layout = QFormLayout(self._form_host)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._form_host, 1)

        # Secondary actions.
        aux_row = QHBoxLayout()
        self._clear_all_btn = QPushButton("Clear all overrides")
        self._clear_all_btn.setToolTip(
            "Remove every override currently installed on the "
            "PauseController — not just the ones from this attack."
        )
        aux_row.addWidget(self._clear_all_btn)
        aux_row.addStretch(1)
        root.addLayout(aux_row)

        # Primary actions.
        buttons = QDialogButtonBox()
        self._apply_btn = buttons.addButton(
            "Apply", QDialogButtonBox.AcceptRole
        )
        self._cancel_btn = buttons.addButton(
            "Cancel", QDialogButtonBox.RejectRole
        )
        root.addWidget(buttons)

        self._apply_btn.setEnabled(bool(self._candidates))

        # Slot containers populated in _rebuild_form.
        self._field_widgets: dict[str, QWidget] = {}

    def _wire(self) -> None:
        self._combo.currentIndexChanged.connect(self._on_attack_changed)
        self._apply_btn.clicked.connect(self._on_apply)
        self._cancel_btn.clicked.connect(self.reject)
        self._clear_all_btn.clicked.connect(self._on_clear_all)

    # ---- slots -----------------------------------------------------

    def _on_attack_changed(self, index: int) -> None:
        cls = self._combo.itemData(index)
        if cls is None:
            self._description.setText("")
            self._rebuild_form([])
            return

        # Build a placeholder instance via object.__new__ (see
        # _class_mode) so we can read ``name`` / ``description``
        # without tripping __post_init__ validation.
        placeholder_desc = ""
        placeholder_name = cls.__name__
        try:
            probe = _probe_instance(cls)
            placeholder_name = (probe.name if probe else cls.__name__) or cls.__name__
            placeholder_desc = probe.description if probe else ""
        except Exception:                                        # noqa: BLE001
            pass

        self._description.setText(
            f"<b>{placeholder_name}</b><br>{placeholder_desc or cls.__doc__ or ''}"
        )
        fields = [
            f for f in dataclasses.fields(cls) if f.name not in _BASE_FIELDS
        ]
        self._rebuild_form(fields)

    def _on_apply(self) -> None:
        cls = self._combo.currentData()
        if cls is None:
            return
        kwargs = self._collect_kwargs()
        try:
            attack = cls(**kwargs)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self, "Attack validation failed", f"{type(exc).__name__}: {exc}"
            )
            return
        attack.apply(self._pause_controller)
        self.attack_launched.emit(attack.name)
        self.accept()

    def _on_clear_all(self) -> None:
        self._pause_controller.clear_override()
        QMessageBox.information(
            self, "Overrides cleared",
            "All pause-controller overrides have been cleared.",
        )
        # Signal with a sentinel name so the main window can log it.
        self.attack_launched.emit("<cleared all overrides>")
        self.accept()

    # ---- form construction ----------------------------------------

    def _rebuild_form(self, fields: list[dataclasses.Field]) -> None:
        # Remove old rows.
        while self._form_layout.rowCount():
            self._form_layout.removeRow(0)
        self._field_widgets.clear()

        attack_cls = self._combo.currentData()
        scope_prefix = attack_cls.__name__ if attack_cls else ""
        for f in fields:
            scope = f"{scope_prefix}.{f.name}"
            widget = _build_widget_for_field(f, scope=scope)
            # Surface field-level operator guidance on hover. The hint
            # comes from the dataclass `field(metadata={"hint": ...})`
            # — see ForcedDischarge for the canonical example. Tooltips
            # that take a multi-line string render with line breaks
            # preserved by Qt, so the source string can include raw \n.
            hint = f.metadata.get("hint")
            if hint:
                widget.setToolTip(str(hint))
            self._field_widgets[f.name] = widget
            self._form_layout.addRow(f.name, widget)

    def _collect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for name, widget in self._field_widgets.items():
            kwargs[name] = _read_widget_value(widget)
        return kwargs


# --- helpers ---------------------------------------------------------


def _probe_instance(cls: type[Attack]) -> Attack | None:
    """Create a dataclass-defaults instance of ``cls`` without running
    any validators that might raise. Returns ``None`` on failure."""
    try:
        instance = object.__new__(cls)
        for f in dataclasses.fields(cls):
            if f.default is not dataclasses.MISSING:
                setattr(instance, f.name, f.default)
            elif f.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
                setattr(instance, f.name, f.default_factory())
        try:
            instance.__post_init__()
        except Exception:                                        # noqa: BLE001
            pass
        return instance
    except Exception:                                            # noqa: BLE001
        return None


def _class_mode(cls: type[Attack]) -> int:
    """Return the ``.mode`` of an Attack subclass without triggering
    its :meth:`__post_init__` validator.

    Most Attack subclasses set ``self.mode = C_{PEV,EVSE}_MODE`` inside
    ``__post_init__``, but calling the constructor with default values
    tends to raise (e.g. Autocharge rejects an empty EVCCID). We bypass
    construction by using :func:`object.__new__` and then running the
    exact same ``__post_init__`` logic — but wrapped so a validation
    error still lets us pluck ``.mode`` out.
    """
    try:
        instance = object.__new__(cls)
        # Populate dataclass fields with their defaults so
        # __post_init__ has something to compute on. Some Attacks
        # reference self.voltage / self.current etc. before setting
        # self.mode, so we need a best-effort default first.
        for f in dataclasses.fields(cls):
            if f.default is not dataclasses.MISSING:
                setattr(instance, f.name, f.default)
            elif f.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
                setattr(instance, f.name, f.default_factory())
        # Now try __post_init__. If it raises (bad validator), catch
        # and fall back to reading the class-level default if any, or
        # whatever the instance picked up before the exception.
        try:
            instance.__post_init__()
        except Exception:                                        # noqa: BLE001
            pass
        mode = getattr(instance, "mode", 0)
        return int(mode) if mode else 0
    except Exception:                                            # noqa: BLE001
        return 0


def _build_widget_for_field(
    f: dataclasses.Field,
    *,
    scope: str = "",
) -> QWidget:
    """Pick a QWidget that reasonably represents ``f``'s declared type.

    String / int fields use the :class:`PresetCombo` so the operator
    can pick a saved preset (with note tooltip) or type a custom value
    AND save it for next time. Booleans stay as plain checkboxes since
    there's nothing to preset.
    """
    annotation = f.type if isinstance(f.type, type) else None
    default = f.default if f.default is not dataclasses.MISSING else None

    # Booleans: no preset support — single checkbox is the natural UI.
    if annotation is bool or isinstance(default, bool):
        box = QCheckBox()
        if isinstance(default, bool):
            box.setChecked(default)
        return box

    # Integer: PresetCombo with int kind. Falls back to QSpinBox if no
    # scope is supplied (e.g. a unit test calls the helper directly).
    if annotation is int or isinstance(default, int):
        if scope:
            initial = str(default) if default is not None else ""
            return PresetCombo(
                scope=scope,
                value_kind="int",
                initial=initial,
            )
        spin = QSpinBox()
        spin.setRange(-1_000_000, 1_000_000)
        spin.setValue(int(default) if default is not None else 0)
        return spin

    # String / hex: PresetCombo with str kind, hex monospace if the
    # field name hints at a hex payload (heuristic — `evccid` / `*id`).
    if scope:
        kind = "hex" if _looks_like_hex_field(f.name) else "str"
        initial = str(default) if default is not None else ""
        placeholder = (
            "12 hex characters (e.g. d83add22f182)"
            if kind == "hex" else ""
        )
        return PresetCombo(
            scope=scope,
            value_kind=kind,
            initial=initial,
            placeholder=placeholder,
        )

    # Unscoped fallback — plain QLineEdit (for unit tests that call
    # this helper without a scope).
    edit = QLineEdit()
    if default is not None and default is not dataclasses.MISSING:
        edit.setText(str(default))
    return edit


def _looks_like_hex_field(name: str) -> bool:
    """Heuristic: which dataclass field names hold hex byte strings."""
    lower = name.lower()
    return any(token in lower for token in ("evccid", "evseid", "mac", "nid", "nmk"))


def _read_widget_value(widget: QWidget) -> Any:
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, PresetCombo):
        try:
            return widget.current_value()
        except ValueError:
            # Operator typed something that doesn't parse for the field's
            # declared kind (e.g. "abc" in an int field). Return the raw
            # text so the Attack validator can produce a useful error
            # message instead of crashing here.
            return widget.text()
    if isinstance(widget, QLineEdit):
        return widget.text()
    # Unknown widget type — return its string representation; Attack
    # validators will reject it if it's wrong.
    return str(widget)
