"""Colormap and value-range editor for raster layers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from petrogis.core.layers import RasterInfo
from petrogis.core.styles import COLORMAPS, RasterStyle
from petrogis.ui.text import plain_label


class RasterStyleDialog(QDialog):
    def __init__(
        self, style: RasterStyle, info: RasterInfo, layer_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Style – {layer_name}")
        self._info = info

        self.colormap = QComboBox()
        self.colormap.addItems(COLORMAPS)
        self.colormap.setCurrentText(style.colormap)
        self.invert = QCheckBox("Invert colours")
        self.invert.setChecked(style.invert)

        span = max(info.stats_max - info.stats_min, 1.0)
        lo, hi = info.stats_min - span, info.stats_max + span
        self.vmin = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=3)
        self.vmax = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=3)
        self.vmin.setValue(style.vmin if style.vmin is not None else info.p2)
        self.vmax.setValue(style.vmax if style.vmax is not None else info.p98)
        self._auto = style.vmin is None and style.vmax is None
        self.vmin.valueChanged.connect(self._mark_manual)
        self.vmax.valueChanged.connect(self._mark_manual)
        auto_button = QPushButton("Auto (2–98%)")
        auto_button.clicked.connect(self.reset_range)
        full_button = QPushButton("Min / max")
        full_button.clicked.connect(self._full_range)
        range_row = QHBoxLayout()
        range_row.addWidget(auto_button)
        range_row.addWidget(full_button)

        self.opacity = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.opacity.setValue(round(style.opacity * 100))

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b00020")
        self.vmin.valueChanged.connect(self._validate)
        self.vmax.valueChanged.connect(self._validate)

        stats = plain_label(
            f"Data range {info.stats_min:.2f} – {info.stats_max:.2f}"
            + (f"  ({info.description})" if info.description else "")
        )
        form = QFormLayout(self)
        form.addRow(stats)
        form.addRow("Colormap", self.colormap)
        form.addRow("", self.invert)
        form.addRow("Minimum", self.vmin)
        form.addRow("Maximum", self.vmax)
        form.addRow("", range_row)
        form.addRow("Opacity", self.opacity)
        form.addRow(self.error)
        form.addRow(self.buttons)

    def _mark_manual(self) -> None:
        self._auto = False

    def _validate(self) -> None:
        ok = self.vmin.value() < self.vmax.value()
        self.error.setText("" if ok else "Minimum must be less than maximum")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def reset_range(self) -> None:
        self.vmin.setValue(self._info.p2)
        self.vmax.setValue(self._info.p98)
        self._auto = True

    def _full_range(self) -> None:
        self.vmin.setValue(self._info.stats_min)
        self.vmax.setValue(self._info.stats_max)

    def result_style(self) -> RasterStyle:
        return RasterStyle(
            colormap=self.colormap.currentText(),
            vmin=None if self._auto else self.vmin.value(),
            vmax=None if self._auto else self.vmax.value(),
            invert=self.invert.isChecked(),
            opacity=self.opacity.value() / 100,
        )


def edit_raster_style(
    style: RasterStyle, info: RasterInfo, layer_name: str, parent: QWidget | None = None
) -> RasterStyle | None:
    dialog = RasterStyleDialog(style, info, layer_name, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_style()
    return None
