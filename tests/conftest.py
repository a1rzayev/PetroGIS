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

    from petrogis.ui import main_window
    from petrogis.ui.recent import RecentProjects

    monkeypatch.setattr(main_window, "ask_save_changes", lambda parent: "discard")
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
