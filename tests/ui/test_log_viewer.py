import math

import numpy as np
import pytest

from petrogis.core.logs import LogCurve, WellLog
from petrogis.io.las_io import read_las
from petrogis.io.wells_io import load_well_layer
from petrogis.ui import main_window as mw
from petrogis.ui.log_viewer import LogViewer
from petrogis.ui.main_window import MainWindow


@pytest.fixture
def viewer(qtbot):
    v = LogViewer()
    v.resize(500, 700)
    qtbot.addWidget(v)
    v.show()
    return v


def test_tracks_share_inverted_depth_axis(viewer, sample_field):
    log = read_las(sample_field.las_files[0])
    viewer.show_well("AB-01", [log], uwi="AZ-ABS-0001", kb=28.3)
    assert "AB-01" in viewer.header.text() and "KB 28.3" in viewer.header.text()
    for plot in (viewer.gr_plot, viewer.res_plot, viewer.nd_plot):
        assert plot.getViewBox().yInverted()
    top, bottom = log.depth_range
    (y0, y1) = viewer.res_plot.getViewBox().viewRange()[1]
    assert y0 <= top and y1 >= bottom  # linked to the GR track's range

    _x, y = viewer.gr_curve.getData()
    assert len(y) == len(log.depth)
    assert viewer.res_plot.getAxis("top").labelText.startswith("RT")
    assert viewer.nd_plot.getAxis("bottom").labelText.startswith("RHOB")
    # Resistivity track is logarithmic: x range is in decades.
    rx0, rx1 = viewer.res_plot.getViewBox().viewRange()[0]
    assert rx0 == pytest.approx(math.log10(0.2)) and rx1 == pytest.approx(math.log10(2000))
    assert viewer.nd_plot.getViewBox().xInverted()  # NPHI reads right to left
    assert viewer.nd_plot.getAxis("top").autoSIPrefixScale == 1.0  # no "x0.001" scaling


def test_missing_curve_and_multiple_sources(viewer):
    depth = np.arange(1000.0, 1010.0, 0.5)
    only_gr = WellLog(
        well_name="X-1", uwi="", source="/data/x1_gr.las", depth=depth, depth_unit="FT",
        curves={"SGR": LogCurve("SGR", "GAPI", "", np.full(depth.shape, 60.0))},
    )  # fmt: skip
    neg_res = WellLog(
        well_name="X-1", uwi="", source="/data/x1_res.las", depth=depth, depth_unit="FT",
        curves={"LLD": LogCurve("LLD", "OHMM", "", np.linspace(-1, 10, depth.size))},
    )  # fmt: skip
    viewer.show_well("X-1", [only_gr, neg_res])
    assert viewer.source_combo.isVisible() and viewer.source_combo.count() == 2
    assert viewer.res_plot.getAxis("top").labelText == "RES_DEEP (missing)"
    assert viewer.gr_plot.getAxis("left").labelText == "MD (m)"
    _, y = viewer.gr_curve.getData()
    assert y[0] == pytest.approx(1000.0 * 0.3048)  # feet logs are converted to the chosen unit
    viewer.unit_combo.setCurrentText("ft")
    assert viewer.gr_plot.getAxis("left").labelText == "MD (ft)"
    assert viewer.gr_curve.getData()[1][0] == pytest.approx(1000.0)
    viewer.source_combo.setCurrentIndex(1)
    assert viewer.current is neg_res
    x, _ = viewer.res_curve.getData()
    assert np.isnan(x[0])  # non-positive resistivity masked for the log axis


def test_well_without_logs(viewer):
    viewer.show_well("AB-09", [])
    assert "no logs" in viewer.header.text()
    assert viewer.current is None


def test_click_well_opens_log_viewer(qtbot, sample_field, monkeypatch):
    window = MainWindow()
    window.resize(1400, 900)
    qtbot.addWidget(window)
    window.show()
    warnings = []
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: warnings.append(a))

    window.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(window.project) == 1 and window.tasks.pending == 0, timeout=10000)
    window.import_las(sample_field.las_files)
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=10000)
    layer = window.project.layers[0]
    assert len(layer.log_sources) == 10
    assert len(warnings) == 1 and "OFFSET_1.las" in warnings[0][2]

    position = layer.find("AB-04")
    x, y = layer.xy()[position]
    assert not window.log_dock.isVisible()
    window.canvas.clicked.emit(float(x) + 1.0, float(y) - 1.0)
    assert window.log_dock.isVisible()
    assert "AB-04" in window.log_viewer.header.text()
    assert window.log_viewer.current.name_key == "AB4"

    # A click far away from any well changes nothing.
    window.canvas.clicked.emit(float(x) + 50_000, float(y))
    assert window.log_viewer.current.name_key == "AB4"


def test_las_import_requires_wells(qtbot, monkeypatch, sample_field):
    window = MainWindow()
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: warnings.append(a))
    window.import_las(sample_field.las_files)
    assert warnings and "well headers first" in warnings[0][2]
    assert window.tasks.pending == 0


def test_target_well_layer_prefers_selection(qtbot, sample_field):
    window = MainWindow()
    qtbot.addWidget(window)
    a = load_well_layer(sample_field.wells_csv, window.project.crs).layer
    b = load_well_layer(sample_field.wells_xlsx, window.project.crs).layer
    window.project.add_layer(a)
    window.project.add_layer(b)
    window.layer_panel.setCurrentRow(1)  # a is second from the top
    assert window._target_well_layer() is a
