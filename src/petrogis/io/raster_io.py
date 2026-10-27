"""Raster metadata and view-resolution reads.

Display code never loads a full-resolution raster. :func:`read_raster_view` reads only the
visible window, decimated to roughly screen size, so GDAL can serve it from overviews. Rasters
in another CRS are warped on the fly with a WarpedVRT. Every function opens and closes its own
dataset, so they are safe to call from worker threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from petrogis.core.crs import crs_equal, normalize_crs
from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, RasterLayer
from petrogis.io.errors import DataReadError, MissingCRSError
from petrogis.io.reproject import TransformationLookup, resolve_transformation

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


def _source_crs(src: DatasetReader, assume_crs: str | None) -> Any:
    """The raster's CRS, or ``assume_crs`` when the file declares none."""
    if src.crs is not None:
        return src.crs
    if assume_crs is None:
        raise MissingCRSError(f"{Path(src.name).name} has no coordinate reference system defined")
    return normalize_crs(assume_crs)


def _project_view(
    src: DatasetReader, project_crs: str, assume_crs: str | None = None
) -> DatasetReader | WarpedVRT:
    """Return ``src`` itself when it is already in the project CRS, else a warped virtual view."""
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    source_crs = _source_crs(src, assume_crs)
    if crs_equal(source_crs, project_crs):
        return src
    extra: dict[str, Any] = {} if src.crs is not None else {"src_crs": source_crs}
    return WarpedVRT(src, crs=project_crs, resampling=Resampling.bilinear, **extra)


# --- explicit datum transformation ------------------------------------------------------------
# rasterio upper-cases warp options, which breaks PROJ pipeline strings, so a raster that must
# follow a user-chosen pipeline is warped here instead: the screen-sized grid of output pixel
# centres goes back through the pipeline into the source, and the source is sampled (nearest).
_EDGE_SAMPLES = 33


def _pipeline_extent(src: DatasetReader, pipeline: str) -> Extent:
    from petrogis.core.transform import pipeline_transformer, transform_xy

    left, bottom, right, top = src.bounds
    t = np.linspace(0.0, 1.0, _EDGE_SAMPLES)
    xs = np.concatenate([left + (right - left) * t, np.full_like(t, right),
                         right - (right - left) * t, np.full_like(t, left)])  # fmt: skip
    ys = np.concatenate([np.full_like(t, bottom), bottom + (top - bottom) * t,
                         np.full_like(t, top), top - (top - bottom) * t])  # fmt: skip
    tx, ty = transform_xy(pipeline_transformer(pipeline), xs, ys)
    ok = np.isfinite(tx) & np.isfinite(ty)
    if not ok.any():
        raise DataReadError(f"{Path(src.name).name} can't be transformed with the chosen pipeline")
    return Extent(
        float(tx[ok].min()), float(ty[ok].min()), float(tx[ok].max()), float(ty[ok].max())
    )


def _read_through_pipeline(
    src: DatasetReader, band: int, pipeline: str, view_extent: Extent, out_w: int, out_h: int
) -> np.ma.MaskedArray:
    """Sample the source on an ``out_h`` x ``out_w`` grid covering ``view_extent`` (project CRS)."""
    from rasterio import windows

    from petrogis.core.transform import pipeline_transformer, transform_xy

    px = view_extent.xmin + (np.arange(out_w) + 0.5) * (view_extent.width / out_w)
    py = view_extent.ymax - (np.arange(out_h) + 0.5) * (view_extent.height / out_h)
    gx, gy = np.meshgrid(px, py)
    sx, sy = transform_xy(pipeline_transformer(pipeline), gx.ravel(), gy.ravel(), inverse=True)
    inv = ~src.transform
    cols = np.floor(inv.a * sx + inv.b * sy + inv.c).reshape(out_h, out_w)
    rows = np.floor(inv.d * sx + inv.e * sy + inv.f).reshape(out_h, out_w)
    inside = (
        np.isfinite(cols) & np.isfinite(rows)
        & (cols >= 0) & (cols < src.width) & (rows >= 0) & (rows < src.height)
    )  # fmt: skip
    result = np.ma.masked_all((out_h, out_w), dtype=np.float32)
    if not inside.any():
        return result
    c0, c1 = int(cols[inside].min()), int(cols[inside].max()) + 1
    r0, r1 = int(rows[inside].min()), int(rows[inside].max()) + 1
    window = windows.Window(c0, r0, c1 - c0, r1 - r0)
    # Decimate the source read to about the output density (served from overviews when present).
    step = max(1.0, (c1 - c0) / out_w, (r1 - r0) / out_h)
    shape = (max(1, int(np.ceil((r1 - r0) / step))), max(1, int(np.ceil((c1 - c0) / step))))
    block = src.read(band, window=window, out_shape=shape, masked=True)
    bi = np.clip(((rows[inside] - r0) * shape[0] / (r1 - r0)).astype(int), 0, shape[0] - 1)
    bj = np.clip(((cols[inside] - c0) * shape[1] / (c1 - c0)).astype(int), 0, shape[1] - 1)
    values = block[bi, bj]
    result[inside] = values.astype(np.float32)
    return np.ma.masked_invalid(result, copy=False)


def read_raster_info(
    path: str | Path,
    project_crs: str,
    band: int = 1,
    assume_crs: str | None = None,
    transformation: str | TransformationLookup | None = None,
) -> RasterInfo:
    path = Path(path)
    with _open(path) as src:
        if not 1 <= band <= src.count:
            raise DataReadError(f"{path.name} has no band {band} (it has {src.count})")
        source_crs = _source_crs(src, assume_crs)
        transformation = resolve_transformation(transformation, source_crs)
        scale = min(1.0, STATS_MAX_SIZE / max(src.width, src.height))
        if transformation is not None and not crs_equal(source_crs, project_crs):
            extent = _pipeline_extent(src, transformation)
            res = (extent.width / src.width, extent.height / src.height)
            sample = _read_through_pipeline(
                src, band, transformation, extent,
                max(1, round(src.width * scale)), max(1, round(src.height * scale)),
            )  # fmt: skip
        else:
            view = _project_view(src, project_crs, assume_crs)
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
            crs=normalize_crs(source_crs),
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


def _output_size(
    clipped: Extent, view_extent: Extent, screen: tuple[int, int], native: tuple[float, float]
) -> tuple[int, int]:
    """Screen pixels covering ``clipped``, never finer than the native resolution."""
    screen_w, screen_h = screen
    frac_w = clipped.width / max(view_extent.width, 1e-12)
    frac_h = clipped.height / max(view_extent.height, 1e-12)
    max_w = max(1, int(np.ceil(clipped.width / max(native[0], 1e-12))))
    max_h = max(1, int(np.ceil(clipped.height / max(native[1], 1e-12))))
    return (
        max(1, min(max_w, round(screen_w * frac_w))),
        max(1, min(max_h, round(screen_h * frac_h))),
    )


def read_raster_view(
    path: str | Path,
    project_crs: str,
    view_extent: Extent,
    screen_size: tuple[int, int],
    band: int = 1,
    assume_crs: str | None = None,
    transformation: str | None = None,
) -> RasterView | None:
    """Read the part of the raster inside ``view_extent`` at about ``screen_size`` (w, h) pixels.

    The result is never upsampled beyond native resolution. Returns ``None`` when the view
    doesn't overlap the raster.
    """
    from rasterio import windows
    from rasterio.enums import Resampling

    path = Path(path)
    screen = (max(1, int(screen_size[0])), max(1, int(screen_size[1])))
    with _open(path) as src:
        source_crs = _source_crs(src, assume_crs)
        if transformation is not None and not crs_equal(source_crs, project_crs):
            full = _pipeline_extent(src, transformation)
            clipped = full.intersection(view_extent)
            if clipped is None or clipped.width <= 0 or clipped.height <= 0:
                return None
            native = (full.width / src.width, full.height / src.height)
            out_w, out_h = _output_size(clipped, view_extent, screen, native)
            data = _read_through_pipeline(src, band, transformation, clipped, out_w, out_h)
            return RasterView(data=data, extent=clipped)

        view = _project_view(src, project_crs, assume_crs)
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
            out_w = max(1, min(int(window.width), round(screen[0] * frac_w)))
            out_h = max(1, min(int(window.height), round(screen[1] * frac_h)))
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
    path: str | Path,
    project_crs: str,
    band: int = 1,
    name: str | None = None,
    assume_crs: str | None = None,
    transformation: str | TransformationLookup | None = None,
) -> RasterLayer:
    path = Path(path)
    info = read_raster_info(
        path, project_crs, band=band, assume_crs=assume_crs, transformation=transformation
    )
    return RasterLayer(
        name=name or path.stem,
        source=str(path),
        info=info,
        band=band,
        assumed_crs=None if assume_crs is None else normalize_crs(assume_crs),
    )


def sample_raster(
    path: str | Path,
    project_crs: str,
    x: float,
    y: float,
    band: int = 1,
    assume_crs: str | None = None,
    transformation: str | None = None,
) -> float | None:
    """Value of the pixel at project-CRS point (x, y), or ``None`` outside the raster or nodata."""
    from rasterio.warp import transform as warp_transform
    from rasterio.windows import Window

    from petrogis.core.transform import pipeline_transformer, transform_xy

    path = Path(path)
    with _open(path) as src:
        source_crs = _source_crs(src, assume_crs)
        if not crs_equal(source_crs, project_crs):
            if transformation is not None:
                sx, sy = transform_xy(pipeline_transformer(transformation), [x], [y], inverse=True)
                x, y = float(sx[0]), float(sy[0])
            else:
                xs, ys = warp_transform(project_crs, source_crs, [x], [y])
                x, y = xs[0], ys[0]
        if not (np.isfinite(x) and np.isfinite(y)):
            return None
        row, col = src.index(x, y)
        if not (0 <= row < src.height and 0 <= col < src.width):
            return None
        value = src.read(band, window=Window(col, row, 1, 1), masked=True)[0, 0]
    if np.ma.is_masked(value) or not np.isfinite(value):
        return None
    return float(value)
