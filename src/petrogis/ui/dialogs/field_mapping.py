"""Map survey or tops table columns to fields, with a preview, depth unit and single-well name."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
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

from petrogis.io.survey_io import (
    DEPTH_UNITS,
    SURVEY_FIELDS,
    SURVEY_LABELS,
    SURVEY_REQUIRED,
    SurveyMapping,
    SurveyTable,
)
from petrogis.io.tops_io import TOPS_FIELDS, TOPS_LABELS, TopsMapping, TopsTable
from petrogis.ui.text import plain_label

NONE_LABEL = "(none)"
PREVIEW_ROWS = 8


@dataclass(frozen=True)
class FieldChoice:
    mapping: dict[str, str | None]
    depth_unit: str
    default_well: str | None


class FieldMappingDialog(QDialog):
    """Generic column-to-field mapping for tables of depths per well.

    With no well column, every row belongs to one well, named in ``single_well`` (by default
    the file name). With ``allow_wide``, a "wide layout" switch reads every numeric column
    as one surface.
    """

    def __init__(
        self,
        title: str,
        intro: str,
        table: SurveyTable | TopsTable,
        fields: tuple[str, ...],
        labels: dict[str, str],
        required: tuple[str, ...],
        allow_wide: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} – {table.path.name}")
        self.resize(700, 480)
        self._required = required
        self._labels = labels
        self.combos: dict[str, QComboBox] = {}
        form = QFormLayout()
        detected = table.mapping.to_dict()
        for name in fields:
            combo = QComboBox()
            combo.addItem(NONE_LABEL, None)
            for column in table.columns:
                combo.addItem(column, column)
            if detected.get(name) is not None:
                combo.setCurrentIndex(combo.findData(detected[name]))
            combo.currentIndexChanged.connect(self._validate)
            self.combos[name] = combo
            form.addRow(labels[name] + (" *" if name in required else ""), combo)
        self.wide = QCheckBox("Wide layout: one depth column per surface")
        self.wide.setVisible(allow_wide)
        is_wide = isinstance(table.mapping, TopsMapping) and table.mapping.is_wide
        self.wide.setChecked(allow_wide and is_wide)
        self.wide.toggled.connect(self._validate)
        if allow_wide:
            form.addRow("", self.wide)
        self.unit = QComboBox()
        self.unit.addItems(DEPTH_UNITS)
        self.unit.setCurrentText(table.depth_unit)
        form.addRow("Depth unit", self.unit)
        self.single_well = QLineEdit(table.path.stem)
        self.single_well.textChanged.connect(self._validate)
        self.single_well_label = QLabel("Well (single-well file)")
        form.addRow(self.single_well_label, self.single_well)

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
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(intro))
        layout.addLayout(form)
        layout.addWidget(plain_label(f"Preview ({len(table.data)} rows):"))
        layout.addWidget(preview)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self._validate()

    def mapping(self) -> dict[str, str | None]:
        chosen = {name: combo.currentData() for name, combo in self.combos.items()}
        if self.wide.isChecked():
            chosen = {name: (chosen[name] if name == "well" else None) for name in chosen}
        return chosen

    def choice(self) -> FieldChoice:
        mapping = self.mapping()
        default = self.single_well.text().strip() if mapping.get("well") is None else None
        return FieldChoice(mapping, self.unit.currentText(), default or None)

    def _validate(self) -> None:
        mapping = self.mapping()
        wide = self.wide.isChecked()
        for name in ("surface", "md"):
            if name in self.combos:
                self.combos[name].setEnabled(not wide)
        problems = []
        required = ("well",) if wide else self._required
        missing = [self._labels[f] for f in required if mapping.get(f) is None]
        if missing:
            problems.append(f"Required: {', '.join(missing)}")
        chosen = [c for c in mapping.values() if c is not None]
        if len(set(chosen)) < len(chosen):
            problems.append("Each column can only be used once")
        single = mapping.get("well") is None
        self.single_well.setVisible(single)
        self.single_well_label.setVisible(single)
        if single and not self.single_well.text().strip():
            problems.append("Name the well, or choose a well column")
        self.error.setText("; ".join(problems))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problems)


def ask_survey_mapping(
    table: SurveyTable, parent: QWidget | None = None
) -> tuple[SurveyMapping, str, str | None] | None:
    dialog = FieldMappingDialog(
        "Import Surveys",
        "Choose which columns hold measured depth, inclination and azimuth.",
        table,
        SURVEY_FIELDS,
        SURVEY_LABELS,
        SURVEY_REQUIRED,
        parent=parent,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    choice = dialog.choice()
    return SurveyMapping.from_dict(choice.mapping), choice.depth_unit, choice.default_well


def ask_tops_mapping(
    table: TopsTable, parent: QWidget | None = None
) -> tuple[TopsMapping, str, str | None] | None:
    dialog = FieldMappingDialog(
        "Import Tops",
        "Choose the well, surface and depth columns, or the wide layout.",
        table,
        TOPS_FIELDS,
        TOPS_LABELS,
        ("surface", "md"),
        allow_wide=True,
        parent=parent,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    choice = dialog.choice()
    return TopsMapping.from_dict(choice.mapping), choice.depth_unit, choice.default_well
