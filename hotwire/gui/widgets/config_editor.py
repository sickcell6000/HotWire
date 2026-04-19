"""
hotwire.ini visual editor.

Reads the in-memory :class:`configparser.ConfigParser` instance via
:func:`hotwire.core.config.load`, infers a widget per key based on the
current value's shape (bool / int / enum / string), lets the operator
edit, and calls :func:`hotwire.core.config.save` on confirm.

Known limitation: :mod:`configparser.write` drops comments. The
operator's action on **Save** is surfaced with a warning so no-one
overwrites the hand-commented ``config/hotwire.ini`` by accident
without knowing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import config as config_mod
from .interface_picker import InterfacePickerCombo


# Config keys that should be rendered with the network-interface picker.
_INTERFACE_KEYS = frozenset({
    "eth_interface", "eth_windows_interface_name",
})


# Known enum-valued keys → option list.
ENUM_KEYS = {
    "mode": ["EvseMode", "PevMode", "ListenMode"],
    "protocol_preference": [
        "din_only", "iso15118_2_only", "prefer_din", "prefer_iso",
    ],
    "charge_parameter_backend": ["none", "rest_api", "static"],
    "analog_input_device": ["none", "dieter", "labjack"],
    "digital_output_device": ["none", "dieter", "labjack"],
}

# Group titles → (header, [key...]). Keys not in any group fall into "Misc".
KEY_GROUPS: list[tuple[str, list[str]]] = [
    ("Operating mode", [
        "mode", "is_simulation_without_modems", "protocol_preference",
    ]),
    ("Hardware interface", [
        "eth_interface", "eth_windows_interface_name",
        "tcp_port_use_well_known", "tcp_port_alternative",
        "tcp_port_15118_compliant",
    ]),
    ("EVSE behaviour", [
        "evse_simulate_precharge",
        "use_evsepresentvoltage_for_precharge_end",
        "use_physical_inlet_voltage_during_chargeloop",
        "u_delta_max_for_end_of_precharge",
        "charge_target_voltage",
        "allow_new_session_after_stopping",
        "exit_on_session_end",
        "exit_if_no_local_link_address_is_found",
    ]),
    ("Peripherals", [
        "digital_output_device", "analog_input_device",
        "serial_port", "serial_baud",
    ]),
    ("Logging / callbacks", [
        "log_the_evse_mac_to_file", "udp_syslog_enable",
        "logging_url", "soc_callback_enabled",
        "soc_callback_endpoint",
    ]),
    ("Simulation / demo", [
        "light_bulb_demo", "soc_simulation", "testsuite_enable",
        "soc_fallback_energy_capacity",
        "soc_fallback_energy_capacity_wh",
        "charge_parameter_backend",
    ]),
]

_BOOL_STRINGS = {"true", "false", "yes", "no", "1", "0", "on", "off"}


class ConfigEditor(QWidget):
    """Dockable form editor for hotwire.ini."""

    config_saved = pyqtSignal(str)                   # saved path

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._widgets: dict[str, QWidget] = {}
        self._path: Optional[Path] = None
        self._build_layout()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self._path_label = QLabel("<i>not loaded</i>")
        root.addWidget(self._path_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        self._reload_btn = QPushButton("Reload")
        self._save_btn = QPushButton("Save")
        self._save_as_btn = QPushButton("Save as…")
        btn_row.addWidget(self._reload_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._save_as_btn)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        self._reload_btn.clicked.connect(self.reload)
        self._save_btn.clicked.connect(self._on_save)
        self._save_as_btn.clicked.connect(self._on_save_as)

        self.reload()

    # ---- data flow -------------------------------------------------

    def reload(self) -> None:
        cfg = config_mod.load()
        self._path = config_mod._config_path            # noqa: SLF001
        self._path_label.setText(
            f"<b>Editing:</b> {self._path}"
            if self._path else "<i>not loaded</i>"
        )

        # Clear old groups.
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._widgets.clear()

        seen_keys: set[str] = set()
        for group_name, keys in KEY_GROUPS:
            box = QGroupBox(group_name)
            form = QFormLayout(box)
            for key in keys:
                if "general" not in cfg.sections() or key not in cfg["general"]:
                    continue
                value = cfg["general"][key]
                widget = _build_widget(key, value)
                self._widgets[key] = widget
                form.addRow(key, widget)
                seen_keys.add(key)
            if form.rowCount() > 0:
                self._body_layout.addWidget(box)

        # "Misc" group for any key we didn't explicitly categorize.
        if "general" in cfg.sections():
            other_keys = [k for k in cfg["general"] if k not in seen_keys]
            if other_keys:
                misc_box = QGroupBox("Misc")
                form = QFormLayout(misc_box)
                for key in other_keys:
                    widget = _build_widget(key, cfg["general"][key])
                    self._widgets[key] = widget
                    form.addRow(key, widget)
                self._body_layout.addWidget(misc_box)

        self._body_layout.addStretch(1)

    def _collect_values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, widget in self._widgets.items():
            # Interface picker first (subclass of QWidget, not any of the below).
            if isinstance(widget, InterfacePickerCombo):
                out[key] = widget.current_interface()
            elif isinstance(widget, QCheckBox):
                out[key] = "true" if widget.isChecked() else "false"
            elif isinstance(widget, QSpinBox):
                out[key] = str(widget.value())
            elif isinstance(widget, QComboBox):
                out[key] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                out[key] = widget.text()
        return out

    # ---- slots ----------------------------------------------------

    def _on_save(self) -> None:
        confirm = QMessageBox.question(
            self, "Save config",
            "Saving will rewrite the INI and lose human-authored "
            "comments (configparser limitation). Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._persist(None)

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save config as…", "hotwire.ini",
            "INI files (*.ini);;All files (*)",
        )
        if path:
            self._persist(Path(path))

    def _persist(self, path: Optional[Path]) -> None:
        values = self._collect_values()
        for key, value in values.items():
            config_mod.setConfigValue(key, value)
        try:
            written = config_mod.save(path)
        except Exception as exc:                                  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._path_label.setText(f"<b>Saved:</b> {written}")
        self.config_saved.emit(str(written))


# --- widget factory ----------------------------------------------------


def _build_widget(key: str, value: str) -> QWidget:
    """Pick a widget based on what the current value looks like."""
    # Interface keys get a ranked combo box instead of a QLineEdit.
    if key in _INTERFACE_KEYS:
        return InterfacePickerCombo(initial=value, show_refresh=True)

    if key in ENUM_KEYS:
        combo = QComboBox()
        combo.addItems(ENUM_KEYS[key])
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    if value.lower() in _BOOL_STRINGS:
        cb = QCheckBox()
        cb.setChecked(value.lower() in ("true", "yes", "1", "on"))
        return cb

    # Integer-looking?
    try:
        int_value = int(value)
        spin = QSpinBox()
        spin.setRange(-1_000_000, 1_000_000)
        spin.setValue(int_value)
        return spin
    except ValueError:
        pass

    # Plain text.
    edit = QLineEdit(value)
    return edit
