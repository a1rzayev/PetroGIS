"""pyqtgraph graphics for each layer kind.

The number of graphics items never depends on the feature count. A vector layer gets, per symbol
class, one fill path for polygons, one NaN-separated curve each for rings and lines, and one
scatter for points, plus one label item. A raster is one image.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any

import numpy as np
import pyqtgraph as pg
from numpy.typing import ArrayLike
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from petrogis.core.geometry import GeometryPaths, label_points, to_paths
from petrogis.core.layers import RasterLayer, VectorLayer
from petrogis.core.styles import RasterStyle
from petrogis.io.raster_io import RasterView

log = logging.getLogger(__name__)


class LayerGraphics:
    """The graphics items of one layer, which are shown, hidden, stacked and faded together."""

    rebuild_on_restyle = False
    """True when a style change can change the item list, so the canvas rebuilds the graphics."""

    def __init__(self, items: list[QGraphicsItem]) -> None:
        self.items = items
        self.z = 0.0
        self.error: str | None = getattr(self, "error", None)
        """Why part of the style could not be applied, for the status bar."""

    def set_visible(self, visible: bool) -> None:
        for item in self.items:
            item.setVisible(visible)

    def set_z(self, z: float) -> None:
        # Items are stacked in list order inside the layer's slot (fills, outlines, lines, points,
        # labels), so points stay clickable and visible. Offsets stay below the next layer's z.
        self.z = z
        step = 0.9 / max(len(self.items), 1)
        for offset, item in enumerate(self.items):
            item.setZValue(z + offset * step)

    def set_opacity(self, opacity: float) -> None:
        for item in self.items:
            item.setOpacity(opacity)

    def apply_style(self, layer: Any) -> None:
        raise NotImplementedError


class FillItem(pg.GraphicsObject):
    """All polygon fills of one symbol class as a single path, painted with the non-zero rule.

    ``to_paths`` orients exteriors counter-clockwise and holes clockwise, so holes stay empty
    and overlapping polygons stay filled.
    """

    def __init__(self, rings: np.ndarray) -> None:
        super().__init__()
        self._path: QPainterPath = pg.arrayToQPath(rings[:, 0], rings[:, 1], connect="finite")
        self._path.setFillRule(Qt.FillRule.WindingFill)
        self._bounds = self._path.boundingRect()
        self._brush = pg.mkBrush(None)

    def set_brush(self, color: str, alpha: float) -> None:
        qcolor = QColor(color)
        qcolor.setAlphaF(max(0.0, min(1.0, alpha)))
        self._brush = pg.mkBrush(qcolor)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._brush)
        painter.drawPath(self._path)


class _ClassItems:
    """The items drawing one symbol class: optional fill, rings, lines and points."""

    def __init__(self, paths: GeometryPaths, color: str, with_fill: bool) -> None:
        self.color = color
        self.fill = FillItem(paths.rings) if with_fill and paths.rings.size else None
        self.rings = pg.PlotCurveItem(connect="finite") if paths.rings.size else None
        self.lines = pg.PlotCurveItem(connect="finite") if paths.lines.size else None
        self.points = pg.ScatterPlotItem(pxMode=True) if paths.points.size else None
        if self.rings is not None:
            self.rings.setData(paths.rings[:, 0], paths.rings[:, 1])
        if self.lines is not None:
            self.lines.setData(paths.lines[:, 0], paths.lines[:, 1])
        if self.points is not None:
            self.points.setData(pos=paths.points)


class VectorGraphics(LayerGraphics):
    """A vector layer drawn by symbol class (see ``core.renderers.classify_features``)."""

    rebuild_on_restyle = True

    def __init__(self, layer: VectorLayer) -> None:
        style = layer.style
        classification = layer.classification()
        self.error = classification.error
        geoms = layer.gdf.geometry.values if layer.gdf is not None else None
        n = layer.feature_count
        with_fill = style.fill_opacity > 0
        self.classes: list[_ClassItems] = []
        for symbol in classification.classes:
            if symbol.rows.size == 0:
                continue
            if symbol.rows.size == n or geoms is None:
                paths = layer.paths  # cached on the layer, built on the loader thread
            else:
                paths = to_paths(geoms[symbol.rows])
            self.classes.append(_ClassItems(paths, symbol.color, with_fill))
        if not self.classes:  # an empty layer still gets its three (empty) items
            self.classes.append(_ClassItems(layer.paths, style.color, False))
            empty = self.classes[0]
            empty.rings = pg.PlotCurveItem(connect="finite")
            empty.lines = pg.PlotCurveItem(connect="finite")
            empty.points = pg.ScatterPlotItem(pxMode=True)
        self.labels = self._make_labels(layer)
        items: list[QGraphicsItem] = []
        for attr in ("fill", "rings", "lines", "points"):
            items.extend(getattr(c, attr) for c in self.classes if getattr(c, attr) is not None)
        if self.labels is not None:
            items.append(self.labels)
        super().__init__(items)
        self.apply_style(layer)

    # Single-class layers expose their items directly (used by tests and the highlight code).
    @property
    def rings(self) -> pg.PlotCurveItem | None:
        return self.classes[0].rings

    @property
    def lines(self) -> pg.PlotCurveItem | None:
        return self.classes[0].lines

    @property
    def points(self) -> pg.ScatterPlotItem | None:
        return self.classes[0].points

    @property
    def fills(self) -> list[FillItem]:
        return [c.fill for c in self.classes if c.fill is not None]

    def _make_labels(self, layer: VectorLayer) -> Any:
        settings = layer.style.labels
        if not settings.enabled or not settings.expression.strip() or layer.gdf is None:
            return None
        from petrogis.core.expressions import ExpressionError
        from petrogis.core.renderers import label_texts
        from petrogis.render.pg.label_items import LabelItem

        try:
            texts = label_texts(settings.expression, layer.gdf)
        except ExpressionError as exc:
            message = f"Labels: {exc}"
            self.error = f"{self.error}; {message}" if self.error else message
            log.warning("Labels for %s: %s", layer.name, exc)
            return None
        anchors = label_points(layer.gdf.geometry.values)
        symbol = layer.style.point_size if layer.paths.points.size else 0.0
        return LabelItem(anchors, texts, settings.size, settings.color, symbol)

    def apply_style(self, layer: VectorLayer) -> None:
        style = layer.style
        for items in self.classes:
            color = QColor(items.color)
            pen = pg.mkPen(color, width=style.line_width, cosmetic=True)
            if items.fill is not None:
                items.fill.set_brush(items.color, style.fill_opacity)
            if items.rings is not None:
                items.rings.setPen(pen)
            if items.lines is not None:
                items.lines.setPen(pen)
            if items.points is not None:
                items.points.setSymbol(style.point_symbol)
                items.points.setSize(style.point_size)
                items.points.setBrush(pg.mkBrush(color))
                items.points.setPen(pg.mkPen(color.darker(150), width=1))
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


HIGHLIGHT_Z = 1e6
HIGHLIGHT_COLOR = "#00e5ff"


class HighlightGraphics(LayerGraphics):
    """Selection overlay: thick cyan outlines and rings, drawn above every layer."""

    def __init__(self) -> None:
        pen = pg.mkPen(HIGHLIGHT_COLOR, width=3, cosmetic=True)
        self.rings = pg.PlotCurveItem(connect="finite", pen=pen)
        self.lines = pg.PlotCurveItem(connect="finite", pen=pen)
        self.points = pg.ScatterPlotItem(
            pxMode=True, symbol="o", size=16, brush=None, pen=pg.mkPen(HIGHLIGHT_COLOR, width=3)
        )
        super().__init__([self.rings, self.lines, self.points])
        self.set_z(HIGHLIGHT_Z)

    def set_geometries(self, geometries: ArrayLike | None) -> None:
        paths = to_paths([] if geometries is None else geometries)
        for item, coords in ((self.rings, paths.rings), (self.lines, paths.lines)):
            if coords.size:
                item.setData(coords[:, 0], coords[:, 1])
            else:
                item.clear()
        if paths.points.size:
            self.points.setData(pos=paths.points)
        else:
            self.points.clear()

    def apply_style(self, layer: Any) -> None:  # the overlay has a fixed style
        pass
