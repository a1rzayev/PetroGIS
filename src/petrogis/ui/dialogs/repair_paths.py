"""Locate data files that moved since a project was saved (repair broken paths)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.io.project_io import MissingLayer, find_files_by_name
from petrogis.ui.text import plain_label

NOT_FOUND = "(not found)"


class RepairPathsDialog(QDialog):
    def __init__(self, missing: list[MissingLayer], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Repair Broken Paths")
        self.resize(820, 220 + 28 * len(missing))
        self.missing = missing
        self.fixed: dict[int, Path] = {}

        self.table = QTableWidget(len(missing), 3)
        self.table.setHorizontalHeaderLabels(["Layer", "Saved path", "New location"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, item in enumerate(missing):
            self.table.setItem(row, 0, QTableWidgetItem(item.name))
            self.table.setItem(row, 1, QTableWidgetItem(item.stored_path))
            self.table.setItem(row, 2, QTableWidgetItem(NOT_FOUND))
        self.table.resizeColumnsToContents()
        self.table.doubleClicked.connect(lambda index: self.locate(index.row()))

        locate = QPushButton("Locate Selected…")
        locate.clicked.connect(self._locate_selected)
        search = QPushButton("Search Folder…")
        search.clicked.connect(self._search_folder_dialog)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(locate)
        buttons_row.addWidget(search)
        buttons_row.addStretch(1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Load Repaired Layers")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        intro = (
            "These layers' data files were not found. Locate them, or search a folder for files "
            "with the same names. Layers you skip stay out of the project."
        )
        layout.addWidget(plain_label(intro))
        layout.addWidget(self.table)
        layout.addLayout(buttons_row)
        layout.addWidget(self.buttons)
        self._update_ok()

    def set_location(self, row: int, path: Path) -> None:
        self.fixed[self.missing[row].position] = path
        self.table.setItem(row, 2, QTableWidgetItem(str(path)))
        self._update_ok()

    def locate(self, row: int) -> None:
        name = Path(self.missing[row].stored_path).name
        chosen, _ = QFileDialog.getOpenFileName(
            self, f"Locate {name}", "", f"{name};;All files (*)"
        )
        if chosen:
            self.set_location(row, Path(chosen))

    def search_folder(self, folder: Path) -> int:
        """Fill in every missing file found under ``folder``. Returns how many were found."""
        names = {Path(item.stored_path).name for item in self.missing}
        found = find_files_by_name(folder, names)
        count = 0
        for row, item in enumerate(self.missing):
            match = found.get(Path(item.stored_path).name)
            if match is not None and item.position not in self.fixed:
                self.set_location(row, match)
                count += 1
        return count

    def _locate_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.locate(rows[0].row())

    def _search_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Search Folder for Missing Files")
        if folder:
            self.search_folder(Path(folder))

    def _update_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.fixed))


def ask_repair_paths(
    missing: list[MissingLayer], parent: QWidget | None = None
) -> dict[int, Path] | None:
    """New locations keyed by stored layer position, or ``None`` if the user skipped repair."""
    dialog = RepairPathsDialog(missing, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.fixed:
        return dict(dialog.fixed)
    return None
