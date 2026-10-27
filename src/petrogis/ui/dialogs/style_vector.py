"""Style editor for vector layers: single, categorized or graduated symbols, fill and labels."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.classify import (
    METHOD_NAMES,
    METHODS,
    RAMPS,
    category_colors,
    class_breaks,
    format_break,
    ramp_colors,
    unique_categories,
)
from petrogis.core.expressions import Expression, ExpressionError
from petrogis.core.layers import VectorLayer
from petrogis.core.styles import (
    POINT_SYMBOLS,
    Category,
    ClassBreak,
    LabelStyle,
    VectorStyle,
)
from petrogis.ui.widgets import ColorButton, ExpressionEdit

SYMBOL_NAMES = {
    "o": "Circle",
    "s": "Square",
    "t": "Triangle",
    "d": "Diamond",
    "+": "Plus",
    "x": "Cross",
}
RENDERER_NAMES = {
    "single": "Single symbol",
    "categorized": "Categorized",
    "graduated": "Graduated",
}
COLOR_COLUMN, VALUE_COLUMN, LABEL_COLUMN = range(3)


def _slider(value: float) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
    slider.setValue(round(value * 100))
    return slider


class VectorStyleDialog(QDialog):
    def __init__(
        self, style: VectorStyle, layer: VectorLayer, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Style – {layer.name}")
        self.resize(560, 560)
        self._frame = layer.gdf
        self._style = style
        self._classes: list[Category | ClassBreak] = list(
            style.categories if style.renderer == "categorized" else style.breaks
        )
        self._classes_kind = style.renderer if style.renderer != "single" else "categorized"

        tabs = QTabWidget()
        tabs.addTab(self._symbol_tab(style), "Symbol")
        tabs.addTab(self._labels_tab(style.labels), "Labels")

        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.hide()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)
        self._on_renderer_changed()

    # --- symbol tab ----------------------------------------------------------------------------
    def _symbol_tab(self, style: VectorStyle) -> QWidget:
        self.renderer = QComboBox()
        for code, name in RENDERER_NAMES.items():
            self.renderer.addItem(name, code)
        self.renderer.setCurrentIndex(list(RENDERER_NAMES).index(style.renderer))
        self.renderer.currentIndexChanged.connect(self._on_renderer_changed)

        self.color_button = ColorButton(style.color, "Layer colour")
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
        self.opacity = _slider(style.opacity)
        self.fill_opacity = _slider(style.fill_opacity)

        form = QFormLayout()
        form.addRow("Renderer", self.renderer)
        self.color_label = QLabel("Colour")
        form.addRow(self.color_label, self.color_button)
        form.addRow("Line width (px)", self.line_width)
        form.addRow("Point size (px)", self.point_size)
        form.addRow("Point symbol", self.symbol)
        form.addRow("Opacity", self.opacity)
        form.addRow("Polygon fill", self.fill_opacity)

        self.classes_box = QGroupBox("Classes")
        self.classify_by = ExpressionEdit(self._frame, style.classify_by)
        self.method = QComboBox()
        for code in METHODS:
            self.method.addItem(METHOD_NAMES[code], code)
        self.method.setCurrentIndex(METHODS.index(style.method))
        self.class_count = QSpinBox(minimum=2, maximum=20, value=max(2, len(style.breaks) or 5))
        self.ramp = QComboBox()
        self.ramp.addItems(list(RAMPS))
        self.ramp.setCurrentText(style.ramp)
        self.invert_ramp = QCheckBox("Invert")
        ramp_row = QHBoxLayout()
        ramp_row.addWidget(self.ramp, 1)
        ramp_row.addWidget(self.invert_ramp)
        self.classify_button = QPushButton("Classify")
        self.classify_button.clicked.connect(self.classify)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_selected)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Colour", "Value", "Legend label"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COLOR_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(VALUE_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(LABEL_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        classes_form = QFormLayout()
        classes_form.addRow("Value", self.classify_by)
        self.method_label = QLabel("Method")
        classes_form.addRow(self.method_label, self.method)
        self.count_label = QLabel("Classes")
        classes_form.addRow(self.count_label, self.class_count)
        self.ramp_label = QLabel("Colour ramp")
        classes_form.addRow(self.ramp_label, ramp_row)
        button_row = QHBoxLayout()
        button_row.addWidget(self.classify_button)
        button_row.addWidget(self.remove_button)
        button_row.addStretch(1)
        box_layout = QVBoxLayout(self.classes_box)
        box_layout.addLayout(classes_form)
        box_layout.addLayout(button_row)
        box_layout.addWidget(self.table)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(form)
        layout.addWidget(self.classes_box, 1)
        self._fill_table()
        return page

    def _labels_tab(self, labels: LabelStyle) -> QWidget:
        self.show_labels = QCheckBox("Show labels")
        self.show_labels.setChecked(labels.enabled)
        self.label_expression = ExpressionEdit(self._frame, labels.expression)
        self.label_size = QDoubleSpinBox(minimum=5.0, maximum=30.0, value=labels.size)
        self.label_color = ColorButton(labels.color, "Label colour")
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("", self.show_labels)
        form.addRow("Text", self.label_expression)
        form.addRow("Size (pt)", self.label_size)
        form.addRow("Colour", self.label_color)
        return page

    # --- behaviour -----------------------------------------------------------------------------
    @property
    def renderer_code(self) -> str:
        return str(self.renderer.currentData())

    def _on_renderer_changed(self) -> None:
        code = self.renderer_code
        self.classes_box.setVisible(code != "single")
        graduated = code == "graduated"
        for widget in (self.method, self.method_label, self.class_count, self.count_label):
            widget.setVisible(graduated)
        for widget in (self.ramp, self.ramp_label, self.invert_ramp):
            widget.setVisible(graduated)
        self.color_label.setText("Colour" if code == "single" else "Other values")
        if code != "single" and code != self._classes_kind:
            self._classes = []  # categories and classes are not interchangeable
            self._classes_kind = code
            self._fill_table()

    def _show_error(self, message: str | None) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def classify(self) -> None:
        """Fill the table with categories or classes computed from the layer's data."""
        text = self.classify_by.text()
        if not text:
            self._show_error("Choose a field or enter an expression to classify on.")
            return
        if self._frame is None or self._frame.empty:
            self._show_error("The layer has no features to classify.")
            return
        try:
            values = Expression(text).evaluate(self._frame)
            if self.renderer_code == "categorized":
                found = unique_categories(values)
                colors = category_colors(len(found))
                self._classes = [Category(v, c) for v, c in zip(found, colors, strict=True)]
            else:
                from petrogis.core.expressions.evaluate import as_num

                numbers = as_num(values).to_numpy(dtype=float, na_value=np.nan)
                edges = class_breaks(
                    numbers, str(self.method.currentData()), self.class_count.value()
                )
                if not edges:
                    self._show_error("The value has no numbers to classify.")
                    return
                colors = ramp_colors(
                    self.ramp.currentText(), len(edges) - 1, self.invert_ramp.isChecked()
                )
                self._classes = [
                    ClassBreak(lo, hi, c)
                    for lo, hi, c in zip(edges[:-1], edges[1:], colors, strict=True)
                ]
        except (ExpressionError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._classes_kind = self.renderer_code
        self._show_error(None)
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._classes))
        for row, item in enumerate(self._classes):
            swatch = QTableWidgetItem("      ")
            swatch.setBackground(QBrush(QColor(item.color)))
            swatch.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            swatch.setToolTip("Double-click to change the colour")
            if isinstance(item, Category):
                value_text = "NULL" if item.value is None else str(item.value)
            else:
                value_text = f"{format_break(item.lower)} – {format_break(item.upper)}"
            value = QTableWidgetItem(value_text)
            value.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            label = QTableWidgetItem(item.label)
            self.table.setItem(row, COLOR_COLUMN, swatch)
            self.table.setItem(row, VALUE_COLUMN, value)
            self.table.setItem(row, LABEL_COLUMN, label)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if column == COLOR_COLUMN:
            chosen = QColorDialog.getColor(QColor(self._classes[row].color), self, "Class colour")
            if chosen.isValid():
                self.set_class_color(row, chosen.name())

    def set_class_color(self, row: int, color: str) -> None:
        self._sync_labels()
        self._classes[row] = replace(self._classes[row], color=color)
        self._fill_table()

    def _remove_selected(self) -> None:
        self._sync_labels()
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            del self._classes[row]
        self._fill_table()

    def _sync_labels(self) -> None:
        """Copy edited legend labels from the table back into the classes."""
        for row, item in enumerate(self._classes):
            cell = self.table.item(row, LABEL_COLUMN)
            text = cell.text().strip() if cell is not None else ""
            if text != item.label:
                self._classes[row] = replace(item, label=text)

    def set_color(self, color: str) -> None:
        self.color_button.set_color(color)

    @property
    def classes(self) -> list[Any]:
        self._sync_labels()
        return list(self._classes)

    def result_style(self) -> VectorStyle:
        """The edited style. Raises ``ValueError`` with a user-facing message if invalid."""
        code = self.renderer_code
        if code != "single":
            if error := self.classify_by.error():
                raise ValueError(error)
            if not self.classes:
                raise ValueError("Press Classify to create the categories or classes first.")
        if self.show_labels.isChecked():
            if not self.label_expression.text():
                raise ValueError("Choose a field or expression for the labels.")
            if error := self.label_expression.error():
                raise ValueError(f"Labels: {error}")
        classes = self.classes if code != "single" else []
        return VectorStyle(
            color=self.color_button.color,
            line_width=self.line_width.value(),
            point_size=self.point_size.value(),
            point_symbol=str(self.symbol.currentData()),
            opacity=self.opacity.value() / 100,
            fill_opacity=self.fill_opacity.value() / 100,
            renderer=code,
            classify_by=self.classify_by.text() if code != "single" else self._style.classify_by,
            categories=[c for c in classes if isinstance(c, Category)],
            breaks=[c for c in classes if isinstance(c, ClassBreak)],
            ramp=self.ramp.currentText(),
            method=str(self.method.currentData()),
            labels=LabelStyle(
                enabled=self.show_labels.isChecked(),
                expression=self.label_expression.text(),
                size=self.label_size.value(),
                color=self.label_color.color,
            ),
        )

    def accept(self) -> None:
        try:
            self.result_style()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        super().accept()


def edit_vector_style(layer: VectorLayer, parent: QWidget | None = None) -> VectorStyle | None:
    dialog = VectorStyleDialog(layer.style, layer, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_style()
    return None
