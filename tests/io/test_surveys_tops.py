"""Survey and tops readers, the tops map layer, and project files version 2."""

import json

import numpy as np
import pytest

from petrogis.analysis.tops import surfaces, tops_points
from petrogis.core.project import Project
from petrogis.io.errors import DataReadError
from petrogis.io.project_io import (
    PROJECT_VERSION,
    load_project,
    project_to_dict,
    reload_layers,
    save_project,
)
from petrogis.io.survey_io import (
    SurveyMapping,
    build_surveys,
    depth_factor,
    load_surveys,
    read_survey_table,
)
from petrogis.io.tables import header_depth_unit, normalize_header
from petrogis.io.tops_io import TopsMapping, build_tops, load_tops, read_tops_table
from petrogis.io.vector_io import load_vector_layer, write_geopackage
from petrogis.io.wells_io import load_well_layer

UTM = "EPSG:32639"


def test_header_helpers():
    assert normalize_header("MD (ft)") == "md"
    assert header_depth_unit("MD (ft)") == "ft"
    assert header_depth_unit("Depth [m]") == "m"
    assert header_depth_unit("Глубина (м)") == "m"
    assert header_depth_unit("MD") is None and header_depth_unit("Inc (deg)") is None
    assert depth_factor("ft") == 0.3048
    with pytest.raises(DataReadError):
        depth_factor("yards")


def test_survey_multi_well_feet_and_decimal_commas(tmp_path):
    path = tmp_path / "surveys.csv"
    path.write_text(
        "Скважина;Глубина (ft);Зенитный угол;Азимут\n"
        "AB-01;0;0;0\nAB-01;1000;10;45\nAB-01;2000;20,5;50\n"
        "AB 02;0;0;0\nAB 02;500;5;90\n;600;1;1\n"
    )
    table = read_survey_table(path)
    assert table.mapping == SurveyMapping("Скважина", "Глубина (ft)", "Зенитный угол", "Азимут")
    assert table.depth_unit == "ft"
    result = build_surveys(table, table.mapping)
    assert set(result.trajectories) == {"AB1", "AB2"}
    assert result.names["AB2"] == "AB 02"
    assert result.trajectories["AB1"].total_md == pytest.approx(2000 * 0.3048)
    assert any("Skipped 1 row" in w for w in result.warnings)
    assert result.options["depth_unit"] == "ft"


def test_single_well_survey_and_bad_wells(tmp_path):
    path = tmp_path / "AB-03.csv"
    path.write_text("MD,INC,AZI\n0,0,0\n100,1,10\n")
    result = load_surveys(path)
    assert list(result.names.values()) == ["AB-03"]  # named after the file
    named = load_surveys(path, default_well="AB-04", depth_unit="ft")
    assert list(named.trajectories) == ["AB4"] and named.trajectories["AB4"].total_md == 30.48

    bad = tmp_path / "bad.csv"
    bad.write_text("Well,MD,Inc,Azi\nA,0,0,0\nA,0,1,1\nB,0,0,0\nB,100,5,5\n")
    result = load_surveys(bad)
    assert list(result.trajectories) == ["B"]
    assert any("Survey for A ignored: Duplicate" in w for w in result.warnings)


def test_survey_errors(tmp_path):
    odd = tmp_path / "odd.csv"
    odd.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(DataReadError, match="Could not detect"):
        load_surveys(odd)
    table = read_survey_table(odd)
    with pytest.raises(DataReadError, match="missing"):
        build_surveys(table, SurveyMapping(md="a"))
    with pytest.raises(DataReadError, match="not found"):
        build_surveys(table, SurveyMapping(md="a", inc="b", azi="zzz"))
    all_bad = tmp_path / "all_bad.csv"
    all_bad.write_text("MD,Inc,Azi\nx,y,z\n")
    with pytest.raises(DataReadError, match="no valid survey"):
        load_surveys(all_bad)
    with pytest.raises(DataReadError, match="not found"):
        read_survey_table(tmp_path / "missing.csv")
    (tmp_path / "x.dat").write_text("MD,Inc,Azi\n0,0,0\n")
    with pytest.raises(DataReadError, match="Unsupported survey"):
        read_survey_table(tmp_path / "x.dat")


def test_tops_long_and_wide(tmp_path):
    long = tmp_path / "tops.csv"
    long.write_text("Well,Formation,MD (ft)\nAB-01,A,1000\nAB-01,A,1100\nAB-01,B,900\n,C,1\n")
    table = read_tops_table(long)
    assert (
        table.mapping == TopsMapping("Well", "Formation", "MD (ft)") and not table.mapping.is_wide
    )
    result = build_tops(table, table.mapping)
    assert [(t.surface, round(t.md, 2)) for t in result.tops["AB1"]] == [
        ("B", 274.32),
        ("A", 304.8),
    ]
    assert result.count == 2
    assert any("picked twice" in w for w in result.warnings)
    assert any("Skipped 1 row" in w for w in result.warnings)

    wide = tmp_path / "wide.csv"
    wide.write_text(
        "Well,Top Reservoir,Base Reservoir,Comment\nAB-01,1500.5,1560,ok\nAB-02,1600,,\n"
    )
    table = read_tops_table(wide)
    assert table.mapping.is_wide and table.mapping.is_complete
    assert table.wide_surfaces(table.mapping) == ["Top Reservoir", "Base Reservoir"]
    result = build_tops(table, table.mapping)
    assert [t.surface for t in result.tops["AB1"]] == ["Top Reservoir", "Base Reservoir"]
    assert [t.surface for t in result.tops["AB2"]] == ["Top Reservoir"]
    assert result.warnings == []


def test_tops_errors(tmp_path):
    odd = tmp_path / "odd.csv"
    odd.write_text("a,b\n1,x\n")
    with pytest.raises(DataReadError, match="Could not detect"):
        load_tops(odd)
    table = read_tops_table(odd)
    with pytest.raises(DataReadError, match="missing"):
        build_tops(table, TopsMapping(surface="a"))
    with pytest.raises(DataReadError, match="not found"):
        build_tops(table, TopsMapping(surface="a", md="nope"))
    text_only = tmp_path / "text.csv"
    text_only.write_text("Well,Note\nA,x\n")
    with pytest.raises(DataReadError, match="no surface depth"):
        load_tops(text_only)
    empty = tmp_path / "empty_tops.csv"
    empty.write_text("Well,Surface,MD\nA,,\n")
    with pytest.raises(DataReadError, match="no valid formation tops"):
        load_tops(empty)
    single = tmp_path / "AB-05.csv"
    single.write_text("Surface,MD\nTop,1500\n")
    assert list(load_tops(single).names.values()) == ["AB-05"]


def _demo_wells(sample_field, project):
    wells = load_well_layer(sample_field.wells_csv, project.crs).layer
    project.add_layer(wells)
    surveys = load_surveys(sample_field.surveys_csv)
    project.attach_trajectories(
        wells.id,
        surveys.trajectories,
        {"path": str(sample_field.surveys_csv), "import_options": surveys.options},
    )
    tops = load_tops(sample_field.tops_csv)
    project.attach_tops(
        wells.id, tops.tops, {"path": str(sample_field.tops_csv), "import_options": tops.options}
    )
    return wells


def test_sample_tops_points_match_structure(sample_field):
    from petrogis import sample_data as sd

    project = Project()
    wells = _demo_wells(sample_field, project)
    assert surfaces(wells) == ["Shale Marker", "Top Reservoir", "Base Reservoir"]
    points = tops_points(wells, "Top Reservoir", project.crs)
    assert len(points) == sd.N_WELLS
    expected = {w.name: w.top_tvdss for w in sample_field.wells}
    for well, tvdss, x, y in zip(points.well, points.tvdss, points.x, points.y, strict=True):
        assert tvdss == pytest.approx(expected[well], abs=0.5)  # through the trajectory
        assert sd.structure_depth(np.array([x]), np.array([y]))[0] == pytest.approx(tvdss, abs=1.0)
    deviated = points.set_index("well").loc["AB-06"]
    assert deviated.md - deviated.tvd > 100  # long measured depth on a 50° well
    assert len(tops_points(wells, "Nope", project.crs)) == 0


def test_write_geopackage_and_reload(tmp_path, sample_field):
    project = Project()
    wells = _demo_wells(sample_field, project)
    points = tops_points(wells, "Top Reservoir", project.crs)
    path = write_geopackage(points, tmp_path / "tops.gpkg", "tops_Top_Reservoir")
    layer = load_vector_layer(path, project.crs)
    assert len(layer.gdf) == len(points) and "tvdss" in layer.gdf.columns
    with pytest.raises(DataReadError, match="Cannot write"):
        write_geopackage(points, tmp_path / "missing_dir" / "x.gpkg", "x")


def test_project_v2_round_trip(tmp_path, sample_field):
    project = Project()
    wells = _demo_wells(sample_field, project)
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    assert data["version"] == PROJECT_VERSION >= 2
    entry = data["layers"][0]
    assert entry["surveys"][0]["path"].endswith("surveys.csv")
    assert entry["tops"][0]["import_options"]["mapping"]["surface"] == "Surface"
    loaded = load_project(path)
    assert loaded.warnings == []
    again = loaded.project.layers[0]
    assert set(again.trajectories) == set(wells.trajectories)
    assert sum(len(t) for t in again.tops.values()) == 30
    assert project_to_dict(loaded.project, path) == project_to_dict(project, path)

    reloaded, warnings = reload_layers(project.layers, "EPSG:28409", {}, tmp_path)
    assert warnings == [] and set(reloaded[0].trajectories) == {"AB4", "AB6"}


def test_project_v1_still_loads(tmp_path, sample_field):
    project = Project()
    project.add_layer(load_well_layer(sample_field.wells_csv, project.crs).layer)
    path = save_project(project, tmp_path / "old.pgis")
    data = json.loads(path.read_text())
    data["version"] = 1
    for entry in data["layers"]:
        entry.pop("surveys")
        entry.pop("tops")
    path.write_text(json.dumps(data))
    loaded = load_project(path)
    assert loaded.warnings == [] and loaded.project.layers[0].trajectories == {}


def test_broken_survey_and_tops_entries_become_warnings(tmp_path, sample_field):
    project = Project()
    _demo_wells(sample_field, project)
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    entry = data["layers"][0]
    entry["surveys"].append({"path": "gone.csv"})
    entry["surveys"].append("not an object")
    entry["tops"][0]["import_options"]["depth_unit"] = "yards"
    entry["tops"].append({"path": "x", "import_options": {"mapping": {"well": 5}}})
    path.write_text(json.dumps(data))
    loaded = load_project(path)
    layer = loaded.project.layers[0]
    assert len(layer.trajectories) == 2  # the good survey still loads
    assert layer.tops == {}
    text = "\n".join(loaded.warnings)
    assert "Source not found: gone.csv" in text
    assert "must be an object" in text
    assert "Unknown depth unit" in text
    entry["surveys"] = "nope"
    path.write_text(json.dumps(data))
    assert any("surveys must be a list" in w for w in load_project(path).warnings)


def test_unmatched_wells_in_project_survey(tmp_path, sample_field):
    project = Project()
    wells = _demo_wells(sample_field, project)
    extra = tmp_path / "extra.csv"
    extra.write_text("Well,MD,Inc,Azi\nZZ-9,0,0,0\nZZ-9,100,5,5\n")
    options = {"mapping": {"well": "Well", "md": "MD", "inc": "Inc", "azi": "Azi"}}
    project.attach_trajectories(wells.id, {}, {"path": str(extra), "import_options": options})
    path = save_project(project, tmp_path / "p.pgis")
    loaded = load_project(path)
    assert any("no matching well for ZZ-9" in w for w in loaded.warnings)
