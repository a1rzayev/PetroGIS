"""The Project: CRS plus an ordered list of layers, with change notifications.

This module has no Qt dependency. The UI subscribes with a plain callback, and a QObject bridge
re-emits the events as Qt signals. ``layers[0]`` is the topmost layer, matching the layer panel.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from petrogis.core.crs import DEFAULT_PROJECT_CRS, normalize_crs
from petrogis.core.extent import Extent, union_all
from petrogis.core.layers import Layer

log = logging.getLogger(__name__)


class EventKind(StrEnum):
    LAYER_ADDED = "layer_added"
    LAYER_REMOVED = "layer_removed"
    LAYER_MOVED = "layer_moved"
    LAYER_CHANGED = "layer_changed"
    """A layer property changed. ``field`` is one of "name", "visible" or "style"."""
    CRS_CHANGED = "crs_changed"
    CLEARED = "cleared"


@dataclass(frozen=True)
class ProjectEvent:
    kind: EventKind
    layer_id: str | None = None
    index: int | None = None
    field: str | None = None


Listener = Callable[[ProjectEvent], None]


class Project:
    def __init__(self, crs: Any = DEFAULT_PROJECT_CRS) -> None:
        self._crs = normalize_crs(crs)
        self._layers: list[Layer] = []
        self._listeners: list[Listener] = []
        self.path: str | None = None
        self.dirty = False

    # --- observation -----------------------------------------------------------------------
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register ``listener``. Returns a function that unsubscribes it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, event: ProjectEvent) -> None:
        if event.kind is not EventKind.CLEARED:
            self.dirty = True
        for listener in list(self._listeners):
            listener(event)

    # --- CRS ---------------------------------------------------------------------------------
    @property
    def crs(self) -> str:
        return self._crs

    def set_crs(self, crs: Any) -> None:
        """Change the project CRS. Only allowed while the project has no layers.

        Reprojecting layers that are already loaded is a planned extension (re-read every source).
        """
        if self._layers:
            raise ValueError("The project CRS can only be changed while the project is empty")
        self._crs = normalize_crs(crs)
        self._emit(ProjectEvent(EventKind.CRS_CHANGED))

    # --- layers ------------------------------------------------------------------------------
    @property
    def layers(self) -> tuple[Layer, ...]:
        return tuple(self._layers)

    def __len__(self) -> int:
        return len(self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(tuple(self._layers))

    def get(self, layer_id: str) -> Layer:
        for layer in self._layers:
            if layer.id == layer_id:
                return layer
        raise KeyError(layer_id)

    def index_of(self, layer_id: str) -> int:
        return self._layers.index(self.get(layer_id))

    def add_layer(self, layer: Layer, index: int = 0) -> None:
        """Insert ``layer`` (default: on top)."""
        if any(existing.id == layer.id for existing in self._layers):
            raise ValueError(f"Layer {layer.id} is already in the project")
        index = max(0, min(index, len(self._layers)))
        self._layers.insert(index, layer)
        log.info("Added %s layer %r", layer.kind, layer.name)
        self._emit(ProjectEvent(EventKind.LAYER_ADDED, layer.id, index))

    def remove_layer(self, layer_id: str) -> Layer:
        layer = self.get(layer_id)
        self._layers.remove(layer)
        self._emit(ProjectEvent(EventKind.LAYER_REMOVED, layer_id))
        return layer

    def move_layer(self, layer_id: str, new_index: int) -> None:
        layer = self.get(layer_id)
        new_index = max(0, min(new_index, len(self._layers) - 1))
        if self._layers.index(layer) == new_index:
            return
        self._layers.remove(layer)
        self._layers.insert(new_index, layer)
        self._emit(ProjectEvent(EventKind.LAYER_MOVED, layer_id, new_index))

    def rename_layer(self, layer_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Layer name cannot be empty")
        layer = self.get(layer_id)
        if layer.name != name:
            layer.name = name
            self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="name"))

    def set_visible(self, layer_id: str, visible: bool) -> None:
        layer = self.get(layer_id)
        if layer.visible != visible:
            layer.visible = visible
            self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="visible"))

    def set_style(self, layer_id: str, style: Any) -> None:
        layer = self.get(layer_id)
        current = getattr(layer, "style", None)
        if current is None or type(current) is not type(style):
            raise TypeError(f"{type(style).__name__} does not apply to layer {layer.name!r}")
        layer.style = style  # type: ignore[attr-defined]
        self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="style"))

    def clear(self) -> None:
        self._layers.clear()
        self.path = None
        self._emit(ProjectEvent(EventKind.CLEARED))
        self.dirty = False

    def extent(self, visible_only: bool = True) -> Extent | None:
        return union_all(
            layer.extent for layer in self._layers if layer.visible or not visible_only
        )
