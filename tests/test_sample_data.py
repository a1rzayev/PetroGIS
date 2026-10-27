import time

import geopandas as gpd
import lasio
import numpy as np
import pandas as pd
import pytest
import rasterio
from shapely import Point

from petrogis import sample_data as sd


def test_structure_raster(sample_field):
    with rasterio.open(sample_field.structure_top) as src:
        assert src.crs.to_epsg() == 32639
        assert (src.width, src.height) == (560, 400)
        assert src.nodata == sd.RASTER_NODATA
        assert src.overviews(1) == [2, 4, 8]
        data = src.read(1, masked=True)
        row, col = src.index(sd.CENTER_X, sd.CENTER_Y)
    assert data.mask.any(), "corners should be nodata"
    # Crest is near the field centre and is the shallowest point.
    assert abs(float(data[row, col]) - sd.CREST_TVDSS) < 10
    assert float(data.min()) == pytest.approx(sd.CREST_TVDSS, abs=10)


def test_field_boundary_reprojects_onto_structure(sample_field):
    gdf = gpd.read_file(sample_field.field_boundary)
    assert gdf.crs.to_epsg() == 4326
    utm = gdf.to_crs(epsg=32639)
    poly = utm.geometry.iloc[0]
    assert poly.is_valid
    assert poly.contains(Point(sd.CENTER_X, sd.CENTER_Y))
    with rasterio.open(sample_field.structure_top) as src:
        left, bottom, right, top = src.bounds
    minx, miny, maxx, maxy = poly.bounds
    assert left < minx and right > maxx and bottom < miny and top > maxy


def test_well_tables(sample_field):
    csv = pd.read_csv(sample_field.wells_csv)
    xlsx = pd.read_excel(sample_field.wells_xlsx)
    assert list(csv.columns) == ["Well", "UWI", "Easting", "Northing", "KB", "TD", "Status"]
    assert list(xlsx.columns) == ["Well Name", "X", "Y", "Elevation", "Total Depth", "Status"]
    assert len(csv) == len(xlsx) == sd.N_WELLS
    assert csv["Well"].is_unique
    np.testing.assert_allclose(csv["Easting"], xlsx["X"])

    boundary = gpd.read_file(sample_field.field_boundary).to_crs(epsg=32639).geometry.iloc[0]
    inside = [
        boundary.contains(Point(x, y)) for x, y in zip(csv.Easting, csv.Northing, strict=True)
    ]
    assert sum(inside) == 7
    assert set(csv["Status"]) <= {"OIL", "DRY"}


def test_las_files(sample_field):
    assert len(sample_field.las_files) == sd.N_WELLS + 1
    names = set()
    families = {family: set() for family in sd.MNEMONIC_VARIANTS}
    for path in sample_field.las_files:
        las = lasio.read(path)
        names.add(las.well["WELL"].value)
        depth = las.index
        assert np.all(np.diff(depth) > 0)
        mnemonics = [c.mnemonic for c in las.curves]
        assert mnemonics[0] == "DEPT"
        for family, variants in sd.MNEMONIC_VARIANTS.items():
            found = [m for m in mnemonics if m in {v for v, _ in variants}]
            assert len(found) == 1, f"{path.name}: {family}"
            families[family].add(found[0])
    assert {"AB-01", "AB 02", "ab_03", "AB-4", "OFFSET-1"} <= names
    # Each family uses more than one mnemonic across the field.
    assert all(len(used) > 1 for used in families.values())


def test_reservoir_signature_in_oil_well(sample_field):
    well = next(w for w in sample_field.wells if w.status == "OIL")
    index = sample_field.wells.index(well)
    las = lasio.read(sample_field.las_files[index])
    df = las.df()
    gr = df[next(c for c in df.columns if c in ("GR", "GR_EDTC", "SGR"))]
    rt = df[next(c for c in df.columns if c in ("RT", "ILD", "LLD", "RDEP"))]
    top_md = well.top_tvdss + well.kb
    in_res = (df.index > top_md + 2) & (df.index < top_md + 15)
    overburden = df.index < top_md - 50
    assert gr[in_res].median() < gr[overburden].median() - 40
    assert rt[in_res].median() > 10 * rt[overburden].median()


def test_null_gap_is_written(sample_field):
    las = lasio.read(sample_field.las_files[2])
    rhob = las.df()[next(c for c in las.df().columns if c in ("RHOB", "RHOZ", "DEN"))]
    assert rhob.isna().sum() > 0


def test_deterministic(tmp_path):
    a = sd.generate(tmp_path / "a", seed=7)
    b = sd.generate(tmp_path / "b", seed=7)
    assert a.wells == b.wells
    assert a.las_files[0].read_text() == b.las_files[0].read_text()


def test_generation_is_fast(tmp_path):
    start = time.perf_counter()
    sd.generate(tmp_path, seed=1)
    assert time.perf_counter() - start < 10.0
