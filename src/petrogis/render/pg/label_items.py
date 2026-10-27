"""One graphics item that paints every visible label of a layer in screen space."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QStyleOptionGraphicsItem, QWidget

from petrogis.render.labels import LabelPlacement, place_labels

LABEL_GAP_PX = 7.0
HALO_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1))


class LabelItem(pg.GraphicsObject):
    """Draws labels at anchor points, dropping overlapping ones (see ``render.labels``).

    A single item paints all labels in one ``paint`` call. This is far cheaper than one
    QGraphicsTextItem per feature, and it lets labels thin out as you zoom out. Labels are drawn
    as plain text, never markup, whatever the data contains.
    """

    def __init__(
        self,
        xy: np.ndarray,
        texts: list[str | None],
        size: float,
        color: str,
        symbol_size: float = 0.0,
    ) -> None:
        super().__init__()
        self._x = np.empty(0)
        self._y = np.empty(0)
        self._texts: list[str] = []
        self._widths = np.zeros(0)
        self._font = QFont()
        self._color = QColor(color)
        self._halo = QColor("#ffffff")
        self._symbol_size = symbol_size
        self._height = 0.0
        self._ascent = 0.0
        self._pad = 0.0
        self._bounds = QRectF()
        self.placement: LabelPlacement | None = None
        """Labels drawn in the last paint (exposed for tests)."""
        self.set_labels(xy, texts)
        self.set_appearance(size, color, symbol_size)

    def set_labels(self, xy: np.ndarray, texts: list[str | None]) -> None:
        """Anchor positions (N, 2) and their texts. ``None``, empty text and NaN anchors are
        skipped."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        if len(texts) != len(xy):
            texts = [None] * len(xy)
        keep = np.array([bool(t) for t in texts], dtype=bool).reshape(-1)
        keep &= np.isfinite(xy).all(axis=1)
        self.prepareGeometryChange()
        self._x = np.ascontiguousarray(xy[keep, 0])
        self._y = np.ascontiguousarray(xy[keep, 1])
        self._texts = [str(t) for t, k in zip(texts, keep, strict=True) if k]
        if self._texts:
            self._bounds = QRectF(
                float(self._x.min()),
                float(self._y.min()),
                float(np.ptp(self._x)),
                float(np.ptp(self._y)),
            )
        else:
            self._bounds = QRectF()
        self._measure()

    def set_appearance(self, size: float, color: str, symbol_size: float = 0.0) -> None:
        self._font = QFont()
        self._font.setPointSizeF(size)
        self._color = QColor(color)
        self._halo = QColor("#ffffff" if self._color.lightness() < 128 else "#000000")
        self._symbol_size = symbol_size
        self._measure()

    @property
    def texts(self) -> list[str]:
        return self._texts

    def _measure(self) -> None:
        metrics = QFontMetricsF(self._font)
        self._widths = np.array([metrics.horizontalAdvance(t) for t in self._texts], dtype=float)
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
        if not self._texts:
            return QRectF()
        p = self._pad
        return self._bounds.adjusted(-p, -p, p, p)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        if not self._texts:
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
            (QPointF(x, y + self._ascent), self._texts[i])
            for i, x, y in zip(placement.indices, placement.x, placement.y, strict=True)
        ]
        # A 1 px halo in a contrasting colour keeps labels readable on any raster colormap.
        painter.setPen(self._halo)
        for dx, dy in HALO_OFFSETS:
            for pos, text in anchors:
                painter.drawText(pos + QPointF(dx, dy), text)
        painter.setPen(self._color)
        for pos, text in anchors:
            painter.drawText(pos, text)
        painter.restore()
