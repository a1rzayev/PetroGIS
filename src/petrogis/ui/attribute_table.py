"""Attribute table dock for vector and well layers.

The model is virtual. Only visible cells are formatted, and sorting and filtering reorder an
array of row positions (see ``core.attributes``), so large tables stay responsive.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.attributes import filter_rows, format_value, sort_rows, visible_columns
from petrogis.core.layers import FeatureLayer

FILTER_DEBOUNCE_MS = 200
Index = QModelIndex | QPersistentModelIndex


class FeatureTableModel(QAbstractTableModel):
    def __init__(self, layer: FeatureLayer | None = None) -> None:
        super().__init__()
        self.layer: FeatureLayer | None = None
        self.columns: list[str] = []
        self.rows: NDArray[np.intp] = np.empty(0, dtype=np.intp)
        self._filter = ""
        self._sort: tuple[str, bool] | None = None
        if layer is not None:
            self.set_layer(layer)

    def set_layer(self, layer: FeatureLayer | None) -> None:
        self.beginResetModel()
        self.layer = layer
        gdf = None if layer is None else layer.gdf
        self.columns = [] if gdf is None else visible_columns(gdf)
        self._filter = ""
        self._sort = None
        self.rows = np.arange(0 if gdf is None else len(gdf), dtype=np.intp)
        self.endResetModel()

    @property
    def total_rows(self) -> int:
        return 0 if self.layer is None or self.layer.gdf is None else len(self.layer.gdf)

    # --- Qt model API ------------------------------------------------------------------------
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else int(self.rows.size)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role != Qt.ItemDataRole.DisplayRole or not index.isValid() or self.layer is None:
            return None
        gdf = self.layer.gdf
        if gdf is None:
            return None
        column = gdf.columns.get_loc(self.columns[index.column()])
        return format_value(gdf.iat[int(self.rows[index.row()]), column])

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.columns[section] if section < len(self.columns) else None
        return str(int(self.rows[section]) + 1) if section < self.rows.size else None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if self.layer is None or self.layer.gdf is None or not 0 <= column < len(self.columns):
            return
        self._sort = (self.columns[column], order == Qt.SortOrder.AscendingOrder)
        self._refresh()

    # --- filtering ---------------------------------------------------------------------------
    def set_filter(self, text: str) -> None:
        self._filter = text
        self._refresh()

    def _refresh(self) -> None:
        if self.layer is None or self.layer.gdf is None:
            return
        self.layoutAboutToBeChanged.emit()
        rows = filter_rows(self.layer.gdf, self._filter)
        if self._sort is not None:
            rows = sort_rows(self.layer.gdf, rows, *self._sort)
        self.rows = rows
        self.layoutChanged.emit()

    def positions(self, view_rows: list[int]) -> NDArray[np.intp]:
        """Map view rows to feature positions in the layer's GeoDataFrame."""
        return self.rows[np.asarray(view_rows, dtype=np.intp)] if view_rows else self.rows[:0]


class AttributeTable(QWidget):
    selection_changed = Signal(object)  # NDArray of feature positions
    zoom_requested = Signal(object)  # NDArray of feature positions

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = FeatureTableModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setDefaultSectionSize(22)
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.view.selectionModel().selectionChanged.connect(self._emit_selection)
        self.view.doubleClicked.connect(
            lambda index: self.zoom_requested.emit(self.model.positions([index.row()]))
        )

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter rows…")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self.apply_filter)
        self.filter_edit.textChanged.connect(self._filter_timer.start)
        self.count_label = QLabel()
        zoom_button = QPushButton("Zoom to Selection")
        zoom_button.clicked.connect(lambda: self.zoom_requested.emit(self.selected_positions()))

        top = QHBoxLayout()
        top.addWidget(self.filter_edit, 1)
        top.addWidget(self.count_label)
        top.addWidget(zoom_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.view)
        self._update_count()

    @property
    def layer_id(self) -> str | None:
        return None if self.model.layer is None else self.model.layer.id

    def set_layer(self, layer: FeatureLayer | None) -> None:
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.model.set_layer(layer)
        self.view.resizeColumnsToContents()
        self._update_count()

    def apply_filter(self) -> None:
        self.view.clearSelection()
        self.model.set_filter(self.filter_edit.text())
        self._update_count()

    def selected_positions(self) -> NDArray[np.intp]:
        rows = sorted({index.row() for index in self.view.selectionModel().selectedRows()})
        return self.model.positions(rows)

    def select_position(self, position: int) -> None:
        """Select and scroll to the row of feature ``position``, if it passes the filter."""
        matches = np.flatnonzero(self.model.rows == position)
        if matches.size:
            row = int(matches[0])
            self.view.selectRow(row)
            self.view.scrollTo(self.model.index(row, 0))

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self.selected_positions())
        self._update_count()

    def _update_count(self) -> None:
        selected = len(self.view.selectionModel().selectedRows()) if self.model.layer else 0
        shown, total = self.model.rowCount(), self.model.total_rows
        text = f"{shown} of {total} features" if shown != total else f"{total} features"
        self.count_label.setText(f"{text}, {selected} selected" if selected else text)
