"""Interactive picks and the correlation section in project files (format version 3)."""

import json

from petrogis.core.correlation import BUILTIN_TEMPLATES, CorrelationSection
from petrogis.core.project import Project
from petrogis.core.tops import PICKED
from petrogis.io.project_io import load_project, project_to_dict, save_project
from petrogis.io.tops_io import load_tops
from petrogis.io.wells_io import load_well_layer


def _project(sample_field):
    project = Project()
    wells = load_well_layer(sample_field.wells_csv, project.crs).layer
    project.add_layer(wells)
    tops = load_tops(sample_field.tops_csv)
    project.attach_tops(
        wells.id, tops.tops, {"path": str(sample_field.tops_csv), "import_options": tops.options}
    )
    return project, wells


def test_picks_and_section_round_trip(tmp_path, sample_field):
    project, wells = _project(sample_field)
    project.set_pick(wells.id, "AB-01", "Top Reservoir", 1560.0)  # moves a file pick
    project.set_pick(wells.id, "AB-02", "OWC", 1650.0)  # a new surface
    project.delete_pick(wells.id, "AB-03", "Shale Marker")  # deletes a file pick
    section = CorrelationSection(
        wells.id, ("AB1", "AB2", "AB3"), BUILTIN_TEMPLATES[1], "TVD", "Top Reservoir"
    )
    project.set_section(section)
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    assert data["version"] == 3
    assert data["correlation"]["wells"] == ["AB1", "AB2", "AB3"]
    assert {p["surface"] for p in data["layers"][0]["picks"]} == {
        "Top Reservoir",
        "OWC",
        "Shale Marker",
    }

    loaded = load_project(path)
    assert loaded.warnings == []
    again = loaded.project.layers[0]
    tops_ab1 = {t.surface: t for t in again.tops["AB1"]}
    assert tops_ab1["Top Reservoir"].md == 1560.0 and tops_ab1["Top Reservoir"].source == PICKED
    assert {t.surface for t in again.tops["AB2"]} >= {"OWC"}
    assert "Shale Marker" not in {t.surface for t in again.tops["AB3"]}
    assert loaded.project.section == section
    assert project_to_dict(loaded.project, path) == project_to_dict(project, path)


def test_bad_picks_and_sections_become_warnings(tmp_path, sample_field):
    project, wells = _project(sample_field)
    project.set_section(CorrelationSection(wells.id, ("AB1", "AB2")))
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    data["layers"][0]["picks"] = [
        {"well": "AB-01", "surface": "Top Reservoir", "md": 1555.5},
        {"well": "ZZ-9", "surface": "Top", "md": 10},
        {"well": "AB-02", "surface": "Top", "md": "deep"},
        {"well": "AB-02", "surface": "", "md": 1},
        {"well": "AB-02", "surface": "Top", "md": True},
        {"well": "AB-02", "surface": "Top", "md": -5},
        "nope",
        {"well": 7, "surface": "Top", "md": 1},
    ]
    data["correlation"]["wells"] = ["AB1", "GONE"]
    path.write_text(json.dumps(data))
    loaded = load_project(path)
    layer = loaded.project.layers[0]
    assert {t.surface: t.md for t in layer.tops["AB1"]}["Top Reservoir"] == 1555.5
    assert any("skipped 7 invalid or unmatched pick(s)" in w for w in loaded.warnings)
    assert loaded.project.section.well_keys == ("AB1",)
    assert any("1 well(s) no longer" in w for w in loaded.warnings)

    for correlation, message in (
        ("x", "must be an object"),
        ({"layer_id": "missing", "wells": []}, "did not load"),
        ({"layer_id": wells.id, "wells": [], "datum": "TWT"}, "Unknown datum"),
    ):
        data["correlation"] = correlation
        data["layers"][0]["picks"] = "nope"
        path.write_text(json.dumps(data))
        loaded = load_project(path)
        assert loaded.project.section is None
        assert any(message in w for w in loaded.warnings), loaded.warnings
        assert any("picks must be a list" in w for w in loaded.warnings)


def test_section_on_non_well_layer_and_pick_cap(tmp_path, sample_field, monkeypatch):
    from petrogis.io import project_io
    from petrogis.io.vector_io import load_vector_layer

    project, _ = _project(sample_field)
    boundary = load_vector_layer(sample_field.field_boundary, project.crs)
    project.add_layer(boundary)
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    data["correlation"] = {"layer_id": boundary.id, "wells": []}
    well_entry = next(e for e in data["layers"] if e["kind"] == "wells")
    well_entry["picks"] = [{"well": "AB-01", "surface": f"S{i}", "md": 100 + i} for i in range(5)]
    path.write_text(json.dumps(data))
    monkeypatch.setattr(project_io, "MAX_PICKS", 3)
    loaded = load_project(path)
    assert loaded.project.section is None
    assert any("not a well layer" in w for w in loaded.warnings)
    assert any("only the first 3 picks" in w for w in loaded.warnings)
