"""Layer model: data, style and metadata for each map layer, always in the project CRS.

Layers hold no rendering or Qt state. Expensive derived data, such as render paths and the
spatial index, is computed lazily and cached on the layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from petrogis.core.extent import Extent
from petrogis.core.styles import RasterStyle, VectorStyle

if TYPE_CHECKING:
    import geopandas as gpd

    from petrogis.core.geometry import GeometryPaths


class LayerKind(StrEnum):
    VECTOR = "vector"
    RASTER = "raster"


def new_layer_id() -> str:
    return uuid.uuid4().hex


@dataclass(eq=False)
class Layer:
    name: str
    source: str
    """Path of the data source on disk. The project file stores it relative to the project file."""
    visible: bool = True
    id: str = field(default_factory=new_layer_id)

    @property
    def kind(self) -> LayerKind:
        raise NotImplementedError

    @property
    def extent(self) -> Extent | None:
        raise NotImplementedError


@dataclass(eq=False)
class VectorLayer(Layer):
    gdf: gpd.GeoDataFrame | None = None
    """Features already reprojected to the project CRS."""
    source_layer: str | None = None
    """Layer name inside multi-layer sources (GeoPackage, KML)."""
    style: VectorStyle = field(default_factory=VectorStyle)
    _paths: GeometryPaths | None = field(default=None, init=False, repr=False)

    @property
    def kind(self) -> LayerKind:
        return LayerKind.VECTOR

    @property
    def extent(self) -> Extent | None:
        if self.gdf is None or self.gdf.empty:
            return None
        extent = Extent.from_bounds(self.gdf.total_bounds)
        return extent if extent.is_valid() else None

    @property
    def feature_count(self) -> int:
        return 0 if self.gdf is None else len(self.gdf)

    @property
    def paths(self) -> GeometryPaths:
        """NaN-separated render paths, built once on first access."""
        if self._paths is None:
            from petrogis.core.geometry import to_paths

            self._paths = to_paths(self.gdf.geometry.values if self.gdf is not None else [])
        return self._paths

    def features_at(self, x: float, y: float, tolerance: float) -> list[int]:
        """Positional indices of features within ``tolerance`` map units of (x, y).

        Uses the GeoDataFrame's lazily built spatial index, so it never scans every feature.
        """
        if self.gdf is None or self.gdf.empty:
            return []
        import shapely

        probe = shapely.Point(x, y).buffer(tolerance)
        hits = self.gdf.sindex.query(probe, predicate="intersects")
        return sorted(int(i) for i in hits)


@dataclass(frozen=True)
class RasterInfo:
    """Raster metadata needed for display, with ``extent`` already in the project CRS."""

    crs: str
    """Native CRS of the file."""
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    extent: Extent
    """Bounds in the project CRS."""
    native_resolution: tuple[float, float]
    """Approximate pixel size in project CRS units (x, y)."""
    stats_min: float
    stats_max: float
    p2: float
    p98: float
    description: str = ""


@dataclass(eq=False)
class RasterLayer(Layer):
    info: RasterInfo | None = None
    band: int = 1
    style: RasterStyle = field(default_factory=RasterStyle)

    @property
    def kind(self) -> LayerKind:
        return LayerKind.RASTER

    @property
    def extent(self) -> Extent | None:
        return None if self.info is None else self.info.extent

    def value_range(self) -> tuple[float, float]:
        """Colour range from the style, falling back to the 2-98 percentile stretch."""
        if self.info is None:
            raise ValueError(f"Raster layer {self.name!r} has no metadata loaded")
        vmin = self.style.vmin if self.style.vmin is not None else self.info.p2
        vmax = self.style.vmax if self.style.vmax is not None else self.info.p98
        if vmax <= vmin:
            vmax = vmin + 1.0
        return vmin, vmax
