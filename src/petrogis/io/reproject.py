"""Reproject GeoDataFrames to the project CRS, honouring an explicit datum transformation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from petrogis.core.crs import crs_equal, normalize_crs

if TYPE_CHECKING:
    import geopandas as gpd


def reproject_frame(
    gdf: gpd.GeoDataFrame, target_crs: str, pipeline: str | None = None
) -> gpd.GeoDataFrame:
    """``gdf`` in ``target_crs``. With ``pipeline``, every vertex goes through that pipeline."""
    import geopandas as gpd

    from petrogis.core.transform import transform_geometries

    if crs_equal(gdf.crs, target_crs):
        return gdf
    if pipeline is None:
        return gdf.to_crs(target_crs)
    geometry = transform_geometries(gdf.geometry.values, pipeline)
    attributes = gdf.drop(columns=gdf.geometry.name)
    return gpd.GeoDataFrame(attributes, geometry=list(geometry), crs=target_crs)


TransformationLookup = Callable[[str], "str | None"]
"""Maps a layer's source CRS to the PROJ pipeline to use (``None`` = automatic)."""


def resolve_transformation(
    transformation: str | TransformationLookup | None, source_crs: Any
) -> str | None:
    """A fixed pipeline, or the one a lookup returns for ``source_crs``."""
    if transformation is None or isinstance(transformation, str):
        return transformation
    return transformation(normalize_crs(source_crs))
