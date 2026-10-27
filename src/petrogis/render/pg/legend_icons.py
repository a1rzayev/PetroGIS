"""Draw ``core.legend.LegendEntry`` symbols as small icons for the legend panel."""

from __future__ import annotations

from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from petrogis.core.legend import LegendEntry
from petrogis.render.pg.items import colormap_lut
from petrogis.render.pg.symbols import marker_shape

ICON_WIDTH, ICON_HEIGHT = 28, 16


def _symbol_path(symbol: QPainterPath | str) -> QPainterPath:
    return symbol if isinstance(symbol, QPainterPath) else Symbols.get(symbol, Symbols["o"])


def _draw_marker(
    painter: QPainter, path: QPainterPath, color: QColor, filled: bool, stroked: bool
) -> None:
    size = ICON_HEIGHT - 2
    painter.save()
    painter.translate(ICON_WIDTH / 2, ICON_HEIGHT / 2)
    painter.scale(size, size)
    pen = QPen(color.darker(150) if not stroked else color, 1.4 / size)
    painter.setPen(pen)
    painter.setBrush(color if filled else Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def legend_icon(entry: LegendEntry) -> QIcon:
    pixmap = QPixmap(ICON_WIDTH, ICON_HEIGHT)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(entry.color)
    if entry.kind == "line":
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(2, ICON_HEIGHT - 4), QPointF(ICON_WIDTH - 2, 4))
    elif entry.kind == "fill":
        fill = QColor(color)
        fill.setAlphaF(max(entry.fill_opacity, 0.0))
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(fill)
        painter.drawRect(QRectF(3, 2, ICON_WIDTH - 6, ICON_HEIGHT - 4))
    elif entry.kind == "ramp":
        lut = colormap_lut(entry.colormap, entry.invert)
        gradient = QLinearGradient(2, 0, ICON_WIDTH - 2, 0)
        for i in range(0, 256, 32):
            r, g, b, *_ = (int(v) for v in lut[i])
            gradient.setColorAt(i / 255, QColor(r, g, b))
        r, g, b, *_ = (int(v) for v in lut[255])
        gradient.setColorAt(1.0, QColor(r, g, b))
        painter.setPen(QPen(QColor("#888888"), 1))
        painter.setBrush(gradient)
        painter.drawRect(QRectF(2, 3, ICON_WIDTH - 4, ICON_HEIGHT - 6))
    elif entry.kind == "well":
        shape = marker_shape(entry.shape)
        _draw_marker(painter, _symbol_path(shape.symbol), color, shape.filled, shape.stroked)
    else:  # marker
        _draw_marker(painter, _symbol_path(entry.shape or "o"), color, True, False)
    painter.end()
    return QIcon(pixmap)
