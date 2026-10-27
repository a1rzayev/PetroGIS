"""Symbol and label style editor for well layers."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QWidget,
)

from petrogis.core.layers import WellLayer
from petrogis.core.renderers import well_status_column
from petrogis.core.styles import WellStyle
from petrogis.ui.widgets import ColorButton, ExpressionEdit

AUTO_STATUS = "(detect automatically)"


class WellStyleDialog(QDialog):
    def __init__(self, style: WellStyle, layer: WellLayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Style – {layer.name}")
        frame = layer.gdf
        self.color = ColorButton(style.color, "Symbol colour")
        self.symbol_size = QDoubleSpinBox(minimum=2.0, maximum=40.0, value=style.symbol_size)
        self.by_status = QCheckBox("Symbols by well status (oil, gas, dry, injector…)")
        self.by_status.setChecked(style.symbol_by_status)
        self.status_field = QComboBox()
        self.status_field.addItem(AUTO_STATUS, "")
        columns = (
            [] if frame is None else [c for c in frame.columns if c not in ("geometry", "name_key")]
        )
        for column in columns:
            self.status_field.addItem(str(column), str(column))
        index = self.status_field.findData(style.status_field)
        self.status_field.setCurrentIndex(max(index, 0))
        detected = well_status_column(replace(style, status_field=""), frame)
        self.detected = QLabel(f"Detected: {detected}" if detected else "No status column found")
        self.detected.setTextFormat(Qt.TextFormat.PlainText)
        self.show_labels = QCheckBox("Show labels")
        self.show_labels.setChecked(style.show_labels)
        self.label_expression = ExpressionEdit(frame, style.label_expression)
        self.label_expression.combo.lineEdit().setPlaceholderText("Well name")
        self.label_size = QDoubleSpinBox(minimum=5.0, maximum=30.0, value=style.label_size)
        self.label_color = ColorButton(style.label_color, "Label colour")
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.hide()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Symbol colour", self.color)
        form.addRow("Symbol size (px)", self.symbol_size)
        form.addRow("", self.by_status)
        form.addRow("Status column", self.status_field)
        form.addRow("", self.detected)
        form.addRow("", self.show_labels)
        form.addRow("Label text", self.label_expression)
        form.addRow("Label size (pt)", self.label_size)
        form.addRow("Label colour", self.label_color)
        form.addRow(self.error_label)
        form.addRow(buttons)

    def result_style(self) -> WellStyle:
        return WellStyle(
            color=self.color.color,
            symbol_size=self.symbol_size.value(),
            show_labels=self.show_labels.isChecked(),
            label_size=self.label_size.value(),
            label_color=self.label_color.color,
            symbol_by_status=self.by_status.isChecked(),
            status_field=str(self.status_field.currentData()),
            label_expression=self.label_expression.text(),
        )

    def accept(self) -> None:
        if error := self.label_expression.error():
            self.error_label.setText(f"Labels: {error}")
            self.error_label.show()
            return
        super().accept()


def edit_well_style(layer: WellLayer, parent: QWidget | None = None) -> WellStyle | None:
    dialog = WellStyleDialog(layer.style, layer, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_style()
    return None
