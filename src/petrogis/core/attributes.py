"""Attribute formatting, filtering, sorting and identify. This module has no Qt dependency.

The attribute table and the identify popup both use these helpers. They work on row-position
arrays, so a 100k-row table is sorted and filtered with vectorised pandas operations and never
copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from petrogis.core.layers import FeatureLayer, WellLayer

if TYPE_CHECKING:
    import pandas as pd

    from petrogis.core.project import Project

HIDDEN_COLUMNS = {"geometry", "name_key"}


def format_value(value: Any) -> str:
    """Display text for one attribute value. Missing values become an empty string."""
    import pandas as pd

    if value is None:
        return ""
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool | np.bool_) and missing:
        return ""
    if isinstance(value, float | np.floating):
        return f"{float(value):.10g}"
    return str(value)


def visible_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns if c not in HIDDEN_COLUMNS]


def filter_rows(df: pd.DataFrame, text: str) -> NDArray[np.intp]:
    """Positions of rows where any visible column contains ``text`` (case-insensitive)."""
    if not text.strip():
        return np.arange(len(df), dtype=np.intp)
    needle = text.strip().lower()
    mask = np.zeros(len(df), dtype=bool)
    for column in visible_columns(df):
        as_text = df[column].astype("string").str.lower()
        mask |= as_text.str.contains(needle, regex=False, na=False).to_numpy()
    return np.flatnonzero(mask).astype(np.intp)


def sort_rows(
    df: pd.DataFrame, rows: NDArray[np.intp], column: str, ascending: bool = True
) -> NDArray[np.intp]:
    """Reorder ``rows`` by ``column``. Missing values always sort last. The sort is stable."""
    import pandas as pd

    values = df[column].iloc[rows].reset_index(drop=True)
    if not pd.api.types.is_numeric_dtype(values):
        values = values.astype("string").str.lower()
    order = values.sort_values(ascending=ascending, kind="stable", na_position="last").index
    return rows[order.to_numpy()]


@dataclass(frozen=True)
class IdentifyHit:
    layer_id: str
    layer_name: str
    position: int
    title: str
    attributes: list[tuple[str, str]]


def _feature_title(layer: FeatureLayer, position: int) -> str:
    assert layer.gdf is not None
    for column in ("name", "Name", "NAME", "id", "ID"):
        if column in layer.gdf.columns:
            text = format_value(layer.gdf[column].iloc[position])
            if text:
                return text
    return f"Feature {position + 1}"


def identify(
    project: Project, x: float, y: float, tolerance: float, max_per_layer: int = 20
) -> list[IdentifyHit]:
    """Features within ``tolerance`` of (x, y) in visible feature layers.

    Layers are searched top-most first, and features within a layer nearest first.
    """
    import shapely

    point = shapely.Point(x, y)
    hits: list[IdentifyHit] = []
    for layer in project.layers:
        if not (isinstance(layer, FeatureLayer) and layer.visible) or layer.gdf is None:
            continue
        positions = layer.features_at(x, y, tolerance)
        if not positions:
            continue
        geoms = layer.gdf.geometry.values[positions]
        order = np.argsort(shapely.distance(geoms, point), kind="stable")
        columns = visible_columns(layer.gdf)
        for k in order[:max_per_layer]:
            pos = positions[int(k)]
            row = layer.gdf.iloc[pos]
            attributes = [(c, format_value(row[c])) for c in columns]
            attributes.append(("geometry", str(row.geometry.geom_type)))
            if isinstance(layer, WellLayer):
                logs = layer.logs_for(pos)
                if logs:
                    attributes.append(("logs", ", ".join(Path(log.source).name for log in logs)))
            hits.append(
                IdentifyHit(layer.id, layer.name, pos, _feature_title(layer, pos), attributes)
            )
    return hits
