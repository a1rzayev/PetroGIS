import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _no_modal_prompts(monkeypatch, tmp_path):
    """Keep tests non-interactive and isolated from the user's settings.

    Closing a window with unsaved changes would otherwise open a modal "save changes?" box, and
    the recent-projects list would otherwise be written to the real QSettings.
    """
    from PySide6.QtCore import QSettings

    from petrogis.ui import correlation, log_viewer, main_window, processing, well_data
    from petrogis.ui.recent import RecentProjects

    monkeypatch.setattr(main_window, "ask_save_changes", lambda parent: "discard")
    monkeypatch.setattr(main_window, "ask_restore_autosave", lambda parent, path: False)
    monkeypatch.setattr(main_window, "default_autosave_dir", lambda: tmp_path / "autosave")
    # Dialog-backed prompts default to "cancel" so a test never blocks on a modal dialog.
    monkeypatch.setattr(main_window, "ask_crs", lambda parent, message, default: None)
    monkeypatch.setattr(main_window, "ask_repair_paths", lambda missing, parent: None)
    monkeypatch.setattr(main_window, "ask_delimited_columns", lambda table, crs, parent: None)
    monkeypatch.setattr(main_window, "choose_transformations", lambda pairs, parent: None)
    monkeypatch.setattr(well_data, "ask_survey_mapping", lambda table, parent: None)
    monkeypatch.setattr(well_data, "ask_tops_mapping", lambda table, parent: None)
    monkeypatch.setattr(well_data, "ask_tops_surface", lambda parent, surfaces: None)
    monkeypatch.setattr(well_data, "ask_tops_layer_path", lambda parent, suggested: None)
    monkeypatch.setattr(log_viewer, "ask_track_scales", lambda parent, scales, cutoff: None)
    monkeypatch.setattr(correlation, "ask_section_wells", lambda layer, keys, parent: None)
    monkeypatch.setattr(correlation, "ask_template", lambda template, parent: None)
    monkeypatch.setattr(processing, "default_history_path", lambda: tmp_path / "history.jsonl")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_window, "RecentProjects", lambda: RecentProjects(settings))


@pytest.fixture(scope="session")
def sample_field(tmp_path_factory: pytest.TempPathFactory):
    """The synthetic demo field, generated once per test session."""
    from petrogis.sample_data import generate

    return generate(tmp_path_factory.mktemp("sample_data"), seed=42)


@pytest.fixture(scope="session")
def sample_dir(sample_field) -> Path:
    return sample_field.out_dir
