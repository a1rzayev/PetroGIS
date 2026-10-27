"""Single-colour style editor for vector layers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QPushButton,
    QSlider,
    QWidget,
)

from petrogis.core.styles import POINT_SYMBOLS, VectorStyle

SYMBOL_NAMES = {
    "o": "Circle",
    "s": "Square",
    "t": "Triangle",
    "d": "Diamond",
    "+": "Plus",
    "x": "Cross",
}


class VectorStyleDialog(QDialog):
    def __init__(self, style: VectorStyle, layer_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Style – {layer_name}")
        self._color = style.color

        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._pick_color)
        self._update_color_button()

        self.line_width = QDoubleSpinBox(
            minimum=0.5, maximum=20.0, singleStep=0.5, value=style.line_width
        )
        self.point_size = QDoubleSpinBox(
            minimum=1.0, maximum=50.0, singleStep=1.0, value=style.point_size
        )
        self.symbol = QComboBox()
        for code in POINT_SYMBOLS:
            self.symbol.addItem(SYMBOL_NAMES[code], code)
        self.symbol.setCurrentIndex(POINT_SYMBOLS.index(style.point_symbol))
        self.opacity = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.opacity.setValue(round(style.opacity * 100))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Colour", self.color_button)
        form.addRow("Line width (px)", self.line_width)
        form.addRow("Point size (px)", self.point_size)
        form.addRow("Point symbol", self.symbol)
        form.addRow("Opacity", self.opacity)
        form.addRow(buttons)

    def _update_color_button(self) -> None:
        self.color_button.setText(self._color)
        text_color = "#000" if QColor(self._color).lightness() > 128 else "#fff"
        self.color_button.setStyleSheet(f"background-color: {self._color}; color: {text_color};")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Layer colour")
        if color.isValid():
            self._color = color.name()
            self._update_color_button()

    def set_color(self, color: str) -> None:
        self._color = color
        self._update_color_button()

    def result_style(self) -> VectorStyle:
        return VectorStyle(
            color=self._color,
            line_width=self.line_width.value(),
            point_size=self.point_size.value(),
            point_symbol=str(self.symbol.currentData()),
            opacity=self.opacity.value() / 100,
        )


def edit_vector_style(
    style: VectorStyle, layer_name: str, parent: QWidget | None = None
) -> VectorStyle | None:
    dialog = VectorStyleDialog(style, layer_name, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_style()
    return None
