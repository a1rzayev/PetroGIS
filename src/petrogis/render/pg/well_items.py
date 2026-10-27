"""Well rendering: one scatter for all symbols plus one item that paints every visible label."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QStyleOptionGraphicsItem, QWidget

from petrogis.core.layers import WellLayer
from petrogis.core.styles import WellStyle
from petrogis.render.labels import LabelPlacement, place_labels
from petrogis.render.pg.items import LayerGraphics

LABEL_GAP_PX = 7.0
HALO_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1))


class WellLabelItem(pg.GraphicsObject):
    """Draws well names in screen space, dropping overlapping labels (see ``render.labels``).

    A single item paints all labels in one ``paint`` call. This is far cheaper than one
    QGraphicsTextItem per well, and it lets labels thin out as you zoom out.
    """

    def __init__(self, xy: np.ndarray, names: list[str], style: WellStyle) -> None:
        super().__init__()
        self._x = np.ascontiguousarray(xy[:, 0], dtype=np.float64)
        self._y = np.ascontiguousarray(xy[:, 1], dtype=np.float64)
        self._names = names
        self._widths = np.zeros(len(names))
        self._font = QFont()
        self._color = QColor(style.label_color)
        self._halo = QColor("#ffffff")
        self._symbol_size = style.symbol_size
        self._pad = 0.0
        self.placement: LabelPlacement | None = None
        """Labels drawn in the last paint (exposed for tests)."""
        if len(names):
            self._bounds = QRectF(
                float(self._x.min()),
                float(self._y.min()),
                float(np.ptp(self._x)),
                float(np.ptp(self._y)),
            )
        else:
            self._bounds = QRectF()
        self.apply_style(style)

    def apply_style(self, style: WellStyle) -> None:
        self._font = QFont()
        self._font.setPointSizeF(style.label_size)
        self._color = QColor(style.label_color)
        self._halo = QColor("#ffffff" if self._color.lightness() < 128 else "#000000")
        self._symbol_size = style.symbol_size
        metrics = QFontMetricsF(self._font)
        self._widths = np.array([metrics.horizontalAdvance(n) for n in self._names], dtype=float)
        self._height = metrics.height()
        self._ascent = metrics.ascent()
        self.viewTransformChanged()
        self.update()

    def viewTransformChanged(self) -> None:  # called by pyqtgraph on pan and zoom
        pixel = self.pixelWidth() or 0.0
        max_width = float(self._widths.max()) if self._widths.size else 0.0
        pad = (max_width + LABEL_GAP_PX + 10.0) * pixel
        if pad != self._pad:
            self.prepareGeometryChange()
            self._pad = pad

    def boundingRect(self) -> QRectF:
        if self._bounds.isNull() and not len(self._names):
            return QRectF()
        p = self._pad
        return self._bounds.adjusted(-p, -p, p, p)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        if not len(self._names):
            return
        t = painter.transform()
        px = t.m11() * self._x + t.m21() * self._y + t.dx()
        py = t.m12() * self._x + t.m22() * self._y + t.dy()
        device = painter.device()
        viewport = (0.0, 0.0, float(device.width()), float(device.height()))
        placement = self.placement = place_labels(
            px,
            py,
            self._widths,
            self._height,
            viewport,
            gap=LABEL_GAP_PX,
            symbol_size=self._symbol_size,
        )
        if not placement.size:
            return
        painter.save()
        painter.resetTransform()
        painter.setFont(self._font)
        anchors = [
            (QPointF(x, y + self._ascent), self._names[i])
            for i, x, y in zip(placement.indices, placement.x, placement.y, strict=True)
        ]
        # A 1 px halo in a contrasting colour keeps names readable on any raster colormap.
        painter.setPen(self._halo)
        for dx, dy in HALO_OFFSETS:
            for pos, name in anchors:
                painter.drawText(pos + QPointF(dx, dy), name)
        painter.setPen(self._color)
        for pos, name in anchors:
            painter.drawText(pos, name)
        painter.restore()


class WellGraphics(LayerGraphics):
    def __init__(self, layer: WellLayer) -> None:
        xy = layer.xy()
        self.symbols = pg.ScatterPlotItem(pxMode=True, symbol="o")
        if len(xy):
            self.symbols.setData(pos=xy)
        self.labels = WellLabelItem(xy, layer.names, layer.style)
        self._visible = layer.visible
        self._show_labels = layer.style.show_labels
        super().__init__([self.symbols, self.labels])
        self.apply_style(layer)

    def apply_style(self, layer: WellLayer) -> None:
        style = layer.style
        color = QColor(style.color)
        self.symbols.setSize(style.symbol_size)
        self.symbols.setBrush(pg.mkBrush(color))
        self.symbols.setPen(pg.mkPen("#ffffff", width=1))
        self.labels.apply_style(style)
        self._show_labels = style.show_labels
        self.labels.setVisible(self._visible and self._show_labels)

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        self.symbols.setVisible(visible)
        self.labels.setVisible(visible and self._show_labels)
