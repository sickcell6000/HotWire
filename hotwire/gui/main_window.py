"""
HotWire main window — wires every widget to the worker thread and pause controller.

Layout (horizontal QSplitter):

    +--------------------+-------------------------+---------------------+
    | StatusPanel        |                         |  TraceLogWidget     |
    | StageNavPanel      |  StageConfigPanel       |                     |
    |                    |  (QScrollArea)          |  [Start/Pause/...]  |
    +--------------------+-------------------------+---------------------+

A modal ``PauseInterceptDialog`` pops up whenever ``signals.pause_hit``
fires while the FSM is blocked.
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.modes import C_EVSE_MODE, C_PEV_MODE
from ..fsm import PauseController
from .signals import Signals
from .stage_schema import schema_for, stage_order
from .widgets import (
    PauseInterceptDialog,
    ReqResTreeView,
    StageConfigPanel,
    StageNavPanel,
    StatusPanel,
    TraceLogWidget,
)
from .worker_thread import QtWorkerThread


MODE_LABEL = {C_EVSE_MODE: "EVSE", C_PEV_MODE: "PEV"}


class HotWireMainWindow(QMainWindow):
    """Top-level window for either EVSE or PEV mode."""

    def __init__(
        self,
        mode: int,
        is_simulation: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._is_simulation = is_simulation
        self.setWindowTitle(
            f"HotWire — {MODE_LABEL.get(mode, '?')}"
            f" ({'simulation' if is_simulation else 'hardware'})"
        )
        self.resize(1400, 800)

        self.signals = Signals()
        self.pause_controller = PauseController()
        self._worker_thread: QtWorkerThread | None = None

        self._build_layout()
        self._wire_signals()

    # ---- layout -----------------------------------------------------

    def _build_layout(self) -> None:
        # Left column (vertical splitter: status, stage-nav, tree view).
        self.status_panel = StatusPanel()
        self.stage_nav = StageNavPanel(self._mode)
        self.tree_view = ReqResTreeView()

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self.status_panel)
        left_splitter.addWidget(self.stage_nav)
        left_splitter.addWidget(self.tree_view)
        left_splitter.setSizes([200, 260, 300])

        # Middle column: stage config in a scroll area.
        self.stage_config = StageConfigPanel(self._mode)
        center_scroll = QScrollArea()
        center_scroll.setWidgetResizable(True)
        center_scroll.setWidget(self.stage_config)

        # Right column: trace log + control buttons.
        self.trace_log = TraceLogWidget()

        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.reset_fsm_btn = QPushButton("Reset FSM")
        self.pause_all_btn = QPushButton("Pause ALL stages")
        self.resume_all_btn = QPushButton("Resume ALL")
        self.clear_all_overrides_btn = QPushButton("Clear overrides")
        self.clear_trees_btn = QPushButton("Clear trees")
        self.save_log_btn = QPushButton("Save log…")
        self.stop_btn.setEnabled(False)
        self.reset_fsm_btn.setEnabled(False)

        btn_row_1 = QHBoxLayout()
        btn_row_1.addWidget(self.start_btn)
        btn_row_1.addWidget(self.stop_btn)
        btn_row_1.addWidget(self.reset_fsm_btn)
        btn_row_2 = QHBoxLayout()
        btn_row_2.addWidget(self.pause_all_btn)
        btn_row_2.addWidget(self.resume_all_btn)
        btn_row_3 = QHBoxLayout()
        btn_row_3.addWidget(self.clear_all_overrides_btn)
        btn_row_3.addWidget(self.clear_trees_btn)
        btn_row_3.addWidget(self.save_log_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.trace_log, 1)
        right_layout.addLayout(btn_row_1)
        right_layout.addLayout(btn_row_2)
        right_layout.addLayout(btn_row_3)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_splitter)
        splitter.addWidget(center_scroll)
        splitter.addWidget(right)
        splitter.setSizes([420, 380, 600])

        self.setCentralWidget(splitter)

        # Pre-select first stage so the form isn't empty.
        order = stage_order(self._mode)
        if order:
            self.stage_config.set_stage(order[0])

    def _wire_signals(self) -> None:
        self.signals.trace_emitted.connect(self.trace_log.on_trace)
        self.signals.status_changed.connect(self.status_panel.on_status)
        self.signals.pause_hit.connect(self._on_pause_hit)
        self.signals.msg_decoded.connect(self.tree_view.on_message)
        self.signals.worker_started.connect(self._on_worker_started)
        self.signals.worker_stopped.connect(self._on_worker_stopped)

        self.stage_nav.stage_selected.connect(self.stage_config.set_stage)
        self.stage_nav.pause_toggled.connect(self._on_pause_toggled)
        self.stage_config.apply_clicked.connect(self._on_apply_override)
        self.stage_config.clear_clicked.connect(self._on_clear_override)

        self.start_btn.clicked.connect(self.start_worker)
        self.stop_btn.clicked.connect(self.stop_worker)
        self.reset_fsm_btn.clicked.connect(self._reset_fsm)
        self.pause_all_btn.clicked.connect(self._pause_all)
        self.resume_all_btn.clicked.connect(self._resume_all)
        self.clear_all_overrides_btn.clicked.connect(self._clear_all_overrides)
        self.clear_trees_btn.clicked.connect(self._clear_trees)
        self.save_log_btn.clicked.connect(
            lambda: self.trace_log.save_to_file(self)
        )

    # ---- worker lifecycle ------------------------------------------

    def start_worker(self) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self.signals.trace_emitted.emit("INFO", "[GUI] Starting worker…")
        self._worker_thread = QtWorkerThread(
            mode=self._mode,
            is_simulation=self._is_simulation,
            signals=self.signals,
            pause_controller=self.pause_controller,
            session_log_dir="sessions",
        )
        self._worker_thread.start()

    def stop_worker(self) -> None:
        if self._worker_thread is None:
            return
        self.signals.trace_emitted.emit("INFO", "[GUI] Stopping worker…")
        self._worker_thread.stop(timeout_s=3.0)
        self._worker_thread = None

    def _on_worker_started(self) -> None:
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.reset_fsm_btn.setEnabled(True)

    def _on_worker_stopped(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.reset_fsm_btn.setEnabled(False)

    def closeEvent(self, event) -> None:
        self.stop_worker()
        super().closeEvent(event)

    # ---- stage interactions ----------------------------------------

    def _on_pause_toggled(self, stage: str, enabled: bool) -> None:
        self.pause_controller.set_pause_enabled(stage, enabled)
        self.signals.trace_emitted.emit(
            "INFO",
            f"[GUI] Pause {'enabled' if enabled else 'disabled'} for {stage}",
        )

    def _on_apply_override(self, stage: str, values: dict[str, Any]) -> None:
        self.pause_controller.set_override(stage, values)
        self.stage_nav.set_override_indicator(stage, True)
        self.signals.trace_emitted.emit(
            "INFO", f"[GUI] Override set for {stage}: {values}"
        )

    def _on_clear_override(self, stage: str) -> None:
        self.pause_controller.clear_override(stage)
        self.stage_nav.set_override_indicator(stage, False)
        self.signals.trace_emitted.emit("INFO", f"[GUI] Override cleared for {stage}")

    def _pause_all(self) -> None:
        stages = list(schema_for(self._mode).keys())
        for s in stages:
            self.pause_controller.set_pause_enabled(s, True)
        # Reflect in the nav checkboxes.
        for s in stages:
            self.stage_nav._items[s].setCheckState(1, Qt.CheckState.Checked)
        self.signals.trace_emitted.emit("INFO", "[GUI] Paused ALL stages")

    def _resume_all(self) -> None:
        stages = list(schema_for(self._mode).keys())
        for s in stages:
            self.pause_controller.set_pause_enabled(s, False)
        for s in stages:
            self.stage_nav._items[s].setCheckState(1, Qt.CheckState.Unchecked)
        # Also release any in-flight pause so the FSM isn't stuck.
        if self.pause_controller.is_currently_paused():
            self.pause_controller.abort()
        self.signals.trace_emitted.emit("INFO", "[GUI] Resumed ALL stages")

    def _clear_all_overrides(self) -> None:
        self.pause_controller.clear_override()
        for s in schema_for(self._mode).keys():
            self.stage_nav.set_override_indicator(s, False)
        self.signals.trace_emitted.emit("INFO", "[GUI] Cleared all overrides")

    def _clear_trees(self) -> None:
        self.tree_view.clear()
        self.signals.trace_emitted.emit("INFO", "[GUI] Cleared Req/Res trees")

    def _reset_fsm(self) -> None:
        """Tell the running worker to re-initialize the FSM in place.

        The FSM lives on :class:`QtWorkerThread`; ``reInit()`` only mutates
        Python state (no socket teardown on the EVSE side, TCP reconnect on
        the PEV side). A brief race window exists if the worker thread is
        mid-state-handler — in practice the handlers finish in microseconds
        and the worst case is one dropped tick.
        """
        if self._worker_thread is None or not self._worker_thread.isRunning():
            return
        worker = self._worker_thread._worker
        if worker is None:
            return
        fsm = worker.evse if self._mode == C_EVSE_MODE else worker.pev
        if fsm is not None and hasattr(fsm, "reInit"):
            fsm.reInit()
            self.signals.trace_emitted.emit("INFO", "[GUI] FSM reInit() issued")

    # ---- pause intercept handler -----------------------------------

    def _on_pause_hit(self, stage: str, params: dict) -> None:
        # This runs on the main thread. The FSM thread is blocked on its
        # threading.Event until we call pause_controller.send() or abort().
        dlg = PauseInterceptDialog(stage, params, self._mode, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            edited = dlg.result_values()
            self.pause_controller.send(edited)
            self.signals.trace_emitted.emit(
                "SUCCESS", f"[GUI] Released {stage} with {edited}"
            )
        else:
            self.pause_controller.abort()
            self.signals.trace_emitted.emit(
                "WARNING", f"[GUI] Aborted pause for {stage}; used defaults"
            )
