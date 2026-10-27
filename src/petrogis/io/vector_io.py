"""Read vector sources (Shapefile, GeoJSON, GeoPackage, KML) into the project CRS.

Reads go through pyogrio, using Arrow when pyarrow is available, which is several times faster
than reading feature by feature.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from petrogis.core.crs import crs_equal, normalize_crs
from petrogis.core.layers import VectorLayer
from petrogis.io.errors import DataReadError, MissingCRSError

if TYPE_CHECKING:
    import geopandas as gpd

log = logging.getLogger(__name__)

VECTOR_EXTENSIONS = {".shp", ".geojson", ".json", ".gpkg", ".kml"}
VECTOR_FILE_FILTER = "Vector files (*.shp *.geojson *.json *.gpkg *.kml)"
KML_CRS = "EPSG:4326"  # KML coordinates are always WGS 84 longitude/latitude.


def _use_arrow() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def list_layers(path: str | Path) -> list[str]:
    """Names of the layers inside ``path``. Single-layer formats return one name."""
    import pyogrio

    path = Path(path)
    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:  # pyogrio raises several DataSourceError subclasses
        raise DataReadError(f"Cannot open {path.name}: {exc}") from exc
    return [str(name) for name, _geom_type in layers]


def read_vector(
    path: str | Path,
    project_crs: str,
    layer: str | None = None,
    assume_crs: Any = None,
) -> gpd.GeoDataFrame:
    """Read ``path`` (optionally one ``layer``) and reproject it to ``project_crs``.

    ``assume_crs`` is used when the source declares no CRS. If it is not given, sources without
    a CRS raise :class:`MissingCRSError` so the UI can ask the user. Null and empty geometries
    are dropped.
    """
    import geopandas as gpd

    path = Path(path)
    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    if path.suffix.lower() not in VECTOR_EXTENSIONS:
        raise DataReadError(f"Unsupported vector format: {path.suffix or path.name}")
    try:
        gdf = gpd.read_file(path, layer=layer, engine="pyogrio", use_arrow=_use_arrow())
    except Exception as exc:  # GDAL/pyogrio errors vary by driver
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc

    if gdf.crs is None:
        if path.suffix.lower() == ".kml":
            assume_crs = assume_crs or KML_CRS
        if assume_crs is None:
            raise MissingCRSError(f"{path.name} has no coordinate reference system defined")
        gdf = gdf.set_crs(assume_crs)

    geoms = gdf.geometry
    keep = ~(geoms.isna() | geoms.is_empty)
    if not keep.all():
        log.warning("%s: dropped %d null/empty geometries", path.name, int((~keep).sum()))
        gdf = gdf.loc[keep]
    gdf = gdf.reset_index(drop=True)

    if not crs_equal(gdf.crs, project_crs):
        gdf = gdf.to_crs(project_crs)
    return gdf


def load_vector_layer(
    path: str | Path,
    project_crs: str,
    layer: str | None = None,
    name: str | None = None,
    assume_crs: Any = None,
) -> VectorLayer:
    """Read a vector source into a :class:`VectorLayer`, with render paths pre-built.

    Building the paths here means the cost lands on the worker thread that loads the layer,
    not on the UI thread when the layer is first drawn.
    """
    path = Path(path)
    gdf = read_vector(path, project_crs, layer=layer, assume_crs=assume_crs)
    if name is None:
        name = path.stem if layer is None or layer == path.stem else f"{path.stem} - {layer}"
    vector_layer = VectorLayer(
        name=name,
        source=str(path),
        gdf=gdf,
        source_layer=layer,
        assumed_crs=None if assume_crs is None else normalize_crs(assume_crs),
    )
    _ = vector_layer.paths
    return vector_layer
