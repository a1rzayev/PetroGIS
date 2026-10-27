"""P2.1 done criterion and reprojection paths: CRS changes, datum transformations, raster CRS."""

import json
import shutil

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from petrogis import sample_data as sd
from petrogis.core.crs import transformation_options
from petrogis.core.extent import Extent
from petrogis.core.project import Project
from petrogis.io.errors import DataReadError
from petrogis.io.project_io import (
    find_files_by_name,
    load_project,
    reload_layers,
    repaired_entry,
    save_project,
)
from petrogis.io.raster_io import load_raster_layer, read_raster_view, sample_raster
from petrogis.io.vector_io import load_vector_layer
from petrogis.io.wells_io import load_well_layer

UTM = "EPSG:32639"
PULKOVO_GK9 = "EPSG:28409"
ABSHERON = (49.0, 39.5, 51.0, 41.0)


@pytest.fixture
def field(sample_dir, tmp_path):
    target = tmp_path / "field"
    shutil.copytree(sample_dir, target)
    return target


def _project(field) -> Project:
    project = Project()
    project.add_layer(load_raster_layer(field / "structure_top.tif", UTM))
    project.add_layer(load_vector_layer(field / "field_boundary.geojson", UTM))
    project.add_layer(load_well_layer(field / "wells.csv", UTM).layer)
    return project


def test_round_trip_through_pulkovo_is_identical(field):
    """Done criterion: UTM 39N → Pulkovo 1942 / GK zone 9 → UTM 39N keeps geometry within 1 cm."""
    project = _project(field)
    originals = [layer for layer in project.layers]
    gk, warnings = reload_layers(originals, PULKOVO_GK9, {}, field)
    assert warnings == []
    assert [layer.id for layer in gk] == [layer.id for layer in originals]
    wells_gk = gk[0]
    assert wells_gk.gdf.crs.to_epsg() == 28409
    assert 9_300_000 < wells_gk.gdf.geometry.iloc[0].x < 9_500_000  # GK false easting with zone

    back, _ = reload_layers(gk, UTM, {}, field)
    for before, after in zip(originals, back, strict=True):
        assert after.extent == pytest.approx(before.extent, abs=0.01)
    np.testing.assert_allclose(back[0].xy(), originals[0].xy(), atol=0.01)


def test_reload_is_all_or_nothing(field):
    project = _project(field)
    (field / "structure_top.tif").unlink()
    with pytest.raises(DataReadError, match="structure_top"):
        reload_layers(list(project.layers), PULKOVO_GK9, {}, field)


def test_explicit_transformation_moves_data_consistently(field, tmp_path):
    wells_csv = tmp_path / "wells_gk.csv"
    wells_csv.write_text("Well,X,Y\nW-1,9405000,4476000\nW-2,9410000,4472000\n")
    options = [o for o in transformation_options(PULKOVO_GK9, UTM, ABSHERON) if o.available]
    default = load_well_layer(wells_csv, UTM, source_crs=PULKOVO_GK9).layer
    shifted = load_well_layer(
        wells_csv, UTM, source_crs=PULKOVO_GK9, transformation=options[-1].pipeline
    ).layer
    gap = np.hypot(*(default.xy() - shifted.xy()).T)
    assert (gap > 0.5).all() and (gap < 200).all()  # datum variants differ by metres, not km

    lookup = lambda src: options[-1].pipeline if src == PULKOVO_GK9 else None  # noqa: E731
    via_lookup = load_well_layer(
        wells_csv, UTM, source_crs=PULKOVO_GK9, transformation=lookup
    ).layer
    np.testing.assert_allclose(via_lookup.xy(), shifted.xy())


def test_raster_pipeline_warp_matches_gdal_for_same_operation(field):
    """The numpy warp used for explicit pipelines agrees with GDAL's WarpedVRT."""
    tif = field / "structure_top.tif"
    pipeline = transformation_options(UTM, PULKOVO_GK9)[0].pipeline
    via_gdal = load_raster_layer(tif, PULKOVO_GK9)
    via_pipeline = load_raster_layer(tif, PULKOVO_GK9, transformation=pipeline)
    assert via_pipeline.extent.width == pytest.approx(via_gdal.extent.width, rel=0.01)
    assert via_pipeline.extent.xmin == pytest.approx(via_gdal.extent.xmin, abs=50)
    assert via_pipeline.info.stats_min == pytest.approx(via_gdal.info.stats_min, abs=5)

    e = via_pipeline.extent
    centre = Extent(e.center[0] - 1000, e.center[1] - 1000, e.center[0] + 1000, e.center[1] + 1000)
    view = read_raster_view(tif, PULKOVO_GK9, centre, (100, 100), transformation=pipeline)
    assert view.data.count() > 0
    assert float(view.data.min()) == pytest.approx(sd.CREST_TVDSS, abs=40)
    value = sample_raster(tif, PULKOVO_GK9, *e.center, transformation=pipeline)
    assert value is not None and 1400 < value < 1800


def test_raster_without_crs_uses_assumed_crs(tmp_path):
    path = tmp_path / "nocrs.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=20, height=20, count=1, dtype="float32",
        transform=from_origin(9_400_000, 4_480_000, 100, 100),
    ) as dst:  # fmt: skip
        dst.write(np.full((20, 20), 3.0, dtype=np.float32), 1)
    layer = load_raster_layer(path, UTM, assume_crs=PULKOVO_GK9)
    assert layer.assumed_crs == PULKOVO_GK9 and layer.source_crs == PULKOVO_GK9
    assert 380_000 < layer.extent.xmin < 420_000
    view = read_raster_view(path, UTM, layer.extent, (40, 40), assume_crs=PULKOVO_GK9)
    assert view is not None and view.data.count() > 0
    assert sample_raster(path, UTM, *layer.extent.center, assume_crs=PULKOVO_GK9) == 3.0


def test_project_file_keeps_transformations_and_rejects_unknown(field):
    project = _project(field)
    options = [o for o in transformation_options(PULKOVO_GK9, UTM, ABSHERON) if o.available]
    project.set_transformation(PULKOVO_GK9, UTM, options[1].pipeline)
    path = save_project(project, field / "p.pgis")
    loaded = load_project(path)
    assert loaded.project.transformations == {(PULKOVO_GK9, UTM): options[1].pipeline}

    data = json.loads(path.read_text())
    data["transformations"].append(
        {
            "source": PULKOVO_GK9,
            "target": UTM,
            "pipeline": "+proj=pipeline +step +proj=affine +xoff=9",
        }
    )
    data["transformations"][0]["pipeline"] = "+proj=pipeline +step +proj=affine +xoff=9"
    path.write_text(json.dumps(data))
    tampered = load_project(path)
    assert tampered.project.transformations == {}
    assert any("unrecognised datum transformation" in w for w in tampered.warnings)


def test_delimited_and_assumed_raster_crs_round_trip(field, tmp_path):
    from petrogis.io.delimited_io import load_delimited_layer

    csv = field / "points.csv"
    csv.write_text("x;y\n405000,5;4475000\n")
    project = Project()
    project.add_layer(load_delimited_layer(csv, UTM).layer)
    nocrs = field / "nocrs.tif"
    with rasterio.open(
        nocrs, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32",
        transform=from_origin(400_000, 4_480_000, 100, 100),
    ) as dst:  # fmt: skip
        dst.write(np.ones((4, 4), dtype=np.float32), 1)
    project.add_layer(load_raster_layer(nocrs, UTM, assume_crs=UTM))
    loaded = load_project(save_project(project, field / "d.pgis"))
    assert loaded.warnings == []
    kinds = [layer.kind.value for layer in loaded.project.layers]
    assert kinds == ["raster", "delimited"]
    assert loaded.project.layers[0].assumed_crs == UTM
    assert loaded.project.layers[1].gdf.geometry.iloc[0].x == pytest.approx(405000.5)


def test_missing_sources_can_be_found_and_repaired(field, tmp_path):
    project = _project(field)
    path = save_project(project, field / "p.pgis")
    moved = tmp_path / "moved" / "deep"
    moved.mkdir(parents=True)
    shutil.move(str(field / "structure_top.tif"), moved / "structure_top.tif")
    loaded = load_project(path)
    assert [m.name for m in loaded.missing] == ["structure_top"]
    assert set(loaded.layer_positions.values()) == {0, 1}

    found = find_files_by_name(tmp_path, {"STRUCTURE_TOP.TIF", "nope.tif"})
    assert found == {"STRUCTURE_TOP.TIF": moved / "structure_top.tif"}
    fixed = repaired_entry(loaded.missing[0].entry, moved / "structure_top.tif")
    assert "source" not in fixed and fixed["source_abs"].endswith("deep/structure_top.tif")

    from petrogis.io.project_io import LoadContext, load_layer_entry

    layer = load_layer_entry(fixed, LoadContext(field, UTM, []))
    assert layer.name == "structure_top" and layer.id == loaded.missing[0].entry["id"]


def test_unknown_layer_kind_is_reported(field):
    project = _project(field)
    path = save_project(project, field / "p.pgis")
    data = json.loads(path.read_text())
    data["layers"][0]["kind"] = "hologram"
    path.write_text(json.dumps(data))
    loaded = load_project(path)
    assert any("unknown layer kind" in w for w in loaded.warnings)
