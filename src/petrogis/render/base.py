"""Renderer-agnostic map canvas interface.

The UI depends only on this protocol. ``render/pg`` implements it with pyqtgraph, and a
QGraphicsView or OpenGL canvas can replace it later without touching ``ui/``.

Every implementation must also be a QWidget that provides these Qt signals:

- ``cursor_moved(float, float)``: the cursor position in map (project CRS) coordinates
- ``view_changed()``: the visible extent or viewport size changed (debounced)
- ``clicked(float, float)``: a left click in map coordinates (not emitted at the end of a drag)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from numpy.typing import ArrayLike

from petrogis.core.extent import Extent
from petrogis.core.layers import Layer, RasterLayer
from petrogis.io.raster_io import RasterView


class MouseMode(StrEnum):
    PAN = "pan"
    ZOOM_BOX = "zoom_box"
    IDENTIFY = "identify"


class MapRenderer(Protocol):
    def add_layer(self, layer: Layer) -> None: ...

    def remove_layer(self, layer_id: str) -> None: ...

    def clear_layers(self) -> None: ...

    def update_layer_style(self, layer: Layer) -> None: ...

    def set_layer_visible(self, layer_id: str, visible: bool) -> None: ...

    def set_layer_order(self, layer_ids_top_first: list[str]) -> None: ...

    def set_raster_view(self, layer: RasterLayer, view: RasterView | None) -> None: ...

    def view_extent(self) -> Extent: ...

    def set_view_extent(self, extent: Extent) -> None: ...

    def viewport_size_px(self) -> tuple[int, int]: ...

    def zoom_by(self, factor: float) -> None: ...

    def set_mouse_mode(self, mode: MouseMode) -> None: ...

    def set_highlight(self, geometries: ArrayLike | None) -> None: ...
