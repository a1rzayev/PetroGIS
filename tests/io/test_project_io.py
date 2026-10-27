import json
import shutil

import pytest

from petrogis.core.extent import Extent
from petrogis.core.logs import match_logs
from petrogis.core.project import Project
from petrogis.core.styles import RasterStyle, VectorStyle, WellStyle
from petrogis.io.errors import DataReadError
from petrogis.io.las_io import read_las_files
from petrogis.io.project_io import (
    PROJECT_VERSION,
    load_project,
    project_to_dict,
    read_project_file,
    save_project,
)
from petrogis.io.raster_io import load_raster_layer
from petrogis.io.vector_io import load_vector_layer
from petrogis.io.wells_io import ColumnMapping, load_well_layer, read_well_table

UTM = "EPSG:32639"


def build_project(field_dir) -> Project:
    project = Project()
    raster = load_raster_layer(field_dir / "structure_top.tif", UTM)
    raster.style = RasterStyle(colormap="turbo", vmin=1450, vmax=1800, invert=True, opacity=0.8)
    boundary = load_vector_layer(field_dir / "field_boundary.geojson", UTM, name="Boundary")
    boundary.style = VectorStyle(color="#ff00ff", line_width=3.0)
    boundary.visible = False
    wells = load_well_layer(field_dir / "wells.xlsx", UTM).layer
    wells.style = WellStyle(symbol_size=12.0, show_labels=False)
    las = sorted((field_dir / "las").glob("*.las"))
    project.add_layer(raster)
    project.add_layer(boundary)
    project.add_layer(wells)
    project.attach_logs(wells.id, match_logs(read_las_files(las).logs, wells).matched)
    return project


@pytest.fixture
def field_copy(sample_dir, tmp_path):
    """A private copy of the sample data, so tests can move or delete files."""
    target = tmp_path / "field"
    shutil.copytree(sample_dir, target)
    return target


def test_round_trip_is_identical(field_copy):
    project = build_project(field_copy)
    path = field_copy / "demo.pgis"
    view = Extent(400_000, 4_470_000, 410_000, 4_480_000)
    saved = save_project(project, path, view=view)
    assert saved == path and project.path == str(path) and not project.dirty

    loaded = load_project(path)
    assert loaded.warnings == []
    assert loaded.view == view
    assert project_to_dict(loaded.project, path, view) == project_to_dict(project, path, view)

    original = {layer.id: layer for layer in project.layers}
    for layer in loaded.project.layers:
        assert layer.extent == pytest.approx(original[layer.id].extent)
    wells = loaded.project.layers[0]
    assert sum(len(v) for v in wells.logs.values()) == 10
    assert not loaded.project.dirty


def test_paths_are_relative_and_project_folder_is_movable(field_copy, tmp_path):
    project = build_project(field_copy)
    path = field_copy / "demo.pgis"
    save_project(project, path)
    data = json.loads(path.read_text())
    assert data["version"] == PROJECT_VERSION
    assert data["crs"] == UTM
    sources = [layer["source"] for layer in data["layers"]]
    assert sources == ["wells.xlsx", "field_boundary.geojson", "structure_top.tif"]
    assert data["layers"][0]["logs"][0]["path"].startswith("las/")
    assert data["layers"][0]["import_options"]["mapping"]["x"] == "X"

    moved = tmp_path / "moved_field"
    field_copy.rename(moved)
    loaded = load_project(moved / "demo.pgis")
    assert loaded.warnings == []
    assert [layer.name for layer in loaded.project.layers] == ["wells", "Boundary", "structure_top"]


def test_missing_sources_become_warnings(field_copy):
    project = build_project(field_copy)
    path = field_copy / "demo.pgis"
    save_project(project, path)
    (field_copy / "structure_top.tif").unlink()
    (field_copy / "las" / "AB_02.las").unlink()
    loaded = load_project(path)
    assert [layer.name for layer in loaded.project.layers] == ["wells", "Boundary"]
    assert any("structure_top" in w and "skipped" in w for w in loaded.warnings)
    assert any("AB_02.las" in w for w in loaded.warnings)
    assert sum(len(v) for v in loaded.project.layers[0].logs.values()) == 9


def test_manual_column_mapping_is_restored(tmp_path):
    path = tmp_path / "kuyular.csv"
    path.write_text("Kuyu,Sharq,Shimal\nAB-1,405000,4475000\n")
    table = read_well_table(path)
    from petrogis.io.wells_io import build_well_layer

    layer = build_well_layer(
        table, ColumnMapping(name="Kuyu", x="Sharq", y="Shimal"), UTM, UTM
    ).layer
    project = Project()
    project.add_layer(layer)
    save_project(project, tmp_path / "p.pgis")
    loaded = load_project(tmp_path / "p.pgis")
    assert loaded.project.layers[0].gdf["name"].tolist() == ["AB-1"]


def test_suffix_added_and_bad_files_rejected(tmp_path):
    saved = save_project(Project(), tmp_path / "noext")
    assert saved.name == "noext.pgis"
    with pytest.raises(DataReadError, match="not found"):
        read_project_file(tmp_path / "nope.pgis")
    bad = tmp_path / "bad.pgis"
    bad.write_text("{not json")
    with pytest.raises(DataReadError, match="Cannot read"):
        read_project_file(bad)
    other = tmp_path / "other.pgis"
    other.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(DataReadError, match="not a PetroGIS project"):
        read_project_file(other)
    future = tmp_path / "future.pgis"
    future.write_text(json.dumps({"format": "petrogis-project", "version": 99, "layers": []}))
    with pytest.raises(DataReadError, match="newer PetroGIS"):
        read_project_file(future)


def test_save_failure_leaves_no_temp_file(tmp_path):
    target = tmp_path / "missing_dir" / "p.pgis"
    with pytest.raises(DataReadError, match="Cannot save"):
        save_project(Project(), target)
    assert not list(tmp_path.glob("**/*.tmp"))
