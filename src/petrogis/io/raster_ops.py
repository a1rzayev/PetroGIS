"""Raster processing on files: sample at points, clip by polygons, reproject, zonal statistics.

Every function opens and closes its own datasets, so it is safe to call from a worker thread.
Geometries and points come in the project CRS and are brought into the raster's own CRS (with
the project's explicit datum transformation when there is one). Long loops call ``check()``,
which raises to cancel, and ``progress(fraction)``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from petrogis.core.crs import crs_equal
from petrogis.io.errors import DataReadError
from petrogis.io.raster_io import _open, _source_crs

Check = Callable[[], None]
Progress = Callable[[float], None]
ZONAL_STATS = ("count", "min", "max", "mean", "std", "sum")
BLOCK_ROWS = 512


def _noop(*_args: Any) -> None:
    return None


def _to_source_xy(
    xs: ArrayLike, ys: ArrayLike, project_crs: str, source_crs: Any, transformation: str | None
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    if crs_equal(source_crs, project_crs):
        return xs, ys
    if transformation is not None:
        from petrogis.core.transform import pipeline_transformer, transform_xy

        return transform_xy(pipeline_transformer(transformation), xs, ys, inverse=True)
    from rasterio.warp import transform as warp_transform

    tx, ty = warp_transform(project_crs, source_crs, xs.tolist(), ys.tolist())
    return np.asarray(tx, dtype=np.float64), np.asarray(ty, dtype=np.float64)


def _to_source_geometries(
    geoms: ArrayLike, project_crs: str, source_crs: Any, transformation: str | None
) -> NDArray[np.object_]:
    geoms = np.asarray(geoms, dtype=object)
    if crs_equal(source_crs, project_crs):
        return geoms
    if transformation is not None:
        from petrogis.core.transform import transform_geometries

        return transform_geometries(geoms, transformation, inverse=True)
    import geopandas as gpd

    return np.asarray(gpd.GeoSeries(geoms, crs=project_crs).to_crs(source_crs).values, dtype=object)


def sample_points(
    path: str | Path,
    project_crs: str,
    xs: ArrayLike,
    ys: ArrayLike,
    band: int = 1,
    assume_crs: str | None = None,
    transformation: str | None = None,
) -> NDArray[np.float64]:
    """Pixel values at project-CRS points. NaN outside the raster, at nodata, or for NaN input."""
    path = Path(path)
    with _open(path) as src:
        if not 1 <= band <= src.count:
            raise DataReadError(f"{path.name} has no band {band}")
        sx, sy = _to_source_xy(xs, ys, project_crs, _source_crs(src, assume_crs), transformation)
        out = np.full(sx.shape, np.nan)
        ok = np.isfinite(sx) & np.isfinite(sy)
        if not ok.any():
            return out
        from rasterio.transform import rowcol

        rows, cols = rowcol(src.transform, sx[ok], sy[ok])
        rows, cols = np.atleast_1d(np.asarray(rows)), np.atleast_1d(np.asarray(cols))
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        index = np.flatnonzero(ok)[inside]
        values = np.full(index.size, np.nan)
        from rasterio.windows import Window

        for i, (row, col) in enumerate(zip(rows[inside], cols[inside], strict=True)):
            pixel = src.read(band, window=Window(int(col), int(row), 1, 1), masked=True)[0, 0]
            values[i] = np.nan if np.ma.is_masked(pixel) else float(pixel)
        out[index] = values
    return out


def clip_raster(
    path: str | Path,
    polygons: ArrayLike,
    project_crs: str,
    out_path: str | Path,
    assume_crs: str | None = None,
    transformation: str | None = None,
    check: Check = _noop,
    progress: Progress = _noop,
) -> Path:
    """Write the part of the raster inside ``polygons`` (cells outside become nodata).

    The output keeps the source CRS, resolution and data type, cropped to the polygons' extent.
    """
    from rasterio.mask import mask

    path, out_path = Path(path), Path(out_path)
    with _open(path) as src:
        source_crs = _source_crs(src, assume_crs)
        shapes = _to_source_geometries(polygons, project_crs, source_crs, transformation)
        shapes = [g for g in shapes if g is not None and not g.is_empty]
        if not shapes:
            raise DataReadError("The clip layer has no polygons")
        check()
        nodata = src.nodata if src.nodata is not None else _default_nodata(src.dtypes[0])
        try:
            data, transform = mask(src, shapes, crop=True, nodata=nodata, filled=True)
        except ValueError as exc:  # rasterio: shapes do not overlap the raster
            raise DataReadError(f"The polygons do not overlap {path.name}") from exc
        progress(0.7)
        check()
        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=data.shape[1],
            width=data.shape[2],
            transform=transform,
            nodata=nodata,
            crs=source_crs,
            compress="deflate",
        )
        for key in ("blockxsize", "blockysize", "tiled"):
            profile.pop(key, None)
    _write(out_path, data, profile)
    return out_path


def _default_nodata(dtype: str) -> float:
    if np.issubdtype(np.dtype(dtype), np.floating):
        return -9999.0
    info = np.iinfo(np.dtype(dtype))
    return float(info.min if info.min < 0 else info.max)


def _write(out_path: Path, data: np.ndarray, profile: dict[str, Any]) -> None:
    import rasterio
    from rasterio.errors import RasterioError

    try:
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data)
    except (RasterioError, OSError) as exc:  # permissions, disk, driver options
        raise DataReadError(f"Cannot write {out_path.name}: {exc}") from exc


def reproject_raster(
    path: str | Path,
    target_crs: str,
    out_path: str | Path,
    resampling: str = "bilinear",
    assume_crs: str | None = None,
    check: Check = _noop,
    progress: Progress = _noop,
) -> Path:
    """Warp the raster to ``target_crs`` at a comparable resolution, block by block.

    Block-wise writing keeps memory flat for large rasters and lets the run be cancelled.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioError
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window

    try:
        method = Resampling[resampling]
    except KeyError as exc:
        raise DataReadError(f"Unknown resampling {resampling!r}") from exc
    path, out_path = Path(path), Path(out_path)
    with _open(path) as src:
        source_crs = _source_crs(src, assume_crs)
        nodata = src.nodata if src.nodata is not None else _default_nodata(src.dtypes[0])
        with WarpedVRT(
            src, src_crs=source_crs, crs=target_crs, resampling=method, nodata=nodata
        ) as vrt:
            profile = {
                "driver": "GTiff",
                "width": vrt.width,
                "height": vrt.height,
                "count": src.count,
                "dtype": src.dtypes[0],
                "crs": target_crs,
                "transform": vrt.transform,
                "nodata": nodata,
                "compress": "deflate",
                "tiled": True,
            }
            try:
                with rasterio.open(out_path, "w", **profile) as dst:
                    for top in range(0, vrt.height, BLOCK_ROWS):
                        check()
                        height = min(BLOCK_ROWS, vrt.height - top)
                        window = Window(0, top, vrt.width, height)
                        dst.write(vrt.read(window=window), window=window)
                        progress((top + height) / vrt.height)
            except (RasterioError, OSError) as exc:  # cancel (from check) passes through
                raise DataReadError(f"Cannot write {out_path.name}: {exc}") from exc
    return out_path


def zonal_statistics(
    path: str | Path,
    polygons: ArrayLike,
    project_crs: str,
    band: int = 1,
    assume_crs: str | None = None,
    transformation: str | None = None,
    check: Check = _noop,
    progress: Progress = _noop,
) -> dict[str, NDArray[np.float64]]:
    """Statistics of the full-resolution cells inside each polygon (cell centres inside).

    Returns arrays keyed by ``ZONAL_STATS``, one value per polygon. NaN where a polygon covers
    no valid cell (``count`` is then 0).
    """
    from rasterio.errors import WindowError
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    path = Path(path)
    geoms = np.asarray(polygons, dtype=object)
    stats = {name: np.full(geoms.size, np.nan) for name in ZONAL_STATS}
    stats["count"][:] = 0
    with _open(path) as src:
        if not 1 <= band <= src.count:
            raise DataReadError(f"{path.name} has no band {band}")
        shapes = _to_source_geometries(
            geoms, project_crs, _source_crs(src, assume_crs), transformation
        )
        full = from_bounds(*src.bounds, transform=src.transform)
        for i, shape in enumerate(shapes):
            check()
            progress(i / max(geoms.size, 1))
            if shape is None or shape.is_empty:
                continue
            window = from_bounds(*shape.bounds, transform=src.transform)
            window = window.round_offsets().round_lengths()
            try:
                window = window.intersection(full)
            except WindowError:  # the polygon lies outside the raster
                continue
            if window.width < 1 or window.height < 1:
                continue
            data = src.read(band, window=window, masked=True)
            inside = ~geometry_mask(
                [shape], out_shape=data.shape, transform=src.window_transform(window)
            )
            values = data.data[inside & ~np.ma.getmaskarray(data)].astype(np.float64)
            values = values[np.isfinite(values)]
            stats["count"][i] = values.size
            if values.size:
                stats["min"][i] = values.min()
                stats["max"][i] = values.max()
                stats["mean"][i] = values.mean()
                stats["std"][i] = values.std()
                stats["sum"][i] = values.sum()
    return stats
