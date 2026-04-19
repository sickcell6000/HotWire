"""
PyQt6 QWizard guiding the operator through the hardware preflight.

Three pages:

1. **Intro** — Reads the interface from the config (or prompts).
2. **Running** — QTreeWidget fills progressively as
   :class:`PreflightRunner` yields each :class:`CheckResult`. A
   background ``QThread`` drives the runner so the UI stays
   responsive.
3. **Summary** — FAIL / WARN rows get a "Copy remediation" button so
   the operator can paste a fix into a shell.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ...preflight import CheckResult, CheckStatus, PreflightRunner
from .interface_picker import InterfacePickerCombo

_STATUS_COLOR = {
    CheckStatus.PASS: QColor("#2E7D32"),
    CheckStatus.FAIL: QColor("#C62828"),
    CheckStatus.WARN: QColor("#EF6C00"),
    CheckStatus.SKIP: QColor("#757575"),
}


class _RunnerThread(QThread):
    """Runs the PreflightRunner off the UI thread."""

    check_done = pyqtSignal(object)                # CheckResult

    def __init__(self, interface: Optional[str], parent=None) -> None:
        super().__init__(parent)
        self._interface = interface

    def run(self) -> None:
        runner = PreflightRunner(interface=self._interface)
        for result in runner.iter_results():
            self.check_done.emit(result)


class _IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("HotWire hardware preflight")
        self.setSubTitle(
            "Runs ~20 checks against your host + the selected network "
            "interface. Before you plug in a PLC modem or CCS cable, "
            "clear every FAIL below."
        )
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Interface</b> (leave blank for host-only checks):"
        ))
        # Checkpoint 15 — replace the blind QLineEdit with a ranked picker.
        self._iface_edit = InterfacePickerCombo(show_refresh=True)
        layout.addWidget(self._iface_edit)

        layout.addWidget(QLabel(
            "<i>Tip:</i> host-only checks still verify Python, OpenV2G "
            "binary, disk space, Npcap/libpcap, and system clock. "
            "Interface-dependent checks (MTU, carrier, IPv6 link-local, "
            "multicast) SKIP gracefully when the picker is empty. "
            "Hover over an entry to see MAC / MTU / carrier / score."
        ))

        # We don't registerField() here — QWizard's field mechanism wants a
        # Qt property on the widget, and InterfacePickerCombo exposes the
        # value via ``current_interface()`` instead. _RunningPage reads it
        # directly via wizard().page(0).


class _RunningPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Running checks…")
        self.setSubTitle("Each row fills in as its check completes.")
        layout = QVBoxLayout(self)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)                    # indeterminate
        layout.addWidget(self._progress)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(
            ["Check", "Status", "Observed", "Expected", "Elapsed"]
        )
        self._tree.setColumnWidth(0, 260)
        self._tree.setColumnWidth(1, 60)
        self._tree.setColumnWidth(2, 300)
        self._tree.setColumnWidth(3, 240)
        layout.addWidget(self._tree, 1)

        self._thread: _RunnerThread | None = None
        self._results: list[CheckResult] = []
        self._completed = False

    # The wizard polls this to decide if "Next" is allowed.
    def isComplete(self) -> bool:
        return self._completed

    def results(self) -> list[CheckResult]:
        return list(self._results)

    def initializePage(self) -> None:
        self._tree.clear()
        self._results.clear()
        self._completed = False
        self._progress.setRange(0, 0)

        # The Intro page owns the InterfacePickerCombo directly; read it.
        intro_page = self.wizard().page(0)
        picker = getattr(intro_page, "_iface_edit", None)
        iface = picker.current_interface() if picker is not None else ""
        self._thread = _RunnerThread(interface=iface or None, parent=self)
        self._thread.check_done.connect(self._on_check_done)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self.completeChanged.emit()

    def _on_check_done(self, result: CheckResult) -> None:
        self._results.append(result)
        row = QTreeWidgetItem(self._tree)
        row.setText(0, result.name)
        row.setText(1, result.status.symbol)
        row.setText(2, result.observed or "")
        row.setText(3, result.expected or "")
        row.setText(4, f"{result.elapsed_ms:.0f} ms")
        color = _STATUS_COLOR.get(result.status, QColor("#000000"))
        row.setForeground(1, QBrush(color))
        self._tree.scrollToItem(row)

    def _on_thread_finished(self) -> None:
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._completed = True
        self.completeChanged.emit()


class _SummaryPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Summary")
        self.setSubTitle("Copy any remediation commands into a shell.")
        layout = QVBoxLayout(self)
        self._scroll_host = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_host)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll_host, 1)

    def initializePage(self) -> None:
        # Grab the running page's results.
        wizard = self.wizard()
        running_page = wizard.page(1)                    # index of _RunningPage
        results: list[CheckResult] = running_page.results()

        # Clear old cards.
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        pass_count = sum(1 for r in results if r.status == CheckStatus.PASS)
        fail_count = sum(1 for r in results if r.status == CheckStatus.FAIL)
        warn_count = sum(1 for r in results if r.status == CheckStatus.WARN)
        skip_count = sum(1 for r in results if r.status == CheckStatus.SKIP)
        header = QLabel(
            f"<b>{pass_count} PASS · {fail_count} FAIL · "
            f"{warn_count} WARN · {skip_count} SKIP</b>  "
            f"({len(results)} total)"
        )
        self._scroll_layout.addWidget(header)

        # One "card" per FAIL / WARN with a Copy button.
        for r in results:
            if r.status in (CheckStatus.FAIL, CheckStatus.WARN) and r.remediation:
                self._scroll_layout.addWidget(_build_remediation_card(r))
        if not any(r.status == CheckStatus.FAIL for r in results):
            self._scroll_layout.addWidget(QLabel(
                "<span style='color: #2E7D32;'>"
                "No blocking failures. Safe to proceed to phase 1+.</span>"
            ))
        self._scroll_layout.addStretch(1)


def _build_remediation_card(result: CheckResult) -> QWidget:
    card = QWidget()
    row = QVBoxLayout(card)
    row.setContentsMargins(6, 6, 6, 6)

    title = QLabel(
        f"<b>{result.status.symbol} {result.name}</b><br>"
        f"<i>{result.observed}</i>"
    )
    row.addWidget(title)

    cmd_layout = QHBoxLayout()
    cmd_label = QLineEdit(result.remediation)
    cmd_label.setReadOnly(True)
    cmd_label.setStyleSheet("font-family: monospace;")
    copy_btn = QPushButton("Copy")
    copy_btn.setMaximumWidth(80)

    def _copy() -> None:
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(result.remediation)

    copy_btn.clicked.connect(_copy)
    cmd_layout.addWidget(cmd_label, 1)
    cmd_layout.addWidget(copy_btn)
    row.addLayout(cmd_layout)

    card.setStyleSheet(
        "QWidget { background: #FFF8E1; border: 1px solid #FFB300; "
        "border-radius: 4px; }"
        if result.status == CheckStatus.WARN else
        "QWidget { background: #FFEBEE; border: 1px solid #C62828; "
        "border-radius: 4px; }"
    )
    return card


class PreflightWizard(QWizard):
    """QWizard wrapping the 3 pages."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("HotWire preflight wizard")
        self.resize(900, 600)
        self.addPage(_IntroPage())
        self.addPage(_RunningPage())
        self.addPage(_SummaryPage())

    # Test helper — return last results so tests can assert on them.
    def results(self) -> list[CheckResult]:
        running_page = self.page(1)
        if running_page is None:
            return []
        return running_page.results()
