"""Style editors per layer type: a small registry, so new layer kinds plug in without edits here."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QWidget

from petrogis.core.layers import Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.ui.dialogs.style_raster import edit_raster_style
from petrogis.ui.dialogs.style_vector import edit_vector_style
from petrogis.ui.dialogs.style_wells import edit_well_style

StyleEditor = Callable[[Any, QWidget | None], Any]
"""Shows a dialog for the layer and returns the new style, or ``None`` if cancelled."""

_EDITORS: dict[type[Layer], StyleEditor] = {}


def register_style_editor(layer_type: type[Layer], editor: StyleEditor) -> None:
    _EDITORS[layer_type] = editor


def edit_style(layer: Layer, parent: QWidget | None = None) -> Any:
    """Run the style editor for ``layer``'s type. Returns the new style or ``None``."""
    for cls in type(layer).__mro__:
        editor = _EDITORS.get(cls)
        if editor is not None:
            return editor(layer, parent)
    return None


def _raster(layer: RasterLayer, parent: QWidget | None) -> Any:
    if layer.info is None:
        return None
    return edit_raster_style(layer.style, layer.info, layer.name, parent)


register_style_editor(VectorLayer, edit_vector_style)
register_style_editor(RasterLayer, _raster)
register_style_editor(WellLayer, edit_well_style)
