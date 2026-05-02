"""
Stage navigator — tree of DIN 70121 stages with per-stage override marker.

Emits:
  * stage_selected(str)   — user clicked a stage row

Earlier versions also offered a per-stage Pause checkbox. The pause
mechanism was removed because it conflicts with DIN 70121 §9.6 spec
timeouts on real vehicles; the only remaining per-stage flag is the
override indicator (●).
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..stage_schema import stage_order


class StageNavPanel(QTreeWidget):
    """Column 0: stage name, column 1: Override marker."""

    stage_selected = pyqtSignal(str)

    def __init__(self, mode: int, parent=None) -> None:
        super().__init__(parent)
        self._mode = mode

        self.setHeaderLabels(["Stage", "Override"])
        self.setColumnCount(2)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)

        self._items: dict[str, QTreeWidgetItem] = {}
        for stage in stage_order(mode):
            item = QTreeWidgetItem(self)
            item.setText(0, stage)
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setText(1, "")
            self._items[stage] = item

        self.setColumnWidth(0, 230)
        self.setColumnWidth(1, 80)

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

    # ---- external update API ---------------------------------------

    def set_override_indicator(self, stage: str, has_override: bool) -> None:
        item = self._items.get(stage)
        if item is not None:
            item.setText(1, "●" if has_override else "")

    def has_stage(self, stage: str) -> bool:
        return stage in self._items
