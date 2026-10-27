"""Processing dialogs: generated inputs, background runs, cancel, history and re-runs."""

import pytest

from petrogis.analysis.processing import get_algorithm
from petrogis.core.layers import VectorLayer
from petrogis.io.processing_history import HistoryEntry, ProcessingHistory
from petrogis.ui import main_window as mw
from petrogis.ui import processing
from petrogis.ui.main_window import MainWindow
from petrogis.ui.processing import AlgorithmDialog, open_algorithm


@pytest.fixture
def window(qtbot, monkeypatch, sample_field, tmp_path):
    w = MainWindow()
    w.resize(1200, 800)
    qtbot.addWidget(w)
    w.show()
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: None)
    w.load_raster(sample_field.structure_top)
    w.load_vector(sample_field.field_boundary)
    w.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(w.project) == 3 and w.tasks.pending == 0, timeout=10000)
    w.project.path = str(tmp_path / "demo.pgis")  # output suggestions go next to the project
    return w


def idle(qtbot, window):
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=15000)


def history():
    return ProcessingHistory(processing.default_history_path()).entries()


def test_menu_lists_every_algorithm(window):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Processing")
    names = {
        action.text()
        for group in menu.actions()
        if group.menu() is not None
        for action in group.menu().actions()
    }
    assert "Zonal statistics" in names and "Clip vector layer by polygons" in names
    assert window.history_action.text() == "&History…"


def test_run_sample_at_wells_loads_output(window, qtbot, tmp_path):
    algorithm = get_algorithm("raster:sample_at_wells")
    dialog = open_algorithm(window, algorithm)
    assert open_algorithm(window, algorithm) is dialog  # reused, not duplicated
    wells_combo = dialog.inputs["wells"]
    assert wells_combo.count() == 1 and dialog.inputs["raster"].count() == 1
    suggested = dialog.inputs["output"].text()
    assert suggested.startswith(str(tmp_path)) and suggested.endswith("_sample_at_wells.gpkg")
    dialog.run()
    assert dialog.running and not dialog.run_button.isEnabled()
    idle(qtbot, window)
    qtbot.waitUntil(lambda: len(window.project) == 4 and window.tasks.pending == 0, timeout=10000)
    loaded = window.project.layers[0]
    assert isinstance(loaded, VectorLayer) and "value" in loaded.gdf.columns
    text = dialog.log.toPlainText()
    assert "Sampled 10 of 10 wells" in text and dialog.progress.value() == 1000
    entry = history()[0]
    assert entry.status == "ok" and entry.algorithm_id == algorithm.id
    assert entry.parameters["output"] == suggested
    dialog.refresh_layers()
    assert dialog.inputs["output"].text() == suggested  # an edited/suggested path is kept


def test_invalid_parameters_are_reported(window, tmp_path):
    dialog = open_algorithm(window, get_algorithm("vector:reproject"))
    dialog.inputs["crs"].set_text("EPSG:999999")
    dialog.run()
    assert "Target CRS" in dialog.log.toPlainText() and not dialog.running
    dialog.inputs["crs"].set_text("EPSG:28409")
    dialog.inputs["output"].set_text(str(tmp_path / "missing" / "x.gpkg"))
    dialog.run()
    assert "does not exist" in dialog.log.toPlainText()
    assert history() == []  # nothing ran


def test_cancel_and_failure_are_recorded(window, qtbot, monkeypatch, tmp_path):
    import threading

    started = threading.Event()

    def slow(*args, check, progress, **kwargs):
        started.set()
        for _ in range(500):  # until the user cancels (at most ~5 s)
            check()
            threading.Event().wait(0.01)
        raise AssertionError("never cancelled")

    monkeypatch.setattr("petrogis.io.raster_ops.reproject_raster", slow)
    dialog = open_algorithm(window, get_algorithm("raster:reproject"))
    dialog.inputs["crs"].set_text("EPSG:28409")
    dialog.run()
    assert started.wait(5)
    dialog.cancel()
    idle(qtbot, window)
    assert "Cancelled" in dialog.log.toPlainText()
    assert history()[0].status == "canceled"
    assert len(window.project) == 3

    from petrogis.io.errors import DataReadError

    def broken(*args, **kwargs):
        raise DataReadError("disk full")

    monkeypatch.setattr("petrogis.io.raster_ops.reproject_raster", broken)
    dialog.run()
    idle(qtbot, window)
    assert "✗ disk full" in dialog.log.toPlainText() and history()[0].status == "failed"

    def crash(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr("petrogis.io.raster_ops.reproject_raster", crash)
    dialog.run()
    idle(qtbot, window)
    assert "Unexpected error: bug" in dialog.log.toPlainText()
    dialog.close()  # closing with nothing running is fine


def test_history_dialog_reruns_with_parameters(window, qtbot, tmp_path):
    boundary = next(lyr for lyr in window.project.layers if lyr.name == "field_boundary")
    ProcessingHistory(processing.default_history_path()).append(
        HistoryEntry(
            "vector:reproject", "Reproject vector layer", "2026-10-28T09:00:00+04:00", 1.0, "ok",
            "done", {"input": {"id": "old-id", "name": "field_boundary"}, "crs": "EPSG:28409",
                     "output": str(tmp_path / "again.gpkg")},
        )
    )  # fmt: skip
    ProcessingHistory(processing.default_history_path()).append(
        HistoryEntry("gone:algorithm", "Removed", "2026-10-28T09:05:00+04:00", 1.0, "ok")
    )
    dialog = window.show_processing_history()
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(1, 1).text() == "Reproject vector layer"
    dialog.table.selectRow(0)
    assert dialog.run_again() is None and "Unknown algorithm" in window.statusBar().currentMessage()
    dialog.table.selectRow(1)
    rerun = dialog.run_again()
    assert isinstance(rerun, AlgorithmDialog)
    assert rerun.inputs["input"].currentData() == boundary.id  # matched by name
    assert rerun.inputs["crs"].text() == "EPSG:28409"
    assert rerun.inputs["output"].text() == str(tmp_path / "again.gpkg")
    dialog.table.clearSelection()
    dialog.table.setCurrentCell(-1, -1)
    assert dialog.run_again() is None
    dialog.clear()
    assert dialog.table.rowCount() == 0


def test_set_values_ignores_bad_types(window):
    dialog = open_algorithm(window, get_algorithm("raster:sample_at_wells"))
    before = dialog.values()
    dialog.set_values({"wells": 5, "location": "Moon", "output": 3, "raster": {"id": "x"}})
    assert dialog.values() == before
    dialog.set_values({"location": "Bottom hole"})
    assert dialog.values()["location"] == "Bottom hole"
