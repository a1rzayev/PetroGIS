"""Identify popup: attributes of the features under a map click, plus raster values there."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.attributes import IdentifyHit, format_value

HIT_ROLE = Qt.ItemDataRole.UserRole


class IdentifyDialog(QDialog):
    """Non-modal tool window that is reused for every identify click."""

    hit_selected = Signal(object)  # IdentifyHit

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Identify")
        self.setWindowFlag(Qt.WindowType.Tool)
        self.setModal(False)
        self.resize(380, 420)
        self.location = QLabel()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Field", "Value"])
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        layout = QVBoxLayout(self)
        layout.addWidget(self.location)
        layout.addWidget(self.tree)
        self._raster_items: dict[str, QTreeWidgetItem] = {}
        self._placeholder: QTreeWidgetItem | None = None

    def show_results(self, x: float, y: float, hits: list[IdentifyHit]) -> None:
        self.location.setText(f"X {x:,.2f}   Y {y:,.2f}")
        self.tree.clear()
        self._raster_items.clear()
        layer_items: dict[str, QTreeWidgetItem] = {}
        for hit in hits:
            parent = layer_items.get(hit.layer_id)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [hit.layer_name])
                parent.setFirstColumnSpanned(True)
                layer_items[hit.layer_id] = parent
            feature = QTreeWidgetItem(parent, [hit.title])
            feature.setData(0, HIT_ROLE, hit)
            for field_name, value in hit.attributes:
                QTreeWidgetItem(feature, [field_name, value])
        for item in layer_items.values():
            item.setExpanded(True)
        if hits:
            first = layer_items[hits[0].layer_id].child(0)
            first.setExpanded(True)
            self.tree.setCurrentItem(first)
        self.tree.resizeColumnToContents(0)
        self._placeholder = None if hits else QTreeWidgetItem(self.tree, ["No features here"])

    def add_raster_value(self, layer_id: str, layer_name: str, value: float | None) -> None:
        """Add or update a raster layer's value row (rasters are sampled in the background)."""
        if value is None:
            return
        if self._placeholder is not None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(self._placeholder))
            self._placeholder = None
        item = self._raster_items.get(layer_id)
        if item is None:
            item = QTreeWidgetItem(self.tree, [layer_name, ""])
            self._raster_items[layer_id] = item
        item.setText(1, format_value(value))

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous: object) -> None:
        while current is not None and current.data(0, HIT_ROLE) is None:
            current = current.parent()
        if current is not None:
            self.hit_selected.emit(current.data(0, HIT_ROLE))
