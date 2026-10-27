"""Convert shapely geometries into NaN-separated coordinate arrays for fast rendering.

A renderer can draw thousands of lines or polygon rings as one path object when the parts are
separated by NaN. The conversions are fully vectorised with shapely 2. There are no per-feature
Python loops.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray

_GEOM_POINT = shapely.GeometryType.POINT
_GEOM_LINESTRING = shapely.GeometryType.LINESTRING
_GEOM_LINEARRING = shapely.GeometryType.LINEARRING
_GEOM_POLYGON = shapely.GeometryType.POLYGON
_SINGLE_TYPES = (_GEOM_POINT, _GEOM_LINESTRING, _GEOM_LINEARRING, _GEOM_POLYGON)


@dataclass(frozen=True)
class GeometryPaths:
    """Render-ready coordinates for a geometry collection.

    ``points`` has shape (N, 2). ``lines`` and ``rings`` have shape (M, 2), with NaN rows between
    parts. Polygons contribute their exterior and interior rings, drawn as outlines.
    """

    points: NDArray[np.float64]
    lines: NDArray[np.float64]
    rings: NDArray[np.float64]

    @property
    def is_empty(self) -> bool:
        return self.points.size == 0 and self.lines.size == 0 and self.rings.size == 0


def _empty() -> NDArray[np.float64]:
    return np.empty((0, 2), dtype=np.float64)


def flatten_parts(geoms: ArrayLike) -> NDArray[np.object_]:
    """Explode multi-geometries and collections (recursively) into single-part geometries."""
    parts = np.asarray(geoms, dtype=object)
    parts = parts[~(shapely.is_missing(parts) | shapely.is_empty(parts))]
    while parts.size:
        types = shapely.get_type_id(parts)
        if np.isin(types, _SINGLE_TYPES).all():
            break
        parts = shapely.get_parts(parts)
        parts = parts[~shapely.is_empty(parts)]
    return parts


def nan_separated(parts: NDArray[np.object_]) -> NDArray[np.float64]:
    """Concatenate coordinates of linear parts, with a NaN row between consecutive parts."""
    if parts.size == 0:
        return _empty()
    coords, index = shapely.get_coordinates(parts, return_index=True)
    if coords.size == 0:
        return _empty()
    # Each coordinate shifts by the number of separators before it, which equals its part index
    # once parts are renumbered densely (empty parts contribute no coordinates).
    _, dense = np.unique(index, return_inverse=True)
    n_parts = int(dense[-1]) + 1
    out = np.full((coords.shape[0] + n_parts - 1, 2), np.nan)
    out[np.arange(coords.shape[0]) + dense] = coords
    return out


def to_paths(geoms: ArrayLike) -> GeometryPaths:
    """Split geometries by type into point coordinates and NaN-separated line and ring paths."""
    parts = flatten_parts(geoms)
    if parts.size == 0:
        return GeometryPaths(_empty(), _empty(), _empty())
    types = shapely.get_type_id(parts)

    point_parts = parts[types == _GEOM_POINT]
    points = shapely.get_coordinates(point_parts) if point_parts.size else _empty()

    line_parts = parts[(types == _GEOM_LINESTRING) | (types == _GEOM_LINEARRING)]
    polygons = parts[types == _GEOM_POLYGON]
    rings = shapely.get_rings(polygons) if polygons.size else np.empty(0, dtype=object)

    return GeometryPaths(points=points, lines=nan_separated(line_parts), rings=nan_separated(rings))
