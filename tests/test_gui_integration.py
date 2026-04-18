"""
Integration test: the real ``QtWorkerThread`` + real ``HotWireWorker`` on
both EVSE and PEV sides in the same Python process, verifying that:

  1. The full DIN 70121 handshake completes under QThread orchestration.
  2. A PauseController override actually changes the wire-level message.

This catches bugs the pure-Qt smoke tests can't, e.g. threading deadlocks
between QThread and the FSM's ``threading.Event``-based pause machinery.

Run with:

    python tests/test_gui_integration.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "HOTWIRE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "config" / "hotwire.ini"),
)

from hotwire.core.config import load as load_config  # noqa: E402

load_config()

from hotwire.core.modes import C_EVSE_MODE, C_PEV_MODE  # noqa: E402
from hotwire.fsm import PauseController  # noqa: E402
from hotwire.gui.signals import Signals  # noqa: E402
from hotwire.gui.worker_thread import QtWorkerThread  # noqa: E402


def test_full_session_under_qthread(timeout_s: float = 15.0) -> bool:
    """Spawn EVSE + PEV QtWorkerThreads, expect CurrentDemand within timeout."""
    app = QApplication.instance() or QApplication(sys.argv)

    evse_signals = Signals()
    pev_signals = Signals()
    evse_pause = PauseController()
    pev_pause = PauseController()

    # Override EVSE side: PEV will request 777 V precharge, we want to
    # confirm the PEV side actually sends that EVTargetVoltage.
    pev_pause.set_override("PreChargeReq", {"EVTargetVoltage": "777"})

    evse_traces: list[str] = []
    pev_traces: list[str] = []
    evse_signals.trace_emitted.connect(
        lambda level, text: evse_traces.append(text)
    )
    pev_signals.trace_emitted.connect(
        lambda level, text: pev_traces.append(text)
    )

    evse = QtWorkerThread(
        mode=C_EVSE_MODE, is_simulation=True,
        signals=evse_signals, pause_controller=evse_pause,
    )
    pev = QtWorkerThread(
        mode=C_PEV_MODE, is_simulation=True,
        signals=pev_signals, pause_controller=pev_pause,
    )

    evse.start()
    time.sleep(2.0)  # let the server bind

    pev.start()

    deadline = time.time() + timeout_s
    saw_current_demand = False
    saw_overridden_voltage = False

    # Pump the Qt event loop so trace signals actually arrive on main thread.
    done = threading.Event()

    def poll() -> None:
        nonlocal saw_current_demand, saw_overridden_voltage
        if any("WaitForCurrentDemandRes" in t for t in pev_traces):
            saw_current_demand = True
        # The PreChargeReq encoded on the wire should contain 777.
        # The PEV trace includes "PreChargeReq: encoding command EDG_<session>_<soc>_777"
        if any("EDG_" in t and "_777" in t for t in pev_traces):
            saw_overridden_voltage = True
        if time.time() > deadline or (saw_current_demand and saw_overridden_voltage):
            done.set()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)

    app.exec()

    evse.stop(timeout_s=3.0)
    pev.stop(timeout_s=3.0)

    print(f"[test] saw CurrentDemand loop:        {saw_current_demand}")
    print(f"[test] saw overridden EVTargetVoltage=777: {saw_overridden_voltage}")
    if not saw_current_demand or not saw_overridden_voltage:
        print("--- PEV trace tail ---")
        for line in pev_traces[-15:]:
            print("  " + line)
        print("--- EVSE trace tail ---")
        for line in evse_traces[-15:]:
            print("  " + line)
        return False
    print("[PASS] Full handshake completed AND override propagated to wire.")
    return True


if __name__ == "__main__":
    ok = test_full_session_under_qthread()
    sys.exit(0 if ok else 1)
