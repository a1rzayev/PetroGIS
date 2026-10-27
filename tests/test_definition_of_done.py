"""Phase 1 definition of done, end to end through the real main window.

``uv run petrogis`` opens the app. You can load the sample field boundary, structure GeoTIFF,
wells CSV and LAS files, see them correctly overlaid in UTM 39N, click a well to see its logs,
and save the project and reopen it with identical state.
"""

import sys

import pytest
from PySide6.QtWidgets import QApplication
from shapely import Point

from petrogis import app as petrogis_app
from petrogis.core.layers import RasterLayer, VectorLayer, WellLayer
from petrogis.io.project_io import project_to_dict
from petrogis.ui import main_window as mw


def _wait(qtbot, window, n_layers=None):
    qtbot.waitUntil(
        lambda: window.tasks.pending == 0 and (n_layers is None or len(window.project) == n_layers),
        timeout=15000,
    )


@pytest.fixture
def launched(qtbot, monkeypatch, tmp_path):
    """Run ``petrogis.app.main`` for real, but return from the event loop immediately."""
    windows = []
    real_window = mw.MainWindow

    def capture(*args, **kwargs):
        window = real_window(*args, **kwargs)
        windows.append(window)
        qtbot.addWidget(window)
        return window

    monkeypatch.setattr(mw, "MainWindow", capture)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(petrogis_app, "setup_logging", lambda: tmp_path / "petrogis.log")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test

    def launch(*argv):
        monkeypatch.setattr(sys, "argv", ["petrogis", *map(str, argv)])
        assert petrogis_app.main() == 0
        return windows[-1]

    return launch


def test_definition_of_done(launched, qtbot, sample_field, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: warnings.append(a))
    window = launched()
    assert window.isVisible()

    # Load the sample data.
    window.load_vector(sample_field.field_boundary)
    window.load_raster(sample_field.structure_top)
    window.import_wells(sample_field.wells_csv)
    _wait(qtbot, window, 3)
    window.import_las(sample_field.las_files)
    _wait(qtbot, window)
    assert len(warnings) == 1 and "OFFSET_1.las" in warnings[0][2]  # the deliberate orphan

    # Correctly overlaid in UTM 39N.
    assert window.project.crs == "EPSG:32639" and window.crs_label.text() == "EPSG:32639"
    boundary = next(lyr for lyr in window.project.layers if isinstance(lyr, VectorLayer))
    raster = next(lyr for lyr in window.project.layers if isinstance(lyr, RasterLayer))
    wells = next(lyr for lyr in window.project.layers if isinstance(lyr, WellLayer))
    assert boundary.gdf.crs.to_epsg() == wells.gdf.crs.to_epsg() == 32639
    assert raster.extent.intersection(boundary.extent) == boundary.extent
    polygon = boundary.gdf.geometry.iloc[0]
    inside = sum(polygon.contains(Point(x, y)) for x, y in wells.xy())
    assert inside == 7  # 7 field wells, 3 flank wells
    for layer in window.project.layers:
        assert window.canvas.has_layer(layer.id)

    # Click a well to see its logs.
    x, y = wells.xy()[wells.find("AB-03")]
    window.canvas.clicked.emit(float(x), float(y))
    assert window.log_dock.isVisible()
    assert window.log_viewer.current.name_key == "AB3"
    _x, depth = window.log_viewer.gr_curve.getData()
    assert len(depth) > 100

    # Save, then reopen through the command line with identical state.
    path = tmp_path / "absheron.pgis"
    assert window.save_project_as(path)
    view = window.canvas.view_extent()
    expected = project_to_dict(window.project, path, view)

    reopened = launched(path)
    _wait(qtbot, reopened, 3)
    assert project_to_dict(reopened.project, path, view) == expected
    assert reopened.windowTitle() == "absheron.pgis – PetroGIS"
