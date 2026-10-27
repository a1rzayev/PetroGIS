"""Layer panel: visibility checkboxes, drag reordering, in-place rename and a context menu.

The project is the single source of truth. User edits go to the project, and the panel redraws
from project events.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QWidget,
)

from petrogis.core.layers import Layer, LayerKind
from petrogis.core.project import Project
from petrogis.ui.project_bridge import ProjectBridge

LAYER_ID_ROLE = Qt.ItemDataRole.UserRole


class LayerPanel(QListWidget):
    zoom_to_layer_requested = Signal(str)
    style_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(
        self, project: Project, bridge: ProjectBridge, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.project = project
        self._syncing = False
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemChanged.connect(self._on_item_changed)
        self.itemDoubleClicked.connect(lambda item: self.style_requested.emit(self._id(item)))

        remove = QAction(self)
        remove.setShortcut(QKeySequence.StandardKey.Delete)
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self._remove_current)
        self.addAction(remove)

        bridge.layer_added.connect(self._on_layer_added)
        bridge.layer_removed.connect(self._on_layer_removed)
        bridge.layer_moved.connect(lambda *_: self._rebuild())
        bridge.layer_changed.connect(self._on_layer_changed)
        bridge.cleared.connect(self._rebuild)
        self._rebuild()

    # --- queries -----------------------------------------------------------------------------
    @staticmethod
    def _id(item: QListWidgetItem) -> str:
        return str(item.data(LAYER_ID_ROLE))

    def current_layer_id(self) -> str | None:
        item = self.currentItem()
        return None if item is None else self._id(item)

    def layer_ids(self) -> list[str]:
        return [self._id(self.item(row)) for row in range(self.count())]

    def _item_for(self, layer_id: str) -> QListWidgetItem | None:
        for row in range(self.count()):
            if self._id(self.item(row)) == layer_id:
                return self.item(row)
        return None

    # --- building ----------------------------------------------------------------------------
    def _make_item(self, layer: Layer) -> QListWidgetItem:
        item = QListWidgetItem(layer.name)
        item.setData(LAYER_ID_ROLE, layer.id)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsDragEnabled
        )
        item.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
        icon = (
            QStyle.StandardPixmap.SP_FileDialogContentsView
            if layer.kind is LayerKind.RASTER
            else QStyle.StandardPixmap.SP_FileIcon
        )
        item.setIcon(self.style().standardIcon(icon))
        item.setToolTip(f"{layer.kind.value}: {layer.source}")
        return item

    def _rebuild(self) -> None:
        current = self.current_layer_id()
        self._syncing = True
        try:
            self.clear()
            for layer in self.project.layers:
                self.addItem(self._make_item(layer))
            if current is not None and (item := self._item_for(current)) is not None:
                self.setCurrentItem(item)
        finally:
            self._syncing = False

    def _on_layer_added(self, layer_id: str, index: int) -> None:
        self._syncing = True
        try:
            item = self._make_item(self.project.get(layer_id))
            self.insertItem(index, item)
            self.setCurrentItem(item)
        finally:
            self._syncing = False

    def _on_layer_removed(self, layer_id: str) -> None:
        item = self._item_for(layer_id)
        if item is not None:
            self._syncing = True
            try:
                self.takeItem(self.row(item))
            finally:
                self._syncing = False

    def _on_layer_changed(self, layer_id: str, field: str) -> None:
        item = self._item_for(layer_id)
        if item is None:
            return
        layer = self.project.get(layer_id)
        self._syncing = True
        try:
            if field == "name":
                item.setText(layer.name)
            elif field == "visible":
                state = Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked
                item.setCheckState(state)
        finally:
            self._syncing = False

    # --- user edits --------------------------------------------------------------------------
    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        layer_id = self._id(item)
        layer = self.project.get(layer_id)
        visible = item.checkState() == Qt.CheckState.Checked
        if visible != layer.visible:
            self.project.set_visible(layer_id, visible)
        text = item.text().strip()
        if text != layer.name:
            if text:
                self.project.rename_layer(layer_id, text)
            else:
                self._syncing = True
                item.setText(layer.name)
                self._syncing = False

    def dropEvent(self, event: QDropEvent) -> None:
        super().dropEvent(event)
        self.apply_panel_order()

    def apply_panel_order(self) -> None:
        """Push the panel's current item order (for example after a drag) into the project."""
        for index, layer_id in enumerate(self.layer_ids()):
            if self.project.index_of(layer_id) != index:
                self.project.move_layer(layer_id, index)

    def _remove_current(self) -> None:
        layer_id = self.current_layer_id()
        if layer_id is not None:
            self.remove_requested.emit(layer_id)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        layer_id = self._id(item)
        menu = QMenu(self)
        menu.addAction("Zoom to Layer", lambda: self.zoom_to_layer_requested.emit(layer_id))
        menu.addAction("Style…", lambda: self.style_requested.emit(layer_id))
        menu.addAction("Rename", lambda: self.editItem(item))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self.remove_requested.emit(layer_id))
        menu.exec(self.viewport().mapToGlobal(pos))
