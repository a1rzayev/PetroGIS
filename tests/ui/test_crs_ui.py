"""P2.1 through the main window: CRS changes, transformations, delimited text, repair."""

import shutil

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from petrogis.core.crs import transformation_options
from petrogis.core.layers import DelimitedLayer, RasterLayer, VectorLayer, WellLayer
from petrogis.io.project_io import save_project
from petrogis.render.pg.canvas import graphics_for
from petrogis.render.pg.items import VectorGraphics
from petrogis.ui import main_window as mw
from petrogis.ui.dialogs.crs_picker import CRSPickerDialog
from petrogis.ui.main_window import MainWindow

UTM = "EPSG:32639"
PULKOVO_GK9 = "EPSG:28409"


@pytest.fixture
def window(qtbot, monkeypatch):
    w = MainWindow()
    w.resize(1200, 800)
    qtbot.addWidget(w)
    w.show()
    w.reported = []
    monkeypatch.setattr(mw, "show_error", lambda *a, **k: w.reported.append(("error", a)))
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: w.reported.append(("warning", a)))
    return w


def wait(qtbot, window, n_layers=None):
    qtbot.waitUntil(
        lambda: window.tasks.pending == 0 and (n_layers is None or len(window.project) == n_layers),
        timeout=20000,
    )


def load_field(qtbot, window, field):
    window.load_raster(field.structure_top)
    window.load_vector(field.field_boundary)
    window.import_wells(field.wells_csv)
    wait(qtbot, window, 3)


def test_crs_picker_search_and_select(qtbot):
    dialog = CRSPickerDialog("pulkovo 1942 gauss-kruger zone 9")
    qtbot.addWidget(dialog)
    dialog.search.setText("28409")
    dialog.refresh()
    assert dialog.selected_code() == PULKOVO_GK9
    assert "Pulkovo" in dialog.details.text()
    dialog.search.setText("no such crs anywhere")
    dialog.refresh()
    assert dialog.selected_code() is None


def test_rasters_go_below_vectors(window, qtbot, sample_field):
    window.load_vector(sample_field.field_boundary)
    wait(qtbot, window, 1)
    window.load_raster(sample_field.structure_top)
    wait(qtbot, window, 2)
    assert [type(layer) for layer in window.project.layers] == [VectorLayer, RasterLayer]


def test_change_project_crs_with_layers_and_back(window, qtbot, sample_field):
    load_field(qtbot, window, sample_field)
    wells = next(layer for layer in window.project.layers if isinstance(layer, WellLayer))
    original_xy = wells.xy().copy()
    original_order = [layer.id for layer in window.project.layers]

    window.set_project_crs(PULKOVO_GK9)
    qtbot.waitUntil(
        lambda: window.project.crs == PULKOVO_GK9 and window.tasks.pending == 0, timeout=20000
    )
    assert [layer.id for layer in window.project.layers] == original_order
    assert window.crs_label.text() == PULKOVO_GK9
    view = window.canvas.view_extent()
    assert 9_300_000 < view.center[0] < 9_500_000
    for layer in window.project.layers:
        assert window.canvas.has_layer(layer.id)

    window.set_project_crs(UTM)
    qtbot.waitUntil(lambda: window.project.crs == UTM and window.tasks.pending == 0, timeout=20000)
    wells = next(layer for layer in window.project.layers if isinstance(layer, WellLayer))
    np.testing.assert_allclose(wells.xy(), original_xy, atol=0.01)
    assert window.reported == []


def test_crs_change_waits_for_pending_loads(window, qtbot, sample_field):
    window.load_raster(sample_field.structure_top)
    window.set_project_crs(PULKOVO_GK9)  # a load is still running
    assert window.project.crs == UTM
    wait(qtbot, window, 1)


def test_choose_datum_transformation_reloads_layers(window, qtbot, tmp_path, monkeypatch):
    csv = tmp_path / "gk_wells.csv"
    csv.write_text("Well,X,Y\nW-1,9405000,4476000\nW-2,9410000,4472000\n")
    monkeypatch.setattr(mw, "ask_column_mapping", lambda *a: None)
    from petrogis.io.wells_io import load_well_layer

    window._on_layer_loaded(load_well_layer(csv, UTM, source_crs=PULKOVO_GK9).layer)
    before = window.project.layers[0].xy().copy()
    pairs = window.transformation_pairs()
    assert [(p.source, p.target) for p in pairs] == [(PULKOVO_GK9, UTM)]
    assert pairs[0].area is not None and pairs[0].current is None

    options = [o for o in transformation_options(PULKOVO_GK9, UTM, pairs[0].area) if o.available]
    chosen = options[-1].pipeline
    monkeypatch.setattr(
        mw, "choose_transformations", lambda p, parent: {(PULKOVO_GK9, UTM): chosen}
    )
    window.edit_transformations()
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=20000)
    assert window.project.transformation_for(PULKOVO_GK9, UTM) == chosen
    after = window.project.layers[0].xy()
    assert (np.hypot(*(after - before).T) > 0.5).all()


def test_delimited_text_import(window, qtbot, tmp_path, monkeypatch):
    csv = tmp_path / "samples.csv"
    csv.write_text("nomre;sharq;shimal\n1;405000,5;4475000\n2;405100;4475100\n")
    monkeypatch.setattr(
        mw, "ask_delimited_columns", lambda table, crs, parent: ("sharq", "shimal", UTM)
    )
    window.import_delimited(csv)
    wait(qtbot, window, 1)
    layer = window.project.layers[0]
    assert isinstance(layer, DelimitedLayer) and len(layer.gdf) == 2
    assert isinstance(window.canvas.graphics(layer.id), VectorGraphics)
    assert isinstance(graphics_for(layer), VectorGraphics)


def test_raster_without_crs_prompts_and_remembers(window, qtbot, tmp_path, monkeypatch):
    tif = tmp_path / "nocrs.tif"
    with rasterio.open(
        tif, "w", driver="GTiff", width=10, height=10, count=1, dtype="float32",
        transform=from_origin(400_000, 4_480_000, 100, 100),
    ) as dst:  # fmt: skip
        dst.write(np.ones((10, 10), dtype=np.float32), 1)
    asked = []
    monkeypatch.setattr(
        mw, "ask_crs", lambda parent, message, default: asked.append(message) or UTM
    )
    window.load_raster(tif)
    wait(qtbot, window, 1)
    assert asked and "coordinate reference system" in asked[0]
    assert window.project.layers[0].assumed_crs == UTM


def test_open_project_offers_repair_and_reinserts_in_place(
    window, qtbot, sample_dir, tmp_path, monkeypatch
):
    field = tmp_path / "field"
    shutil.copytree(sample_dir, field)
    from petrogis.core.project import Project
    from petrogis.io.raster_io import load_raster_layer
    from petrogis.io.vector_io import load_vector_layer

    project = Project()
    project.add_layer(load_raster_layer(field / "structure_top.tif", UTM))
    project.add_layer(load_vector_layer(field / "field_boundary.geojson", UTM))
    path = save_project(project, field / "p.pgis")
    moved = tmp_path / "elsewhere" / "structure_top.tif"
    moved.parent.mkdir()
    shutil.move(str(field / "structure_top.tif"), moved)

    offered = []

    def fake_repair(missing, parent):
        offered.append([m.name for m in missing])
        return {missing[0].position: moved}

    monkeypatch.setattr(mw, "ask_repair_paths", fake_repair)
    window.open_project(path)
    wait(qtbot, window, 2)
    assert offered == [["structure_top"]]
    assert [layer.name for layer in window.project.layers] == ["field_boundary", "structure_top"]
    assert window.project.dirty  # the repaired path still has to be saved
