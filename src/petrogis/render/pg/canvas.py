"""pyqtgraph implementation of the map canvas (see ``render.base.MapRenderer``)."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pyqtgraph as pg
from numpy.typing import ArrayLike
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from petrogis.core.extent import Extent
from petrogis.core.layers import Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.io.raster_io import RasterView
from petrogis.render.base import MouseMode
from petrogis.render.pg.items import (
    HighlightGraphics,
    LayerGraphics,
    RasterGraphics,
    VectorGraphics,
)
from petrogis.render.pg.well_items import WellGraphics

VIEW_CHANGE_DEBOUNCE_MS = 120

pg.setConfigOptions(antialias=False, imageAxisOrder="row-major")


GraphicsFactory = Callable[[Any], LayerGraphics]
_GRAPHICS: dict[type[Layer], GraphicsFactory] = {}


def register_graphics(layer_type: type[Layer], factory: GraphicsFactory) -> None:
    """Choose how a layer type is drawn. Subclasses use their nearest registered base class."""
    _GRAPHICS[layer_type] = factory


def graphics_for(layer: Layer) -> LayerGraphics:
    for cls in type(layer).__mro__:
        factory = _GRAPHICS.get(cls)
        if factory is not None:
            return factory(layer)
    raise TypeError(f"Cannot render layer type {type(layer).__name__}")


register_graphics(VectorLayer, VectorGraphics)  # also DelimitedLayer
register_graphics(RasterLayer, RasterGraphics)
register_graphics(WellLayer, WellGraphics)


class CoordinateAxis(pg.AxisItem):
    """Axis that labels projected coordinates as plain numbers instead of 4.47e+06."""

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        decimals = 0 if spacing >= 1 else min(6, math.ceil(-math.log10(spacing)))
        return [f"{v * scale:.{decimals}f}" for v in values]


class PgMapCanvas(pg.PlotWidget):
    cursor_moved = Signal(float, float)
    view_changed = Signal()
    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent=parent,
            background="w",
            axisItems={"left": CoordinateAxis("left"), "bottom": CoordinateAxis("bottom")},
        )
        plot = self.getPlotItem()
        plot.setAspectLocked(True, ratio=1.0)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        for name in ("left", "bottom"):
            axis = plot.getAxis(name)
            axis.enableAutoSIPrefix(False)
            axis.setPen(pg.mkPen("#888"))
            axis.setTextPen(pg.mkPen("#444"))
        self._vb: pg.ViewBox = plot.getViewBox()
        self._vb.disableAutoRange()
        self._vb.setMouseMode(pg.ViewBox.PanMode)
        self._layers: dict[str, LayerGraphics] = {}
        self.mouse_mode = MouseMode.PAN
        self._highlight = HighlightGraphics()
        for item in self._highlight.items:
            self.addItem(item)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(VIEW_CHANGE_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.view_changed)
        self._vb.sigRangeChanged.connect(self._debounce.start)
        self._vb.sigResized.connect(self._debounce.start)

        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.scene().sigMouseClicked.connect(self._on_mouse_clicked)

    # --- layers ------------------------------------------------------------------------------
    def add_layer(self, layer: Layer) -> None:
        if layer.id in self._layers:
            raise ValueError(f"Layer {layer.id} is already on the canvas")
        graphics = graphics_for(layer)
        for item in graphics.items:
            self.addItem(item)
        graphics.set_visible(layer.visible)
        self._layers[layer.id] = graphics

    def remove_layer(self, layer_id: str) -> None:
        graphics = self._layers.pop(layer_id)
        for item in graphics.items:
            self.removeItem(item)

    def clear_layers(self) -> None:
        # Not named ``clear``: pg.PlotWidget sets ``clear`` as an instance attribute forwarding to
        # PlotItem.clear, which would silently shadow an override and wipe the whole scene.
        for layer_id in list(self._layers):
            self.remove_layer(layer_id)

    def has_layer(self, layer_id: str) -> bool:
        return layer_id in self._layers

    def graphics(self, layer_id: str) -> LayerGraphics:
        return self._layers[layer_id]

    def update_layer_style(self, layer: Layer) -> None:
        graphics = self._layers[layer.id]
        if not graphics.rebuild_on_restyle:
            graphics.apply_style(layer)
            return
        # The style can change the number of symbol classes, and so the item list: rebuild.
        for item in graphics.items:
            self.removeItem(item)
        rebuilt = graphics_for(layer)
        for item in rebuilt.items:
            self.addItem(item)
        rebuilt.set_z(graphics.z)
        rebuilt.set_visible(layer.visible)
        self._layers[layer.id] = rebuilt

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        self._layers[layer_id].set_visible(visible)

    def set_layer_order(self, layer_ids_top_first: list[str]) -> None:
        count = len(layer_ids_top_first)
        for index, layer_id in enumerate(layer_ids_top_first):
            if layer_id in self._layers:
                self._layers[layer_id].set_z(float(count - index))

    def set_raster_view(self, layer: RasterLayer, view: RasterView | None) -> None:
        graphics = self._layers.get(layer.id)
        if isinstance(graphics, RasterGraphics):
            graphics.set_view(layer, view)

    # --- view --------------------------------------------------------------------------------
    def view_extent(self) -> Extent:
        (x0, x1), (y0, y1) = self._vb.viewRange()
        return Extent(x0, y0, x1, y1)

    def set_view_extent(self, extent: Extent) -> None:
        self._vb.setRange(
            xRange=(extent.xmin, extent.xmax), yRange=(extent.ymin, extent.ymax), padding=0
        )

    def viewport_size_px(self) -> tuple[int, int]:
        rect = self._vb.sceneBoundingRect()
        ratio = self.devicePixelRatioF()
        return max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio))

    def zoom_by(self, factor: float) -> None:
        """Zoom around the view centre. ``factor`` > 1 zooms in."""
        self._vb.scaleBy((1.0 / factor, 1.0 / factor))

    def set_mouse_mode(self, mode: MouseMode) -> None:
        self.mouse_mode = mode
        self._vb.setMouseMode(
            pg.ViewBox.RectMode if mode is MouseMode.ZOOM_BOX else pg.ViewBox.PanMode
        )
        cursor = (
            Qt.CursorShape.CrossCursor if mode is MouseMode.IDENTIFY else Qt.CursorShape.ArrowCursor
        )
        self.setCursor(cursor)

    def set_highlight(self, geometries: ArrayLike | None) -> None:
        """Draw ``geometries`` (project CRS) as a selection overlay. ``None`` clears it."""
        self._highlight.set_geometries(geometries)

    def map_from_scene(self, pos: QPointF) -> tuple[float, float]:
        point = self._vb.mapSceneToView(pos)
        return point.x(), point.y()

    def map_units_per_pixel(self) -> float:
        width_px, _ = self.viewport_size_px()
        return self.view_extent().width / width_px * self.devicePixelRatioF()

    # --- events ------------------------------------------------------------------------------
    def _on_mouse_moved(self, pos: QPointF) -> None:
        if self._vb.sceneBoundingRect().contains(pos):
            self.cursor_moved.emit(*self.map_from_scene(pos))

    def _on_mouse_clicked(self, event: object) -> None:
        if event.button() != Qt.MouseButton.LeftButton or event.double():  # type: ignore[attr-defined]
            return
        pos = event.scenePos()  # type: ignore[attr-defined]
        if self._vb.sceneBoundingRect().contains(pos):
            self.clicked.emit(*self.map_from_scene(pos))
