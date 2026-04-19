"""
Stage navigator — tree of DIN 70121 stages with per-stage pause + override flags.

Emits:
  * stage_selected(str)   — user clicked a stage row
  * pause_toggled(str, bool) — user ticked/unticked the Pause checkbox
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..stage_schema import stage_order


class StageNavPanel(QTreeWidget):
    """Column 0: stage name, column 1: Pause checkbox, column 2: Override marker."""

    stage_selected = pyqtSignal(str)
    pause_toggled = pyqtSignal(str, bool)

    def __init__(self, mode: int, parent=None) -> None:
        super().__init__(parent)
        self._mode = mode

        self.setHeaderLabels(["Stage", "Pause", "Override"])
        self.setColumnCount(3)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)

        self._items: dict[str, QTreeWidgetItem] = {}
        for stage in stage_order(mode):
            item = QTreeWidgetItem(self)
            item.setText(0, stage)
            item.setFlags(
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(1, Qt.CheckState.Unchecked)
            item.setText(2, "")
            self._items[stage] = item

        self.setColumnWidth(0, 230)
        self.setColumnWidth(1, 60)
        self.setColumnWidth(2, 80)

        # Wire signals.
        self.itemChanged.connect(self._on_item_changed)
        self.itemSelectionChanged.connect(self._on_selection_changed)

        # Select first stage by default so the config panel has something to show.
        order = stage_order(mode)
        if order:
            self.setCurrentItem(self._items[order[0]])

    # ---- event handlers --------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self.selectedItems()
        if items:
            self.stage_selected.emit(items[0].text(0))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 1:
            return
        stage = item.text(0)
        enabled = item.checkState(1) == Qt.CheckState.Checked
        self.pause_toggled.emit(stage, enabled)

    # ---- external update API ---------------------------------------

    def set_override_indicator(self, stage: str, has_override: bool) -> None:
        item = self._items.get(stage)
        if item is not None:
            item.setText(2, "●" if has_override else "")

    def set_pause_state(self, stage: str, enabled: bool) -> None:
        """Public API: set the Pause column's checkbox for ``stage``.

        Replaces the prior ``main_window._items[...]`` direct access so
        the tree widget stays the owner of its QTreeWidgetItems. Safe
        to call with a stage that doesn't exist — no-op.
        """
        item = self._items.get(stage)
        if item is None:
            return
        state = Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        # setCheckState fires itemChanged → pause_toggled signal. The
        # caller typically wants the signal to fire (so PauseController
        # stays in sync), so we do NOT block it here.
        item.setCheckState(1, state)

    def has_stage(self, stage: str) -> bool:
        return stage in self._items
