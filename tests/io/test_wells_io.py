import time

import numpy as np
import pandas as pd
import pytest

from petrogis.io.errors import DataReadError
from petrogis.io.wells_io import (
    ColumnMapping,
    build_well_layer,
    detect_columns,
    list_sheets,
    load_well_layer,
    normalize_header,
    read_well_table,
)

UTM = "EPSG:32639"


def test_normalize_header():
    assert normalize_header(" Well Name ") == "wellname"
    assert normalize_header("X (m)") == "x"
    assert normalize_header("KB_Elev [m]") == "kbelev"


def test_detect_sample_csv_columns():
    m = detect_columns(["Well", "UWI", "Easting", "Northing", "KB", "TD", "Status"])
    assert m == ColumnMapping(name="Well", x="Easting", y="Northing", kb="KB", uwi="UWI", td="TD")


def test_detect_sample_xlsx_columns():
    m = detect_columns(["Well Name", "X", "Y", "Elevation", "Total Depth", "Status"])
    assert m == ColumnMapping(name="Well Name", x="X", y="Y", kb="Elevation", td="Total Depth")
    assert m.is_complete


def test_detect_falls_back_to_uwi_and_reports_incomplete():
    m = detect_columns(["UWI", "Lon", "Lat"])
    assert m.name == "UWI" and m.uwi == "UWI" and m.x == "Lon" and m.y == "Lat"
    incomplete = detect_columns(["Kuyu", "Sharq", "Shimal"])
    assert not incomplete.is_complete


def test_read_and_build_sample_csv(sample_field):
    table = read_well_table(sample_field.wells_csv)
    assert table.mapping.is_complete
    assert table.suggested_crs is None
    result = build_well_layer(table, table.mapping, UTM, UTM)
    gdf = result.layer.gdf
    assert result.warnings == []
    assert len(gdf) == 10
    assert list(gdf.columns[:5]) == ["name", "uwi", "kb", "td", "Status"]
    assert gdf.loc[0, "name"] == "AB-01" and gdf.loc[0, "name_key"] == "AB1"
    expected = sample_field.wells[0]
    assert gdf.geometry.iloc[0].x == pytest.approx(expected.x)
    assert gdf.loc[0, "kb"] == pytest.approx(expected.kb)
    assert result.layer.import_options["mapping"]["x"] == "Easting"


def test_xlsx_sheets_and_detection(sample_field):
    assert list_sheets(sample_field.wells_xlsx) == ["Wells"]
    result = load_well_layer(sample_field.wells_xlsx, UTM)
    assert len(result.layer.gdf) == 10
    assert result.layer.gdf["kb"].notna().all()
    assert result.layer.gdf["uwi"].isna().all()


def test_semicolon_csv_and_lonlat_reprojection(tmp_path):
    path = tmp_path / "wells.csv"
    path.write_text("Name;Longitude;Latitude\nW-1;49.87;40.41\nW-2;49.90;40.42\n")
    table = read_well_table(path)
    assert table.mapping.x == "Longitude"
    assert table.suggested_crs == "EPSG:4326"
    result = load_well_layer(path, UTM)
    x, y = result.layer.gdf.geometry.iloc[0].x, result.layer.gdf.geometry.iloc[0].y
    assert 380_000 < x < 420_000 and 4_460_000 < y < 4_490_000


def test_numeric_lonlat_without_geographic_headers(tmp_path):
    path = tmp_path / "w.csv"
    path.write_text("well,x,y\nA,49.8,40.4\n")
    assert read_well_table(path).suggested_crs == "EPSG:4326"


def test_bad_rows_and_duplicates_become_warnings(tmp_path):
    path = tmp_path / "messy.csv"
    path.write_text(
        "Well,X,Y\nA-1,405000,4475000\n,405100,4475100\nA-2,abc,4475200\nA 01,405300,4475300\n"
    )
    result = load_well_layer(path, UTM)
    assert len(result.layer.gdf) == 2
    assert any("Skipped 2 row(s)" in w and "3, 4" in w for w in result.warnings)
    assert any("Duplicate" in w and "A1" in w for w in result.warnings)


def test_errors(tmp_path):
    with pytest.raises(DataReadError, match="File not found"):
        read_well_table(tmp_path / "none.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("Well,X,Y\n")
    with pytest.raises(DataReadError, match="no rows"):
        read_well_table(empty)
    odd = tmp_path / "odd.csv"
    odd.write_text("Kuyu,Sharq,Shimal\nA,1,2\n")
    with pytest.raises(DataReadError, match="Could not detect"):
        load_well_layer(odd, UTM)
    table = read_well_table(odd)
    with pytest.raises(DataReadError, match="missing"):
        build_well_layer(table, ColumnMapping(name="Kuyu"), UTM, UTM)
    with pytest.raises(DataReadError, match="not found"):
        build_well_layer(table, ColumnMapping(name="Kuyu", x="X", y="Shimal"), UTM, UTM)
    explicit = build_well_layer(table, ColumnMapping(name="Kuyu", x="Sharq", y="Shimal"), UTM, UTM)
    assert len(explicit.layer.gdf) == 1


def test_large_table_is_fast(tmp_path):
    n = 100_000
    rng = np.random.default_rng(0)
    path = tmp_path / "many.csv"
    pd.DataFrame(
        {
            "Well": [f"W-{i}" for i in range(n)],
            "Easting": rng.uniform(380_000, 420_000, n),
            "Northing": rng.uniform(4_450_000, 4_500_000, n),
            "KB": rng.uniform(0, 50, n),
        }
    ).to_csv(path, index=False)
    start = time.perf_counter()
    result = load_well_layer(path, UTM)
    elapsed = time.perf_counter() - start
    assert len(result.layer.gdf) == n
    assert elapsed < 3.0, f"100k well import took {elapsed:.2f}s"
