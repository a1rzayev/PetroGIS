"""Property-based tests: readers must fail cleanly on malformed input.

Data files and project files are untrusted (security baseline, docs/phase2-plan.md). For any
input, a reader either returns a result or raises ``DataReadError`` with a user-facing message.
Any other exception is a bug.
"""

import contextlib
import json
import string
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from petrogis.core.styles import RasterStyle, VectorStyle, WellStyle
from petrogis.io.delimited_io import load_delimited_layer
from petrogis.io.errors import DataReadError
from petrogis.io.las_io import read_las
from petrogis.io.project_io import load_project
from petrogis.io.wells_io import load_well_layer, read_well_table

UTM = "EPSG:32639"
SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=20,
)
layer_keys = st.sampled_from(
    ["id", "kind", "name", "visible", "source", "source_abs", "style", "band", "source_layer",
     "assume_crs", "encoding", "import_options", "logs"]
)  # fmt: skip
layer_entries = st.dictionaries(layer_keys, json_values, max_size=8) | json_values


def _write(suffix: str, content: str | bytes) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="petrogis-fuzz-"))
    path = directory / f"input{suffix}"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", errors="surrogatepass")
    return path


# --- LAS -------------------------------------------------------------------------------------
las_lines = st.lists(
    st.one_of(
        st.sampled_from(["~V", "~W", "~C", "~A", "~P", "~O", "VERS. 2.0 :", "WRAP. NO :",
                         "NULL. -999.25 :", "DEPT.M :", "GR.GAPI :", "STRT.M 1 :"]),
        st.text(alphabet=string.printable, max_size=40),
        st.lists(st.floats(allow_nan=True, allow_infinity=True), max_size=4).map(
            lambda xs: " ".join(map(str, xs))
        ),
    ),
    max_size=25,
)  # fmt: skip


@SETTINGS
@given(las_lines)
def test_las_reader_fails_cleanly_on_structured_garbage(lines):
    path = _write(".las", "\n".join(lines))
    try:
        log = read_las(path)
    except DataReadError:
        return
    assert log.depth.ndim == 1 and log.depth.size > 0


@SETTINGS
@given(st.binary(max_size=2000))
def test_las_reader_fails_cleanly_on_random_bytes(data):
    path = _write(".las", data)
    with contextlib.suppress(DataReadError):
        read_las(path)


# --- well headers ----------------------------------------------------------------------------
csv_cells = st.one_of(
    st.text(alphabet=string.ascii_letters + string.digits + " .-_", max_size=8),
    st.floats(allow_nan=True, allow_infinity=True).map(str),
    st.just(""),
)
headers = st.lists(
    st.sampled_from(["Well", "Name", "X", "Y", "Easting", "Northing", "KB", "UWI", "TD", "Lat",
                     "Lon", "Foo", ""]),
    min_size=1, max_size=6,
)  # fmt: skip


@SETTINGS
@given(
    headers,
    st.lists(st.lists(csv_cells, max_size=7), max_size=8),
    st.sampled_from([",", ";", "\t"]),
)
def test_well_import_fails_cleanly(header, rows, sep):
    text = "\n".join([sep.join(header), *(sep.join(row) for row in rows)])
    path = _write(".csv", text)
    try:
        table = read_well_table(path)
        load_well_layer(path, UTM)
    except DataReadError:
        return
    assert table.columns


# --- project files ---------------------------------------------------------------------------
@SETTINGS
@given(json_values)
def test_project_loader_fails_cleanly_on_any_json(document):
    path = _write(".pgis", json.dumps(document))
    with contextlib.suppress(DataReadError):
        load_project(path)


@SETTINGS
@given(
    st.dictionaries(st.sampled_from(["crs", "view", "version", "transformations"]), json_values),
    st.lists(layer_entries, max_size=5),
)
def test_project_loader_skips_bad_layers_with_warnings(top, layers):
    document = {"format": "petrogis-project", "version": 1, "layers": layers, **top}
    document["format"] = "petrogis-project"
    if not isinstance(document.get("version"), int) or document["version"] > 1:
        document["version"] = 1
    path = _write(".pgis", json.dumps(document))
    try:
        loaded = load_project(path)
    except DataReadError:
        return
    # Nothing in these random documents points at a real data file, so no layer can load.
    assert len(loaded.project) == 0
    assert len(loaded.warnings) >= len(layers) or not layers


@SETTINGS
@given(st.dictionaries(st.text(max_size=12), json_values, max_size=6))
def test_styles_reject_bad_values_with_value_error(data):
    for cls in (VectorStyle, RasterStyle, WellStyle):
        with contextlib.suppress(ValueError):
            cls.from_dict(data)


@SETTINGS
@given(
    headers,
    st.lists(st.lists(csv_cells, max_size=7), max_size=8),
    st.sampled_from([",", ";", "\t"]),
)
def test_delimited_import_fails_cleanly(header, rows, sep):
    text = "\n".join([sep.join(header), *(sep.join(row) for row in rows)])
    path = _write(".csv", text)
    try:
        result = load_delimited_layer(path, UTM)
    except DataReadError:
        return
    assert len(result.layer.gdf) >= 1


# --- surveys and tops ------------------------------------------------------------------------
survey_headers = st.lists(
    st.sampled_from(["Well", "MD", "MD (ft)", "Inc", "Azi", "Depth", "Surface", "Formation",
                     "Top Reservoir", "Foo", ""]),
    min_size=1, max_size=5,
)  # fmt: skip


@SETTINGS
@given(
    survey_headers,
    st.lists(st.lists(csv_cells, max_size=6), max_size=10),
    st.sampled_from([",", ";", "\t"]),
)
def test_survey_and_tops_import_fail_cleanly(header, rows, sep):
    from petrogis.io.survey_io import load_surveys
    from petrogis.io.tops_io import load_tops

    text = "\n".join([sep.join(header), *(sep.join(row) for row in rows)])
    path = _write(".csv", text)
    for load in (load_surveys, load_tops):
        try:
            result = load(path)
        except DataReadError:
            continue
        assert result.names


@SETTINGS
@given(json_values | st.dictionaries(
    st.sampled_from(["layer_id", "wells", "template", "datum", "flatten_on"]), json_values,
    max_size=5,
))  # fmt: skip
def test_correlation_sections_reject_bad_values_with_value_error(data):
    from petrogis.core.correlation import CorrelationSection

    if not isinstance(data, dict):
        return
    with contextlib.suppress(ValueError):
        CorrelationSection.from_dict(data)


@SETTINGS
@given(st.binary(max_size=3000) | st.lists(json_values, max_size=6).map(
    lambda items: "\n".join(json.dumps(i) for i in items).encode()
))  # fmt: skip
def test_processing_history_never_fails(data):
    from petrogis.io.processing_history import ProcessingHistory

    path = _write(".jsonl", data)
    entries = ProcessingHistory(path).entries()
    assert all(e.status in ("ok", "failed", "canceled") for e in entries)
