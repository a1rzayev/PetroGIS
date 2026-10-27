"""pyqtgraph graphics for each layer kind.

Each layer is drawn with a constant number of graphics items regardless of feature count:
one scatter for points, one NaN-separated curve for lines, one for polygon rings, and one
image for rasters.
"""

from __future__ import annotations

from functools import cache

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsItem

from petrogis.core.layers import RasterLayer, VectorLayer
from petrogis.core.styles import RasterStyle
from petrogis.io.raster_io import RasterView


class LayerGraphics:
    """The graphics items of one layer, which are shown, hidden, stacked and faded together."""

    def __init__(self, items: list[QGraphicsItem]) -> None:
        self.items = items

    def set_visible(self, visible: bool) -> None:
        for item in self.items:
            item.setVisible(visible)

    def set_z(self, z: float) -> None:
        # Polygons below lines below points inside a layer, so points stay clickable and visible.
        for offset, item in enumerate(self.items):
            item.setZValue(z + offset * 0.01)

    def set_opacity(self, opacity: float) -> None:
        for item in self.items:
            item.setOpacity(opacity)


class VectorGraphics(LayerGraphics):
    def __init__(self, layer: VectorLayer) -> None:
        paths = layer.paths
        self.rings = pg.PlotCurveItem(connect="finite")
        self.lines = pg.PlotCurveItem(connect="finite")
        self.points = pg.ScatterPlotItem(pxMode=True)
        if paths.rings.size:
            self.rings.setData(paths.rings[:, 0], paths.rings[:, 1])
        if paths.lines.size:
            self.lines.setData(paths.lines[:, 0], paths.lines[:, 1])
        if paths.points.size:
            self.points.setData(pos=paths.points)
        super().__init__([self.rings, self.lines, self.points])
        self.apply_style(layer)

    def apply_style(self, layer: VectorLayer) -> None:
        style = layer.style
        color = QColor(style.color)
        pen = pg.mkPen(color, width=style.line_width, cosmetic=True)
        self.rings.setPen(pen)
        self.lines.setPen(pen)
        self.points.setSymbol(style.point_symbol)
        self.points.setSize(style.point_size)
        self.points.setBrush(pg.mkBrush(color))
        self.points.setPen(pg.mkPen(color.darker(150), width=1))
        self.set_opacity(style.opacity)


@cache
def colormap_lut(name: str, invert: bool) -> np.ndarray:
    """A 256-entry RGBA lookup table for a colormap name from ``core.styles.COLORMAPS``."""
    if name == "gray":
        cmap = pg.ColorMap([0.0, 1.0], [(0, 0, 0), (255, 255, 255)])
    else:
        cmap = pg.colormap.get(name)
    lut = cmap.getLookupTable(nPts=256, alpha=True)
    return lut[::-1].copy() if invert else lut


class RasterGraphics(LayerGraphics):
    def __init__(self, layer: RasterLayer) -> None:
        self.image = pg.ImageItem(axisOrder="row-major")
        super().__init__([self.image])
        self.apply_style(layer)

    def set_view(self, layer: RasterLayer, view: RasterView | None) -> None:
        if view is None:
            self.image.clear()
            return
        # Row 0 of the raster is north, but the view's y axis points up, so flip vertically.
        data = np.ma.filled(view.data.astype(np.float32), np.nan)[::-1]
        vmin, vmax = layer.value_range()
        self.image.setImage(data, autoLevels=False, levels=(vmin, vmax))
        e = view.extent
        self.image.setRect(QRectF(e.xmin, e.ymin, e.width, e.height))

    def apply_style(self, layer: RasterLayer) -> None:
        style: RasterStyle = layer.style
        self.image.setLookupTable(colormap_lut(style.colormap, style.invert))
        if layer.info is not None:
            self.image.setLevels(layer.value_range())
        self.set_opacity(style.opacity)
