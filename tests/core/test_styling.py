"""Classification, style validation, symbol assignment, well status and legend entries."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import LineString, Point, Polygon

from petrogis.core.classify import (
    MAX_AUTO_CATEGORIES,
    RAMPS,
    assign_breaks,
    assign_categories,
    category_colors,
    class_breaks,
    format_break,
    natural_breaks,
    python_value,
    ramp_colors,
    unique_categories,
)
from petrogis.core.extent import Extent
from petrogis.core.geometry import label_points
from petrogis.core.layers import RasterInfo, RasterLayer, VectorLayer, WellLayer
from petrogis.core.legend import legend_entries
from petrogis.core.renderers import classify_features, classify_wells, label_texts
from petrogis.core.styles import (
    MAX_CLASSES,
    Category,
    ClassBreak,
    LabelStyle,
    RasterStyle,
    VectorStyle,
    WellStyle,
)
from petrogis.core.wells import (
    WellStatus,
    classify_status,
    classify_statuses,
    detect_status_column,
)


# --- classification ----------------------------------------------------------------------------
def test_equal_interval_and_quantile():
    values = [0, 1, 2, 3, 4, 5, 6, 7, 8, 100, np.nan]
    assert class_breaks(values, "equal_interval", 4) == [0, 25, 50, 75, 100]
    assert class_breaks(values, "quantile", 2) == [0, 4.5, 100]
    assert class_breaks([3, 3, 3], "quantile", 4) == [3, 3]
    assert class_breaks([np.nan], "equal_interval", 3) == []
    with pytest.raises(ValueError, match="Unknown"):
        class_breaks(values, "bogus", 3)


def test_natural_breaks_finds_clusters():
    assert natural_breaks([1, 2, 3, 10, 11, 12, 30, 31], 3) == [1, 3, 12, 31]
    assert natural_breaks([5, 5, 5], 3) == [5, 5]
    rng = np.random.default_rng(0)
    clusters = np.concatenate([rng.normal(10, 1, 50_000), rng.normal(60, 1, 50_000)])
    edges = class_breaks(clusters, "natural_breaks", 2)
    assert 13 < edges[1] < 57  # the break falls in the gap


def test_ramps_and_palettes():
    for name in RAMPS:
        colors = ramp_colors(name, 7)
        assert len(colors) == 7 and all(c.startswith("#") and len(c) == 7 for c in colors)
    assert ramp_colors("viridis", 2) == ["#440154", "#fde725"]
    assert ramp_colors("viridis", 2, invert=True) == ["#fde725", "#440154"]
    assert ramp_colors("viridis", 0) == []
    assert len(ramp_colors("reds", 1)) == 1
    colors = category_colors(25)
    assert len(colors) == 25 and colors[0] == colors[20]


def test_categories_and_assignment():
    values = pd.Series(["OIL", "DRY", None, "OIL", "GAS"])
    found = unique_categories(values)
    assert found == ["DRY", "GAS", "OIL", None]
    np.testing.assert_array_equal(assign_categories(values, found), [2, 0, 3, 2, 1])
    np.testing.assert_array_equal(assign_categories(values, ["OIL"]), [0, -1, -1, 0, -1])
    numbers = pd.Series([1, 2.0, None, 3], dtype="Float64")
    np.testing.assert_array_equal(assign_categories(numbers, [2, 1]), [1, 0, -1, -1])
    many = pd.Series([f"v{i % 150}" for i in range(1000)])
    assert len(unique_categories(many)) == MAX_AUTO_CATEGORIES
    mixed = pd.Series([3, 1, "b", "A", True], dtype=object)
    assert unique_categories(mixed)[-2:] == ["A", "b"]


def test_python_value():
    assert python_value(pd.NA) is None
    assert python_value(np.float64("nan")) is None
    assert python_value(np.int64(3)) == 3 and isinstance(python_value(np.int64(3)), int)
    assert python_value(np.bool_(True)) is True
    assert python_value(np.float32(1.5)) == 1.5
    assert python_value(pd.Timestamp("2024-01-01")) == "2024-01-01 00:00:00"


def test_assign_breaks_uses_qgis_bounds():
    np.testing.assert_array_equal(
        assign_breaks([0, 1, 1.5, 3, 3.1, np.nan], [(0, 1), (1, 3)]), [0, 0, 1, 1, -1, -1]
    )


def test_format_break():
    assert format_break(5.0) == "5"
    assert format_break(1560.257) == "1560.3"
    assert format_break(0.123456) == "0.1235"


# --- styles ----------------------------------------------------------------------------------
def _classified_style():
    return VectorStyle(
        renderer="categorized",
        classify_by='"Status"',
        categories=[Category("OIL", "#00ff00", "Oil wells"), Category(None, "#cccccc")],
        labels=LabelStyle(enabled=True, expression="name || ' ' || kb", size=10),
        fill_opacity=0.4,
    )


def test_style_round_trip_with_nested_objects():
    style = _classified_style()
    data = style.to_dict()
    assert data["categories"][0] == {"value": "OIL", "color": "#00ff00", "label": "Oil wells"}
    assert VectorStyle.from_dict(data) == style
    graduated = VectorStyle(
        renderer="graduated",
        classify_by="kb * 2",
        breaks=[ClassBreak(0, 10, "#000000"), ClassBreak(10, 20.5, "#ffffff", "high")],
        ramp="reds",
        method="natural_breaks",
    )
    assert VectorStyle.from_dict(graduated.to_dict()) == graduated
    # Old project files have none of the new keys.
    assert VectorStyle.from_dict({"color": "#123456"}).renderer == "single"
    well = WellStyle(symbol_by_status=False, status_field="Type", label_expression="uwi")
    assert WellStyle.from_dict(well.to_dict()) == well


def test_display_labels():
    assert Category(None, "#000000").display_label == "NULL"
    assert Category(3, "#000000").display_label == "3"
    assert Category("x", "#000000", "Custom").display_label == "Custom"
    assert ClassBreak(1, 2.5, "#000000").display_label == "1 – 2.5"


@pytest.mark.parametrize(
    "data",
    [
        {"renderer": "heatmap"},
        {"renderer": "categorized"},  # needs classify_by
        {"classify_by": "1 +"},
        {"classify_by": "__import__('os')"},
        {"fill_opacity": 1.5},
        {"ramp": "rainbow"},
        {"method": "bogus"},
        {"categories": "OIL"},
        {"categories": ["OIL"]},
        {"categories": [{"value": [1], "color": "#000000"}]},
        {"categories": [{"value": float("inf"), "color": "#000000"}]},
        {"categories": [{"value": "x", "color": "red"}]},
        {"categories": [{"value": "x", "color": "#000000", "label": "y" * 501}]},
        {"categories": [{"value": "x", "color": "#000000"}] * (MAX_CLASSES + 1)},
        {"breaks": [{"lower": 5, "upper": 1, "color": "#000000"}]},
        {"breaks": [{"lower": "a", "upper": 1, "color": "#000000"}]},
        {"labels": "yes"},
        {"labels": {"enabled": "yes"}},
        {"labels": {"expression": "concat("}},
        {"labels": {"size": 0}},
    ],
)
def test_invalid_vector_styles_rejected(data):
    with pytest.raises((ValueError, TypeError)):
        VectorStyle.from_dict(data)


def test_invalid_style_objects_rejected():
    with pytest.raises(ValueError, match="Category"):
        VectorStyle(categories=["OIL"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="ClassBreak"):
        VectorStyle(breaks=[1])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="LabelStyle"):
        VectorStyle(labels={})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at most"):
        VectorStyle(categories=[Category("x", "#000000")] * (MAX_CLASSES + 1))
    with pytest.raises(ValueError):
        WellStyle(label_expression="(((")
    with pytest.raises(ValueError):
        WellStyle(status_field=3)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RasterStyle(opacity=2)


# --- renderers ---------------------------------------------------------------------------------
@pytest.fixture
def frame():
    return gpd.GeoDataFrame(
        {
            "Status": ["OIL", "DRY", None, "GAS"],
            "kb": [5.0, 15.0, 25.0, None],
            "name": list("abcd"),
        },
        geometry=[Point(i, i) for i in range(4)],
    )


def test_classify_categorized(frame):
    result = classify_features(_classified_style(), frame)
    assert result.error is None
    assert [c.label for c in result.classes] == ["Oil wells", "NULL", "Other"]
    assert [c.rows.tolist() for c in result.classes] == [[0], [2], [1, 3]]
    assert result.classes[-1].is_other and result.classes[-1].color == "#1f77b4"


def test_classify_graduated_and_single(frame):
    style = VectorStyle(
        renderer="graduated",
        classify_by="kb",
        breaks=[
            ClassBreak(0, 10, "#000000"),
            ClassBreak(10, 20, "#111111"),
            ClassBreak(20, 30, "#222222"),
        ],
    )
    result = classify_features(style, frame)
    assert [c.rows.tolist() for c in result.classes] == [[0], [1], [2], [3]]
    assert result.classes[-1].label == "Other"
    single = classify_features(VectorStyle(), frame)
    assert len(single.classes) == 1 and single.classes[0].rows.tolist() == [0, 1, 2, 3]
    assert classify_features(style, None).classes[0].rows.size == 0


def test_classify_falls_back_on_bad_expression(frame):
    style = VectorStyle(renderer="categorized", classify_by='"missing"', categories=[])
    result = classify_features(style, frame)
    assert "not found" in result.error
    assert len(result.classes) == 1 and result.classes[0].rows.size == 4
    text_graduated = VectorStyle(
        renderer="graduated", classify_by="Status", breaks=[ClassBreak(0, 1, "#000000")]
    )
    assert "number" in classify_features(text_graduated, frame).error


def test_layer_caches_classification_per_style(frame):
    layer = VectorLayer(name="v", source="v.shp", gdf=frame, style=_classified_style())
    first = layer.classification()
    assert layer.classification() is first
    layer.style = VectorStyle()
    assert layer.classification() is not first


def test_label_texts_and_anchors(frame):
    assert label_texts("name || ': ' || kb", frame) == ["a: 5", "b: 15", "c: 25", None]
    geoms = [
        Point(1, 2),
        LineString([(0, 0), (10, 0)]),
        Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
        None,
        Polygon(),
    ]
    anchors = label_points(geoms)
    assert anchors.shape == (5, 2)
    np.testing.assert_array_equal(anchors[0], [1, 2])
    assert anchors[1, 1] == 0 and 0 <= anchors[1, 0] <= 10
    assert 0 < anchors[2, 0] < 4 and 0 < anchors[2, 1] < 4
    assert np.isnan(anchors[3]).all() and np.isnan(anchors[4]).all()
    assert label_points([]).shape == (0, 2)


# --- wells -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("OIL", WellStatus.OIL),
        ("Oil producer (ESP)", WellStatus.OIL),
        ("нефтяная", WellStatus.OIL),
        ("Gas", WellStatus.GAS),
        ("qaz", WellStatus.GAS),
        ("Oil & Gas", WellStatus.OIL_GAS),
        ("Water injector", WellStatus.WATER_INJECTOR),
        ("WI", WellStatus.WATER_INJECTOR),
        ("нагнетательная", WellStatus.WATER_INJECTOR),
        ("Gas Injector", WellStatus.GAS_INJECTOR),
        ("Water source", WellStatus.WATER),
        ("DRY", WellStatus.DRY),
        ("D&A", WellStatus.DRY),
        ("P&A", WellStatus.ABANDONED),
        ("ləğv edilmiş", WellStatus.ABANDONED),
        ("ликвидирована", WellStatus.ABANDONED),
        ("Shut-in", WellStatus.SUSPENDED),
        ("консервация", WellStatus.SUSPENDED),
        ("Proposed", WellStatus.PLANNED),
        ("Observation", WellStatus.OBSERVATION),
        ("xyz", WellStatus.UNKNOWN),
        ("", WellStatus.UNKNOWN),
        ("123", WellStatus.UNKNOWN),
        (None, WellStatus.UNKNOWN),
        (float("nan"), WellStatus.UNKNOWN),
    ],
)
def test_classify_status(text, status):
    assert classify_status(text) is status


def test_status_helpers():
    assert classify_statuses(["OIL", "OIL", ["unhashable"]]) == [
        WellStatus.OIL,
        WellStatus.OIL,
        WellStatus.UNKNOWN,
    ]
    assert detect_status_column(["Well", "X", "Status"]) == "Status"
    assert detect_status_column(["Скважина", "Состояние"]) == "Состояние"
    assert detect_status_column(["Well Status"]) == "Well Status"
    assert detect_status_column(["a", "b"]) is None


def _wells(statuses):
    gdf = gpd.GeoDataFrame(
        {"name": [f"W{i}" for i in range(len(statuses))], "Status": statuses},
        geometry=[Point(i, 0) for i in range(len(statuses))],
    )
    return WellLayer(name="w", source="w.csv", gdf=gdf)


def test_classify_wells_groups_by_status():
    layer = _wells(["OIL", "DRY", "??", "OIL", "Gas"])
    groups = classify_wells(layer.style, layer.gdf)
    assert [(g.status, g.rows.tolist()) for g in groups] == [
        (WellStatus.OIL, [0, 3]),
        (WellStatus.GAS, [4]),
        (WellStatus.DRY, [1]),
        (WellStatus.UNKNOWN, [2]),
    ]
    off = classify_wells(WellStyle(symbol_by_status=False), layer.gdf)
    assert [(g.status, g.rows.size) for g in off] == [(WellStatus.UNKNOWN, 5)]
    missing = classify_wells(WellStyle(status_field="Nope"), layer.gdf)
    assert [g.status for g in missing] == [WellStatus.UNKNOWN]
    assert classify_wells(WellStyle(), None) == []


# --- legend ------------------------------------------------------------------------------------
def test_legend_entries_per_layer_kind(frame):
    layer = VectorLayer(name="v", source="v.shp", gdf=frame, style=_classified_style())
    entries = legend_entries(layer)
    assert [(e.label, e.kind) for e in entries] == [
        ("Oil wells", "marker"),
        ("NULL", "marker"),
        ("Other", "marker"),
    ]
    poly = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (1, 0), (1, 1)])])
    fill = legend_entries(
        VectorLayer(name="p", source="p", gdf=poly, style=VectorStyle(fill_opacity=0.3))
    )
    assert [(e.label, e.kind, e.fill_opacity) for e in fill] == [("", "fill", 0.3)]
    line = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (1, 1)])])
    assert legend_entries(VectorLayer(name="l", source="l", gdf=line))[0].kind == "line"

    wells = legend_entries(_wells(["OIL", "DRY", "??"]))
    assert [(e.label, e.shape) for e in wells] == [
        ("Oil", "circle"),
        ("Dry", "crossed"),
        ("Other", "circle"),
    ]
    unknown_only = legend_entries(_wells(["??"]))
    assert [(e.label, e.shape) for e in unknown_only] == [("", "circle")]

    info = RasterInfo(
        crs="EPSG:32639", width=1, height=1, band_count=1, dtype="float32", nodata=None,
        extent=Extent(0, 0, 1, 1), native_resolution=(1, 1), stats_min=0, stats_max=10,
        p2=1, p98=9,
    )  # fmt: skip
    raster = legend_entries(RasterLayer(name="r", source="r", info=info))
    assert [(e.kind, e.label, e.colormap) for e in raster] == [("ramp", "1 – 9", "viridis")]
    assert legend_entries(RasterLayer(name="r", source="r")) == []
