import pytest
from PySide6.QtCore import QSettings

from petrogis.core.layers import RasterLayer, WellLayer
from petrogis.core.styles import RasterStyle
from petrogis.io.project_io import project_to_dict
from petrogis.ui import main_window as mw
from petrogis.ui.main_window import MainWindow
from petrogis.ui.recent import MAX_RECENT, RecentProjects


@pytest.fixture
def recent(tmp_path):
    return RecentProjects(QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat))


def make_window(qtbot, recent):
    window = MainWindow(recent=recent)
    window.resize(1200, 800)
    qtbot.addWidget(window)
    window.show()
    return window


def wait_idle(qtbot, window, n_layers=None):
    qtbot.waitUntil(
        lambda: window.tasks.pending == 0 and (n_layers is None or len(window.project) == n_layers),
        timeout=15000,
    )


def test_recent_projects_order_dedupe_and_limit(recent, tmp_path):
    for i in range(MAX_RECENT + 3):
        recent.add(tmp_path / f"p{i}.pgis")
    recent.add(tmp_path / "p5.pgis")
    paths = recent.paths()
    assert len(paths) == MAX_RECENT
    assert paths[0].endswith("p5.pgis") and paths.count(paths[0]) == 1
    recent.remove(tmp_path / "p5.pgis")
    assert not any(p.endswith("p5.pgis") for p in recent.paths())
    recent.clear()
    assert recent.paths() == []


def test_save_and_reopen_restores_identical_state(
    qtbot, recent, sample_field, tmp_path, monkeypatch
):
    warnings = []
    monkeypatch.setattr(mw, "show_warning", lambda *a, **k: warnings.append(a))
    window = make_window(qtbot, recent)
    window.load_raster(sample_field.structure_top)
    window.load_vector(sample_field.field_boundary)
    window.import_wells(sample_field.wells_csv)
    wait_idle(qtbot, window, 3)
    las = [p for p in sample_field.las_files if p.stem != "OFFSET_1"]
    window.import_las(las)
    wait_idle(qtbot, window)
    raster = next(layer for layer in window.project.layers if isinstance(layer, RasterLayer))
    window.project.set_style(raster.id, RasterStyle(colormap="magma", vmin=1500, vmax=1700))
    window.project.set_visible(raster.id, False)
    assert window.windowTitle().startswith("Untitled *")

    path = tmp_path / "demo.pgis"
    assert window.save_project_as(path)
    assert window.windowTitle().startswith("demo.pgis –")
    assert recent.paths()[0] == str(path.resolve())
    view = window.canvas.view_extent()
    expected = project_to_dict(window.project, path, view)

    second = make_window(qtbot, recent)
    second.open_project(path)
    wait_idle(qtbot, second, 3)
    assert warnings == []
    assert project_to_dict(second.project, path, view) == expected
    reopened = second.canvas.view_extent()
    assert reopened.intersection(view) is not None
    assert not second.project.dirty and "*" not in second.windowTitle()
    wells = next(layer for layer in second.project.layers if isinstance(layer, WellLayer))
    assert len(wells.log_sources) == 10
    raster2 = next(layer for layer in second.project.layers if isinstance(layer, RasterLayer))
    assert second.canvas.has_layer(raster2.id)
    assert not second.canvas.graphics(raster2.id).image.isVisible()

    second.project.rename_layer(wells.id, "Wells (renamed)")
    assert second.windowTitle().startswith("demo.pgis *")
    assert second.save_project()
    assert "*" not in second.windowTitle()


def test_unsaved_changes_prompt(qtbot, recent, sample_field, tmp_path, monkeypatch):
    window = make_window(qtbot, recent)
    window.load_vector(sample_field.field_boundary)
    wait_idle(qtbot, window, 1)

    monkeypatch.setattr(mw, "ask_save_changes", lambda parent: "cancel")
    window.new_project()
    assert len(window.project) == 1  # cancelled

    monkeypatch.setattr(mw, "ask_save_changes", lambda parent: "save")
    monkeypatch.setattr(
        window, "save_project_as", lambda path=None: window._write_project(tmp_path / "auto.pgis")
    )
    window.new_project()
    assert (tmp_path / "auto.pgis").exists()
    assert len(window.project) == 0 and window.project.path is None
    assert window.windowTitle().startswith("Untitled –")

    monkeypatch.setattr(mw, "ask_save_changes", lambda parent: "discard")
    window.load_vector(sample_field.field_boundary)
    wait_idle(qtbot, window, 1)
    window.new_project()
    assert len(window.project) == 0


def test_open_missing_project_reports_and_forgets_it(qtbot, recent, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(mw, "show_error", lambda *a, **k: errors.append(a))
    missing = tmp_path / "gone.pgis"
    recent.add(missing)
    window = make_window(qtbot, recent)
    window.open_project(missing)
    wait_idle(qtbot, window)
    assert errors and "not found" in errors[0][2]
    assert recent.paths() == []


def test_recent_menu_lists_projects(qtbot, recent, tmp_path):
    recent.add(tmp_path / "a.pgis")
    window = make_window(qtbot, recent)
    window._build_recent_menu()
    texts = [a.text() for a in window.recent_menu.actions() if not a.isSeparator()]
    assert texts == ["a.pgis", "Clear Recent"]


def test_highlight_works_after_new_project(qtbot, recent, sample_field):
    window = make_window(qtbot, recent)
    window.import_wells(sample_field.wells_csv)
    wait_idle(qtbot, window, 1)
    window.new_project()
    window.import_wells(sample_field.wells_csv)
    wait_idle(qtbot, window, 1)
    wells = window.project.layers[0]
    window.open_attribute_table(wells.id)
    window.attribute_table.select_position(0)
    points = window.canvas._highlight.points
    assert points.scene() is not None and points.data.size == 1
