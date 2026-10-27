import geopandas as gpd
import pandas as pd
import pytest
from shapely import Point

from petrogis.core.layers import DelimitedLayer, LayerKind
from petrogis.io.delimited_io import (
    build_delimited_layer,
    load_delimited_layer,
    read_delimited_table,
)
from petrogis.io.errors import DataReadError
from petrogis.io.tables import infer_numeric_columns, to_numbers
from petrogis.io.vector_io import read_vector
from petrogis.io.wells_io import load_well_layer

UTM = "EPSG:32639"


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("405123,45", 405123.45),
        ("405 123,45", 405123.45),
        ("405\N{NO-BREAK SPACE}123,45", 405123.45),
        ("1.234.567,8", 1234567.8),
        ("1,234,567.8", 1234567.8),
        ("4'475'000", 4475000.0),
        ("-12,5", -12.5),
        ("abc", None),
        ("", None),
    ],
)
def test_to_numbers_locales(raw, value):
    result = to_numbers(pd.Series([raw], dtype=object)).iloc[0]
    assert (pd.isna(result) and value is None) or result == pytest.approx(value)


def test_comma_thousands_column_and_numeric_passthrough():
    assert to_numbers(pd.Series(["1,234", "12,345"])).tolist() == [1234.0, 12345.0]
    assert to_numbers(pd.Series([1, 2.5])).tolist() == [1.0, 2.5]


def test_infer_numeric_columns_keeps_text():
    df = pd.DataFrame({"kb": ["21,8", "33,9"], "status": ["OIL", "DRY"], "n": ["1", ""]})
    out = infer_numeric_columns(df)
    assert out["kb"].tolist() == [21.8, 33.9]
    assert out["status"].tolist() == ["OIL", "DRY"]
    assert out["n"].iloc[0] == 1.0


def test_cyrillic_and_azerbaijani_headers_detected():
    from petrogis.io.wells_io import detect_columns, normalize_header

    assert normalize_header("Скважина №") == "скважина"
    m = detect_columns(["Скважина", "X", "Y", "Альтитуда ротора", "Забой"])
    assert (m.name, m.kb, m.td) == ("Скважина", "Альтитуда ротора", "Забой")
    assert detect_columns(["Quyu", "X", "Y"]).name == "Quyu"


def test_well_import_semicolon_decimal_comma(tmp_path):
    path = tmp_path / "quyular.csv"
    path.write_text("Quyu;X;Y;KB\nAB-1;405123,45;4475000,5;21,8\nAB-2;405500,00;4475200,25;33,9\n")
    result = load_well_layer(path, UTM)
    gdf = result.layer.gdf
    assert result.warnings == []
    assert gdf.geometry.iloc[0].x == pytest.approx(405123.45)
    assert gdf.geometry.iloc[1].y == pytest.approx(4475200.25)
    assert gdf["kb"].tolist() == [21.8, 33.9]
    assert result.layer.source_crs == UTM


def test_delimited_layer_build_and_warnings(tmp_path):
    path = tmp_path / "samples.csv"
    path.write_text(
        "id;Easting;Northing;depth\n1;405000,5;4475000;10\n2;;4475100;11\n3;405200;4475200;12\n"
    )
    table = read_delimited_table(path)
    assert (table.mapping.x, table.mapping.y) == ("Easting", "Northing")
    result = build_delimited_layer(table, "Easting", "Northing", UTM, UTM)
    layer = result.layer
    assert isinstance(layer, DelimitedLayer) and layer.kind is LayerKind.DELIMITED
    assert len(layer.gdf) == 2 and layer.paths.points.shape == (2, 2)
    assert layer.import_options == {"x": "Easting", "y": "Northing", "source_crs": UTM}
    assert any("Skipped 1 row" in w and "3" in w for w in result.warnings)
    with pytest.raises(DataReadError, match="different"):
        build_delimited_layer(table, "Easting", "Easting", UTM, UTM)
    with pytest.raises(DataReadError, match="not found"):
        build_delimited_layer(table, "X", "Northing", UTM, UTM)


def test_delimited_lonlat_reprojected_and_bad_inputs(tmp_path):
    path = tmp_path / "pts.txt"
    path.write_text("lon,lat\n49.87,40.41\n")
    layer = load_delimited_layer(path, UTM).layer
    assert layer.source_crs == "EPSG:4326"
    assert 380_000 < layer.gdf.geometry.iloc[0].x < 420_000
    with pytest.raises(DataReadError, match="Unsupported"):
        read_delimited_table(tmp_path / "x.xlsx")
    odd = tmp_path / "odd.csv"
    odd.write_text("a,b\nx,y\n")
    with pytest.raises(DataReadError, match="detect"):
        load_delimited_layer(odd, UTM)


def test_cpg_encoding_and_override(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"ad": ["Бузовна", "Гюнешли"]}, geometry=[Point(0, 0), Point(1, 1)], crs=UTM
    )
    with_cpg = tmp_path / "with_cpg.shp"
    gdf.to_file(with_cpg, encoding="cp1251")
    assert (tmp_path / "with_cpg.cpg").exists()
    assert read_vector(with_cpg, UTM)["ad"].tolist() == ["Бузовна", "Гюнешли"]

    (tmp_path / "with_cpg.cpg").unlink()  # a legacy file without its .cpg
    assert read_vector(with_cpg, UTM, encoding="cp1251")["ad"].tolist() == ["Бузовна", "Гюнешли"]
