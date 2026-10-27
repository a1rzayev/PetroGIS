"""Map well-header table columns to well fields, with a data preview and a source CRS field."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.crs import CRSError, crs_label, normalize_crs
from petrogis.io.wells_io import (
    FIELD_LABELS,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    ColumnMapping,
    WellTable,
)

NONE_LABEL = "(none)"
PREVIEW_ROWS = 8


class ColumnMappingDialog(QDialog):
    def __init__(self, table: WellTable, default_crs: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Import Wells – {table.path.name}")
        self.resize(720, 480)
        self._table = table

        self.combos: dict[str, QComboBox] = {}
        form = QFormLayout()
        for field_name in (*REQUIRED_FIELDS, *OPTIONAL_FIELDS):
            combo = QComboBox()
            if field_name in OPTIONAL_FIELDS:
                combo.addItem(NONE_LABEL, None)
            for column in table.columns:
                combo.addItem(column, column)
            detected = getattr(table.mapping, field_name)
            if detected is not None:
                combo.setCurrentIndex(combo.findData(detected))
            elif field_name in REQUIRED_FIELDS:
                combo.setCurrentIndex(-1)
            combo.currentIndexChanged.connect(self._validate)
            self.combos[field_name] = combo
            label = FIELD_LABELS[field_name] + (" *" if field_name in REQUIRED_FIELDS else "")
            form.addRow(label, combo)

        self.crs_edit = QLineEdit(table.suggested_crs or default_crs)
        self.crs_edit.textChanged.connect(self._validate)
        self.crs_info = QLabel()
        form.addRow("Coordinate CRS *", self.crs_edit)
        form.addRow("", self.crs_info)

        preview = QTableWidget(min(PREVIEW_ROWS, len(table.data)), len(table.columns))
        preview.setHorizontalHeaderLabels(table.columns)
        for r, row in enumerate(table.data.head(PREVIEW_ROWS).itertuples(index=False)):
            for c, value in enumerate(row):
                preview.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        intro = "Choose which columns hold the well name and surface coordinates."
        layout.addWidget(QLabel(intro))
        layout.addLayout(form)
        layout.addWidget(QLabel(f"Preview ({len(table.data)} rows):"))
        layout.addWidget(preview)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self._validate()

    def mapping(self) -> ColumnMapping:
        return ColumnMapping(**{f: combo.currentData() for f, combo in self.combos.items()})

    def source_crs(self) -> str:
        return normalize_crs(self.crs_edit.text().strip())

    def _validate(self) -> None:
        problems: list[str] = []
        mapping = self.mapping()
        missing = [FIELD_LABELS[f] for f in REQUIRED_FIELDS if getattr(mapping, f) is None]
        if missing:
            problems.append(f"Required: {', '.join(missing)}")
        chosen = [c for c in mapping.to_dict().values() if c is not None]
        # Name and UWI may share a column; any other reuse is a mistake.
        if len(set(chosen)) < len(chosen) - (1 if mapping.name == mapping.uwi else 0):
            problems.append("Each column can only be used once")
        try:
            self.crs_info.setText(crs_label(self.source_crs()))
        except CRSError:
            self.crs_info.setText("")
            problems.append("Unrecognised CRS")
        self.error.setText("; ".join(problems))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problems)


def ask_column_mapping(
    table: WellTable, default_crs: str, parent: QWidget | None = None
) -> tuple[ColumnMapping, str] | None:
    dialog = ColumnMappingDialog(table, default_crs, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.mapping(), dialog.source_crs()
    return None
