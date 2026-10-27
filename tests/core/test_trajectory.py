"""Minimum-curvature trajectories, formation tops, log decimation and well-data attachment."""

import math

import geopandas as gpd
import numpy as np
import pytest
from shapely import Point

from petrogis.core.layers import WellLayer
from petrogis.core.logs import METRES_PER_FOOT, metres_per_unit, minmax_decimate
from petrogis.core.project import EventKind, Project
from petrogis.core.tops import Top, surface_colors, surface_order
from petrogis.core.trajectory import Trajectory, minimum_curvature


def test_vertical_well_has_tvd_equal_to_md():
    t = Trajectory.from_survey([0, 500, 1500], [0, 0, 0], [0, 123, 0])
    np.testing.assert_allclose(t.tvd, t.md)
    np.testing.assert_allclose(t.dx, 0, atol=1e-12)
    assert t.is_vertical
    np.testing.assert_allclose(t.md_to_tvd([250, 2000]), [250, 2000])
    np.testing.assert_allclose(t.tvd_to_md([1200]), [1200])


@pytest.mark.parametrize("azimuth", [0.0, 45.0, 210.0])
def test_constant_build_arc_is_exact(azimuth):
    """A circular arc from vertical to horizontal: TVD = R and displacement = R at 90°."""
    radius = 1000.0
    inc = np.linspace(0, 90, 7)
    md = radius * np.radians(inc)
    t = Trajectory.from_survey(md, inc, np.full(inc.size, azimuth))
    assert t.tvd[-1] == pytest.approx(radius)
    assert math.hypot(t.dx[-1], t.dy[-1]) == pytest.approx(radius)
    expected_tvd = radius * np.sin(np.radians(inc))
    np.testing.assert_allclose(t.tvd, expected_tvd, atol=1e-9)
    assert math.degrees(math.atan2(t.dx[-1], t.dy[-1])) % 360 == pytest.approx(azimuth % 360)


def test_worked_example():
    """MD 1000→1100 m, inclination 0→10°, azimuth 45°: hand-computed with RF = 1.002539."""
    tvd, east, north = minimum_curvature([1000, 1100], [0, 10], [45, 45])
    assert tvd[-1] == pytest.approx(99.4924, abs=1e-3)
    assert east[-1] == pytest.approx(6.15497, abs=1e-4)
    assert north[-1] == pytest.approx(6.15497, abs=1e-4)


def test_straight_inclined_segment_uses_rf_limit():
    tvd, east, north = minimum_curvature([0, 100], [30, 30], [90, 90])
    assert tvd[-1] == pytest.approx(100 * math.cos(math.radians(30)))
    assert east[-1] == pytest.approx(50.0)
    assert north[-1] == pytest.approx(0.0, abs=1e-12)


def test_tie_in_sorting_and_azimuth_wrap():
    t = Trajectory.from_survey([1000, 500], [20, 0], [370, 10], source="s.csv")
    assert t.md.tolist() == [0, 500, 1000] and t.source == "s.csv"
    assert t.azi[-1] == pytest.approx(10.0)
    assert t.total_md == 1000


@pytest.mark.parametrize(
    ("md", "inc", "azi", "message"),
    [
        ([0, 100], [0], [0, 0], "same length"),
        ([], [], [], "no stations"),
        ([0, np.nan], [0, 0], [0, 0], "missing"),
        ([0, 100, 100], [0, 1, 2], [0, 0, 0], "Duplicate"),
        ([-5, 100], [0, 0], [0, 0], "negative"),
        ([0, 100], [0, 190], [0, 0], "Inclination"),
    ],
)
def test_invalid_surveys(md, inc, azi, message):
    with pytest.raises(ValueError, match=message):
        Trajectory.from_survey(md, inc, azi)


def test_interpolation_and_extension():
    t = Trajectory.from_survey([0, 1000, 2000], [0, 0, 60], [0, 0, 90])
    dx, _ = t.md_to_xy([0, 2100, np.nan])
    assert dx[0] == 0 and np.isnan(dx[2])
    # Beyond the last station the path continues along its direction (60° towards east).
    assert dx[1] - t.dx[-1] == pytest.approx(100 * math.sin(math.radians(60)))
    assert t.md_to_tvd([2100])[0] - t.tvd[-1] == pytest.approx(50.0)
    assert np.isnan(t.md_to_tvd([np.nan])[0])
    assert t.tvd_to_md([-1])[0] != t.tvd_to_md([-1])[0]  # NaN above the surface
    deep = t.tvd[-1] + 50
    assert t.tvd_to_md([deep])[0] == pytest.approx(2100.0)
    x, y = t.path(step=50)
    assert x.size > t.md.size and y[-1] == pytest.approx(t.dy[-1])


def test_horizontal_and_climbing_wells():
    t = Trajectory.from_survey([0, 1000, 1500, 2000], [0, 0, 90, 95], [0, 0, 0, 0])
    first = t.tvd_to_md([t.tvd.max() - 1])[0]
    assert 1000 < first < 1500  # the first time the path reaches that depth
    assert np.isnan(t.tvd_to_md([t.tvd.max() + 10])[0])  # never reached
    assert Trajectory.vertical(0).total_md == 1.0
    single = Trajectory.from_survey([0], [0], [0])
    assert single.path()[0].tolist() == [0.0]


def test_tops_order_and_colours():
    tops = [
        Top("A", "A", "Base", 1600),
        Top("A", "A", "Top", 1500),
        Top("B", "B", "Top", 1520),
        Top("B", "B", "Marker", 1300),
    ]
    order = surface_order(tops)
    assert order == ["Marker", "Top", "Base"]
    colors = surface_colors(order)
    assert list(colors) == order and len(set(colors.values())) == 3


def test_log_units_and_decimation():
    assert metres_per_unit("FT") == METRES_PER_FOOT
    assert metres_per_unit("f.") == METRES_PER_FOOT
    assert metres_per_unit("M") == 1.0 and metres_per_unit("") == 1.0
    depth = np.arange(100_000.0)
    values = np.sin(depth / 500.0)
    values[12_345] = 99.0
    values[50_000:60_000] = np.nan
    d, v = minmax_decimate(depth, values, 1000)
    assert d.size == 2000 and np.nanmax(v) == 99.0
    assert np.all(np.diff(d) >= 0) and np.isnan(v).any()
    short_d, _ = minmax_decimate(depth[:100], values[:100], 1000)
    assert short_d is depth[:100] or short_d.size == 100


def _wells():
    gdf = gpd.GeoDataFrame(
        {"name": ["AB-01", "AB-02"], "uwi": [None, None], "kb": [25.0, np.nan],
         "td": [2000.0, 2000.0], "name_key": ["AB1", "AB2"]},
        geometry=[Point(1000, 2000), Point(5000, 2000)],
    )  # fmt: skip
    return WellLayer(name="w", source="w.csv", gdf=gdf)


def test_project_attaches_trajectories_and_tops():
    project = Project()
    layer = _wells()
    project.add_layer(layer)
    events = []
    project.subscribe(lambda e: events.append((e.kind, e.field)))
    deviated = Trajectory.from_survey([0, 500, 1500], [0, 0, 45], [0, 0, 90])
    assert project.attach_trajectories(layer.id, {"AB1": deviated}, {"path": "s.csv"}) == 1
    project.attach_trajectories(layer.id, {}, {"path": "s.csv"})  # same file: record replaced
    assert layer.survey_imports == [{"path": "s.csv"}]
    picks = {"AB1": [Top("AB-01", "AB1", "Top", 1500), Top("AB-01", "AB1", "Marker", 1200)]}
    assert project.attach_tops(layer.id, picks, {"path": "t.csv"}) == 2
    project.attach_tops(layer.id, {"AB1": [Top("AB-01", "AB1", "Top", 1510)]})
    assert [(t.surface, t.md) for t in layer.tops["AB1"]] == [("Marker", 1200), ("Top", 1510)]
    assert events == [
        (EventKind.LAYER_CHANGED, "trajectories"),
        (EventKind.LAYER_CHANGED, "tops"),
        (EventKind.LAYER_CHANGED, "tops"),
    ]
    assert layer.trajectory_for(0) is deviated and layer.trajectory_for(1) is None
    assert layer.tops_for(1) == [] and layer.kb_at(0) == 25.0 and layer.kb_at(1) is None
    paths, bottoms = layer.well_paths()
    assert np.isfinite(paths).all() and bottoms.shape == (1, 2)
    assert bottoms[0, 0] == pytest.approx(1000 + deviated.dx[-1])
    with pytest.raises(TypeError):
        from petrogis.core.layers import VectorLayer

        other = VectorLayer(name="v", source="v")
        project.add_layer(other)
        project.attach_tops(other.id, picks)


def test_well_paths_skip_vertical_and_empty():
    layer = _wells()
    assert layer.well_paths()[0].shape == (0, 2)
    layer.trajectories["AB2"] = Trajectory.vertical(1000)
    assert layer.well_paths()[1].shape == (0, 2)
    layer.trajectories["AB1"] = Trajectory.from_survey([0, 1000], [0, 30], [0, 0])
    layer.trajectories["AB2"] = Trajectory.from_survey([0, 1000], [0, 30], [0, 180])
    paths, bottoms = layer.well_paths()
    assert np.isnan(paths).any(axis=1).sum() == 1 and bottoms.shape == (2, 2)
    empty = WellLayer(name="e", source="e")
    assert empty.well_paths()[0].shape == (0, 2) and empty.kb_at(0) is None
    assert empty.trajectory_for(0) is None and empty.tops_for(0) == []
    with pytest.raises(IndexError):
        empty.key_at(0)
