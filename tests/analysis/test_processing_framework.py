"""Processing framework and the built-in algorithms, run on the demo field."""

import threading

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from shapely import Point, box

from petrogis import sample_data as sd
from petrogis.analysis.processing import (
    Algorithm,
    AlgorithmResult,
    BoolParam,
    Canceled,
    ChoiceParam,
    CrsParam,
    Feedback,
    LayerParam,
    NumberParam,
    OutputParam,
    ParameterError,
    RunContext,
    ValidationContext,
    algorithms,
    get_algorithm,
    register_algorithm,
)
from petrogis.core.layers import LayerKind, VectorLayer
from petrogis.io.errors import DataReadError
from petrogis.io.raster_io import load_raster_layer
from petrogis.io.survey_io import load_surveys
from petrogis.io.vector_io import load_vector_layer
from petrogis.io.wells_io import load_well_layer

UTM = "EPSG:32639"
GK9 = "EPSG:28409"


@pytest.fixture(scope="module")
def field(sample_field):
    wells = load_well_layer(sample_field.wells_csv, UTM).layer
    wells.trajectories.update(load_surveys(sample_field.surveys_csv).trajectories)
    raster = load_raster_layer(sample_field.structure_top, UTM)
    boundary = load_vector_layer(sample_field.field_boundary, UTM)
    return wells, raster, boundary


def run(algorithm_id, values, field):
    algorithm = get_algorithm(algorithm_id)
    context = ValidationContext({layer.id: layer for layer in field})
    return algorithm.execute(algorithm.validate(values, context), RunContext(UTM), Feedback())


def test_registry():
    ids = [a.id for a in algorithms()]
    assert ids == sorted(ids) or len(ids) == 6
    assert set(ids) == {
        "raster:sample_at_wells",
        "raster:clip",
        "raster:reproject",
        "raster:zonal_statistics",
        "vector:clip",
        "vector:reproject",
    }
    with pytest.raises(ParameterError, match="Unknown algorithm"):
        get_algorithm("evil:eval")
    with pytest.raises(ValueError, match="already registered"):
        register_algorithm(get_algorithm("vector:clip"))


def test_parameter_validation(field, tmp_path):
    wells, raster, boundary = field
    context = ValidationContext({layer.id: layer for layer in field})
    polygons = LayerParam("p", "Zones", kinds=(LayerKind.VECTOR,), geometry="polygon")
    assert polygons.validate(boundary.id, context) is boundary
    assert polygons.validate({"id": "gone", "name": boundary.name}, context) is boundary
    for bad in (None, "gone", raster.id, wells.id):
        with pytest.raises(ParameterError):
            polygons.validate(bad, context)
    assert LayerParam("o", "O", optional=True).validate(None, context) is None
    assert not LayerParam("p", "P", geometry="point").accepts(VectorLayer(name="e", source="e"))
    assert context.layer(42) is None

    number = NumberParam("n", "N", default=2, minimum=0, maximum=10, integer=True)
    assert number.validate(None, context) == 2 and number.validate(3.0, context) == 3
    for bad in ("3", True, 11, float("nan")):
        with pytest.raises(ParameterError):
            number.validate(bad, context)
    choice = ChoiceParam("c", "C", choices=("a", "b"), default="a")
    assert choice.validate(None, context) == "a"
    with pytest.raises(ParameterError):
        choice.validate("z", context)
    flag = BoolParam("b", "B")
    assert flag.validate(None, context) is False
    with pytest.raises(ParameterError):
        flag.validate("yes", context)
    crs = CrsParam("crs", "CRS")
    assert crs.validate("epsg:28409", context) == GK9
    for bad in ("", "EPSG:999999"):
        with pytest.raises(ParameterError):
            crs.validate(bad, context)
    output = OutputParam("o", "Out", extension=".gpkg")
    assert output.validate(str(tmp_path / "x.shp"), context) == tmp_path / "x.gpkg"
    assert output.to_json(tmp_path / "x.gpkg") == str(tmp_path / "x.gpkg")
    for bad in ("", "  ", str(tmp_path / "missing" / "x.gpkg")):
        with pytest.raises(ParameterError):
            output.validate(bad, context)
    algorithm = get_algorithm("vector:clip")
    with pytest.raises(ParameterError, match="different layer"):
        algorithm.validate(
            {"input": boundary.id, "mask": boundary.id, "output": str(tmp_path / "o")}, context
        )
    json_values = algorithm.to_json(
        algorithm.validate(
            {"input": wells.id, "mask": boundary.id, "output": str(tmp_path / "o")}, context
        )
    )
    assert json_values["input"] == {"id": wells.id, "name": wells.name}


def test_feedback_is_thread_safe_and_cancels():
    seen = []
    feedback = Feedback(on_progress=seen.append)
    feedback.progress(1.5)
    feedback.progress(-1)
    assert seen == [1.0, 0.0]
    threads = [threading.Thread(target=feedback.log, args=(f"m{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(feedback.messages) == 20
    feedback.cancel()
    assert feedback.canceled
    with pytest.raises(Canceled):
        feedback.check()
    with pytest.raises(Canceled):
        feedback.progress(0.5)
    assert RunContext(UTM).transformation(None) is None


class _Writer(Algorithm):
    id = "test:writer"
    name = "Writer"

    @property
    def parameters(self):
        return (OutputParam("output", "Out", extension=".txt"),)

    def run(self, values, context, feedback):
        values["output"].write_text("partial")
        feedback.check()
        return AlgorithmResult({"output": values["output"]})


def test_execute_removes_partial_output_on_cancel(tmp_path):
    algorithm = _Writer()
    target = tmp_path / "out.txt"
    feedback = Feedback()
    feedback.cancel()
    with pytest.raises(Canceled):
        algorithm.execute({"output": target}, RunContext(UTM), feedback)
    assert not target.exists()
    existing = tmp_path / "keep.txt"
    existing.write_text("old")
    with pytest.raises(Canceled):
        algorithm.execute({"output": existing}, RunContext(UTM), feedback)
    assert existing.exists()  # a file that was there before is never deleted
    assert algorithm.execute({"output": target}, RunContext(UTM), Feedback()).outputs


def test_sample_raster_at_wells(field, tmp_path):
    wells, raster, _ = field
    for location in ("Wellhead", "Bottom hole"):
        out = tmp_path / f"{location}.gpkg"
        result = run(
            "raster:sample_at_wells",
            {"wells": wells.id, "raster": raster.id, "location": location, "output": str(out)},
            field,
        )
        assert result.summary == "Sampled 10 of 10 wells"
        points = gpd.read_file(out)
        expected = sd.structure_depth(points["x"].to_numpy(), points["y"].to_numpy())
        # Nearest 25 m cell: within a few metres, up to ~6 m on the steep flank (AB-10).
        np.testing.assert_allclose(points["value"], expected, atol=8.0)
        assert "name_key" not in points.columns
    head = gpd.read_file(tmp_path / "Wellhead.gpkg").set_index("name")
    bottom = gpd.read_file(tmp_path / "Bottom hole.gpkg").set_index("name")
    assert bottom.loc["AB-06", "x"] != head.loc["AB-06", "x"]
    assert bottom.loc["AB-01", "x"] == head.loc["AB-01", "x"]


def test_clip_vector(field, tmp_path):
    wells, _, boundary = field
    out = tmp_path / "inside.gpkg"
    result = run("vector:clip", {"input": wells.id, "mask": boundary.id, "output": str(out)}, field)
    assert result.summary == "Kept 7 of 10 features"
    assert len(gpd.read_file(out)) == 7
    far = VectorLayer(
        name="far", source="far",
        gdf=gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs=UTM),
    )  # fmt: skip
    empty = tmp_path / "none.gpkg"
    result = run(
        "vector:clip", {"input": wells.id, "mask": far.id, "output": str(empty)}, (*field, far)
    )
    assert result.summary == "Kept 0 of 10 features"


def test_clip_and_reproject_raster(field, tmp_path):
    _, raster, boundary = field
    clipped = tmp_path / "clip.tif"
    run("raster:clip", {"raster": raster.id, "mask": boundary.id, "output": str(clipped)}, field)
    with rasterio.open(clipped) as src:
        data = src.read(1, masked=True)
        minx, _, maxx, _ = boundary.gdf.total_bounds
        assert src.bounds.left >= minx - 25 and src.bounds.right <= maxx + 25
        assert data.mask.any() and data.min() == pytest.approx(sd.CREST_TVDSS, abs=10)
        assert data.max() <= sd.SPILL_TVDSS + 15
    projected = tmp_path / "gk.tif"
    result = run(
        "raster:reproject",
        {"raster": raster.id, "crs": GK9, "resampling": "nearest", "output": str(projected)},
        field,
    )
    assert "EPSG:28409" in result.summary
    with rasterio.open(projected) as src:
        assert src.crs.to_epsg() == 28409
        assert 9_390_000 < src.bounds.left < 9_420_000  # GK zone 9 eastings carry the zone prefix


def test_reproject_vector(field, tmp_path):
    _, _, boundary = field
    out = tmp_path / "gk.gpkg"
    run("vector:reproject", {"input": boundary.id, "crs": GK9, "output": str(out)}, field)
    written = gpd.read_file(out)
    assert written.crs.to_epsg() == 28409
    assert 9_390_000 < written.total_bounds[0] < 9_420_000
    back = load_vector_layer(out, UTM)
    np.testing.assert_allclose(back.gdf.total_bounds, boundary.gdf.total_bounds, atol=1.0)


def test_zonal_statistics(field, tmp_path):
    _, raster, boundary = field
    zones = VectorLayer(
        name="zones", source="z",
        gdf=gpd.GeoDataFrame(
            {"zone": ["field", "outside", "empty"]},
            geometry=[boundary.gdf.geometry.iloc[0], box(0, 0, 10, 10), Point(0, 0).buffer(0)],
            crs=UTM,
        ),
    )  # fmt: skip
    out = tmp_path / "zs.gpkg"
    result = run("raster:zonal_statistics", {"polygons": zones.id, "raster": raster.id,
                                             "output": str(out)}, (*field, zones))  # fmt: skip
    assert "2 cover no raster cells" in result.summary
    stats = gpd.read_file(out).set_index("zone")
    inside = stats.loc["field"]
    area_cells = boundary.gdf.geometry.iloc[0].area / sd.RASTER_PIXEL_M**2
    assert inside["zs_count"] == pytest.approx(area_cells, rel=0.01)
    assert inside["zs_min"] == pytest.approx(sd.CREST_TVDSS, abs=10)
    assert sd.CREST_TVDSS < inside["zs_mean"] < sd.SPILL_TVDSS
    assert inside["zs_sum"] == pytest.approx(inside["zs_mean"] * inside["zs_count"])
    assert stats.loc["outside", "zs_count"] == 0 and np.isnan(stats.loc["outside", "zs_mean"])


@pytest.mark.parametrize(
    ("algorithm_id", "extra"),
    [
        ("raster:reproject", {"crs": GK9, "resampling": "bilinear"}),
        ("raster:zonal_statistics", {}),
        ("raster:clip", {}),
        ("vector:clip", {}),
    ],
)
def test_cancel_stops_runs_and_removes_output(field, tmp_path, algorithm_id, extra):
    wells, raster, boundary = field
    algorithm = get_algorithm(algorithm_id)
    names = {p.name for p in algorithm.parameters}
    values = {"output": str(tmp_path / "out")}
    candidates = {"raster": raster.id, "polygons": boundary.id, "mask": boundary.id,
                  "input": wells.id}  # fmt: skip
    values.update({k: v for k, v in candidates.items() if k in names})
    values.update(extra)
    context = ValidationContext({layer.id: layer for layer in field})
    checked = algorithm.validate(values, context)
    calls = []

    def cancel_on_first_progress(fraction):
        calls.append(fraction)
        feedback.cancel()

    feedback = Feedback(on_progress=cancel_on_first_progress)
    with pytest.raises(Canceled):
        algorithm.execute(checked, RunContext(UTM), feedback)
    assert calls and not checked["output"].exists()


def test_raster_errors_are_data_read_errors(field, tmp_path):
    from petrogis.io.raster_ops import (
        clip_raster,
        reproject_raster,
        sample_points,
        zonal_statistics,
    )

    _, raster, _ = field
    with pytest.raises(DataReadError, match="no band 5"):
        sample_points(raster.source, UTM, [0], [0], band=5)
    with pytest.raises(DataReadError, match="no band 5"):
        zonal_statistics(raster.source, [box(0, 0, 1, 1)], UTM, band=5)
    with pytest.raises(DataReadError, match="do not overlap"):
        clip_raster(raster.source, [box(0, 0, 10, 10)], UTM, tmp_path / "x.tif")
    with pytest.raises(DataReadError, match="no polygons"):
        clip_raster(raster.source, [None], UTM, tmp_path / "x.tif")
    with pytest.raises(DataReadError, match="Unknown resampling"):
        reproject_raster(raster.source, GK9, tmp_path / "x.tif", resampling="magic")
    with pytest.raises(DataReadError, match="Cannot write"):
        reproject_raster(raster.source, GK9, tmp_path / "missing" / "x.tif")
    cx, cy = sd.CENTER_X, sd.CENTER_Y
    values = sample_points(raster.source, UTM, [cx, 0.0, np.nan], [cy, 0.0, 1.0])
    assert values[0] == pytest.approx(sd.CREST_TVDSS, abs=10) and np.isnan(values[1:]).all()
    corner = sample_points(raster.source, UTM, [cx + 6900], [cy + 4900])  # nodata corner
    assert np.isnan(corner[0])
    lon, lat = 49.94, 40.42
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(lon, lat)
    in_wgs = sample_points(raster.source, "EPSG:4326", [lon], [lat])
    in_utm = sample_points(raster.source, UTM, [x], [y])
    np.testing.assert_allclose(in_wgs, in_utm, equal_nan=True)


def test_raster_ops_honour_explicit_transformations(field, tmp_path):
    """A raster in UTM used in a Pulkovo GK9 project through an explicit datum pipeline."""
    from petrogis.core.crs import lonlat_area, transformation_options
    from petrogis.io.raster_ops import clip_raster, sample_points, zonal_statistics

    _, raster, boundary = field
    area = lonlat_area(UTM, tuple(raster.info.extent.as_tuple()))
    pipeline = next(o.pipeline for o in transformation_options(UTM, GK9, area) if o.available)
    gk_boundary = boundary.gdf.to_crs(GK9).geometry.values
    gx, gy = gpd.GeoSeries([Point(sd.CENTER_X, sd.CENTER_Y)], crs=UTM).to_crs(GK9).iloc[0].coords[0]
    explicit = sample_points(raster.source, GK9, [gx], [gy], transformation=pipeline)
    default = sample_points(raster.source, GK9, [gx], [gy])
    assert explicit[0] == pytest.approx(default[0], abs=5)
    stats = zonal_statistics(raster.source, gk_boundary, GK9, transformation=pipeline)
    assert stats["count"][0] > 30_000
    out = clip_raster(raster.source, gk_boundary, GK9, tmp_path / "c.tif", transformation=pipeline)
    with rasterio.open(out) as src:
        assert src.crs.to_epsg() == 32639  # the output keeps the raster's own CRS
