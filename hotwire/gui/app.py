"""
QApplication bootstrap.

Usage from Python:

    from hotwire.gui.app import run_gui
    from hotwire.core.modes import C_EVSE_MODE
    run_gui(mode=C_EVSE_MODE, is_simulation=True)

Or use ``scripts/run_gui.py`` for the CLI surface.
"""
from __future__ import annotations

import sys
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from ..attacks.base import Attack
from ..core.modes import C_EVSE_MODE, C_PEV_MODE
from .main_window import HotWireMainWindow


class ModeDialog(QDialog):
    """Startup prompt: choose EVSE or PEV."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("HotWire — Select mode")
        self.resize(360, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Select operating mode</b><br>"
            "Run two HotWire processes (one EVSE, one PEV) to validate "
            "the full DIN 70121 session over loopback."
        ))
        self._evse_rb = QRadioButton("EVSE — charging-station emulator")
        self._pev_rb = QRadioButton("PEV — electric-vehicle emulator")
        self._evse_rb.setChecked(True)
        layout.addWidget(self._evse_rb)
        layout.addWidget(self._pev_rb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_mode(self) -> int:
        return C_EVSE_MODE if self._evse_rb.isChecked() else C_PEV_MODE


def run_gui(
    mode: Optional[int] = None,
    is_simulation: bool = True,
    attack: Optional[Attack] = None,
) -> int:
    """Create the QApplication and show the main window.

    If ``mode`` is None, shows :class:`ModeDialog` first. If ``attack`` is
    provided, its ``mode`` must match (or override) and its overrides are
    applied to the window's :class:`PauseController` before the worker
    starts, so the very first outbound message already carries the
    attacker-shaped fields.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    if attack is not None and mode is None:
        mode = attack.mode
    elif attack is not None and mode != attack.mode:
        raise ValueError(
            f"attack requires mode={attack.mode} but caller specified mode={mode}"
        )

    if mode is None:
        dlg = ModeDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0
        mode = dlg.selected_mode()

    window = HotWireMainWindow(mode=mode, is_simulation=is_simulation)
    if attack is not None:
        attack.apply(window.pause_controller)
        # Reflect override markers in the StageNav so the operator sees
        # which stages the attack touched.
        for stage in attack.overrides:
            window.stage_nav.set_override_indicator(stage, True)
        window.signals.trace_emitted.emit(
            "SUCCESS", f"[attack] applied: {attack.name}"
        )
    window.show()
    return app.exec()
