"""Small reusable widgets: a colour button and a validated field-or-expression editor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.expressions import Expression, ExpressionError, quote_field

if TYPE_CHECKING:
    import pandas as pd


class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, color: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self.clicked.connect(self._pick)
        self.color = color
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = color
        text = "#000" if QColor(color).lightness() > 128 else "#fff"
        self.setText(color)
        self.setStyleSheet(f"background-color: {color}; color: {text};")
        self.color_changed.emit(color)

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.color), self, self._title)
        if chosen.isValid():
            self.set_color(chosen.name())


class ExpressionEdit(QWidget):
    """An editable combo listing the layer's fields, with live validation underneath.

    Picking a field inserts it quoted (``"Field Name"``). Any expression can be typed instead.
    Errors are shown as plain text, never markup.
    """

    changed = Signal()

    def __init__(
        self, frame: pd.DataFrame | None, text: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._frame = frame
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        columns = [] if frame is None else [c for c in frame.columns if c != "geometry"]
        for column in columns:
            self.combo.addItem(quote_field(str(column)))
        self.combo.setCurrentText(text)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.status)
        self.combo.currentTextChanged.connect(self._validate)
        self._validate()

    def text(self) -> str:
        return self.combo.currentText().strip()

    def set_text(self, text: str) -> None:
        self.combo.setCurrentText(text)

    def error(self) -> str | None:
        """Why the current text is unusable, or ``None`` when it is valid (or empty)."""
        text = self.text()
        if not text:
            return None
        try:
            expression = Expression(text)
            if self._frame is not None:
                expression.check_fields(self._frame)
        except ExpressionError as exc:
            return str(exc)
        return None

    def _validate(self) -> None:
        error = self.error()
        self.status.setText(error or "")
        self.status.setStyleSheet("color: #c0392b;" if error else "")
        self.status.setVisible(bool(error))
        self.changed.emit()
