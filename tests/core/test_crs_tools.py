import numpy as np
import pytest
import shapely
from pyproj import Transformer

from petrogis.core.crs import (
    is_known_pipeline,
    list_crs,
    lonlat_area,
    search_crs,
    transformation_options,
)
from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, RasterLayer
from petrogis.core.project import EventKind, Project
from petrogis.core.transform import transform_geometries

ABSHERON = (49.0, 39.5, 51.0, 41.0)
PULKOVO_GK9 = "EPSG:28409"
UTM39 = "EPSG:32639"


def test_catalogue_and_search():
    assert len(list_crs()) > 5000
    assert search_crs("32639")[0].code == UTM39
    assert search_crs("EPSG:32639")[0].code == UTM39
    results = search_crs("pulkovo 1942 gauss-kruger zone 9")
    assert results[0].code == PULKOVO_GK9  # not "... zone 3", where "9" only appears in "1942"
    assert "EPSG:2525" in [e.code for e in results[:3]]  # the 3-degree zone 9 variant
    assert all("pulkovo" in e.name.lower() for e in results)
    assert search_crs("no such crs anywhere") == []
    assert len(search_crs("", limit=10)) == 10


def test_transformation_options_for_caspian_pulkovo():
    options = transformation_options(PULKOVO_GK9, UTM39, ABSHERON)
    available = [o for o in options if o.available]
    assert len(available) >= 2  # several Pulkovo 1942 → WGS 84 variants exist here
    assert all(o.pipeline.startswith("+proj=pipeline") for o in available)
    accuracies = [o.accuracy for o in available if o.accuracy is not None]
    assert accuracies and min(accuracies) < max(accuracies)


def test_pipeline_validation_rejects_unknown_pipelines():
    options = transformation_options(PULKOVO_GK9, UTM39, ABSHERON)
    assert is_known_pipeline(PULKOVO_GK9, UTM39, options[0].pipeline)
    assert not is_known_pipeline(PULKOVO_GK9, UTM39, "+proj=pipeline +step +proj=affine +xoff=1")


def test_lonlat_area():
    west, south, east, north = lonlat_area(UTM39, (400_000, 4_470_000, 410_000, 4_480_000))
    assert 49.8 < west < east < 50.0 and 40.3 < south < north < 40.5


def test_transform_geometries_matches_pyproj():
    pipeline = transformation_options(PULKOVO_GK9, UTM39, ABSHERON)[1].pipeline
    pts = shapely.points([(9_402_000.0, 4_475_000.0), (9_410_000.0, 4_480_000.0)])
    out = transform_geometries(pts, pipeline)
    expected = Transformer.from_pipeline(pipeline).transform(
        [9_402_000.0, 9_410_000.0], [4_475_000.0, 4_480_000.0]
    )
    np.testing.assert_allclose(shapely.get_coordinates(out), np.column_stack(expected))


def test_project_transformations_and_replace_contents(tmp_path):
    project = Project()
    project.set_transformation(PULKOVO_GK9, UTM39, "+proj=pipeline +step +proj=noop")
    assert project.transformation_for("epsg:28409", 32639) == "+proj=pipeline +step +proj=noop"
    assert project.transformation_for(None, UTM39) is None
    assert project.dirty
    project.set_transformation(PULKOVO_GK9, UTM39, None)
    assert project.transformations == {}

    info = RasterInfo(
        crs=UTM39, width=1, height=1, band_count=1, dtype="float32", nodata=None,
        extent=Extent(0, 0, 1, 1), native_resolution=(1, 1),
        stats_min=0, stats_max=1, p2=0, p98=1,
    )  # fmt: skip
    a = RasterLayer(name="a", source="a.tif", info=info)
    b = RasterLayer(name="b", source="b.tif", info=info)
    project.add_layer(a)
    project.path = str(tmp_path / "x.pgis")
    project.dirty = False
    events = []
    project.subscribe(events.append)
    project.replace_contents(PULKOVO_GK9, [b, a])
    assert project.crs == PULKOVO_GK9 and [layer.name for layer in project.layers] == ["b", "a"]
    assert project.path == str(tmp_path / "x.pgis") and project.dirty
    assert [e.kind for e in events][:2] == [EventKind.CLEARED, EventKind.CRS_CHANGED]
    with pytest.raises(ValueError):
        project.replace_contents(UTM39, [a, a])
    project.clear()
    assert project.transformations == {}
