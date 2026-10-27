"""Choose the X/Y columns and CRS for a delimited text (CSV/TXT) point layer."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.crs import CRSError, crs_label, normalize_crs
from petrogis.io.wells_io import WellTable
from petrogis.ui.dialogs.crs_picker import pick_crs
from petrogis.ui.text import plain_label

PREVIEW_ROWS = 8


class DelimitedTextDialog(QDialog):
    def __init__(self, table: WellTable, default_crs: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add Delimited Text Layer – {table.path.name}")
        self.resize(720, 460)
        self.x_combo = QComboBox()
        self.y_combo = QComboBox()
        for combo, detected in ((self.x_combo, table.mapping.x), (self.y_combo, table.mapping.y)):
            for column in table.columns:
                combo.addItem(column, column)
            combo.setCurrentIndex(combo.findData(detected) if detected else -1)
            combo.currentIndexChanged.connect(self._validate)

        self.crs_edit = QLineEdit(table.suggested_crs or default_crs)
        self.crs_edit.textChanged.connect(self._validate)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_crs)
        crs_row = QHBoxLayout()
        crs_row.addWidget(self.crs_edit, 1)
        crs_row.addWidget(browse)
        self.crs_info = plain_label()

        preview = QTableWidget(min(PREVIEW_ROWS, len(table.data)), len(table.columns))
        preview.setHorizontalHeaderLabels(table.columns)
        for r, row in enumerate(table.data.head(PREVIEW_ROWS).itertuples(index=False)):
            for c, value in enumerate(row):
                preview.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.error = plain_label()
        self.error.setStyleSheet("color: #b00020")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("X / Easting / Longitude *", self.x_combo)
        form.addRow("Y / Northing / Latitude *", self.y_combo)
        form.addRow("Coordinate CRS *", crs_row)
        form.addRow("", self.crs_info)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(plain_label(f"Preview ({len(table.data)} rows):"))
        layout.addWidget(preview)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self._validate()

    def _browse_crs(self) -> None:
        chosen = pick_crs(self, self.crs_edit.text().strip())
        if chosen:
            self.crs_edit.setText(chosen)

    def columns(self) -> tuple[str, str]:
        return str(self.x_combo.currentData()), str(self.y_combo.currentData())

    def source_crs(self) -> str:
        return normalize_crs(self.crs_edit.text().strip())

    def _validate(self) -> None:
        problems: list[str] = []
        x, y = self.x_combo.currentData(), self.y_combo.currentData()
        if x is None or y is None:
            problems.append("Choose the X and Y columns")
        elif x == y:
            problems.append("X and Y must be different columns")
        try:
            self.crs_info.setText(crs_label(self.source_crs()))
        except CRSError:
            self.crs_info.setText("")
            problems.append("Unrecognised CRS")
        self.error.setText("; ".join(problems))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problems)


def ask_delimited_columns(
    table: WellTable, default_crs: str, parent: QWidget | None = None
) -> tuple[str, str, str] | None:
    """Returns (x column, y column, source CRS), or ``None`` if cancelled."""
    dialog = DelimitedTextDialog(table, default_crs, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        x, y = dialog.columns()
        return x, y, dialog.source_crs()
    return None
