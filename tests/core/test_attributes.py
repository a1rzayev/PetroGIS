import time

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import LineString, Point, Polygon

from petrogis.core.attributes import filter_rows, format_value, identify, sort_rows
from petrogis.core.layers import VectorLayer
from petrogis.core.project import Project


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        (float("nan"), ""),
        (pd.NA, ""),
        (np.float32(1.5), "1.5"),
        (405123.456, "405123.456"),
        (1 / 3, "0.3333333333"),
        (7, "7"),
        ("AB-01", "AB-01"),
        (True, "True"),
    ],
)
def test_format_value(value, text):
    assert format_value(value) == text


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "name": ["AB-02", "ab-10", None, "XY-1"],
            "kb": [21.8, np.nan, 5.0, 30.0],
            "geometry": [None] * 4,
        }
    )


def test_filter_rows_case_insensitive_any_column(frame):
    assert filter_rows(frame, "ab").tolist() == [0, 1]
    assert filter_rows(frame, "30").tolist() == [3]
    assert filter_rows(frame, "  ").tolist() == [0, 1, 2, 3]
    assert filter_rows(frame, "none").tolist() == []


def test_sort_rows_numeric_and_text_missing_last(frame):
    rows = np.arange(4)
    assert sort_rows(frame, rows, "kb").tolist() == [2, 0, 3, 1]
    assert sort_rows(frame, rows, "kb", ascending=False).tolist() == [3, 0, 2, 1]
    assert sort_rows(frame, rows, "name").tolist() == [0, 1, 3, 2]
    assert sort_rows(frame, np.array([3, 1]), "name").tolist() == [1, 3]


def test_identify_orders_layers_and_features():
    project = Project()
    area = VectorLayer(
        name="lease",
        source="lease.gpkg",
        gdf=gpd.GeoDataFrame(
            {"name": ["Block A"]},
            geometry=[Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])],
            crs="EPSG:32639",
        ),
    )
    pts = VectorLayer(
        name="points",
        source="p.gpkg",
        gdf=gpd.GeoDataFrame(
            {"id": [1, 2, 3]},
            geometry=[Point(52, 50), Point(50, 50.5), Point(90, 90)],
            crs="EPSG:32639",
        ),
    )
    hidden = VectorLayer(
        name="hidden",
        source="h.gpkg",
        visible=False,
        gdf=gpd.GeoDataFrame(geometry=[LineString([(0, 50), (100, 50)])], crs="EPSG:32639"),
    )
    project.add_layer(area)
    project.add_layer(hidden)
    project.add_layer(pts)

    hits = identify(project, 50, 50, tolerance=3)
    assert [h.layer_name for h in hits] == ["points", "points", "lease"]
    assert [h.position for h in hits[:2]] == [1, 0]  # nearest first
    assert hits[0].title == "2"
    assert ("geometry", "Point") in hits[0].attributes
    assert hits[2].title == "Block A"
    assert identify(project, 500, 500, tolerance=3) == []


def test_filter_and_sort_large_table_fast():
    n = 100_000
    df = pd.DataFrame(
        {"name": [f"W-{i}" for i in range(n)], "kb": np.random.default_rng(0).random(n)}
    )
    start = time.perf_counter()
    rows = filter_rows(df, "w-99")
    rows = sort_rows(df, rows, "kb")
    sorted_all = sort_rows(df, np.arange(n), "name")
    elapsed = time.perf_counter() - start
    assert rows.size == 1111 and sorted_all.size == n
    assert elapsed < 1.0, f"filter+sort took {elapsed:.2f}s"
