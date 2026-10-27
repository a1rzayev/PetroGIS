import time
from pathlib import Path

import numpy as np
import pytest

from petrogis.core.logs import match_logs
from petrogis.core.mnemonics import CurveFamily
from petrogis.core.project import EventKind, Project
from petrogis.io.errors import DataReadError
from petrogis.io.las_io import read_las, read_las_files
from petrogis.io.wells_io import load_well_layer

UTM = "EPSG:32639"


def test_read_sample_las(sample_field):
    log = read_las(sample_field.las_files[1])
    assert log.well_name == "AB 02"
    assert log.name_key == "AB2"
    assert log.uwi == "AZ-ABS-0002"
    assert log.depth_unit == "M"
    assert np.all(np.diff(log.depth) > 0)
    assert set(log.curves) == {"GR_EDTC", "ILD", "TNPH", "RHOZ"}
    assert log.families == {
        CurveFamily.GR: "GR_EDTC",
        CurveFamily.RES_DEEP: "ILD",
        CurveFamily.NPHI: "TNPH",
        CurveFamily.RHOB: "RHOZ",
    }
    gr = log.curve_for(CurveFamily.GR)
    assert gr.unit == "GAPI" and gr.values.shape == log.depth.shape
    assert log.curve_for(CurveFamily.DT) is None
    top, bottom = log.depth_range
    assert top < bottom


def test_null_values_become_nan(sample_field):
    log = read_las(sample_field.las_files[2])
    assert np.isnan(log.curve_for(CurveFamily.RHOB).values).sum() > 0


def test_batch_collects_failures(sample_field, tmp_path):
    bad = tmp_path / "broken.las"
    bad.write_text("this is not a LAS file\n")
    batch = read_las_files([*sample_field.las_files, bad, tmp_path / "missing.las"])
    assert len(batch.logs) == 11
    assert [name for name, _ in batch.failures] == ["broken.las", "missing.las"]
    txt = tmp_path / "x.txt"
    txt.write_text("~V\n")
    with pytest.raises(DataReadError, match="Not a LAS"):
        read_las(txt)


def test_match_sample_logs_to_wells(sample_field):
    layer = load_well_layer(sample_field.wells_csv, UTM).layer
    batch = read_las_files(sample_field.las_files)
    match = match_logs(batch.logs, layer)
    assert sorted(match.matched) == sorted(f"AB{i}" for i in range(1, 11))
    assert match.matched_count == 10
    assert [log.well_name for log in match.unmatched] == ["OFFSET-1"]


def test_match_falls_back_to_uwi_and_file_name(sample_field, tmp_path):
    layer = load_well_layer(sample_field.wells_csv, UTM).layer
    log = read_las(sample_field.las_files[0])
    log.well_name = "Something Else"  # UWI AZ-ABS-0001 still identifies AB-01
    by_file = read_las(sample_field.las_files[4])
    by_file.well_name = ""
    by_file.uwi = ""
    by_file.source = str(Path(tmp_path) / "AB-05.las")
    match = match_logs([log, by_file], layer)
    assert set(match.matched) == {"AB1", "AB5"}


def test_attach_logs_replaces_same_source_and_emits(sample_field):
    project = Project()
    layer = load_well_layer(sample_field.wells_csv, UTM).layer
    project.add_layer(layer)
    events = []
    project.subscribe(events.append)
    logs = read_las_files(sample_field.las_files).logs
    matched = match_logs(logs, layer).matched
    assert project.attach_logs(layer.id, matched) == 10
    assert project.attach_logs(layer.id, matched) == 10  # re-import replaces, no duplicates
    assert sum(len(v) for v in layer.logs.values()) == 10
    assert events[-1].kind is EventKind.LAYER_CHANGED and events[-1].field == "logs"
    assert layer.logs_for(layer.find("AB-03"))[0].well_name == "ab_03"
    assert len(layer.log_sources) == 10


@pytest.mark.perf
def test_batch_read_is_fast(sample_field):
    paths = sample_field.las_files * 5
    start = time.perf_counter()
    read_las_files(paths)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"reading {len(paths)} LAS files took {elapsed:.2f}s"
