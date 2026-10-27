"""Autosave and crash recovery through the main window."""

import pytest

from petrogis.core.layers import WellLayer
from petrogis.io.project_io import autosave_path
from petrogis.ui import main_window as mw
from petrogis.ui.main_window import MainWindow


def make_window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def wait_idle(qtbot, window, n_layers=None):
    qtbot.waitUntil(
        lambda: window.tasks.pending == 0 and (n_layers is None or len(window.project) == n_layers),
        timeout=15000,
    )


@pytest.fixture
def window_with_wells(qtbot, sample_field):
    window = make_window(qtbot)
    window.import_wells(sample_field.wells_csv)
    wait_idle(qtbot, window, 1)
    return window


def test_autosave_skips_clean_and_empty_projects(qtbot):
    window = make_window(qtbot)
    assert window.autosave_now() is None
    assert window.autosave_timer.isActive()


def test_untitled_autosave_and_recovery(qtbot, window_with_wells, monkeypatch):
    target = window_with_wells.autosave_now()
    assert target == autosave_path(None, window_with_wells.autosave_dir) and target.exists()

    # Simulate a crash: the old window is gone, a new one starts and offers recovery.
    monkeypatch.setattr(mw, "ask_restore_autosave", lambda parent, path: True)
    restored = make_window(qtbot)
    restored.offer_untitled_recovery()
    wait_idle(qtbot, restored, 1)
    assert isinstance(restored.project.layers[0], WellLayer)
    assert restored.project.path is None and restored.project.dirty
    assert restored.windowTitle().startswith("Untitled *")


def test_declined_recovery_removes_autosave(qtbot, window_with_wells):
    target = window_with_wells.autosave_now()
    other = make_window(qtbot)  # conftest answers "no" to recovery
    other.offer_untitled_recovery()
    assert not target.exists()
    assert len(other.project) == 0


def test_saved_project_recovery_keeps_identity(qtbot, window_with_wells, tmp_path, monkeypatch):
    path = tmp_path / "field.pgis"
    assert window_with_wells.save_project_as(path)
    assert not autosave_path(None, window_with_wells.autosave_dir).exists()
    wells = window_with_wells.project.layers[0]
    window_with_wells.project.rename_layer(wells.id, "Renamed after save")
    sidecar = window_with_wells.autosave_now()
    assert sidecar == tmp_path / "field.pgis.autosave"

    monkeypatch.setattr(mw, "ask_restore_autosave", lambda parent, p: True)
    restored = make_window(qtbot)
    restored.open_project(path)
    wait_idle(qtbot, restored, 1)
    assert restored.project.layers[0].name == "Renamed after save"
    assert restored.project.path == str(path) and restored.project.dirty
    assert restored.save_project()
    assert not sidecar.exists()
    assert (tmp_path / "field.pgis.bak").exists()


def test_closing_discards_autosave(qtbot, window_with_wells):
    target = window_with_wells.autosave_now()
    window_with_wells.close()  # conftest answers "discard" to the save prompt
    assert not target.exists()


def test_autosave_failure_is_reported_not_raised(qtbot, window_with_wells, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    window_with_wells.autosave_dir = blocker / "autosave"
    assert window_with_wells.autosave_now() is None
    assert "Autosave failed" in window_with_wells.statusBar().currentMessage()
