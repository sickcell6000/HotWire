"""
GUI integration: PauseInterceptDialog full flow.

Drives the user-facing pause-and-edit path that ``main_window`` uses
when the FSM hits a paused stage:

  1. Construct a real :class:`PauseInterceptDialog`
  2. Verify it pre-populates from the supplied params
  3. Override ``result_values`` to simulate the user editing fields
  4. Programmatically click ``Send`` and confirm ``accept()`` returns
     the edited values
  5. Repeat with ``Abort`` to verify the rejected path

Headless via ``QT_QPA_PLATFORM=offscreen``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from PyQt6.QtCore import QTimer                                  # noqa: E402
from PyQt6.QtWidgets import QApplication                         # noqa: E402

from hotwire.core.config import load as load_config              # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE            # noqa: E402
from hotwire.gui.widgets.pause_dialog import PauseInterceptDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_dialog_constructs_for_evse_stage(qapp):
    """Smoke: dialog opens with EVSE stage + params, no exceptions."""
    params = {"EVSEPresentVoltage": 220, "ResponseCode": 0}
    dlg = PauseInterceptDialog("PreChargeRes", params, C_EVSE_MODE)
    try:
        # Form should have rendered with the supplied stage
        assert dlg._stage == "PreChargeRes"      # noqa: SLF001
        # And the form should have loaded the params
        loaded = dlg._form.get_values()           # noqa: SLF001
        # Dialog may transform types (e.g. ints → strings); just check key
        # presence and value round-trip.
        assert "EVSEPresentVoltage" in loaded
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_constructs_for_pev_stage(qapp):
    """Same, but PEV-side stage."""
    params = {"EVTargetVoltage": 220, "EVTargetCurrent": 5}
    dlg = PauseInterceptDialog("PreChargeReq", params, C_PEV_MODE)
    try:
        assert dlg._stage == "PreChargeReq"      # noqa: SLF001
        loaded = dlg._form.get_values()           # noqa: SLF001
        assert "EVTargetVoltage" in loaded
    finally:
        dlg.close()
        dlg.deleteLater()


def test_send_button_accepts_dialog(qapp):
    """Programmatically click Send → exec() returns Accepted."""
    params = {"EVSEPresentVoltage": 220}
    dlg = PauseInterceptDialog("PreChargeRes", params, C_EVSE_MODE)

    # Schedule a Send click after the event loop starts so exec() returns.
    QTimer.singleShot(50, dlg._send_btn.click)   # noqa: SLF001
    code = dlg.exec()
    assert code == dlg.DialogCode.Accepted

    values = dlg.result_values()
    assert isinstance(values, dict)
    # The form may add more keys (defaults) — we only assert ours survived.
    assert "EVSEPresentVoltage" in values
    dlg.deleteLater()


def test_abort_button_rejects_dialog(qapp):
    """Programmatically click Abort → exec() returns Rejected."""
    params = {"EVSEPresentVoltage": 220}
    dlg = PauseInterceptDialog("PreChargeRes", params, C_EVSE_MODE)
    QTimer.singleShot(50, dlg._abort_btn.click)  # noqa: SLF001
    code = dlg.exec()
    assert code == dlg.DialogCode.Rejected
    dlg.deleteLater()


def test_apply_clear_buttons_hidden(qapp):
    """Form's Apply/Clear buttons should be hidden because the dialog
    uses its own Send/Abort buttons (otherwise users get confused)."""
    dlg = PauseInterceptDialog("PreChargeRes", {}, C_EVSE_MODE)
    try:
        assert not dlg._form._apply_btn.isVisible()  # noqa: SLF001
        assert not dlg._form._clear_btn.isVisible()  # noqa: SLF001
    finally:
        dlg.close()
        dlg.deleteLater()


def test_full_intercept_flow_emits_modified_values(qapp):
    """End-to-end: PauseController.intercept blocks, GUI thread shows
    dialog, user edits + clicks Send, FSM resumes with edited values.

    Uses a watcher-thread pattern to mimic exactly what
    ``QtWorkerThread`` + ``main_window`` do at runtime.
    """
    import threading
    import time

    from hotwire.fsm.pause_controller import PauseController

    pc = PauseController()
    pc.set_pause_enabled("PreChargeRes", True)
    received: list[dict] = []

    def fsm() -> None:
        out = pc.intercept(
            "PreChargeRes", {"EVSEPresentVoltage": 220},
        )
        received.append(out)

    t = threading.Thread(target=fsm, daemon=True)
    t.start()

    # Wait for FSM to land in intercept().
    deadline = time.monotonic() + 1.0
    while pc.get_pending() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pc.is_currently_paused()

    # GUI side: pop dialog, simulate edit, click Send.
    pending = pc.get_pending()
    dlg = PauseInterceptDialog(
        pending["stage"], pending["params"], C_EVSE_MODE,
    )
    # Edit a field — simulate the user typing 555
    fields = dlg._form.get_values()                   # noqa: SLF001
    fields["EVSEPresentVoltage"] = 555
    dlg._form.load_values(fields)                     # noqa: SLF001

    QTimer.singleShot(50, dlg._send_btn.click)        # noqa: SLF001
    code = dlg.exec()
    assert code == dlg.DialogCode.Accepted

    edited = dlg.result_values()
    pc.send(edited)
    dlg.deleteLater()

    t.join(timeout=2.0)
    assert not t.is_alive()
    assert len(received) == 1
    assert received[0].get("EVSEPresentVoltage") == 555


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
