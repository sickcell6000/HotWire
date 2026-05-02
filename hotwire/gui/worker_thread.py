"""
QThread wrapping ``HotWireWorker`` so the FSM ticks at 30 ms off the UI thread.

Ownership:
  * The ``QtWorkerThread`` constructs and owns the ``HotWireWorker``.
  * Trace / status callbacks are bound to lambdas that emit queued signals.
  * The :class:`PauseController` (override-only — interactive pause was
    removed because it conflicts with DIN 70121 §9.6 spec timeouts on
    real vehicles) is shared with the FSM and consulted on every
    outbound message via ``intercept(stage, params)``.

Lifecycle:
  * ``start()`` spawns the thread. ``run()`` loops until ``_stop`` is set.
  * ``stop()`` sets the flag and calls ``wait(timeout)`` to join.
"""
from __future__ import annotations

import threading
import time

from PyQt5.QtCore import QThread

import datetime as _dt
from pathlib import Path

from ..core.session_log import SessionLogger, TeeObserver
from ..core.worker import HotWireWorker
from ..fsm import PauseController
from ..fsm.message_observer import MessageObserver
from .signals import Signals


class _SignalObserver:
    """Adapts the plain-Python :class:`MessageObserver` protocol onto a Qt
    ``pyqtSignal`` so the GUI's widgets can react to every decoded Req/Res
    without the FSMs knowing Qt exists."""

    def __init__(self, signals: Signals) -> None:
        self._signals = signals

    def on_message(self, direction: str, msg_name: str, params: dict) -> None:
        self._signals.msg_decoded.emit(direction, msg_name, params)


class QtWorkerThread(QThread):
    """Runs ``HotWireWorker.mainfunction()`` on a 30 ms cadence."""

    TICK_S = 0.030

    def __init__(
        self,
        mode: int,
        is_simulation: bool,
        signals: Signals,
        pause_controller: PauseController,
        session_log_dir: str | Path | None = None,
    ) -> None:
        """``session_log_dir`` — if provided, every decoded Req/Res is
        persisted as JSONL under this directory (one file per session,
        timestamped). Pass ``None`` (the default) to disable persistence
        entirely, which is what the tests want to avoid polluting the
        working directory."""
        super().__init__()
        self._mode = mode
        self._is_simulation = 1 if is_simulation else 0
        self._signals = signals
        self._pause_controller = pause_controller
        self._stop = threading.Event()
        self._worker: HotWireWorker | None = None
        self._session_logger: SessionLogger | None = None
        self._session_log_dir = Path(session_log_dir) if session_log_dir else None

    # ---- callbacks bound to the worker ------------------------------

    def _trace(self, text: str) -> None:
        level = "INFO"
        # Cheap level inference; the FSM passes prefixed strings like "[PEV] ...".
        low = text.lower()
        if "error" in low or "fail" in low:
            level = "ERROR"
        elif "warn" in low:
            level = "WARNING"
        elif "[connmgr]" in low or "[workerthread]" in low:
            level = "DEBUG"
        self._signals.trace_emitted.emit(level, text)

    def _status(self, s: str, selection: str = "", *_rest) -> None:
        # The FSMs call this in two shapes — (text, selection) and
        # (text, selection, aux1, aux2). We only forward the first two.
        self._signals.status_changed.emit(selection or "generic", s)

    # ---- thread body ------------------------------------------------

    def run(self) -> None:
        self._trace("[WorkerThread] starting")
        # Build the observer stack: signal emitter (for GUI widgets) plus
        # an optional JSONL session logger (for post-hoc analysis).
        observer = _SignalObserver(self._signals)
        if self._session_log_dir is not None:
            mode_label = "EVSE" if self._mode == 2 else "PEV" if self._mode == 1 else "unknown"
            filename = (
                f"{mode_label}_"
                f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            )
            path = self._session_log_dir / filename
            self._session_logger = SessionLogger(path, mode=mode_label)
            self._trace(f"[WorkerThread] logging session to {path}")
            observer = TeeObserver(observer, self._session_logger)

        self._worker = HotWireWorker(
            callbackAddToTrace=self._trace,
            callbackShowStatus=self._status,
            mode=self._mode,
            isSimulationMode=self._is_simulation,
            pause_controller=self._pause_controller,
            message_observer=observer,
        )

        self._signals.worker_started.emit()

        last = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    self._worker.mainfunction()
                except SystemExit:
                    # ``connMgr`` calls ``sys.exit(0)`` when
                    # ``exit_on_session_end = True`` and the V2G session
                    # ends normally. In headless / phaseN-script mode
                    # that's the desired behaviour, but in the GUI it
                    # would silently take the worker thread down with
                    # us — without ``worker_stopped.emit()`` the Start
                    # button stays disabled forever and the operator
                    # can't launch a fresh session. Catch it, log a
                    # friendly trace, and break out of the run loop so
                    # the normal shutdown path runs.
                    self._trace(
                        "[WorkerThread] FSM/connMgr requested SystemExit; "
                        "stopping worker cleanly so GUI can restart"
                    )
                    break
                except Exception as e:                              # noqa: BLE001
                    self._trace(f"[WorkerThread] mainfunction raised: {e}")
                # Pace to TICK_S; skip sleep if we're already behind.
                now = time.monotonic()
                delay = self.TICK_S - (now - last)
                if delay > 0:
                    time.sleep(delay)
                last = time.monotonic()
        finally:
            # Always emit worker_stopped so the GUI re-enables the Start
            # button — even if we exited via SystemExit, KeyboardInterrupt,
            # or an unhandled exception above. Without this the lifecycle
            # callbacks desync and the operator gets a wedged GUI.
            self._signals.worker_stopped.emit()
            self._trace("[WorkerThread] stopped")

    # ---- shutdown ---------------------------------------------------

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        self.wait(int(timeout_s * 1000))
        # Release the worker's external resources (TCP server / SDP /
        # pcap RX) so a subsequent ``start()`` constructs a clean
        # worker. Without this the GUI's stop→start cycle hits the
        # same dangling-socket bug as phase7_stress (commit e4b7ee2).
        if self._worker is not None:
            try:
                self._worker.shutdown()
            except Exception as e:                                  # noqa: BLE001
                self._trace(f"[WorkerThread] worker.shutdown raised: {e}")
            self._worker = None
        if self._session_logger is not None:
            self._session_logger.close()
            self._session_logger = None
