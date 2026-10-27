"""Keep the map canvas in sync with the project and schedule view-dependent raster reads."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from petrogis.core.layers import RasterLayer
from petrogis.core.project import Project
from petrogis.io.raster_io import RasterView, read_raster_view
from petrogis.render.pg.canvas import PgMapCanvas
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.workers import TaskRunner

log = logging.getLogger(__name__)


class MapController(QObject):
    raster_read_failed = Signal(str, str)  # layer name, message

    def __init__(
        self,
        project: Project,
        bridge: ProjectBridge,
        canvas: PgMapCanvas,
        tasks: TaskRunner,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.canvas = canvas
        self.tasks = tasks
        # Per-raster request counter: results from superseded requests are dropped.
        self._raster_tokens: dict[str, int] = {}
        # Last (extent, size) requested per raster, so an unchanged view doesn't trigger a re-read.
        self._raster_requests: dict[str, tuple[tuple[float, ...], tuple[int, int]]] = {}

        bridge.layer_added.connect(self._on_layer_added)
        bridge.layer_removed.connect(self._on_layer_removed)
        bridge.layer_moved.connect(lambda *_: self._sync_order())
        bridge.layer_changed.connect(self._on_layer_changed)
        bridge.cleared.connect(self._on_cleared)
        canvas.view_changed.connect(self.refresh_rasters)

    def _sync_order(self) -> None:
        self.canvas.set_layer_order([layer.id for layer in self.project.layers])

    def _on_layer_added(self, layer_id: str, _index: int) -> None:
        layer = self.project.get(layer_id)
        self.canvas.add_layer(layer)
        self._sync_order()
        if isinstance(layer, RasterLayer):
            self.request_raster(layer)

    def _on_layer_removed(self, layer_id: str) -> None:
        self._raster_tokens.pop(layer_id, None)
        self._raster_requests.pop(layer_id, None)
        if self.canvas.has_layer(layer_id):
            self.canvas.remove_layer(layer_id)

    def _on_layer_changed(self, layer_id: str, field: str) -> None:
        layer = self.project.get(layer_id)
        if field == "visible":
            self.canvas.set_layer_visible(layer_id, layer.visible)
            if layer.visible and isinstance(layer, RasterLayer):
                self.request_raster(layer)
        elif field in ("style", "trajectories"):
            self.canvas.update_layer_style(layer)

    def _on_cleared(self) -> None:
        self._raster_tokens.clear()
        self._raster_requests.clear()
        self.canvas.clear_layers()

    def refresh_rasters(self) -> None:
        for layer in self.project.layers:
            if isinstance(layer, RasterLayer) and layer.visible:
                self.request_raster(layer)

    def request_raster(self, layer: RasterLayer) -> None:
        if layer.info is None or not layer.visible:
            return
        extent = self.canvas.view_extent()
        size = self.canvas.viewport_size_px()
        key = (tuple(round(v, 3) for v in extent.as_tuple()), size)
        if self._raster_requests.get(layer.id) == key:
            return
        self._raster_requests[layer.id] = key
        token = self._raster_tokens.get(layer.id, 0) + 1
        self._raster_tokens[layer.id] = token

        def apply(view: RasterView | None) -> None:
            if self._raster_tokens.get(layer.id) != token:
                return  # superseded by a newer request, or the layer was removed
            self.canvas.set_raster_view(layer, view)

        def failed(exc: BaseException) -> None:
            self._raster_requests.pop(layer.id, None)
            log.error("Reading raster %s failed", layer.source, exc_info=exc)
            self.raster_read_failed.emit(layer.name, str(exc))

        self.tasks.submit(
            read_raster_view,
            layer.source,
            self.project.crs,
            extent,
            size,
            layer.band,
            assume_crs=layer.assumed_crs,
            transformation=self.project.transformation_for(layer.source_crs, self.project.crs),
            on_result=apply,
            on_error=failed,
        )
