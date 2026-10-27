"""Error paths and background results arriving at awkward moments."""

import shutil

import geopandas as gpd
import pytest
from PySide6.QtCore import QThreadPool
from shapely import Point

from petrogis.core.extent import Extent
from petrogis.core.layers import RasterLayer, VectorLayer
from petrogis.io.project_io import load_project, save_project
from petrogis.ui import main_window as mw
from petrogis.ui.main_window import MainWindow
from petrogis.ui.workers import TaskRunner


@pytest.fixture
def window(qtbot, monkeypatch):
    w = MainWindow()
    w.resize(1000, 700)
    qtbot.addWidget(w)
    w.show()
    w.reported = []
    monkeypatch.setattr(mw, "show_error", lambda *a, **k: w.reported.append(("error", a)))
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: w.reported.append(("warning", a)))
    return w


def wait_idle(qtbot, window):
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=10000)


def test_load_finishing_after_new_project_is_dropped(window, qtbot, sample_field):
    window.load_vector(sample_field.field_boundary)
    window.load_raster(sample_field.structure_top)
    window.new_project()  # empty and clean, so no prompt; the loads are still running
    wait_idle(qtbot, window)
    assert len(window.project) == 0
    assert window.reported == []


def test_las_import_after_layer_removed_is_discarded(window, qtbot, sample_field):
    window.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(window.project) == 1 and window.tasks.pending == 0, timeout=10000)
    wells = window.project.layers[0]
    window.import_las(sample_field.las_files)
    window.project.remove_layer(wells.id)
    wait_idle(qtbot, window)  # pytest-qt fails the test if a slot raised
    assert "discarded" in window.statusBar().currentMessage()


def test_shutdown_drops_late_callbacks(qtbot):
    runner = TaskRunner()
    results, errors = [], []

    def boom():
        raise RuntimeError("late")

    runner.submit(lambda: 42, on_result=results.append)
    runner.submit(boom, on_error=errors.append)
    runner.shutdown()
    QThreadPool.globalInstance().waitForDone(5000)
    qtbot.waitUntil(lambda: runner.pending == 0, timeout=5000)
    assert results == [] and errors == []


def test_raster_read_failure_is_shown(window, qtbot, sample_dir, tmp_path):
    tif = tmp_path / "structure_top.tif"
    shutil.copy(sample_dir / "structure_top.tif", tif)
    window.load_raster(tif)
    qtbot.waitUntil(lambda: len(window.project) == 1 and window.tasks.pending == 0, timeout=10000)
    tif.unlink()
    layer = window.project.layers[0]
    assert isinstance(layer, RasterLayer)
    e = layer.extent
    window.canvas.set_view_extent(Extent(e.xmin, e.ymin, e.center[0], e.center[1]))
    qtbot.waitUntil(
        lambda: "Could not draw raster" in window.statusBar().currentMessage(), timeout=5000
    )


def test_assumed_crs_survives_project_round_trip(window, qtbot, tmp_path, monkeypatch):
    shp = tmp_path / "nocrs.shp"
    with pytest.warns(UserWarning):
        gpd.GeoDataFrame({"name": ["W"]}, geometry=[Point(405000, 4475000)]).to_file(shp)
    monkeypatch.setattr(mw, "ask_crs", lambda parent, message, default: "EPSG:32639")
    window.load_vector(shp)  # first attempt fails with MissingCRSError, then the prompt retries
    qtbot.waitUntil(lambda: len(window.project) == 1 and window.tasks.pending == 0, timeout=10000)
    layer = window.project.layers[0]
    assert isinstance(layer, VectorLayer) and layer.assumed_crs == "EPSG:32639"

    path = save_project(window.project, tmp_path / "p.pgis")
    loaded = load_project(path)
    assert loaded.warnings == []
    assert loaded.project.layers[0].assumed_crs == "EPSG:32639"
    assert loaded.project.layers[0].gdf.geometry.iloc[0].x == 405000
