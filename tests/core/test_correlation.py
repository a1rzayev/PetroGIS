"""Correlation model: templates, sections, depth datums, flattening and interactive picks."""

import geopandas as gpd
import numpy as np
import pytest
from shapely import Point

from petrogis.core.correlation import (
    BUILTIN_TEMPLATES,
    DEFAULT_TEMPLATE,
    MAX_SECTION_WELLS,
    MAX_TRACKS,
    CorrelationSection,
    CurveSpec,
    TrackSpec,
    TrackTemplate,
    md_at,
    normalize_curve,
    section_depth,
    well_datum,
)
from petrogis.core.layers import WellLayer
from petrogis.core.mnemonics import CurveFamily
from petrogis.core.project import EventKind, Project
from petrogis.core.tops import PICKED, Top
from petrogis.core.trajectory import Trajectory


def test_builtin_templates_round_trip():
    for template in BUILTIN_TEMPLATES:
        assert TrackTemplate.from_dict(template.to_dict()) == template
    section = CorrelationSection("layer", ("A", "B"), DEFAULT_TEMPLATE, "MD", "Top")
    assert CorrelationSection.from_dict(section.to_dict()) == section
    bare = CorrelationSection.from_dict({"layer_id": "x", "wells": ["A"]})
    assert bare.template == DEFAULT_TEMPLATE and bare.datum == "TVDSS"


GR = CurveSpec(CurveFamily.GR, 0, 150)


@pytest.mark.parametrize(
    "build",
    [
        lambda: CurveSpec("GR", 0, 1),  # type: ignore[arg-type]
        lambda: CurveSpec(CurveFamily.GR, 1, 1),
        lambda: CurveSpec(CurveFamily.GR, 0, float("nan")),
        lambda: CurveSpec(CurveFamily.GR, True, 1),  # type: ignore[arg-type]
        lambda: CurveSpec(CurveFamily.GR, 0, 1, "green"),
        lambda: CurveSpec(CurveFamily.GR, 0, 1, dashed="yes"),  # type: ignore[arg-type]
        lambda: TrackSpec("", (GR,)),
        lambda: TrackSpec("T", ()),
        lambda: TrackSpec("T", (GR,) * 5),
        lambda: TrackSpec("T", ("GR",)),  # type: ignore[arg-type]
        lambda: TrackSpec("T", (GR,), log="no"),  # type: ignore[arg-type]
        lambda: TrackSpec("T", (GR,), log=True),  # scale starts at 0
        lambda: TrackSpec("T", (GR,), shading="rainbow"),
        lambda: TrackSpec("T", (GR,), shading="crossover"),
        lambda: TrackSpec("T", (GR,), cutoff=float("inf")),
        lambda: TrackTemplate("T", ()),
        lambda: TrackTemplate("T", (TrackSpec("t", (GR,)),) * (MAX_TRACKS + 1)),
        lambda: TrackTemplate("T", ("x",)),  # type: ignore[arg-type]
        lambda: TrackTemplate("x" * 300, (TrackSpec("t", (GR,)),)),
        lambda: CorrelationSection("", ("A",)),
        lambda: CorrelationSection("L", ("A", "A")),
        lambda: CorrelationSection("L", ("",)),
        lambda: CorrelationSection("L", tuple(f"W{i}" for i in range(MAX_SECTION_WELLS + 1))),
        lambda: CorrelationSection("L", ("A",), "tpl"),  # type: ignore[arg-type]
        lambda: CorrelationSection("L", ("A",), datum="TWT"),
        lambda: CorrelationSection("L", ("A",), flatten_on=""),
    ],
)
def test_invalid_models_rejected(build):
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize(
    "data",
    [
        {"layer_id": "x", "wells": "A"},
        {"layer_id": "x", "wells": [], "template": "Standard"},
        {"layer_id": "x", "wells": [], "template": {"name": "T", "tracks": "x"}},
        {"layer_id": "x", "wells": [], "template": {"name": "T", "tracks": [{}] * 9}},
        {"layer_id": "x", "wells": [], "template": {"name": "T", "tracks": [{"name": "t"}]}},
        {"layer_id": "x", "wells": [], "template": {"name": "T", "tracks": [
            {"name": "t", "curves": [{"family": "NOPE", "left": 0, "right": 1}]}]}},
        {"layer_id": "x", "wells": [], "template": {"name": "T", "tracks": [
            {"name": "t", "curves": [{"family": "GR", "left": 0, "right": 1}] * 5}]}},
    ],
)  # fmt: skip
def test_invalid_section_dicts(data):
    with pytest.raises(ValueError):
        CorrelationSection.from_dict(data)


def test_normalize_curve():
    np.testing.assert_allclose(
        normalize_curve(np.array([0, 75, 150, 300]), GR, False), [0, 0.5, 1, 2]
    )
    nphi = CurveSpec(CurveFamily.NPHI, 0.45, -0.15)
    np.testing.assert_allclose(
        normalize_curve(np.array([0.45, 0.15, -0.15]), nphi, False), [0, 0.5, 1]
    )
    res = CurveSpec(CurveFamily.RES_DEEP, 0.2, 2000)
    out = normalize_curve(np.array([0.2, 20, 2000, 0, -1]), res, True)
    np.testing.assert_allclose(out[:3], [0, 0.5, 1])
    assert np.isnan(out[3:]).all()


def _layer():
    gdf = gpd.GeoDataFrame(
        {"name": ["AB-01", "AB-02", "AB-03"], "uwi": [None] * 3, "kb": [20.0, 30.0, np.nan],
         "td": [3000.0] * 3, "name_key": ["AB1", "AB2", "AB3"]},
        geometry=[Point(0, 0), Point(1000, 0), Point(2000, 0)],
    )  # fmt: skip
    layer = WellLayer(name="w", source="w.csv", gdf=gdf)
    layer.trajectories["AB2"] = Trajectory.from_survey([0, 1000, 3000], [0, 0, 60], [0, 0, 90])
    layer.tops["AB1"] = [Top("AB-01", "AB1", "Top", 1520.0)]
    layer.tops["AB2"] = [Top("AB-02", "AB2", "Top", 2000.0)]
    return layer


def test_depth_datums_vertical_and_deviated():
    layer = _layer()
    vertical = well_datum(layer, "AB-01", "TVDSS", None)
    assert vertical.name == "AB-01" and vertical.kb == 20.0 and not vertical.warnings
    np.testing.assert_allclose(section_depth(layer, vertical, np.array([1520.0]), "TVDSS"), [1500])
    np.testing.assert_allclose(section_depth(layer, vertical, np.array([1520.0]), "MD"), [1520])
    deviated = well_datum(layer, "AB2", "TVD", None)
    tvd = section_depth(layer, deviated, np.array([2000.0]), "TVD")[0]
    assert tvd == pytest.approx(layer.trajectories["AB2"].md_to_tvd([2000.0])[0]) and tvd < 2000
    # md_at inverts section_depth for every datum.
    for datum in ("TVDSS", "TVD", "MD"):
        info = well_datum(layer, "AB2", datum, None)
        depth = section_depth(layer, info, np.array([2200.0]), datum)[0]
        assert md_at(layer, info, depth, datum) == pytest.approx(2200.0, abs=0.5)
    assert md_at(layer, vertical, -100.0, "MD") is None
    assert md_at(layer, vertical, -100.0, "TVDSS") is None  # above the KB
    assert md_at(layer, deviated, -10.0, "TVD") is None
    assert well_datum(layer, "ZZ-9", "MD", None) is None


def test_flattening_and_missing_kb_or_pick():
    layer = _layer()
    a = well_datum(layer, "AB1", "TVDSS", "Top")
    b = well_datum(layer, "AB2", "TVDSS", "Top")
    assert a.flattened and b.flattened
    for info, top in ((a, 1520.0), (b, 2000.0)):
        assert section_depth(layer, info, np.array([top]), "TVDSS")[0] == pytest.approx(0.0)
        assert md_at(layer, info, 0.0, "TVDSS") == pytest.approx(top, abs=0.5)
    c = well_datum(layer, "AB3", "TVDSS", "Top")
    assert not c.flattened and c.shift == 0.0
    assert c.warnings == ["no KB elevation: shown as TVD", "no Top pick: not flattened"]
    np.testing.assert_allclose(section_depth(layer, c, np.array([100.0]), "TVDSS"), [100.0])


def test_project_picks_and_overrides():
    project = Project()
    layer = _layer()
    project.add_layer(layer)
    events = []
    project.subscribe(lambda e: events.append((e.kind, e.field)))
    top = project.set_pick(layer.id, "ab-01", "Top", 1530.0)
    assert top.source == PICKED and top.name_key == "AB1"
    assert [(t.surface, t.md) for t in layer.tops["AB1"]] == [("Top", 1530.0)]
    project.set_pick(layer.id, "AB1", "Marker", 1400.0)
    assert [t.surface for t in layer.tops["AB1"]] == ["Marker", "Top"]
    assert project.delete_pick(layer.id, "AB2", "Top")
    assert "AB2" not in layer.tops and layer.pick_overrides[("AB2", "Top")] is None
    assert not project.delete_pick(layer.id, "AB2", "Top")
    assert not project.delete_pick(layer.id, "ZZ", "Top")
    assert project.delete_pick(layer.id, "AB1", "Marker")
    assert set(layer.pick_overrides) == {("AB1", "Top"), ("AB1", "Marker"), ("AB2", "Top")}
    # A fresh import of a surface wins over interactive edits of it.
    project.attach_tops(layer.id, {"AB1": [Top("AB-01", "AB1", "Top", 1525.0, "file.csv")]})
    assert ("AB1", "Top") not in layer.pick_overrides
    assert events.count((EventKind.LAYER_CHANGED, "tops")) == 5
    with pytest.raises(KeyError):
        project.set_pick(layer.id, "ZZ-9", "Top", 10.0)
    with pytest.raises(ValueError):
        project.set_pick(layer.id, "AB1", " ", 10.0)
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            project.set_pick(layer.id, "AB1", "Top", bad)


def test_section_state_and_events():
    project = Project()
    events = []
    project.subscribe(lambda e: events.append(e.kind))
    section = CorrelationSection("L", ("A",))
    project.set_section(section)
    project.set_section(section)  # unchanged: no event
    assert project.section is section and events == [EventKind.SECTION_CHANGED]
    project.clear()
    assert project.section is None
