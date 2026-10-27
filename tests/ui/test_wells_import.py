import pytest
from PySide6.QtWidgets import QDialogButtonBox

from petrogis.core.extent import Extent
from petrogis.core.layers import WellLayer
from petrogis.core.styles import WellStyle
from petrogis.io.wells_io import ColumnMapping, read_well_table
from petrogis.render.pg.well_items import WellGraphics
from petrogis.ui import main_window as mw
from petrogis.ui.dialogs.column_mapping import ColumnMappingDialog
from petrogis.ui.main_window import MainWindow


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


def _wait(qtbot, window, n_layers):
    qtbot.waitUntil(
        lambda: len(window.project) == n_layers and window.tasks.pending == 0, timeout=10000
    )


def test_import_sample_csv_renders_wells_and_labels(window, qtbot, sample_field):
    window.import_wells(sample_field.wells_csv)
    _wait(qtbot, window, 1)
    assert window.reported == []
    layer = window.project.layers[0]
    assert isinstance(layer, WellLayer) and len(layer.gdf) == 10
    graphics = window.canvas.graphics(layer.id)
    assert isinstance(graphics, WellGraphics)
    assert len(graphics.symbols.data) == 10

    window.canvas.set_view_extent(layer.extent.buffered(0.2))
    window.canvas.grab()  # force a paint
    assert 0 < graphics.labels.placement.size <= 10

    # Zoomed far out, every well collapses onto a few pixels and most labels are culled.
    cx, cy = layer.extent.center
    window.canvas.set_view_extent(Extent(cx - 5e6, cy - 5e6, cx + 5e6, cy + 5e6))
    window.canvas.grab()
    assert graphics.labels.placement.size < 3

    window.project.set_style(layer.id, WellStyle(show_labels=False, color="#ff0000"))
    assert not graphics.labels.isVisible()
    window.project.set_visible(layer.id, False)
    window.project.set_visible(layer.id, True)
    assert graphics.symbols.isVisible() and not graphics.labels.isVisible()


def test_import_unrecognised_headers_asks_for_mapping(window, qtbot, tmp_path, monkeypatch):
    path = tmp_path / "kuyular.csv"
    path.write_text("Kuyu,Sharq,Shimal\nAB-1,405000,4475000\n,1,2\n")
    asked = []

    def fake_ask(table, default_crs, parent):
        asked.append(table.columns)
        return ColumnMapping(name="Kuyu", x="Sharq", y="Shimal"), "EPSG:32639"

    monkeypatch.setattr(mw, "ask_column_mapping", fake_ask)
    window.import_wells(path)
    _wait(qtbot, window, 1)
    assert asked == [["Kuyu", "Sharq", "Shimal"]]
    assert window.reported and window.reported[0][0] == "warning"  # one row skipped


def test_cancelled_mapping_imports_nothing(window, qtbot, tmp_path, monkeypatch):
    path = tmp_path / "odd.csv"
    path.write_text("A,B,C\n1,2,3\n")
    monkeypatch.setattr(mw, "ask_column_mapping", lambda *a: None)
    window.import_wells(path)
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=5000)
    assert len(window.project) == 0


def test_mapping_dialog_validation(qtbot, tmp_path):
    path = tmp_path / "odd.csv"
    path.write_text("Kuyu,Sharq,Shimal\nAB-1,405000,4475000\n")
    dialog = ColumnMappingDialog(read_well_table(path), "EPSG:32639")
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    for field_name, column in (("name", "Kuyu"), ("x", "Sharq"), ("y", "Shimal")):
        combo = dialog.combos[field_name]
        combo.setCurrentIndex(combo.findData(column))
    assert ok.isEnabled()
    assert dialog.mapping() == ColumnMapping(name="Kuyu", x="Sharq", y="Shimal")
    dialog.combos["y"].setCurrentIndex(dialog.combos["y"].findData("Sharq"))
    assert not ok.isEnabled() and "once" in dialog.error.text()
    dialog.combos["y"].setCurrentIndex(dialog.combos["y"].findData("Shimal"))
    dialog.crs_edit.setText("nonsense")
    assert not ok.isEnabled()
    dialog.crs_edit.setText("EPSG:4326")
    assert ok.isEnabled() and dialog.source_crs() == "EPSG:4326"
