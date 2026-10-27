import pytest

from petrogis.core.crs import DEFAULT_PROJECT_CRS
from petrogis.core.layers import RasterLayer, VectorLayer
from petrogis.core.styles import RasterStyle
from petrogis.render.pg.items import RasterGraphics
from petrogis.ui import main_window as mw
from petrogis.ui.main_window import MainWindow, format_coord


@pytest.fixture
def window(qtbot):
    w = MainWindow()
    w.resize(1200, 800)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def no_dialogs(monkeypatch):
    errors = []
    monkeypatch.setattr(mw, "show_error", lambda *args, **kwargs: errors.append(args))
    return errors


def test_main_window_shell(window):
    menus = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert menus == ["File", "Layers", "Processing", "View", "Help"]
    assert window.layer_dock.isVisible()
    assert window.crs_label.text() == DEFAULT_PROJECT_CRS


def test_load_sample_layers(window, qtbot, sample_field, no_dialogs):
    window.load_raster(sample_field.structure_top)
    window.load_vector(sample_field.field_boundary)
    qtbot.waitUntil(lambda: len(window.project) == 2 and window.tasks.pending == 0, timeout=10000)
    assert no_dialogs == []

    kinds = {type(layer) for layer in window.project.layers}
    assert kinds == {RasterLayer, VectorLayer}
    for layer in window.project.layers:
        assert window.canvas.has_layer(layer.id)

    # The view covers the field and the raster was read at view resolution.
    raster = next(layer for layer in window.project.layers if isinstance(layer, RasterLayer))
    view = window.canvas.view_extent()
    boundary = next(layer for layer in window.project.layers if isinstance(layer, VectorLayer))
    assert view.intersection(boundary.extent) == boundary.extent
    graphics = window.canvas.graphics(raster.id)
    assert isinstance(graphics, RasterGraphics)
    qtbot.waitUntil(lambda: graphics.image.image is not None, timeout=5000)
    rows, cols = graphics.image.image.shape
    assert cols <= raster.info.width and rows <= raster.info.height

    window.project.set_style(raster.id, RasterStyle(colormap="turbo", invert=True))
    window.zoom_to_layer(boundary.id)
    window.project.remove_layer(raster.id)
    assert not window.canvas.has_layer(raster.id)


def test_load_error_is_reported(window, qtbot, tmp_path, no_dialogs):
    bad = tmp_path / "broken.geojson"
    bad.write_text("not json")
    window.load_vector(bad)
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=5000)
    assert len(no_dialogs) == 1
    assert "broken.geojson" in no_dialogs[0][2]
    assert len(window.project) == 0


def test_cursor_label(window):
    window.canvas.cursor_moved.emit(405123.456, 4475000.0)
    assert (
        window.coord_label.text() == f"X: {format_coord(405123.456)}  Y: {format_coord(4475000.0)}"
    )
    assert format_coord(4475012.346) == "4\N{THIN SPACE}475\N{THIN SPACE}012.35"
