"""Survey and tops import flows, the tops map layer, and the depth-aware log viewer."""

import numpy as np
import pytest
from PySide6.QtWidgets import QDialog

from petrogis.core.layers import VectorLayer, WellLayer
from petrogis.core.logs import LogCurve, WellLog
from petrogis.core.mnemonics import CurveFamily
from petrogis.core.tops import Top
from petrogis.core.trajectory import Trajectory
from petrogis.io.survey_io import SurveyMapping, read_survey_table
from petrogis.io.tops_io import TopsMapping, read_tops_table
from petrogis.render.pg.well_items import WellGraphics
from petrogis.ui import log_viewer as lv
from petrogis.ui import main_window as mw
from petrogis.ui import well_data as wd
from petrogis.ui.dialogs.field_mapping import FieldMappingDialog, ask_survey_mapping
from petrogis.ui.log_viewer import LogViewer, TrackScalesDialog
from petrogis.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    w = MainWindow()
    w.resize(1200, 800)
    qtbot.addWidget(w)
    w.show()
    w.reported = []
    for module in (mw, wd):
        monkeypatch.setattr(module, "show_warning", lambda *a, **k: w.reported.append(a[1:]))
        monkeypatch.setattr(module, "show_error", lambda *a, **k: w.reported.append(a[1:]))
    return w


def idle(qtbot, window):
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=10000)


def wells_in(window, qtbot, sample_field):
    window.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(window.project) == 1 and window.tasks.pending == 0, timeout=10000)
    return window.project.layers[0]


def test_import_surveys_and_tops(window, qtbot, sample_field):
    window.well_data.import_surveys(sample_field.surveys_csv)
    assert window.reported and "Import well headers first" in window.reported[0][1]
    window.reported.clear()
    layer = wells_in(window, qtbot, sample_field)
    window.well_data.import_surveys(sample_field.surveys_csv)
    idle(qtbot, window)
    assert set(layer.trajectories) == {"AB4", "AB6"}
    assert "2 deviated" in window.statusBar().currentMessage()
    graphics = window.canvas.graphics(layer.id)
    assert isinstance(graphics, WellGraphics)
    assert graphics.paths.xData is not None and len(graphics.bottoms.data) == 2
    window.well_data.import_tops(sample_field.tops_csv)
    idle(qtbot, window)
    assert sum(len(t) for t in layer.tops.values()) == 30
    assert window.reported == []
    assert layer.survey_imports and layer.tops_imports


def test_unmatched_wells_and_mapping_prompt(window, qtbot, sample_field, tmp_path, monkeypatch):
    layer = wells_in(window, qtbot, sample_field)
    single = tmp_path / "survey.csv"
    single.write_text("Depth,Angle,Dir\n0,0,0\n500,10,90\n")
    window.well_data.import_surveys(single)  # no well column: the prompt cancels by default
    idle(qtbot, window)
    assert layer.trajectories == {}
    monkeypatch.setattr(
        wd, "ask_survey_mapping", lambda table, parent: (table.mapping, "m", "AB-05")
    )
    window.well_data.import_surveys(single)
    idle(qtbot, window)
    assert list(layer.trajectories) == ["AB5"]

    other = tmp_path / "tops.csv"
    other.write_text("Well,Surface,MD\nZZ-1,Top,100\nAB-01,Top,1500\n")
    window.well_data.import_tops(other)
    idle(qtbot, window)
    assert layer.tops["AB1"][0].md == 1500
    assert any("No matching well for: ZZ-1" in r[1] for r in window.reported)
    window.reported.clear()
    broken = tmp_path / "broken.csv"
    broken.write_text("a\n1\n")
    window.well_data.import_tops(broken)
    idle(qtbot, window)  # incomplete mapping: the prompt cancels
    window.well_data.import_surveys(tmp_path / "missing.csv")
    idle(qtbot, window)
    assert any("not found" in r[1] for r in window.reported)


def test_import_discarded_when_layer_removed(window, qtbot, sample_field):
    layer = wells_in(window, qtbot, sample_field)
    window.well_data.import_tops(sample_field.tops_csv)
    window.project.remove_layer(layer.id)
    idle(qtbot, window)
    assert "discarded" in window.statusBar().currentMessage()


def test_create_tops_layer(window, qtbot, sample_field, tmp_path, monkeypatch):
    window.well_data.create_tops_layer()  # no well layer
    layer = wells_in(window, qtbot, sample_field)
    window.reported.clear()
    window.well_data.create_tops_layer()
    assert "Import formation tops first" in window.reported[0][1]
    window.well_data.import_tops(sample_field.tops_csv)
    idle(qtbot, window)
    window.well_data.create_tops_layer()  # surface prompt cancels
    monkeypatch.setattr(wd, "ask_tops_surface", lambda parent, names: names[1])
    window.well_data.create_tops_layer()  # path prompt cancels
    target = tmp_path / "tops.gpkg"
    monkeypatch.setattr(wd, "ask_tops_layer_path", lambda parent, suggested: str(target))
    window.well_data.create_tops_layer()
    qtbot.waitUntil(lambda: len(window.project) == 2 and window.tasks.pending == 0, timeout=10000)
    tops_layer = window.project.layers[0]
    assert isinstance(tops_layer, VectorLayer) and target.exists()
    assert len(tops_layer.gdf) == 10 and "tvdss" in tops_layer.gdf.columns
    assert layer.id != tops_layer.id


def test_log_viewer_follows_attached_data(window, qtbot, sample_field):
    layer = wells_in(window, qtbot, sample_field)
    position = layer.find("AB-06")
    window.show_well_logs(layer, position)
    assert window.log_viewer.trajectory is None
    window.well_data.import_surveys(sample_field.surveys_csv)
    window.well_data.import_tops(sample_field.tops_csv)
    idle(qtbot, window)
    viewer = window.log_viewer
    assert viewer.trajectory is not None and len(viewer.tops) == 3
    viewer.depth_combo.setCurrentText("TVDSS")
    assert viewer.gr_plot.getAxis("left").labelText == "TVDSS (m)"
    assert len(viewer._top_lines) == 9  # three surfaces across three tracks


def _log(depth, unit="M"):
    n = depth.size
    curves = {
        "GR": LogCurve("GR", "GAPI", "", np.where(np.arange(n) % 40 < 20, 40.0, 110.0)),
        "NPHI": LogCurve("NPHI", "V/V", "", np.full(n, 0.10)),
        "RHOB": LogCurve("RHOB", "G/CC", "", np.full(n, 2.10)),
    }
    return WellLog("W", "", "/x/w.las", depth, unit, curves)


@pytest.fixture
def viewer(qtbot):
    v = LogViewer()
    v.resize(500, 700)
    qtbot.addWidget(v)
    v.show()
    return v


def test_depth_references_and_units(viewer):
    depth = np.arange(1000.0, 1100.0, 0.5)
    trajectory = Trajectory.from_survey([0, 500, 1500], [0, 0, 60], [0, 0, 90])
    tops = [Top("W", "W", "Top", 1050.0)]
    viewer.show_well("W", [_log(depth)], kb=30.0, trajectory=trajectory, tops=tops)
    _, y = viewer.gr_curve.getData()
    assert y[0] == pytest.approx(1000.0)
    viewer.depth_combo.setCurrentText("TVD")
    expected_tvd = trajectory.md_to_tvd([1000.0])[0]
    assert viewer.gr_curve.getData()[1][0] == pytest.approx(expected_tvd)
    assert expected_tvd < 1000.0
    viewer.depth_combo.setCurrentText("TVDSS")
    assert viewer.gr_curve.getData()[1][0] == pytest.approx(expected_tvd - 30.0)
    viewer.unit_combo.setCurrentText("ft")
    assert viewer.gr_curve.getData()[1][0] == pytest.approx((expected_tvd - 30.0) / 0.3048)
    line = viewer._top_lines[0][1]
    assert line.value() == pytest.approx((trajectory.md_to_tvd([1050.0])[0] - 30) / 0.3048)
    assert "deviated" in viewer.header.text()

    # Without KB, TVDSS is unavailable and falls back to TVD.
    viewer.show_well("W", [_log(depth)], kb=None, trajectory=trajectory)
    assert viewer.depth_reference == "TVD"
    assert not viewer.depth_combo.model().item(2).isEnabled()


def test_shading_and_scales(viewer, monkeypatch):
    depth = np.arange(1000.0, 1100.0, 0.5)
    viewer.show_well("W", [_log(depth)])
    sand = viewer.sand_shade.boundingRect()
    assert sand.width() > 0 and sand.left() >= 40 - 1e-6 and sand.right() <= 75 + 1e-6
    # NPHI 0.10 sits right of RHOB 2.10 on the overlay: a gas-like crossover gets shaded.
    assert viewer.crossover_shade.boundingRect().width() > 0
    viewer.sand_check.setChecked(False)
    viewer.crossover_check.setChecked(False)
    assert viewer.sand_shade.boundingRect().isEmpty()
    assert viewer.crossover_shade.boundingRect().isEmpty()

    scales = dict(viewer.scales)
    scales[CurveFamily.GR] = lv.replace(scales[CurveFamily.GR], lo=0.0, hi=200.0)
    monkeypatch.setattr(lv, "ask_track_scales", lambda parent, s, cutoff: (scales, 60.0))
    viewer.edit_scales()
    assert viewer.sand_cutoff == 60.0
    assert viewer.gr_plot.getViewBox().viewRange()[0] == pytest.approx([0.0, 200.0])


def test_track_scales_dialog(qtbot):
    dialog = TrackScalesDialog(dict(lv.DEFAULT_SCALES), 75.0)
    qtbot.addWidget(dialog)
    low, high = dialog.spins[CurveFamily.NPHI]
    assert (low.value(), high.value()) == (0.45, -0.15)  # as shown: reversed track
    high.setValue(-0.1)
    assert dialog.result_scales()[CurveFamily.NPHI].lo == -0.1
    res_low, _ = dialog.spins[CurveFamily.RES_DEEP]
    res_low.setValue(0.0)
    with pytest.raises(ValueError, match="positive"):
        dialog.result_scales()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "positive" in dialog.error.text()
    _, gr_high = dialog.spins[CurveFamily.GR]
    gr_high.setValue(-5)
    with pytest.raises(ValueError, match="empty"):
        dialog.result_scales()
    dialog.restore_defaults()
    assert dialog.result_scales() == lv.DEFAULT_SCALES and dialog.cutoff.value() == 75.0


def test_long_logs_are_decimated(viewer):
    depth = np.arange(0.0, 20_000.0, 0.1)  # 200k samples
    viewer.show_well("W", [_log(depth)])
    x, _ = viewer.gr_curve.getData()
    assert x.size <= 2 * lv.MAX_DISPLAY_BINS


def test_tops_without_logs(viewer):
    viewer.show_well(
        "W", [], kb=10.0, tops=[Top("W", "W", "A", 1500.0), Top("W", "W", "B", 1600.0)]
    )
    assert "no logs" in viewer.header.text() and len(viewer._top_lines) == 6
    low, high = viewer.gr_plot.getViewBox().viewRange()[1]
    assert low < 1500 < 1600 < high
    viewer.depth_combo.setCurrentText("TVDSS")
    assert viewer.gr_plot.getAxis("left").labelText == "TVDSS (m)"
    viewer.clear()
    assert viewer._top_lines == []


def test_field_mapping_dialog(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "AB-07.csv"
    path.write_text("Depth (ft),Angle,Dir\n0,0,0\n100,1,1\n")
    table = read_survey_table(path)
    dialog = FieldMappingDialog(
        "Import Surveys", "intro", table, ("well", "md", "inc", "azi"),
        {"well": "Well", "md": "MD", "inc": "Inc", "azi": "Azi"}, ("md", "inc", "azi"),
    )  # fmt: skip
    qtbot.addWidget(dialog)
    assert dialog.unit.currentText() == "ft"
    assert dialog.single_well.text() == "AB-07" and dialog.single_well.isVisibleTo(dialog)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok.isEnabled()
    dialog.single_well.setText("")
    assert not ok.isEnabled() and "Name the well" in dialog.error.text()
    dialog.combos["inc"].setCurrentIndex(dialog.combos["inc"].findData("Dir"))
    assert "only be used once" in dialog.error.text()
    dialog.combos["inc"].setCurrentIndex(0)
    assert "Required" in dialog.error.text()

    monkeypatch.setattr(FieldMappingDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    mapping, unit, default = ask_survey_mapping(table)
    assert mapping == SurveyMapping(None, "Depth (ft)", "Angle", "Dir")
    assert (unit, default) == ("ft", "AB-07")

    wide = tmp_path / "wide.csv"
    wide.write_text("Well,Top A,Top B\nW1,1,2\n")
    from petrogis.ui.dialogs.field_mapping import ask_tops_mapping

    mapping, unit, default = ask_tops_mapping(read_tops_table(wide))
    assert mapping == TopsMapping("Well", None, None) and default is None
    monkeypatch.setattr(FieldMappingDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert ask_survey_mapping(table) is None and ask_tops_mapping(read_tops_table(wide)) is None


def test_hidden_paths_and_wells_layer_style(window, qtbot, sample_field):
    layer = wells_in(window, qtbot, sample_field)
    window.well_data.import_surveys(sample_field.surveys_csv)
    idle(qtbot, window)
    graphics = window.canvas.graphics(layer.id)
    assert graphics.paths.isVisible()
    window.project.set_style(layer.id, lv.replace(layer.style, show_paths=False))
    assert not graphics.paths.isVisible() and not graphics.bottoms.isVisible()
    assert isinstance(layer, WellLayer)
