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

from petrogis.core.correlation import CorrelationSection
from petrogis.core.crs import DEFAULT_PROJECT_CRS, normalize_crs
from petrogis.core.extent import Extent, union_all
from petrogis.core.layers import Layer, WellLayer
from petrogis.core.logs import WellLog
from petrogis.core.tops import PICKED, Top
from petrogis.core.trajectory import Trajectory

log = logging.getLogger(__name__)


class EventKind(StrEnum):
    LAYER_ADDED = "layer_added"
    LAYER_REMOVED = "layer_removed"
    LAYER_MOVED = "layer_moved"
    LAYER_CHANGED = "layer_changed"
    """A layer property changed. ``field`` is one of "name", "visible", "style", "logs",
    "trajectories" or "tops"."""
    CRS_CHANGED = "crs_changed"
    SECTION_CHANGED = "section_changed"
    """The correlation section (wells, template, datum or flattening) changed."""
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
        self._transformations: dict[tuple[str, str], str] = {}
        self._section: CorrelationSection | None = None

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

        To change the CRS of a project with layers, re-read them in the new CRS and pass them to
        :meth:`replace_contents`.
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
        log.debug("Added %s layer %r", layer.kind, layer.name)
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

    def attach_logs(self, layer_id: str, logs: dict[str, list[WellLog]]) -> int:
        """Attach logs (keyed by normalised well name) to a well layer.

        A log from the same source file replaces the earlier copy. Returns the number of logs added.
        """
        layer = self.get(layer_id)
        if not isinstance(layer, WellLayer):
            raise TypeError(f"Layer {layer.name!r} is not a well layer")
        added = 0
        for key, new_logs in logs.items():
            current = layer.logs.setdefault(key, [])
            for log_ in new_logs:
                current[:] = [old for old in current if old.source != log_.source]
                current.append(log_)
                added += 1
        if added:
            self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="logs"))
        return added

    def _well_layer(self, layer_id: str) -> WellLayer:
        layer = self.get(layer_id)
        if not isinstance(layer, WellLayer):
            raise TypeError(f"Layer {layer.name!r} is not a well layer")
        return layer

    def attach_trajectories(
        self,
        layer_id: str,
        trajectories: dict[str, Trajectory],
        record: dict[str, Any] | None = None,
    ) -> int:
        """Attach survey paths (keyed by normalised well name), replacing earlier ones.

        ``record`` (``path`` and ``import_options``) is kept so the project file can re-read the
        survey. Returns the number of wells updated.
        """
        layer = self._well_layer(layer_id)
        layer.trajectories.update(trajectories)
        if record is not None:
            layer.survey_imports[:] = [
                r for r in layer.survey_imports if r.get("path") != record.get("path")
            ]
            layer.survey_imports.append(record)
        if trajectories:
            self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="trajectories"))
        return len(trajectories)

    def attach_tops(
        self, layer_id: str, tops: dict[str, list[Top]], record: dict[str, Any] | None = None
    ) -> int:
        """Attach formation tops (keyed by normalised well name).

        Picks of the same surface in the same well are replaced. Returns the number of picks.
        """
        layer = self._well_layer(layer_id)
        added = 0
        for key, picks in tops.items():
            surfaces = {t.surface for t in picks}
            kept = [t for t in layer.tops.get(key, []) if t.surface not in surfaces]
            layer.tops[key] = sorted(kept + list(picks), key=lambda t: t.md)
            for surface in surfaces:  # a fresh import wins over earlier interactive edits
                layer.pick_overrides.pop((key, surface), None)
            added += len(picks)
        if record is not None:
            layer.tops_imports[:] = [
                r for r in layer.tops_imports if r.get("path") != record.get("path")
            ]
            layer.tops_imports.append(record)
        if added:
            self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="tops"))
        return added

    def set_pick(self, layer_id: str, key: str, surface: str, md: float) -> Top:
        """Pick (or move) ``surface`` in well ``key`` at measured depth ``md`` (metres)."""
        layer = self._well_layer(layer_id)
        position = layer.find(key)
        if position is None:
            raise KeyError(key)
        if not surface.strip():
            raise ValueError("Surface name cannot be empty")
        if not (md >= 0 and md == md and md != float("inf")):
            raise ValueError(f"Invalid measured depth {md!r}")
        name = str(layer.gdf["name"].iloc[position])  # type: ignore[index]
        top = Top(name, layer.key_at(position), surface, float(md), PICKED)
        key = top.name_key
        kept = [t for t in layer.tops.get(key, []) if t.surface != surface]
        layer.tops[key] = sorted([*kept, top], key=lambda t: t.md)
        layer.pick_overrides[(key, surface)] = top
        self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="tops"))
        return top

    def delete_pick(self, layer_id: str, key: str, surface: str) -> bool:
        """Remove the ``surface`` pick from well ``key``. Returns whether there was one."""
        layer = self._well_layer(layer_id)
        position = layer.find(key)
        if position is None:
            return False
        key = layer.key_at(position)
        current = layer.tops.get(key, [])
        remaining = [t for t in current if t.surface != surface]
        if len(remaining) == len(current):
            return False
        if remaining:
            layer.tops[key] = remaining
        else:
            layer.tops.pop(key, None)
        layer.pick_overrides[(key, surface)] = None
        self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="tops"))
        return True

    # --- correlation section -----------------------------------------------------------------
    @property
    def section(self) -> CorrelationSection | None:
        return self._section

    def set_section(self, section: CorrelationSection | None) -> None:
        if section != self._section:
            self._section = section
            self._emit(ProjectEvent(EventKind.SECTION_CHANGED))

    def set_style(self, layer_id: str, style: Any) -> None:
        layer = self.get(layer_id)
        current = getattr(layer, "style", None)
        if current is None or type(current) is not type(style):
            raise TypeError(f"{type(style).__name__} does not apply to layer {layer.name!r}")
        layer.style = style  # type: ignore[attr-defined]
        self._emit(ProjectEvent(EventKind.LAYER_CHANGED, layer_id, field="style"))

    def clear(self) -> None:
        self._layers.clear()
        self._transformations.clear()
        self._section = None
        self.path = None
        self._emit(ProjectEvent(EventKind.CLEARED))
        self.dirty = False

    def replace_contents(self, crs: Any, layers: list[Layer]) -> None:
        """Swap in a new CRS and layer list (top-first) in one step, keeping the project's path.

        Used after re-reading every layer in another CRS. Listeners see CLEARED, CRS_CHANGED and
        then one LAYER_ADDED per layer. The project is marked dirty.
        """
        new_crs = normalize_crs(crs)
        ids = [layer.id for layer in layers]
        if len(set(ids)) != len(ids):
            raise ValueError("Layer ids must be unique")
        self._layers.clear()
        self._emit(ProjectEvent(EventKind.CLEARED))
        self._crs = new_crs
        self._emit(ProjectEvent(EventKind.CRS_CHANGED))
        for layer in reversed(layers):
            self.add_layer(layer)
        self.dirty = True

    # --- datum transformations -----------------------------------------------------------------
    @property
    def transformations(self) -> dict[tuple[str, str], str]:
        """User-chosen PROJ pipelines per (source CRS, target CRS); others use the default."""
        return dict(self._transformations)

    def transformation_for(self, source_crs: str | None, target_crs: str) -> str | None:
        if source_crs is None:
            return None
        return self._transformations.get((normalize_crs(source_crs), normalize_crs(target_crs)))

    def set_transformation(self, source_crs: Any, target_crs: Any, pipeline: str | None) -> None:
        """Choose a pipeline for a CRS pair (``None`` restores the automatic choice).

        Callers must offer only pipelines listed by ``core.crs.transformation_options``, and the
        project loader validates stored pipelines the same way. Re-reading affected layers is the
        caller's job.
        """
        key = (normalize_crs(source_crs), normalize_crs(target_crs))
        if pipeline is None:
            changed = self._transformations.pop(key, None) is not None
        else:
            changed = self._transformations.get(key) != pipeline
            self._transformations[key] = pipeline
        if changed:
            self.dirty = True

    def extent(self, visible_only: bool = True) -> Extent | None:
        return union_all(
            layer.extent for layer in self._layers if layer.visible or not visible_only
        )
