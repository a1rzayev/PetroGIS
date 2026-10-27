"""Raster metadata and view-resolution reads.

Display code never loads a full-resolution raster. :func:`read_raster_view` reads only the
visible window, decimated to roughly screen size, so GDAL can serve it from overviews. Rasters
in another CRS are warped on the fly with a WarpedVRT. Every function opens and closes its own
dataset, so they are safe to call from worker threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from petrogis.core.crs import crs_equal, normalize_crs
from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, RasterLayer
from petrogis.io.errors import DataReadError, MissingCRSError

if TYPE_CHECKING:
    from rasterio.io import DatasetReader
    from rasterio.vrt import WarpedVRT

RASTER_EXTENSIONS = {".tif", ".tiff"}
RASTER_FILE_FILTER = "GeoTIFF (*.tif *.tiff)"
STATS_MAX_SIZE = 1024
"""Statistics are computed on a decimated read whose longest side is at most this many pixels."""


@dataclass(frozen=True)
class RasterView:
    data: np.ma.MaskedArray
    """Shape (rows, cols), float32, masked where nodata. Row 0 is the northern edge."""
    extent: Extent
    """The exact project-CRS extent covered by ``data``."""


def _open(path: Path) -> DatasetReader:
    import rasterio
    from rasterio.errors import RasterioIOError

    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    if path.suffix.lower() not in RASTER_EXTENSIONS:
        raise DataReadError(f"Unsupported raster format: {path.suffix or path.name}")
    try:
        return rasterio.open(path)
    except RasterioIOError as exc:
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc


def _project_view(src: DatasetReader, project_crs: str) -> DatasetReader | WarpedVRT:
    """Return ``src`` itself when it is already in the project CRS, else a warped virtual view."""
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    if src.crs is None:
        raise MissingCRSError(f"{Path(src.name).name} has no coordinate reference system defined")
    if crs_equal(src.crs, project_crs):
        return src
    return WarpedVRT(src, crs=project_crs, resampling=Resampling.bilinear)


def read_raster_info(path: str | Path, project_crs: str, band: int = 1) -> RasterInfo:
    path = Path(path)
    with _open(path) as src:
        if not 1 <= band <= src.count:
            raise DataReadError(f"{path.name} has no band {band} (it has {src.count})")
        view = _project_view(src, project_crs)
        try:
            extent = Extent.from_bounds(view.bounds)
            res = (abs(view.transform.a), abs(view.transform.e))
            scale = min(1.0, STATS_MAX_SIZE / max(view.width, view.height))
            shape = (max(1, round(view.height * scale)), max(1, round(view.width * scale)))
            sample = view.read(band, out_shape=shape, masked=True)
        finally:
            if view is not src:
                view.close()
        values = sample.compressed().astype(np.float64)
        values = values[np.isfinite(values)]
        if values.size == 0:
            raise DataReadError(f"{path.name} band {band} contains only nodata")
        p2, p98 = np.percentile(values, [2, 98])
        return RasterInfo(
            crs=normalize_crs(src.crs),
            width=src.width,
            height=src.height,
            band_count=src.count,
            dtype=str(src.dtypes[band - 1]),
            nodata=None if src.nodata is None else float(src.nodata),
            extent=extent,
            native_resolution=res,
            stats_min=float(values.min()),
            stats_max=float(values.max()),
            p2=float(p2),
            p98=float(p98),
            description=src.descriptions[band - 1] or "",
        )


def read_raster_view(
    path: str | Path,
    project_crs: str,
    view_extent: Extent,
    screen_size: tuple[int, int],
    band: int = 1,
) -> RasterView | None:
    """Read the part of the raster inside ``view_extent`` at about ``screen_size`` (w, h) pixels.

    The result is never upsampled beyond native resolution. Returns ``None`` when the view
    doesn't overlap the raster.
    """
    from rasterio import windows
    from rasterio.enums import Resampling

    path = Path(path)
    screen_w, screen_h = (max(1, int(v)) for v in screen_size)
    with _open(path) as src:
        view = _project_view(src, project_crs)
        try:
            clipped = Extent.from_bounds(view.bounds).intersection(view_extent)
            if clipped is None or clipped.width <= 0 or clipped.height <= 0:
                return None
            window = windows.from_bounds(*clipped.as_tuple(), transform=view.transform)
            window = window.round_offsets(op="floor").round_lengths(op="ceil")
            window = window.intersection(windows.Window(0, 0, view.width, view.height))
            if window.width < 1 or window.height < 1:
                return None
            # Fraction of the screen the clipped area covers, so a small raster in a big view
            # isn't read at full screen size.
            frac_w = clipped.width / max(view_extent.width, 1e-12)
            frac_h = clipped.height / max(view_extent.height, 1e-12)
            out_w = max(1, min(int(window.width), round(screen_w * frac_w)))
            out_h = max(1, min(int(window.height), round(screen_h * frac_h)))
            data = view.read(
                band,
                window=window,
                out_shape=(out_h, out_w),
                masked=True,
                resampling=Resampling.nearest,
            )
            extent = Extent.from_bounds(windows.bounds(window, view.transform))
        finally:
            if view is not src:
                view.close()
    data = data.astype(np.float32)
    data = np.ma.masked_invalid(data, copy=False)
    return RasterView(data=data, extent=extent)


def load_raster_layer(
    path: str | Path, project_crs: str, band: int = 1, name: str | None = None
) -> RasterLayer:
    path = Path(path)
    info = read_raster_info(path, project_crs, band=band)
    return RasterLayer(name=name or path.stem, source=str(path), info=info, band=band)


def sample_raster(
    path: str | Path, project_crs: str, x: float, y: float, band: int = 1
) -> float | None:
    """Value of the pixel at project-CRS point (x, y), or ``None`` outside the raster or nodata."""
    from rasterio.warp import transform as warp_transform

    path = Path(path)
    with _open(path) as src:
        if src.crs is None:
            raise MissingCRSError(f"{path.name} has no coordinate reference system defined")
        if not crs_equal(src.crs, project_crs):
            xs, ys = warp_transform(project_crs, src.crs, [x], [y])
            x, y = xs[0], ys[0]
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return None
        from rasterio.windows import Window

        value = src.read(band, window=Window(col, row, 1, 1), masked=True)[0, 0]
    if np.ma.is_masked(value) or not np.isfinite(value):
        return None
    return float(value)
