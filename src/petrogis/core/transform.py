"""Apply coordinate transformations to geometries and coordinates. No Qt dependency.

Layers are normally reprojected with geopandas/pyproj defaults. When the project has an explicit
datum transformation for a CRS pair, the readers use these helpers with that PROJ pipeline.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray


def pipeline_transformer(pipeline: str) -> Any:
    """A pyproj Transformer for a pipeline in x/y (easting/northing, lon/lat) axis order."""
    from pyproj import Transformer

    return Transformer.from_pipeline(pipeline)


def transform_xy(
    transformer: Any, x: ArrayLike, y: ArrayLike, inverse: bool = False
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    from pyproj.enums import TransformDirection

    direction = TransformDirection.INVERSE if inverse else TransformDirection.FORWARD
    tx, ty = transformer.transform(
        np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), direction=direction
    )
    return np.asarray(tx, dtype=np.float64), np.asarray(ty, dtype=np.float64)


def transform_geometries(
    geoms: ArrayLike, pipeline: str, inverse: bool = False
) -> NDArray[np.object_]:
    """Transform every vertex of ``geoms`` with ``pipeline`` (vectorised over all coordinates).

    With ``inverse``, the pipeline runs backwards (project CRS to source CRS).
    """
    transformer = pipeline_transformer(pipeline)

    def apply(coords: NDArray[np.float64]) -> NDArray[np.float64]:
        tx, ty = transform_xy(transformer, coords[:, 0], coords[:, 1], inverse=inverse)
        return np.column_stack([tx, ty])

    return shapely.transform(np.asarray(geoms, dtype=object), apply)
