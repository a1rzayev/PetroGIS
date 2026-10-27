"""Correlation panel: building sections, flattening, picking, templates and well selection."""

import glob
from dataclasses import replace

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QDialog

from petrogis.core.correlation import BUILTIN_TEMPLATES, CorrelationSection
from petrogis.core.mnemonics import CurveFamily
from petrogis.ui import correlation as corr
from petrogis.ui import main_window as mw
from petrogis.ui import well_data as wd
from petrogis.ui.correlation import SectionWellsDialog, TemplateDialog
from petrogis.ui.main_window import MainWindow

PAYLOAD = "<b>X</b><img src='x.png'>{value.__class__}"


@pytest.fixture
def window(qtbot, monkeypatch, sample_field):
    w = MainWindow()
    w.resize(1400, 900)
    qtbot.addWidget(w)
    w.show()
    w.reported = []
    for module in (mw, wd):
        monkeypatch.setattr(module, "show_warning", lambda *a, **k: w.reported.append(a[1:]))
        monkeypatch.setattr(module, "show_error", lambda *a, **k: w.reported.append(a[1:]))
    w.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(w.project) == 1 and w.tasks.pending == 0, timeout=10000)
    w.import_las(sorted(glob.glob(str(sample_field.las_dir / "*.las"))))
    w.well_data.import_surveys(sample_field.surveys_csv)
    w.well_data.import_tops(sample_field.tops_csv)
    qtbot.waitUntil(lambda: w.tasks.pending == 0, timeout=10000)
    w.reported.clear()
    w.wells = w.project.layers[0]
    return w


def test_section_builds_one_plot_per_track(window):
    panel = window.correlation
    assert panel.columns == [] and not panel.pick_button.isEnabled()
    panel.set_wells(window.wells, ("AB1", "AB6", "ZZ-9", "AB1"))
    assert window.correlation_dock.isVisible()
    assert [c.info.name for c in panel.columns] == ["AB-01", "AB-06"]
    assert all(len(c.plots) == 3 for c in panel.columns) and len(panel.gaps) == 1
    # Correlation lines join the three surfaces both wells pick.
    assert len(panel.gaps[0].listDataItems()) == 3
    assert panel.flatten_combo.count() == 4  # (none) + three surfaces
    assert panel.pick_button.isEnabled()
    gr_items = panel.columns[0].plots[0].listDataItems()
    assert gr_items and gr_items[0].xData is not None
    low, high = panel.columns[0].plots[0].getViewBox().viewRange()[1]
    assert low < 1500 < high  # TVDSS range around the reservoir


def test_flattening_and_datum_controls(window):
    panel = window.correlation
    panel.set_wells(window.wells, ("AB1", "AB4"))
    panel.flatten_combo.setCurrentText("Top Reservoir")
    assert window.project.section.flatten_on == "Top Reservoir"
    assert all(c.info.flattened for c in panel.columns)
    line_ys = [item.yData for item in panel.gaps[0].listDataItems() if abs(item.yData[0]) < 1e-6]
    assert line_ys and abs(line_ys[0][1]) < 1e-6  # flattened surface is level at 0
    panel.datum_combo.setCurrentText("MD")
    assert window.project.section.datum == "MD"
    assert "MD relative to Top Reservoir" in panel.columns[0].plots[0].getAxis("left").labelText
    panel.template_combo.setCurrentText(BUILTIN_TEMPLATES[1].name)
    assert all(len(c.plots) == 1 for c in panel.columns)
    panel.flatten_combo.setCurrentText(corr.NO_FLATTEN)
    assert window.project.section.flatten_on is None


def test_picking_and_deleting(window, qtbot):
    panel = window.correlation
    panel.set_wells(window.wells, ("AB1", "AB2"))
    column = panel.columns[0]
    panel.surface_combo.setCurrentText("")
    assert "Type or choose" in panel.pick_at(column, 1500.0)
    panel.surface_combo.setCurrentText("OWC")
    message = panel.pick_at(column, 1600.0)  # TVDSS 1600 in a vertical well
    kb = window.wells.kb_at(column.info.position)
    assert message.startswith("Picked OWC in AB-01")
    pick = next(t for t in window.wells.tops["AB1"] if t.surface == "OWC")
    assert pick.md == pytest.approx(1600.0 + kb)
    assert "OWC" in [panel.flatten_combo.itemText(i) for i in range(panel.flatten_combo.count())]
    assert panel.pick_at(panel.columns[0], 0.0, delete=True) == "Removed OWC from AB-01"
    assert panel.pick_at(panel.columns[0], 0.0, delete=True) is None
    panel.datum_combo.setCurrentText("MD")
    assert "no measured depth" in panel.pick_at(panel.columns[0], -50.0)

    # A real click in pick mode, on the first track of the second well.
    panel.pick_button.setChecked(True)
    plot = panel.columns[1].plots[0]
    box = plot.getViewBox()
    scene_pos = box.mapViewToScene(box.viewRect().center())

    class Click:
        def button(self):
            return Qt.MouseButton.LeftButton

        def scenePos(self):
            return scene_pos

        def modifiers(self):
            return Qt.KeyboardModifier.NoModifier

        def accept(self):
            self.accepted = True

    click = Click()
    panel._on_click(click)
    assert click.accepted and any(t.surface == "OWC" for t in window.wells.tops["AB2"])
    assert "Picked OWC in AB-02" in panel.status.text()


def test_template_editor(qtbot, monkeypatch, window):
    dialog = TemplateDialog(BUILTIN_TEMPLATES[0])
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 3
    template = dialog.result_template()
    assert template.name == "Custom" and len(template.tracks) == 3
    assert template.tracks[2].curves[1].family is CurveFamily.RHOB
    dialog.add_track()
    combo = dialog.table.cellWidget(3, 1)
    combo.setCurrentIndex(combo.findData(CurveFamily.DT))
    assert dialog.table.cellWidget(3, 2).value() == 140.0  # default DT scale filled in
    dialog.table.cellWidget(3, 9).setValue(80)
    assert len(dialog.result_template().tracks) == 4
    dialog.table.cellWidget(3, 7).setChecked(True)  # log scale with a non-positive end
    dialog.table.cellWidget(3, 3).setValue(-1)
    with pytest.raises(ValueError, match="logarithmic"):
        dialog.result_template()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "logarithmic" in dialog.error.text()
    dialog.table.setCurrentCell(3, 0)
    dialog.remove_track()
    for _ in range(10):
        dialog.add_track()
    assert dialog.table.rowCount() == 8 and "at most 8" in dialog.error.text()

    panel = window.correlation
    panel.set_wells(window.wells, ("AB1",))
    custom = replace(BUILTIN_TEMPLATES[1], name="Mine")
    monkeypatch.setattr(corr, "ask_template", lambda template, parent: custom)
    panel.edit_template()
    assert window.project.section.template == custom
    assert panel.template_combo.currentText() == "Mine"


def test_wells_dialog_and_choose(window, qtbot, monkeypatch):
    dialog = SectionWellsDialog(window.wells, ("AB3", "AB1"))
    qtbot.addWidget(dialog)
    assert dialog.keys() == ("AB3", "AB1")
    assert dialog.list.item(0).text() == "AB-03"
    dialog.list.item(2).setCheckState(Qt.CheckState.Checked)
    assert len(dialog.keys()) == 3
    monkeypatch.setattr(corr, "MAX_SECTION_WELLS", 2)
    dialog.accept()
    assert "at most 2" in dialog.error.text()

    panel = window.correlation
    monkeypatch.setattr(corr, "ask_section_wells", lambda layer, keys, parent: ("AB5", "AB2"))
    panel.choose_wells()
    assert window.project.section.well_keys == ("AB5", "AB2")
    panel.clear_button.click()
    assert window.project.section is None and panel.columns == []


def test_map_ctrl_click_and_attribute_table(window, qtbot, monkeypatch):
    layer = window.wells
    x, y = layer.xy()[layer.find("AB-03")]
    monkeypatch.setattr(
        mw.QApplication, "keyboardModifiers", lambda: Qt.KeyboardModifier.ControlModifier
    )
    window.canvas.set_view_extent(layer.extent.buffered(0.2))
    window.on_map_clicked(x, y)
    assert window.project.section.well_keys == ("AB3",)
    window.on_map_clicked(x, y)  # toggles it off again
    assert window.project.section is None

    window.correlate_selected_wells()
    assert window.reported and "Select wells" in window.reported[0][1]
    window.open_attribute_table(layer.id)
    selection = window.attribute_table.view.selectionModel()
    model = window.attribute_table.model
    for row in (0, 3):
        selection.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    window.correlate_selected_wells()
    assert window.project.section.well_keys == ("AB1", "AB4")


def test_section_cleared_with_its_layer(window):
    window.correlation.set_wells(window.wells, ("AB1",))
    window.project.remove_layer(window.wells.id)
    assert window.project.section is None
    window.correlation.choose_wells()
    assert "Import well headers first" in window.correlation.status.text()


def test_data_text_is_plain_in_the_panel(window):
    project = window.project
    layer = window.wells
    project.set_pick(layer.id, "AB1", PAYLOAD, 1500.0)
    project.set_section(CorrelationSection(layer.id, ("AB1",), flatten_on=PAYLOAD))
    panel = window.correlation
    import pyqtgraph as pg

    labels = [
        child.toPlainText()
        for plot in panel.columns[0].plots
        for item in plot.items
        if isinstance(item, pg.InfiniteLine)
        for child in item.childItems()
        if isinstance(child, pg.InfLineLabel)
    ]
    assert PAYLOAD in labels  # literal text: neither markup nor a format string
    axis = panel.columns[0].plots[0].getAxis("left").labelText
    assert "<img" not in axis and "&lt;img" in axis
