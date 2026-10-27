import geopandas as gpd
import pytest
from shapely import Point

from petrogis.core.layers import LayerKind, WellLayer
from petrogis.core.styles import WellStyle
from petrogis.core.wells import normalize_well_name


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("AB-01", "AB1"),
        ("AB 02", "AB2"),
        ("ab_03", "AB3"),
        ("AB-4", "AB4"),
        ("AB1", "AB1"),
        ("AB-4A", "AB4A"),
        ("  Guneshli #12 ", "GUNESHLI12"),
        ("OFFSET-1", "OFFSET1"),
        ("00", "0"),
        (17, "17"),
    ],
)
def test_normalize_well_name(raw, key):
    assert normalize_well_name(raw) == key


def test_sidetracks_stay_distinct():
    assert normalize_well_name("AB-4") != normalize_well_name("AB-4ST1")


def test_well_layer_lookup_and_xy():
    gdf = gpd.GeoDataFrame(
        {"name": ["AB-01", "AB-02", "AB 01"], "name_key": ["AB1", "AB2", "AB1"]},
        geometry=[Point(1, 2), Point(3, 4), Point(5, 6)],
        crs="EPSG:32639",
    )
    layer = WellLayer(name="wells", source="wells.csv", gdf=gdf)
    assert layer.kind is LayerKind.WELLS
    assert layer.find("ab_02") == 1
    assert layer.find("AB 1") == 0  # first duplicate wins
    assert layer.find("XX-9") is None
    assert layer.xy().tolist() == [[1, 2], [3, 4], [5, 6]]
    assert layer.names == ["AB-01", "AB-02", "AB 01"]
    assert layer.features_at(3, 4, tolerance=0.5) == [1]


def test_well_style_validation():
    assert WellStyle.from_dict(WellStyle(symbol_size=12).to_dict()).symbol_size == 12
    with pytest.raises(ValueError):
        WellStyle(label_color="black")
    with pytest.raises(ValueError):
        WellStyle(symbol_size=0)
