"""Recently opened projects, persisted in QSettings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

RECENT_KEY = "recent_projects"
MAX_RECENT = 10


class RecentProjects:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings()

    def paths(self) -> list[str]:
        value = self._settings.value(RECENT_KEY, [])
        if isinstance(value, str):  # QSettings returns a bare string for one-item lists
            value = [value]
        return [str(v) for v in (value or [])]

    def add(self, path: str | Path) -> None:
        entry = str(Path(path).resolve())
        paths = [entry, *(p for p in self.paths() if p != entry)][:MAX_RECENT]
        self._settings.setValue(RECENT_KEY, paths)

    def remove(self, path: str | Path) -> None:
        entry = str(Path(path).resolve())
        self._settings.setValue(RECENT_KEY, [p for p in self.paths() if p != entry])

    def clear(self) -> None:
        self._settings.setValue(RECENT_KEY, [])
