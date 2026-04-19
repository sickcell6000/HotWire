"""
Live 0x88E1 packet viewer.

While HotWire's phase 1 hw_check does a 15-second passive sniff + offline
analysis, reviewers often want to SEE the frames arriving. This panel
starts a tcpdump / dumpcap subprocess writing to a rotating pcap, and
every ~1 second re-analyses the file to update an MMTYPE frequency
table + per-source-MAC frame counters.

Fully headless-testable: :meth:`update_from_counts` accepts dicts
directly, so pytest-qt tests can skip the subprocess machinery.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .interface_picker import InterfacePickerCombo


class LivePcapViewer(QWidget):
    """Dockable live MMTYPE counter."""

    update_applied = pyqtSignal(int)            # total frames after update

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[subprocess.Popen] = None
        self._pcap_path: Optional[Path] = None
        self._last_counts: dict[str, int] = {}
        self._last_macs: dict[str, int] = {}
        self._poll_timer: Optional[QTimer] = None
        self._build_layout()
        self._wire()

    # ---- layout ----------------------------------------------------

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Interface:"))
        # Checkpoint 15 — ranked picker instead of raw QLineEdit.
        self._iface_edit = InterfacePickerCombo()
        ctrl.addWidget(self._iface_edit, 1)
        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        ctrl.addWidget(self._start_btn)
        ctrl.addWidget(self._stop_btn)
        root.addLayout(ctrl)

        self._status = QLabel("<i>Idle</i>")
        root.addWidget(self._status)

        body = QHBoxLayout()

        # MMTYPE table.
        self._mmtype_table = QTableWidget(0, 2)
        self._mmtype_table.setHorizontalHeaderLabels(["MMTYPE", "Count"])
        self._mmtype_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        body.addWidget(self._mmtype_table, 1)

        # MAC table.
        self._mac_table = QTableWidget(0, 2)
        self._mac_table.setHorizontalHeaderLabels(["Source MAC", "Frames"])
        self._mac_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        body.addWidget(self._mac_table, 1)

        root.addLayout(body, 1)

    def _wire(self) -> None:
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)

    # ---- public API (headless-testable) ---------------------------

    def update_from_counts(
        self,
        mmtype_counts: dict[str, int],
        mac_counts: dict[str, int],
    ) -> None:
        """Populate both tables from pre-computed dicts."""
        self._last_counts = dict(mmtype_counts)
        self._last_macs = dict(mac_counts)
        self._render_tables()
        total = sum(mmtype_counts.values())
        self.update_applied.emit(total)

    def clear(self) -> None:
        self._last_counts.clear()
        self._last_macs.clear()
        self._mmtype_table.setRowCount(0)
        self._mac_table.setRowCount(0)

    # ---- rendering -------------------------------------------------

    def _render_tables(self) -> None:
        self._mmtype_table.setRowCount(len(self._last_counts))
        for i, (name, n) in enumerate(sorted(self._last_counts.items())):
            self._mmtype_table.setItem(i, 0, QTableWidgetItem(name))
            self._mmtype_table.setItem(i, 1, QTableWidgetItem(str(n)))

        self._mac_table.setRowCount(len(self._last_macs))
        for i, (mac, n) in enumerate(sorted(
                self._last_macs.items(), key=lambda kv: -kv[1])):
            self._mac_table.setItem(i, 0, QTableWidgetItem(_format_mac(mac)))
            self._mac_table.setItem(i, 1, QTableWidgetItem(str(n)))

    # ---- subprocess lifecycle -------------------------------------

    def _on_start(self) -> None:
        iface = self._iface_edit.current_interface()
        if not iface:
            self._status.setText("<b style='color: #C62828;'>Enter an interface.</b>")
            return
        tool = _find_capture_tool()
        if tool is None:
            self._status.setText(
                "<b style='color: #C62828;'>No tcpdump/dumpcap on PATH.</b>"
            )
            return
        tmpd = tempfile.mkdtemp(prefix="hotwire_live_")
        self._pcap_path = Path(tmpd) / "live.pcap"

        argv = _build_capture_argv(tool, iface, self._pcap_path)
        try:
            self._proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            self._status.setText(
                f"<b style='color: #C62828;'>Failed to spawn {tool}: {e}</b>"
            )
            return

        self._status.setText(
            f"<b>Capturing</b> with {tool} on {iface} → {self._pcap_path}"
        )
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        # Poll every second.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_pcap)
        self._poll_timer.start()

    def _on_stop(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status.setText("<i>Stopped.</i>")

    def _poll_pcap(self) -> None:
        if not self._pcap_path or not self._pcap_path.exists():
            return
        try:
            counts, macs = _analyse_pcap(self._pcap_path)
        except Exception as e:                                   # noqa: BLE001
            self._status.setText(f"<i>poll error: {e}</i>")
            return
        mac_counts: dict[str, int] = {}
        for m in macs:
            mac_counts[m] = mac_counts.get(m, 0) + 1
        self.update_from_counts(counts, mac_counts)
        self._status.setText(
            f"<b>Live</b> — {sum(counts.values())} frames, {len(macs)} MAC(s)"
        )


# --- helpers ----------------------------------------------------------


def _find_capture_tool() -> Optional[str]:
    for t in ("dumpcap", "tcpdump"):
        if shutil.which(t):
            return t
    return None


def _build_capture_argv(tool: str, iface: str, out: Path) -> list[str]:
    if tool == "dumpcap":
        return ["dumpcap", "-i", iface, "-q", "-w", str(out),
                "-f", "ether proto 0x88E1"]
    return ["tcpdump", "-i", iface, "-w", str(out), "-U",
            "ether proto 0x88E1"]


def _analyse_pcap(path: Path) -> tuple[dict[str, int], list[str]]:
    """Re-analyse the live pcap. Reuses the same offline logic as phase1."""
    # Local import so the GUI module doesn't pull hotwire.plc eagerly at
    # import time (keeps startup fast and tests cleaner).
    from ...plc.homeplug_frames import HomePlugFrame
    from ...plc.pcapng_reader import iter_packets

    counts: dict[str, int] = {}
    macs: list[str] = []
    for pkt in iter_packets(path):
        if len(pkt) < 17:
            continue
        if pkt[12] != 0x88 or pkt[13] != 0xE1:
            continue
        fr = HomePlugFrame.from_bytes(pkt)
        if fr is None:
            continue
        label = f"0x{fr.mmtype_base:04X}.{fr.mmsub}"
        counts[label] = counts.get(label, 0) + 1
        macs.append(fr.src_mac.hex())
    return counts, macs


def _format_mac(raw_hex: str) -> str:
    """Turn ``'aabbccddeeff'`` into ``'aa:bb:cc:dd:ee:ff'``."""
    if len(raw_hex) != 12:
        return raw_hex
    return ":".join(raw_hex[i:i + 2] for i in range(0, 12, 2))
