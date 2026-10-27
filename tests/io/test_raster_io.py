import time

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from petrogis import sample_data as sd
from petrogis.core.extent import Extent
from petrogis.io.errors import DataReadError, MissingCRSError
from petrogis.io.raster_io import load_raster_layer, read_raster_info, read_raster_view

UTM = "EPSG:32639"


def test_info(sample_field):
    info = read_raster_info(sample_field.structure_top, UTM)
    assert info.crs == UTM
    assert (info.width, info.height) == (560, 400)
    assert info.native_resolution == (25.0, 25.0)
    assert info.extent.width == pytest.approx(sd.RASTER_WIDTH_M)
    assert info.nodata == sd.RASTER_NODATA
    assert info.stats_min == pytest.approx(sd.CREST_TVDSS, abs=10)
    assert info.stats_min < info.p2 < info.p98 < info.stats_max
    assert info.description == "Top Reservoir TVDSS (m)"


def test_full_view_is_decimated_to_screen(sample_field):
    info = read_raster_info(sample_field.structure_top, UTM)
    view = read_raster_view(sample_field.structure_top, UTM, info.extent, (140, 100))
    assert view.data.shape == (100, 140)
    assert view.data.dtype == np.float32
    assert view.data.mask.any()  # nodata corners
    assert view.extent == info.extent


def test_never_upsamples_beyond_native(sample_field):
    centre = Extent(sd.CENTER_X - 250, sd.CENTER_Y - 250, sd.CENTER_X + 250, sd.CENTER_Y + 250)
    view = read_raster_view(sample_field.structure_top, UTM, centre, (2000, 2000))
    assert view.data.shape == (20, 20)  # 500 m / 25 m
    assert float(view.data.min()) == pytest.approx(sd.CREST_TVDSS, abs=10)


def test_partial_overlap_and_no_overlap(sample_field):
    info = read_raster_info(sample_field.structure_top, UTM)
    e = info.extent
    half = Extent(e.xmin - e.width, e.ymin, e.xmin + e.width / 2, e.ymax)
    view = read_raster_view(sample_field.structure_top, UTM, half, (300, 100))
    assert view.extent.xmin == pytest.approx(e.xmin)
    assert view.extent.xmax == pytest.approx(e.xmin + e.width / 2, abs=25)
    # Only a third of the view width is raster, so roughly a third of the screen pixels are used.
    assert view.data.shape[1] == pytest.approx(100, abs=2)

    far = Extent(0, 0, 10, 10)
    assert read_raster_view(sample_field.structure_top, UTM, far, (100, 100)) is None


def test_reprojected_raster(tmp_path):
    path = tmp_path / "wgs84.tif"
    data = np.arange(100 * 100, dtype=np.float32).reshape(100, 100)
    with rasterio.open(
        path, "w", driver="GTiff", width=100, height=100, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(49.8, 40.45, 0.001, 0.001),
    ) as dst:  # fmt: skip
        dst.write(data, 1)
    layer = load_raster_layer(path, UTM)
    assert layer.info.crs == "EPSG:4326"
    e = layer.extent
    assert 380_000 < e.xmin < e.xmax < 420_000
    assert 4_460_000 < e.ymin < e.ymax < 4_490_000
    view = read_raster_view(path, UTM, e, (50, 50))
    assert view is not None and view.data.count() > 0


def test_errors(tmp_path):
    with pytest.raises(DataReadError, match="File not found"):
        read_raster_info(tmp_path / "x.tif", UTM)
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"not a tiff")
    with pytest.raises(DataReadError, match="Cannot read"):
        read_raster_info(bad, UTM)
    nocrs = tmp_path / "nocrs.tif"
    with rasterio.open(
        nocrs, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
        transform=from_origin(0, 4, 1, 1),
    ) as dst:  # fmt: skip
        dst.write(np.zeros((4, 4), dtype=np.uint8), 1)
    with pytest.raises(MissingCRSError):
        read_raster_info(nocrs, UTM)


@pytest.mark.perf
def test_large_raster_view_reads_are_fast(tmp_path):
    """A 6000x6000 raster with overviews: a screen-sized full view must be quick."""
    path = tmp_path / "big.tif"
    size = 6000
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1, dtype="float32",
        crs=UTM, transform=from_origin(400000, 4480000, 2, 2), tiled=True,
        blockxsize=512, blockysize=512,
    ) as dst:  # fmt: skip
        dst.write(np.sin(xx / 300) + np.cos(yy / 300), 1)
        dst.build_overviews([2, 4, 8, 16], rasterio.enums.Resampling.average)
    del xx, yy

    start = time.perf_counter()
    info = read_raster_info(path, UTM)
    view = read_raster_view(path, UTM, info.extent, (1600, 1000))
    elapsed = time.perf_counter() - start
    assert view.data.shape == (1000, 1600)
    assert elapsed < 1.0, f"info + full view took {elapsed:.2f}s"


def test_sample_raster(sample_field, tmp_path):
    from petrogis.io.raster_io import sample_raster

    crest = sample_raster(sample_field.structure_top, UTM, sd.CENTER_X, sd.CENTER_Y)
    assert crest == pytest.approx(sd.CREST_TVDSS, abs=10)
    info = read_raster_info(sample_field.structure_top, UTM)
    e = info.extent
    assert (
        sample_raster(sample_field.structure_top, UTM, e.xmin + 10, e.ymax - 10) is None
    )  # nodata
    assert sample_raster(sample_field.structure_top, UTM, 0, 0) is None  # outside

    path = tmp_path / "wgs84.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=10, height=10, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(49.8, 40.45, 0.01, 0.01),
    ) as dst:  # fmt: skip
        dst.write(np.full((10, 10), 7.0, dtype=np.float32), 1)
    layer = load_raster_layer(path, UTM)
    cx, cy = layer.extent.center
    assert sample_raster(path, UTM, cx, cy) == 7.0
