"""
Hardware runner panel — dockable widget that shells out to the
``scripts/hw_check/*.py`` CLIs and surfaces the results in a tree.

The FSM phases (link / slac / sdp / v2g) need real hardware or at
least a PLC modem on an interface, so we run them as subprocesses to
avoid dragging the worker thread + socket stack into the GUI process.
The subprocess writes its own ``runs/<ts>/`` directory — the panel
just tails stdout / stderr for progress + reads the final REPORT.md
when exit code returns.

Not wired to any signal hub — the panel is fully self-contained.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .interface_picker import InterfacePickerCombo


_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent.parent \
    / "scripts" / "hw_check"

_PHASE_SCRIPTS = {
    "phase0_env — environment": "phase0_env.py",
    "phase0_hw — hardware preflight": "phase0_hw.py",
    "phase1 — link (passive sniff)": "phase1_link.py",
    "phase2 — SLAC pairing": "phase2_slac.py",
    "phase3 — SDP discovery": "phase3_sdp.py",
    "phase4 — V2G session": "phase4_v2g.py",
    "run_all — all phases sequentially": "run_all.py",
}


class HwRunnerPanel(QWidget):
    """Dockable panel: pick phase + interface + role, Run, see output."""

    phase_finished = pyqtSignal(str, int)             # (phase_name, exit_code)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._current_phase: Optional[str] = None
        self._build_layout()
        self._wire()

    # ---- layout ----------------------------------------------------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        config_box = QGroupBox("Run configuration")
        form = QFormLayout(config_box)

        self._phase_combo = QComboBox()
        for label in _PHASE_SCRIPTS:
            self._phase_combo.addItem(label)
        form.addRow("Phase", self._phase_combo)

        # Checkpoint 15 — ranked picker instead of raw QLineEdit.
        self._iface_edit = InterfacePickerCombo()
        form.addRow("Interface", self._iface_edit)

        self._role_combo = QComboBox()
        self._role_combo.addItems(["pev", "evse"])
        form.addRow("Role (phases 2-4)", self._role_combo)

        self._budget_spin = QSpinBox()
        self._budget_spin.setRange(1, 600)
        self._budget_spin.setValue(60)
        self._budget_spin.setSuffix(" s")
        form.addRow("Budget", self._budget_spin)

        root.addWidget(config_box)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._clear_btn = QPushButton("Clear output")
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._clear_btn)
        root.addLayout(btn_row)

        self._status_label = QLabel("<i>Idle</i>")
        root.addWidget(self._status_label)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumBlockCount(4000)
        self._output.setStyleSheet("font-family: monospace;")
        root.addWidget(self._output, 1)

    def _wire(self) -> None:
        self._run_btn.clicked.connect(self._on_run)
        self._stop_btn.clicked.connect(self._on_stop)
        self._clear_btn.clicked.connect(self._output.clear)

    # ---- run / stop ------------------------------------------------

    def _on_run(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            return
        phase_label = self._phase_combo.currentText()
        script = _PHASE_SCRIPTS[phase_label]
        self._current_phase = phase_label

        argv = [str(_SCRIPT_DIR / script)]
        iface = self._iface_edit.current_interface()
        if iface:
            argv += ["-i", iface]
        if "phase2" in script or "phase3" in script or "phase4" in script:
            argv += ["--role", self._role_combo.currentText()]
        # Budgets: the runner scripts have their own flag names.
        budget = self._budget_spin.value()
        if "phase4" in script:
            argv += ["--budget", str(budget)]
        if "phase2" in script:
            argv += ["--budget", str(budget)]
        if "phase3" in script:
            argv += ["--budget", str(budget)]
        if "phase1" in script:
            argv += ["--duration", str(budget)]

        self._output.appendPlainText(
            f"$ python {' '.join(argv)}\n"
        )
        self._status_label.setText(f"<b>Running:</b> {phase_label}")
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self._append_stdout(proc)
        )
        proc.finished.connect(self._on_finished)
        proc.start(sys.executable, argv)
        self._proc = proc

    def _on_stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.waitForFinished(1000)

    def _append_stdout(self, proc: QProcess) -> None:
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._output.appendPlainText(data.rstrip())

    def _on_finished(self, exit_code: int,
                     _exit_status: QProcess.ExitStatus) -> None:
        phase = self._current_phase or "?"
        self._output.appendPlainText(
            f"\n[exit code {exit_code}]"
        )
        self._status_label.setText(
            f"<b>{'Finished' if exit_code == 0 else 'Failed'}:</b> {phase}"
        )
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._proc = None
        self.phase_finished.emit(phase, exit_code)
