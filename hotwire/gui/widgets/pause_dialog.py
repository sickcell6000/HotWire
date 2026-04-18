"""
Modal dialog shown when the FSM hits a paused stage.

The FSM thread is blocked in ``PauseController.intercept()`` on a
``threading.Event``. This dialog collects any edits from the user, then
calls ``pause_controller.send(params)`` or ``pause_controller.abort()``
to release the FSM.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from .stage_config import StageConfigPanel


class PauseInterceptDialog(QDialog):
    """Block-and-edit dialog for a paused stage."""

    def __init__(
        self,
        stage: str,
        params: dict[str, Any],
        mode: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pause intercept — {stage}")
        self.setModal(True)
        self.resize(500, 400)

        self._stage = stage
        self._mode = mode

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            f"FSM is paused before sending <b>{stage}</b>.\n"
            "Edit the parameters below, then press <b>Send</b> to release.\n"
            "<b>Abort</b> releases with the default (unmodified) values."
        ))

        self._form = StageConfigPanel(mode, self)
        # Hide the Apply/Clear buttons — they don't apply here; we use OK/Cancel.
        self._form._apply_btn.hide()
        self._form._clear_btn.hide()
        self._form.set_stage(stage)
        self._form.load_values(params)
        root.addWidget(self._form, 1)

        buttons = QDialogButtonBox()
        self._send_btn = buttons.addButton(
            "Send", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._abort_btn = buttons.addButton(
            "Abort", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._send_btn.clicked.connect(self.accept)
        self._abort_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def result_values(self) -> dict[str, Any]:
        """Read the edited values back out (only meaningful if accept())."""
        return self._form.get_values()
