"""Legend entries for each layer, shared by the legend panel and (later) figure export.

An entry describes a symbol abstractly (kind, colour, shape). Renderers turn it into pixels.
"""

from __future__ import annotations

from dataclasses import dataclass

from petrogis.core.layers import Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.core.wells import WELL_SYMBOLS, WellStatus


@dataclass(frozen=True)
class LegendEntry:
    label: str
    kind: str
    """``marker``, ``line``, ``fill``, ``well`` or ``ramp``."""
    color: str = "#000000"
    shape: str = ""
    """Point symbol code for ``marker``, well shape name for ``well``."""
    fill_opacity: float = 0.0
    colormap: str = ""
    invert: bool = False


def _vector_kind(layer: VectorLayer) -> str:
    paths = layer.paths
    if paths.rings.size:
        return "fill"
    if paths.lines.size:
        return "line"
    return "marker"


def vector_legend(layer: VectorLayer) -> list[LegendEntry]:
    kind = _vector_kind(layer)
    style = layer.style

    def entry(label: str, color: str) -> LegendEntry:
        return LegendEntry(label, kind, color, style.point_symbol, style.fill_opacity)

    classification = layer.classification()
    return [entry(c.label, c.color) for c in classification.classes]


def well_legend(layer: WellLayer) -> list[LegendEntry]:
    from petrogis.core.renderers import classify_wells

    entries = []
    for group in classify_wells(layer.style, layer.gdf):
        if group.status is WellStatus.UNKNOWN:
            label = "Other" if layer.style.symbol_by_status else ""
            entries.append(LegendEntry(label, "well", layer.style.color, "circle"))
        else:
            symbol = WELL_SYMBOLS[group.status]
            entries.append(LegendEntry(symbol.label, "well", symbol.color, symbol.shape))
    if len(entries) == 1 and entries[0].label == "Other":
        entries = [LegendEntry("", "well", layer.style.color, "circle")]
    return entries


def raster_legend(layer: RasterLayer) -> list[LegendEntry]:
    if layer.info is None:
        return []
    from petrogis.core.classify import format_break

    vmin, vmax = layer.value_range()
    label = f"{format_break(vmin)} – {format_break(vmax)}"
    style = layer.style
    return [LegendEntry(label, "ramp", colormap=style.colormap, invert=style.invert)]


def legend_entries(layer: Layer) -> list[LegendEntry]:
    """Symbols to show under the layer's name. A single unlabelled entry means one symbol."""
    if isinstance(layer, VectorLayer):
        return vector_legend(layer)
    if isinstance(layer, WellLayer):
        return well_legend(layer)
    if isinstance(layer, RasterLayer):
        return raster_legend(layer)
    return []
