"""Style dialogs, the legend panel and styles flowing through the main window and project files."""

import geopandas as gpd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel
from shapely import Point, Polygon

from petrogis.core.layers import VectorLayer, WellLayer
from petrogis.core.project import Project
from petrogis.core.styles import Category, ClassBreak, LabelStyle, VectorStyle, WellStyle
from petrogis.io.project_io import load_project, save_project
from petrogis.ui import main_window as mw
from petrogis.ui import style_editors
from petrogis.ui.dialogs import style_vector, style_wells
from petrogis.ui.dialogs.style_vector import VectorStyleDialog
from petrogis.ui.dialogs.style_wells import WellStyleDialog
from petrogis.ui.legend import LegendPanel
from petrogis.ui.main_window import MainWindow
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.widgets import ColorButton, ExpressionEdit

PAYLOAD = "<b>X</b><img src='x.png'>"


def _layer():
    gdf = gpd.GeoDataFrame(
        {"zone": ["A", "B", "A", None], "depth": [1500.0, 1600.0, 1700.0, 1800.0]},
        geometry=[Polygon([(i, 0), (i + 1, 0), (i + 1, 1)]) for i in range(4)],
    )
    return VectorLayer(name="zones", source="z.gpkg", gdf=gdf)


def test_vector_dialog_categorized(qtbot):
    layer = _layer()
    dialog = VectorStyleDialog(layer.style, layer)
    qtbot.addWidget(dialog)
    assert not dialog.classes_box.isVisibleTo(dialog)
    dialog.renderer.setCurrentIndex(1)  # categorized
    assert dialog.classes_box.isVisibleTo(dialog)
    assert dialog.color_label.text() == "Other values"
    dialog.classify_by.set_text('"zone"')
    dialog.classify()
    assert [c.value for c in dialog.classes] == ["A", "B", None]
    dialog.table.item(0, 2).setText("Zone A")
    dialog.set_class_color(1, "#123456")
    dialog.table.selectRow(2)
    dialog._remove_selected()
    dialog.fill_opacity.setValue(40)
    dialog.show_labels.setChecked(True)
    dialog.label_expression.set_text("zone || ' ' || depth")
    style = dialog.result_style()
    assert style.renderer == "categorized" and style.classify_by == '"zone"'
    assert [(c.value, c.color, c.label) for c in style.categories] == [
        ("A", style.categories[0].color, "Zone A"),
        ("B", "#123456", ""),
    ]
    assert style.fill_opacity == 0.4
    assert style.labels == LabelStyle(True, "zone || ' ' || depth", 9.0, "#000000")


def test_vector_dialog_graduated_and_switching(qtbot):
    layer = _layer()
    dialog = VectorStyleDialog(layer.style, layer)
    qtbot.addWidget(dialog)
    dialog.renderer.setCurrentIndex(2)  # graduated
    dialog.classify_by.set_text("depth")
    dialog.class_count.setValue(3)
    dialog.ramp.setCurrentText("reds")
    dialog.classify()
    style = dialog.result_style()
    assert [(b.lower, b.upper) for b in style.breaks] == [(1500, 1600), (1600, 1700), (1700, 1800)]
    assert style.ramp == "reds" and style.method == "equal_interval"
    assert dialog.table.item(0, 1).text() == "1500 – 1600"
    dialog.renderer.setCurrentIndex(1)  # categories and classes don't mix
    assert dialog.classes == []
    dialog.renderer.setCurrentIndex(0)
    assert dialog.result_style().renderer == "single"


def test_vector_dialog_errors_keep_dialog_open(qtbot):
    layer = _layer()
    dialog = VectorStyleDialog(layer.style, layer)
    qtbot.addWidget(dialog)
    dialog.renderer.setCurrentIndex(1)
    dialog.classify()
    assert "Choose a field" in dialog.error_label.text()
    dialog.classify_by.set_text("nope")
    assert "not found" in dialog.classify_by.status.text()
    dialog.classify()
    assert "not found" in dialog.error_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.error_label.isVisibleTo(dialog)
    dialog.renderer.setCurrentIndex(2)
    dialog.classify_by.set_text("zone")
    dialog.classify()
    assert "number" in dialog.error_label.text()
    dialog.renderer.setCurrentIndex(0)
    dialog.show_labels.setChecked(True)
    with pytest.raises(ValueError, match="labels"):
        dialog.result_style()
    dialog.label_expression.set_text("concat(")
    with pytest.raises(ValueError, match="Labels"):
        dialog.result_style()
    empty = VectorLayer(name="e", source="e", gdf=gpd.GeoDataFrame({"a": []}, geometry=[]))
    dialog = VectorStyleDialog(empty.style, empty)
    qtbot.addWidget(dialog)
    dialog.renderer.setCurrentIndex(1)
    dialog.classify_by.set_text("a")
    dialog.classify()
    assert "no features" in dialog.error_label.text()


def test_vector_dialog_loads_existing_style(qtbot):
    layer = _layer()
    layer.style = VectorStyle(
        renderer="graduated",
        classify_by="depth",
        breaks=[ClassBreak(0, 1, "#000000", "low")],
        labels=LabelStyle(enabled=True, expression="zone"),
    )
    dialog = VectorStyleDialog(layer.style, layer)
    qtbot.addWidget(dialog)
    assert dialog.renderer_code == "graduated" and dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).text() == "low"
    assert dialog.result_style() == layer.style


def test_well_dialog(qtbot):
    gdf = gpd.GeoDataFrame({"name": ["A"], "uwi": ["U"], "Status": ["OIL"]}, geometry=[Point(0, 0)])
    layer = WellLayer(name="w", source="w.csv", gdf=gdf)
    dialog = WellStyleDialog(layer.style, layer)
    qtbot.addWidget(dialog)
    assert dialog.detected.text() == "Detected: Status"
    dialog.by_status.setChecked(False)
    dialog.status_field.setCurrentIndex(dialog.status_field.findData("Status"))
    dialog.label_expression.set_text("name || '/' || uwi")
    style = dialog.result_style()
    assert style.symbol_by_status is False and style.status_field == "Status"
    assert style.label_expression == "name || '/' || uwi"
    dialog.label_expression.set_text("(((")
    dialog.accept()
    assert dialog.error_label.isVisibleTo(dialog) and dialog.result() != QDialog.DialogCode.Accepted
    bare = WellLayer(name="w", source="w.csv", gdf=gdf[["name", "geometry"]])
    assert WellStyleDialog(bare.style, bare).detected.text() == "No status column found"


def test_style_editors_registry(qtbot, monkeypatch):
    layer = _layer()
    monkeypatch.setattr(
        style_vector.VectorStyleDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    assert isinstance(style_editors.edit_style(layer), VectorStyle)
    monkeypatch.setattr(
        style_vector.VectorStyleDialog, "exec", lambda self: QDialog.DialogCode.Rejected
    )
    assert style_editors.edit_style(layer) is None
    wells = WellLayer(
        name="w", source="w", gdf=gpd.GeoDataFrame({"name": ["A"]}, geometry=[Point(0, 0)])
    )
    monkeypatch.setattr(
        style_wells.WellStyleDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    assert isinstance(style_editors.edit_style(wells), WellStyle)


def test_widgets(qtbot):
    button = ColorButton("#ffffff", "c")
    qtbot.addWidget(button)
    seen = []
    button.color_changed.connect(seen.append)
    button.set_color("#000000")
    assert seen == ["#000000"] and button.text() == "#000000"
    edit = ExpressionEdit(None, "")
    qtbot.addWidget(edit)
    assert edit.error() is None and not edit.status.isVisibleTo(edit)
    edit.set_text("1 +")
    assert "Syntax" in edit.error()
    assert edit.status.textFormat() == Qt.TextFormat.PlainText


def test_legend_panel_follows_project(qtbot):
    project = Project()
    bridge = ProjectBridge(project)
    legend = LegendPanel(project, bridge)
    qtbot.addWidget(legend)
    layer = _layer()
    project.add_layer(layer)
    qtbot.waitUntil(lambda: legend.topLevelItemCount() == 1)
    assert legend.entry_labels() == [("zones", [])]  # single symbol: icon on the layer row
    project.set_style(
        layer.id,
        VectorStyle(
            renderer="categorized",
            classify_by="zone",
            categories=[Category("A", "#ff0000"), Category(PAYLOAD, "#00ff00")],
        ),
    )
    qtbot.waitUntil(lambda: legend.entry_labels() == [("zones", ["A", PAYLOAD, "Other"])])
    project.set_visible(layer.id, False)
    qtbot.waitUntil(lambda: legend.topLevelItemCount() == 0)


def test_main_window_legend_and_style_errors(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.legend_dock.isVisible()
    layer = _layer()
    window.project.add_layer(layer)
    broken = VectorStyle(renderer="categorized", classify_by='"zone"', categories=[])
    gdf = layer.gdf.drop(columns=["zone"])
    layer.gdf = gdf  # the field disappears from the source
    monkeypatch.setattr(mw, "edit_style", lambda lyr, parent: broken)
    window.edit_layer_style(layer.id)
    assert "Style not fully applied" in window.statusBar().currentMessage()


def test_styles_survive_project_round_trip(tmp_path, sample_field):
    from petrogis.io.vector_io import load_vector_layer

    project = Project()
    layer = load_vector_layer(sample_field.field_boundary, project.crs)
    layer.style = VectorStyle(
        renderer="categorized",
        classify_by="name",
        categories=[Category("Absheron", "#ff0000", "Field")],
        fill_opacity=0.3,
        labels=LabelStyle(enabled=True, expression="upper(name)"),
    )
    project.add_layer(layer)
    path = save_project(project, tmp_path / "p.pgis")
    loaded = load_project(path)
    assert loaded.warnings == []
    assert loaded.project.layers[0].style == layer.style


def test_invalid_style_in_project_keeps_layer(tmp_path, sample_field):
    import json

    from petrogis.io.vector_io import load_vector_layer

    project = Project()
    project.add_layer(load_vector_layer(sample_field.field_boundary, project.crs))
    path = save_project(project, tmp_path / "p.pgis")
    data = json.loads(path.read_text())
    data["layers"][0]["style"]["classify_by"] = "__import__('os').system('x')"
    data["layers"][0]["style"]["renderer"] = "categorized"
    path.write_text(json.dumps(data))
    loaded = load_project(path)
    assert len(loaded.project.layers) == 1
    assert loaded.project.layers[0].style == VectorStyle()
    assert any("invalid style replaced" in w for w in loaded.warnings)


def test_legend_labels_are_plain_text(qtbot):
    project = Project()
    legend = LegendPanel(project, ProjectBridge(project))
    qtbot.addWidget(legend)
    gdf = gpd.GeoDataFrame({"name": [PAYLOAD]}, geometry=[Point(0, 0)])
    project.add_layer(VectorLayer(name=PAYLOAD, source="x", gdf=gdf))
    legend.rebuild()
    assert legend.topLevelItem(0).text(0) == PAYLOAD  # item text, never rendered as HTML
    assert not [lbl for lbl in legend.findChildren(QLabel) if "<img" in lbl.text()]
