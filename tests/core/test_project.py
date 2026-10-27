import geopandas as gpd
import pytest
from shapely import Point

from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, RasterLayer, VectorLayer
from petrogis.core.project import EventKind, Project
from petrogis.core.styles import RasterStyle, VectorStyle


def _vector(name: str, *xy: tuple[float, float]) -> VectorLayer:
    gdf = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in xy], crs="EPSG:32639")
    return VectorLayer(name=name, source=f"{name}.geojson", gdf=gdf)


def _raster(name: str = "r") -> RasterLayer:
    info = RasterInfo(
        crs="EPSG:32639", width=10, height=10, band_count=1, dtype="float32", nodata=None,
        extent=Extent(0, 0, 100, 100), native_resolution=(10, 10),
        stats_min=0, stats_max=10, p2=1, p98=9,
    )  # fmt: skip
    return RasterLayer(name=name, source=f"{name}.tif", info=info)


@pytest.fixture
def project():
    p = Project()
    events = []
    p.subscribe(events.append)
    p.events = events
    return p


def test_default_crs():
    assert Project().crs == "EPSG:32639"


def test_add_inserts_on_top_and_emits(project):
    a, b = _vector("a", (0, 0)), _vector("b", (1, 1))
    project.add_layer(a)
    project.add_layer(b)
    assert [layer.name for layer in project.layers] == ["b", "a"]
    assert [e.kind for e in project.events] == [EventKind.LAYER_ADDED] * 2
    assert project.dirty
    with pytest.raises(ValueError):
        project.add_layer(a)


def test_move_rename_visibility_remove(project):
    a, b, c = _vector("a", (0, 0)), _vector("b", (1, 1)), _vector("c", (2, 2))
    for layer in (a, b, c):
        project.add_layer(layer)
    project.events.clear()

    project.move_layer(a.id, 0)
    assert [layer.name for layer in project.layers] == ["a", "c", "b"]
    project.move_layer(a.id, 0)  # no-op, no event
    project.rename_layer(b.id, "  Boundary ")
    assert b.name == "Boundary"
    with pytest.raises(ValueError):
        project.rename_layer(b.id, "  ")
    project.set_visible(c.id, False)
    project.set_visible(c.id, False)  # no-op
    removed = project.remove_layer(a.id)
    assert removed is a and len(project) == 2
    with pytest.raises(KeyError):
        project.get(a.id)

    kinds = [(e.kind, e.field) for e in project.events]
    assert kinds == [
        (EventKind.LAYER_MOVED, None),
        (EventKind.LAYER_CHANGED, "name"),
        (EventKind.LAYER_CHANGED, "visible"),
        (EventKind.LAYER_REMOVED, None),
    ]


def test_set_style_type_checked(project):
    v, r = _vector("v", (0, 0)), _raster()
    project.add_layer(v)
    project.add_layer(r)
    project.set_style(v.id, VectorStyle(color="#ff0000"))
    assert v.style.color == "#ff0000"
    with pytest.raises(TypeError):
        project.set_style(v.id, RasterStyle())


def test_extent_respects_visibility(project):
    v = _vector("v", (500, 500), (600, 700))
    r = _raster()
    project.add_layer(v)
    project.add_layer(r)
    assert project.extent() == Extent(0, 0, 600, 700)
    project.set_visible(v.id, False)
    assert project.extent() == Extent(0, 0, 100, 100)
    assert project.extent(visible_only=False) == Extent(0, 0, 600, 700)


def test_crs_change_only_when_empty(project):
    project.set_crs(4326)
    assert project.crs == "EPSG:4326"
    project.add_layer(_raster())
    with pytest.raises(ValueError):
        project.set_crs(32639)


def test_unsubscribe_and_clear(project):
    seen = []
    unsubscribe = project.subscribe(seen.append)
    project.add_layer(_raster())
    unsubscribe()
    project.clear()
    assert len(seen) == 1
    assert len(project) == 0 and not project.dirty


def test_vector_layer_paths_cached_and_picking():
    layer = _vector("wells", (0, 0), (100, 0), (100.5, 0))
    assert layer.paths is layer.paths
    assert layer.paths.points.shape == (3, 2)
    assert layer.features_at(100.2, 0, tolerance=1.0) == [1, 2]
    assert layer.features_at(50, 50, tolerance=1.0) == []


def test_raster_value_range_defaults_to_percentiles():
    r = _raster()
    assert r.value_range() == (1, 9)
    r.style = RasterStyle(vmin=2)
    assert r.value_range() == (2, 9)
