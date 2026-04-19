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

import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core.modes import C_EVSE_MODE, C_PEV_MODE
from ..fsm import PauseController
from .signals import Signals
from .stage_schema import schema_for, stage_order
from .widgets import (
    AttackLauncherDialog,
    PauseInterceptDialog,
    ReqResTreeView,
    SessionReplayPanel,
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

        # Checkpoint 13 — msg-rate tracking for the status bar.
        self._msg_count = 0
        self._msg_count_last_sample = 0
        self._last_sample_time = time.monotonic()
        self._replay_dock: QDockWidget | None = None
        self._replay_panel: SessionReplayPanel | None = None

        self._build_layout()
        self._build_menu_bar()
        self._build_status_bar()
        self._wire_signals()

        # Sample msg count → rate every 500 ms.
        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(500)
        self._rate_timer.timeout.connect(self._update_rate_display)
        self._rate_timer.start()

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

    def _build_menu_bar(self) -> None:
        """File / Attacks / Help menus. Pure UI surface — no new state."""
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        self._open_session_action = QAction("&Open session for replay…", self)
        self._save_log_action = QAction("&Save trace log…", self)
        self._quit_action = QAction("&Quit", self)
        self._quit_action.setShortcut("Ctrl+Q")
        file_menu.addAction(self._open_session_action)
        file_menu.addAction(self._save_log_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)

        attacks_menu = mb.addMenu("&Attacks")
        self._launch_attack_action = QAction("&Launch attack…", self)
        self._clear_attacks_action = QAction("&Clear all overrides", self)
        attacks_menu.addAction(self._launch_attack_action)
        attacks_menu.addAction(self._clear_attacks_action)

        help_menu = mb.addMenu("&Help")
        self._about_action = QAction("&About HotWire…", self)
        help_menu.addAction(self._about_action)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self._msg_count_label = QLabel("0 msgs")
        self._rate_label = QLabel("0.0 Hz")
        self._fsm_state_label = QLabel("idle")
        # Small fixed-width font for numeric readout.
        for lbl in (self._msg_count_label, self._rate_label,
                    self._fsm_state_label):
            lbl.setStyleSheet("padding: 0 10px; font-family: monospace;")
        bar.addPermanentWidget(self._fsm_state_label)
        bar.addPermanentWidget(self._msg_count_label)
        bar.addPermanentWidget(self._rate_label)
        self.setStatusBar(bar)

    def _wire_signals(self) -> None:
        self.signals.trace_emitted.connect(self.trace_log.on_trace)
        self.signals.status_changed.connect(self.status_panel.on_status)
        self.signals.status_changed.connect(self._on_status_for_bar)
        self.signals.pause_hit.connect(self._on_pause_hit)
        self.signals.msg_decoded.connect(self.tree_view.on_message)
        self.signals.msg_decoded.connect(self._on_msg_for_counter)
        self.signals.worker_started.connect(self._on_worker_started)
        self.signals.worker_stopped.connect(self._on_worker_stopped)
        self.signals.replay_event_selected.connect(self.tree_view.on_message)

        # Menu actions.
        self._open_session_action.triggered.connect(self._on_open_replay)
        self._save_log_action.triggered.connect(
            lambda: self.trace_log.save_to_file(self)
        )
        self._quit_action.triggered.connect(self.close)
        self._launch_attack_action.triggered.connect(self._on_launch_attack)
        self._clear_attacks_action.triggered.connect(
            self._clear_all_overrides
        )
        self._about_action.triggered.connect(self._on_about)

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
            # Reflect in the nav checkbox via its public API — keeps the
            # tree widget the sole owner of its QTreeWidgetItems.
            self.stage_nav.set_pause_state(s, True)
        self.signals.trace_emitted.emit("INFO", "[GUI] Paused ALL stages")

    def _resume_all(self) -> None:
        stages = list(schema_for(self._mode).keys())
        for s in stages:
            self.pause_controller.set_pause_enabled(s, False)
            self.stage_nav.set_pause_state(s, False)
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

    # ---- Checkpoint 13: menu bar + status bar handlers ------------

    def _on_msg_for_counter(self, _direction: str, _msg: str, _params: dict) -> None:
        self._msg_count += 1

    def _on_status_for_bar(self, key: str, value: str) -> None:
        """Bubble FSM state into the status bar's leftmost cell."""
        if key in ("evseState", "pevState", "state"):
            self._fsm_state_label.setText(str(value)[:40])

    def _update_rate_display(self) -> None:
        """Compute msg/sec since the last sample; called by QTimer."""
        now = time.monotonic()
        dt = now - self._last_sample_time
        delta = self._msg_count - self._msg_count_last_sample
        rate = (delta / dt) if dt > 0 else 0.0
        self._msg_count_label.setText(f"{self._msg_count} msgs")
        self._rate_label.setText(f"{rate:5.1f} Hz")
        self._msg_count_last_sample = self._msg_count
        self._last_sample_time = now

    def _on_launch_attack(self) -> None:
        dlg = AttackLauncherDialog(
            mode=self._mode,
            pause_controller=self.pause_controller,
            parent=self,
        )
        dlg.attack_launched.connect(self._on_attack_installed)
        dlg.exec()

    def _on_attack_installed(self, attack_name: str) -> None:
        self.signals.trace_emitted.emit(
            "SUCCESS", f"[attack] launched: {attack_name}"
        )
        # Refresh override markers in the stage-nav.
        for stage in schema_for(self._mode).keys():
            self.stage_nav.set_override_indicator(
                stage, self.pause_controller.has_override(stage)
            )
        self.signals.attack_applied.emit(attack_name)

    def _on_open_replay(self) -> None:
        if self._replay_dock is None:
            self._replay_panel = SessionReplayPanel(self)
            self._replay_panel.event_selected.connect(
                self.signals.replay_event_selected
            )
            self._replay_dock = QDockWidget("Session replay", self)
            self._replay_dock.setWidget(self._replay_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, self._replay_dock
            )
        self._replay_dock.show()
        self._replay_dock.raise_()
        # Fire the open dialog immediately for one-click access.
        self._replay_panel._on_open_clicked()                    # noqa: SLF001

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About HotWire",
            "<h3>HotWire</h3>"
            "<p>DIN 70121 / ISO 15118-2 charging-security testbed.</p>"
            "<p><b>Mode:</b> "
            f"{MODE_LABEL.get(self._mode, '?')}"
            f"{' (simulation)' if self._is_simulation else ' (hardware)'}</p>"
            "<p>Security research use only. "
            "See <b>SAFETY.md</b> before any real-hardware test.</p>",
        )
