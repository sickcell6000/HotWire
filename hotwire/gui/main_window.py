"""
HotWire main window — wires every widget to the worker thread and override controller.

Layout (horizontal QSplitter):

    +--------------------+-------------------------+---------------------+
    | StatusPanel        |  StageNavPanel          | [Combined][rx][tx] |
    | (Session State,    |  (stage list +          | [Trace log]        |
    |  Connection,       |   override marker)      |   ↑ four flat tabs |
    |  Live Parameters)  |                         |                    |
    |                    |-------------------------|                    |
    |                    |  StageConfigPanel       | [Start/Stop/...]   |
    |                    |  (Override editor)      |                    |
    +--------------------+-------------------------+---------------------+

The right-column tab bar collapses what used to be a separate center
column (message history) and trace log into a single tab strip — this
freed up center-column real estate for the stage-nav + override-editor
pair, which had been crammed into the bottom of the left column and
felt cramped during paper-validation testing.

Per-stage overrides applied through StageConfigPanel feed into the
shared :class:`PauseController` (override-only — interactive pause is
gone), which the FSM consults via ``intercept(stage, params)`` before
EXI-encoding each outbound message.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QAction
from PyQt5.QtWidgets import (
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
    ConfigEditor,
    HwRunnerPanel,
    LivePcapViewer,
    NetworkInterfacesDock,
    PreflightWizard,
    ReqResTreeView,
    SessionComparePanel,
    SessionReplayPanel,
    SessionToolsPanel,
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

        # Checkpoint 14 — lazy-created dock widgets.
        self._compare_dock: QDockWidget | None = None
        self._compare_panel: SessionComparePanel | None = None
        self._session_tools_dock: QDockWidget | None = None
        self._session_tools_panel: SessionToolsPanel | None = None
        self._hw_runner_dock: QDockWidget | None = None
        self._hw_runner_panel: HwRunnerPanel | None = None
        self._live_pcap_dock: QDockWidget | None = None
        self._live_pcap_panel: LivePcapViewer | None = None
        self._config_editor_dock: QDockWidget | None = None
        self._config_editor_panel: ConfigEditor | None = None

        # Checkpoint 15 — global network-interface status dock.
        self._network_dock: QDockWidget | None = None
        self._network_panel: NetworkInterfacesDock | None = None

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
        # ---- Left column: pure readout (no editing) -----------------
        # The Live Parameters group inside StatusPanel grew to 12+ rows
        # in commit 4e2657d (SoC, Target V/I, Present V/I, EV flags,
        # EVSE limits, cumulative kWh). Giving it the whole column
        # makes the readout legible at a glance during a session — no
        # more squinting at "30%" jammed under everything else.
        self.status_panel = StatusPanel()

        # ---- Center column: stage selection + Override editor ------
        # These were the cramped pair at the bottom of the old left
        # column. Promoting them to their own column gives the
        # PresetCombo widgets enough horizontal room for hex strings
        # plus the 💾 / ⚙ buttons without truncation, and the stage
        # nav can show all 14+ stages without scrolling.
        self.stage_nav = StageNavPanel(self._mode)
        self.stage_config = StageConfigPanel(
            self._mode, pause_controller=self.pause_controller
        )

        # The Override editor's internal QFormLayout grows tall for
        # stages with many fields (PreChargeRes, ChargeParameterDiscoveryRes)
        # — wrap in a scroll area so the column doesn't push the whole
        # window taller.
        stage_config_scroll = QScrollArea()
        stage_config_scroll.setWidgetResizable(True)
        stage_config_scroll.setWidget(self.stage_config)

        center_splitter = QSplitter(Qt.Vertical)
        center_splitter.addWidget(self.stage_nav)
        center_splitter.addWidget(stage_config_scroll)
        center_splitter.setSizes([280, 480])

        # ---- Right column: unified tab strip + control buttons -----
        # All three message tabs (Combined / Received / Sent) plus the
        # Trace log share one tab bar so the operator can swipe between
        # protocol-level and free-text views without juggling two
        # vertical splits. ReqResTreeView's existing internal QTabWidget
        # becomes the host (we just add a fourth tab for the log) so the
        # msg_decoded routing logic stays unchanged.
        self.tree_view = ReqResTreeView()
        self.trace_log = TraceLogWidget()
        self.tree_view.tab_widget().addTab(self.trace_log, "Trace log")
        # Default to the Trace log tab on startup — operators most often
        # want to watch the live free-text log first (FSM transitions,
        # connection-manager notes, attack confirmations) before drilling
        # into the per-message Combined / rx / tx breakdown.
        self.tree_view.tab_widget().setCurrentWidget(self.trace_log)

        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.reset_fsm_btn = QPushButton("Reset FSM")
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
        btn_row_2.addWidget(self.clear_all_overrides_btn)
        btn_row_2.addWidget(self.clear_trees_btn)
        btn_row_2.addWidget(self.save_log_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.tree_view, 1)
        right_layout.addLayout(btn_row_1)
        right_layout.addLayout(btn_row_2)

        # Wrap the readout-only StatusPanel in its own scroll so the
        # tall Live Parameters group still fits on lower-resolution
        # displays (Pi VNC, 1280×720 demo laptops).
        status_scroll = QScrollArea()
        status_scroll.setWidgetResizable(True)
        status_scroll.setWidget(self.status_panel)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(status_scroll)
        splitter.addWidget(center_splitter)
        splitter.addWidget(right)
        splitter.setSizes([320, 380, 700])

        self.setCentralWidget(splitter)

        # Pre-select first stage so the form isn't empty.
        order = stage_order(self._mode)
        if order:
            self.stage_config.set_stage(order[0])

    def _build_menu_bar(self) -> None:
        """File / Edit / Attacks / Tools / Hardware / Help menus."""
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

        # Checkpoint 14: Edit → Preferences
        edit_menu = mb.addMenu("&Edit")
        self._preferences_action = QAction("&Preferences… (hotwire.ini)", self)
        self._preferences_action.setShortcut("Ctrl+,")
        edit_menu.addAction(self._preferences_action)

        attacks_menu = mb.addMenu("&Attacks")
        self._launch_attack_action = QAction("&Launch attack…", self)
        self._clear_attacks_action = QAction("&Clear all overrides", self)
        attacks_menu.addAction(self._launch_attack_action)
        attacks_menu.addAction(self._clear_attacks_action)

        # Checkpoint 14: Tools → compare / redact / export.
        tools_menu = mb.addMenu("&Tools")
        self._compare_action = QAction("&Compare sessions…", self)
        self._session_tools_action = QAction(
            "Redact / Export &pcap / Export &CSV…", self
        )
        tools_menu.addAction(self._compare_action)
        tools_menu.addAction(self._session_tools_action)

        # Checkpoint 14: Hardware → preflight / runner / live.
        hw_menu = mb.addMenu("&Hardware")
        self._preflight_wizard_action = QAction("Run preflight &wizard…", self)
        self._hw_runner_action = QAction("Run hw_check &phase…", self)
        self._live_pcap_action = QAction("Live pcap &viewer…", self)
        # Checkpoint 15.
        self._network_dock_action = QAction(
            "&Network interfaces…", self
        )
        hw_menu.addAction(self._preflight_wizard_action)
        hw_menu.addAction(self._hw_runner_action)
        hw_menu.addAction(self._live_pcap_action)
        hw_menu.addAction(self._network_dock_action)

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
        self.signals.msg_decoded.connect(self.tree_view.on_message)
        self.signals.msg_decoded.connect(self._on_msg_for_counter)
        # StatusPanel pulls SoC / Voltage / Current / Energy directly
        # from each decoded message so the Live Parameters group stays
        # in lock-step with the wire instead of relying on the FSM to
        # push every field via status_changed.
        self.signals.msg_decoded.connect(self.status_panel.on_message)
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

        # Checkpoint 14 menu actions.
        self._preferences_action.triggered.connect(self._on_open_preferences)
        self._compare_action.triggered.connect(self._on_open_compare)
        self._session_tools_action.triggered.connect(
            self._on_open_session_tools
        )
        self._preflight_wizard_action.triggered.connect(
            self._on_preflight_wizard
        )
        self._hw_runner_action.triggered.connect(self._on_open_hw_runner)
        self._live_pcap_action.triggered.connect(self._on_open_live_pcap)
        self._network_dock_action.triggered.connect(self._on_open_network_dock)

        self.stage_nav.stage_selected.connect(self.stage_config.set_stage)
        self.stage_config.apply_clicked.connect(self._on_apply_override)
        self.stage_config.clear_clicked.connect(self._on_clear_override)

        self.start_btn.clicked.connect(self.start_worker)
        self.stop_btn.clicked.connect(self.stop_worker)
        self.reset_fsm_btn.clicked.connect(self._reset_fsm)
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
        dlg.exec_()

    def _on_attack_installed(self, attack_name: str) -> None:
        self.signals.trace_emitted.emit(
            "SUCCESS", f"[attack] launched: {attack_name}"
        )
        # Refresh override markers in the stage-nav.
        for stage in schema_for(self._mode).keys():
            self.stage_nav.set_override_indicator(
                stage, self.pause_controller.has_override(stage)
            )
        # Mirror the just-installed overrides into the Stage Config Panel
        # so the editor on the left bottom shows the same EVCCID /
        # voltage / etc. that the FSM is now transmitting. Without this,
        # operators see "set the EVCCID" in the trace but blank fields
        # in the editor — a confusing disconnect during paper testing.
        self.stage_config.refresh_overrides()
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

    # ---- Checkpoint 14: Tools / Hardware / Edit menu handlers -----

    def _on_preflight_wizard(self) -> None:
        """Run the 20-item hardware preflight wizard as a modal."""
        wizard = PreflightWizard(self)
        wizard.exec_()
        # After the wizard closes, surface a summary in the trace log.
        results = wizard.results()
        if not results:
            return
        fails = sum(1 for r in results if r.status.value == "FAIL")
        warns = sum(1 for r in results if r.status.value == "WARN")
        level = "ERROR" if fails else "WARNING" if warns else "SUCCESS"
        self.signals.trace_emitted.emit(
            level,
            f"[preflight] {len(results)} checks: "
            f"{fails} fail, {warns} warn",
        )

    def _on_open_hw_runner(self) -> None:
        if self._hw_runner_dock is None:
            self._hw_runner_panel = HwRunnerPanel(self)
            self._hw_runner_panel.phase_finished.connect(
                self.signals.hw_phase_done
            )
            self._hw_runner_dock = QDockWidget("Hardware runner", self)
            self._hw_runner_dock.setWidget(self._hw_runner_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, self._hw_runner_dock
            )
        self._hw_runner_dock.show()
        self._hw_runner_dock.raise_()

    def _on_open_live_pcap(self) -> None:
        if self._live_pcap_dock is None:
            self._live_pcap_panel = LivePcapViewer(self)
            self._live_pcap_dock = QDockWidget("Live pcap viewer", self)
            self._live_pcap_dock.setWidget(self._live_pcap_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, self._live_pcap_dock
            )
        self._live_pcap_dock.show()
        self._live_pcap_dock.raise_()

    def _on_open_compare(self) -> None:
        if self._compare_dock is None:
            self._compare_panel = SessionComparePanel(self)
            self._compare_dock = QDockWidget("Compare sessions", self)
            self._compare_dock.setWidget(self._compare_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, self._compare_dock
            )
        self._compare_dock.show()
        self._compare_dock.raise_()

    def _on_open_session_tools(self) -> None:
        if self._session_tools_dock is None:
            self._session_tools_panel = SessionToolsPanel(self)
            self._session_tools_panel.tool_finished.connect(
                self._on_session_tool_finished
            )
            self._session_tools_dock = QDockWidget("Session tools", self)
            self._session_tools_dock.setWidget(self._session_tools_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea,
                self._session_tools_dock,
            )
        self._session_tools_dock.show()
        self._session_tools_dock.raise_()

    def _on_open_preferences(self) -> None:
        if self._config_editor_dock is None:
            self._config_editor_panel = ConfigEditor(self)
            self._config_editor_panel.config_saved.connect(
                self.signals.config_saved
            )
            self._config_editor_dock = QDockWidget("Preferences (hotwire.ini)", self)
            self._config_editor_dock.setWidget(self._config_editor_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self._config_editor_dock,
            )
        self._config_editor_dock.show()
        self._config_editor_dock.raise_()

    def _on_session_tool_finished(self, tool_name: str, msg: str) -> None:
        self.signals.trace_emitted.emit(
            "SUCCESS", f"[{tool_name}] {msg}"
        )

    def _on_open_network_dock(self) -> None:
        if self._network_dock is None:
            self._network_panel = NetworkInterfacesDock(self)
            self._network_panel.best_changed.connect(
                lambda name: self.signals.trace_emitted.emit(
                    "INFO", f"[network] best candidate: {name}"
                )
            )
            self._network_dock = QDockWidget("Network interfaces", self)
            self._network_dock.setWidget(self._network_panel)
            self.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self._network_dock
            )
        self._network_dock.show()
        self._network_dock.raise_()
