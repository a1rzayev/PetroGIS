"""Symbol and label style editor for well layers."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QPushButton,
    QWidget,
)

from petrogis.core.styles import WellStyle


class _ColorButton(QPushButton):
    def __init__(self, color: str, title: str) -> None:
        super().__init__()
        self._title = title
        self.clicked.connect(self._pick)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = color
        text = "#000" if QColor(color).lightness() > 128 else "#fff"
        self.setText(color)
        self.setStyleSheet(f"background-color: {color}; color: {text};")

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.color), self, self._title)
        if chosen.isValid():
            self.set_color(chosen.name())


class WellStyleDialog(QDialog):
    def __init__(self, style: WellStyle, layer_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Style – {layer_name}")
        self.color = _ColorButton(style.color, "Symbol colour")
        self.symbol_size = QDoubleSpinBox(minimum=2.0, maximum=40.0, value=style.symbol_size)
        self.show_labels = QCheckBox("Show well names")
        self.show_labels.setChecked(style.show_labels)
        self.label_size = QDoubleSpinBox(minimum=5.0, maximum=30.0, value=style.label_size)
        self.label_color = _ColorButton(style.label_color, "Label colour")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Symbol colour", self.color)
        form.addRow("Symbol size (px)", self.symbol_size)
        form.addRow("", self.show_labels)
        form.addRow("Label size (pt)", self.label_size)
        form.addRow("Label colour", self.label_color)
        form.addRow(buttons)

    def result_style(self) -> WellStyle:
        return WellStyle(
            color=self.color.color,
            symbol_size=self.symbol_size.value(),
            show_labels=self.show_labels.isChecked(),
            label_size=self.label_size.value(),
            label_color=self.label_color.color,
        )


def edit_well_style(
    style: WellStyle, layer_name: str, parent: QWidget | None = None
) -> WellStyle | None:
    dialog = WellStyleDialog(style, layer_name, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_style()
    return None
