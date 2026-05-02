"""
GUI worker-reuse regression test.

The phase 7 stress run on real hardware caught a bug where calling
``HotWireWorker.shutdown()`` left the ``fsmEvse.Tcp`` listening socket
bound; subsequent worker constructions in the same Python process
got a fresh socket fd but the kernel kept routing connections to
the dead one. The fix shipped in ``e4b7ee2`` (HotWireWorker.shutdown
now closes the TCP server explicitly).

This test confirms the GUI's wrapper — :class:`QtWorkerThread` —
also calls the fixed ``shutdown()`` and that three consecutive
``start()`` / ``stop()`` cycles all complete a full DIN session.
Without the GUI fix (``QtWorkerThread.stop()`` calling
``self._worker.shutdown()``) only the first cycle would succeed.

Runs in headless Qt mode (``QT_QPA_PLATFORM=offscreen``) so it works
on CI / SSH without a display server.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


def _docker_daemon_reachable() -> bool:
    """True iff `docker info` succeeds. Just having the docker CLI on
    PATH is not enough — Ubuntu installs ship the client even when no
    daemon is running."""
    try:
        return subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# Two QtWorkerThread instances inside one Python process race against
# each other on the IPv6 loopback inside some Linux VM kernels — they
# get stuck before reaching CurrentDemand even though the protocol
# logic is fine (F2 sim_loopback.sh, which uses two subprocesses,
# always passes on the same machine). When Docker is available we
# run inside the CI container which doesn't show the race, so we
# only enforce this regression then.
_REQUIRES_DOCKER = pytest.mark.skipif(
    not _docker_daemon_reachable(),
    reason="Same-process Qt-worker timing is flaky outside the Docker CI env; "
           "run via `docker compose run --rm hotwire-ci` to exercise it.",
)

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


def _run_one_cycle(cycle_idx: int, timeout_s: float = 15.0) -> dict:
    """One start→V2G→stop cycle. Returns metrics dict."""
    app = QApplication.instance() or QApplication(sys.argv)

    evse_signals = Signals()
    pev_signals = Signals()
    evse_pause = PauseController()
    pev_pause = PauseController()

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

    t0 = time.monotonic()
    evse.start()
    time.sleep(1.5)
    pev.start()

    deadline = time.time() + timeout_s
    saw_current_demand = False
    done = threading.Event()

    def poll() -> None:
        nonlocal saw_current_demand
        if any("WaitForCurrentDemandRes" in t for t in pev_traces):
            saw_current_demand = True
        if time.time() > deadline or saw_current_demand:
            done.set()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(150)

    app.exec_()
    timer.stop()

    elapsed = time.monotonic() - t0

    evse.stop(timeout_s=3.0)
    pev.stop(timeout_s=3.0)

    return {
        "cycle": cycle_idx,
        "elapsed_s": round(elapsed, 2),
        "saw_current_demand": saw_current_demand,
        "evse_trace_count": len(evse_traces),
        "pev_trace_count": len(pev_traces),
    }


@_REQUIRES_DOCKER
def test_three_consecutive_start_stop_cycles():
    """Run three start/stop cycles back-to-back; all three must reach
    CurrentDemand. Pre-fix only cycle 1 would; later cycles' PEV
    couldn't TCP-connect to a half-dead EVSE listening socket."""
    results = []
    for i in range(1, 4):
        rec = _run_one_cycle(i)
        results.append(rec)
        # Don't share QApplication state between cycles — modest
        # inter-cycle pause lets any background SLAC/SDP timers wind
        # down. Real bug shows up regardless of pause length.
        time.sleep(1.0)

    print("\nCycle results:")
    for r in results:
        print(f"  cycle {r['cycle']}: "
              f"elapsed={r['elapsed_s']:>5.1f}s  "
              f"cd_seen={r['saw_current_demand']}  "
              f"traces={r['pev_trace_count']}/{r['evse_trace_count']}")

    failed = [r for r in results if not r["saw_current_demand"]]
    assert not failed, (
        f"{len(failed)}/3 cycles failed to reach CurrentDemand: "
        f"{[r['cycle'] for r in failed]}. "
        f"Pre-fix this regression hits cycle 2+ — confirm worker.shutdown() "
        f"is being invoked from QtWorkerThread.stop()."
    )


if __name__ == "__main__":
    test_three_consecutive_start_stop_cycles()
    print("[PASS] all three cycles completed")
