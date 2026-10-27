"""Choose the datum transformation for each source CRS used in the project."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.crs import TransformOption, best_transformation_warning, transformation_options
from petrogis.ui.text import plain_label

AUTOMATIC = "Automatic (PROJ's default choice)"


@dataclass(frozen=True)
class TransformationPair:
    source: str
    target: str
    area: tuple[float, float, float, float] | None
    current: str | None
    """The pipeline chosen so far, or ``None`` for automatic."""


def _label(option: TransformOption) -> str:
    accuracy = f"{option.accuracy:g} m" if option.accuracy is not None else "accuracy unknown"
    if option.available:
        return f"{option.description} ({accuracy})"
    grids = ", ".join(option.missing_grids) or "grid"
    return f"{option.description} ({accuracy}) — needs {grids}, not installed"


class TransformationsDialog(QDialog):
    def __init__(self, pairs: list[TransformationPair], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Datum Transformations")
        self.resize(760, 160 + 70 * max(1, len(pairs)))
        self.combos: dict[tuple[str, str], QComboBox] = {}
        layout = QVBoxLayout(self)
        intro = (
            "Layers whose CRS differs from the project CRS are converted with these "
            "transformations. Accuracy is as published by EPSG."
        )
        layout.addWidget(plain_label(intro))
        form = QFormLayout()
        if not pairs:
            form.addRow(plain_label("All layers already use the project CRS."))
        for pair in pairs:
            combo = QComboBox()
            combo.addItem(AUTOMATIC, None)
            for option in transformation_options(pair.source, pair.target, pair.area):
                combo.addItem(_label(option), option.pipeline if option.available else None)
                if not option.available:
                    item = combo.model().item(combo.count() - 1)  # type: ignore[attr-defined]
                    item.setEnabled(False)
            if pair.current is not None:
                index = combo.findData(pair.current)
                combo.setCurrentIndex(max(0, index))
            self.combos[(pair.source, pair.target)] = combo
            form.addRow(plain_label(f"{pair.source} → {pair.target}"), combo)
            warning = best_transformation_warning(pair.source, pair.target, pair.area)
            if warning:
                note = plain_label(warning)
                note.setWordWrap(True)
                note.setStyleSheet("color: #8a5a00")
                form.addRow("", note)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choices(self) -> dict[tuple[str, str], str | None]:
        return {pair: combo.currentData() for pair, combo in self.combos.items()}


def choose_transformations(
    pairs: list[TransformationPair], parent: QWidget | None = None
) -> dict[tuple[str, str], str | None] | None:
    dialog = TransformationsDialog(pairs, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.choices()
    return None
