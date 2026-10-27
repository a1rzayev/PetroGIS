"""Searchable CRS picker over the EPSG catalogue (projected and geographic CRSs)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.crs import CRSEntry, normalize_crs, search_crs
from petrogis.ui.text import plain_label

SEARCH_DEBOUNCE_MS = 150
COLUMNS = ("Code", "Name", "Type")
Index = QModelIndex | QPersistentModelIndex


class CRSTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[CRSEntry] = []

    def set_entries(self, entries: list[CRSEntry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role != Qt.ItemDataRole.DisplayRole or not index.isValid():
            return None
        entry = self.entries[index.row()]
        return (entry.code, entry.name, entry.kind)[index.column()]

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None


class CRSPickerDialog(QDialog):
    def __init__(
        self, current: str | None = None, message: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Coordinate Reference System")
        self.resize(720, 520)
        self.message = plain_label(message)
        self.message.setWordWrap(True)
        self.message.setVisible(bool(message))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by code, name or area, e.g. 32639, Pulkovo zone 9")
        self.search.setClearButtonEnabled(True)
        self.model = CRSTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.doubleClicked.connect(lambda _index: self._accept_if_selected())
        self.details = plain_label()
        self.details.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_if_selected)
        self.buttons.rejected.connect(self.reject)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(self._timer.start)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.details)
        layout.addWidget(self.buttons)

        self.table.selectionModel().selectionChanged.connect(self._show_details)
        if current:
            self.search.setText(current)
        self.refresh()

    def refresh(self) -> None:
        self.model.set_entries(search_crs(self.search.text()))
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 460)
        if self.model.entries:
            self.table.selectRow(0)
        self._show_details()

    def selected(self) -> CRSEntry | None:
        rows = self.table.selectionModel().selectedRows()
        return self.model.entries[rows[0].row()] if rows else None

    def selected_code(self) -> str | None:
        entry = self.selected()
        return None if entry is None else normalize_crs(entry.code)

    def _show_details(self) -> None:
        entry = self.selected()
        self.details.setText("" if entry is None else f"{entry.code}: {entry.name}\n{entry.area}")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(entry is not None)

    def _accept_if_selected(self) -> None:
        if self.selected() is not None:
            self.accept()


def pick_crs(parent: QWidget | None, current: str | None = None, message: str = "") -> str | None:
    dialog = CRSPickerDialog(current, message, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.selected_code()
    return None
