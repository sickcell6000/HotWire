"""pytest-qt smoke test: construct the main window, exercise signal wiring.

These tests do NOT start the real ``HotWireWorker`` — that would open
sockets and pollute CI. Instead they validate the widgets can be built in
both modes, the signal hub delivers messages to the trace log on the main
thread, and the override / pause flows reach the PauseController.

Run with:

    pytest tests/test_gui_smoke.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.core.config import load as load_config  # noqa: E402
from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402

load_config()

from hotwire.gui.main_window import HotWireMainWindow  # noqa: E402


@pytest.mark.parametrize("mode", [C_EVSE_MODE, C_PEV_MODE])
def test_main_window_constructs(qtbot, mode):
    w = HotWireMainWindow(mode=mode, is_simulation=True)
    qtbot.addWidget(w)
    assert w.stage_config is not None
    assert w.trace_log is not None
    assert w.stage_nav is not None


def test_trace_signal_reaches_log(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    w.signals.trace_emitted.emit("INFO", "hello world")
    # Force the batched flush that normally fires every 50 ms.
    w.trace_log._flush()
    assert "hello world" in w.trace_log.toPlainText()


def test_status_signal_updates_panel(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    w.signals.status_changed.emit("evseState", "Running")
    assert w.status_panel._labels["evseState"].text() == "Running"
    assert w.status_panel._primary_label.text() == "Running"


def test_apply_override_flows_to_pause_controller(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    # Select the PreChargeRes stage, then apply an override with a known value.
    # After Checkpoint 8 the PreChargeRes schema exposes 7 fields; applying
    # overrides with a single user-edited value still collects every field
    # from the form into the override dict (using schema defaults for the
    # untouched ones).
    w.stage_config.set_stage("PreChargeRes")
    w.stage_config.load_values({"EVSEPresentVoltage": 777})
    w.stage_config._on_apply()

    got = w.pause_controller.get_override("PreChargeRes")
    assert got is not None
    assert got["EVSEPresentVoltage"] == 777
    # The other 6 fields must also be present — otherwise a PauseController
    # intercept that only sees a partial override could drop mandatory
    # DIN DC_EVSEStatus fields on the wire.
    for k in ("ResponseCode", "IsolationStatusUsed", "IsolationStatus",
              "EVSEStatusCode", "NotificationMaxDelay", "EVSENotification"):
        assert k in got, f"override is missing {k}"


def test_pause_toggle_flows_to_pause_controller(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    w.stage_nav.pause_toggled.emit("PreChargeRes", True)
    assert w.pause_controller.is_paused_for("PreChargeRes")
    w.stage_nav.pause_toggled.emit("PreChargeRes", False)
    assert not w.pause_controller.is_paused_for("PreChargeRes")


def test_pause_all_sets_every_stage(qtbot):
    w = HotWireMainWindow(mode=C_PEV_MODE, is_simulation=True)
    qtbot.addWidget(w)
    from hotwire.gui.stage_schema import schema_for

    w._pause_all()
    for stage in schema_for(C_PEV_MODE).keys():
        assert w.pause_controller.is_paused_for(stage), stage


def test_clear_all_overrides(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    w.pause_controller.set_override("PreChargeRes", {"EVSEPresentVoltage": 1})
    w.pause_controller.set_override("SessionSetupRes", {"EVSEID": "AABB"})
    w._clear_all_overrides()
    assert w.pause_controller.get_override("PreChargeRes") is None
    assert w.pause_controller.get_override("SessionSetupRes") is None


def test_msg_decoded_signal_populates_tree_view(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    assert w.tree_view.rx_count() == 0
    assert w.tree_view.tx_count() == 0

    w.signals.msg_decoded.emit("rx", "SessionSetupReq", {"EVCCID": "d83add22f182"})
    w.signals.msg_decoded.emit("tx", "SessionSetupRes", {"EVSEID": "ZZDEFLT", "ResponseCode": 1})
    w.signals.msg_decoded.emit("rx", "PreChargeReq", {"EVTargetVoltage": 350})

    assert w.tree_view.rx_count() == 2
    assert w.tree_view.tx_count() == 1


def test_clear_trees_button_empties_tree_view(qtbot):
    w = HotWireMainWindow(mode=C_EVSE_MODE, is_simulation=True)
    qtbot.addWidget(w)
    w.signals.msg_decoded.emit("rx", "Req1", {"k": 1})
    w.signals.msg_decoded.emit("tx", "Res1", {"k": 2})
    assert w.tree_view.rx_count() == 1 and w.tree_view.tx_count() == 1
    w._clear_trees()
    assert w.tree_view.rx_count() == 0
    assert w.tree_view.tx_count() == 0


def test_reset_fsm_noop_without_worker(qtbot):
    """_reset_fsm must be safe to call when no worker thread is running."""
    w = HotWireMainWindow(mode=C_PEV_MODE, is_simulation=True)
    qtbot.addWidget(w)
    # Should be a silent no-op; must not raise.
    w._reset_fsm()
