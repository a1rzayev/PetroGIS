"""Re-emit core ``Project`` events as Qt signals for the UI."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from petrogis.core.project import EventKind, Project, ProjectEvent


class ProjectBridge(QObject):
    layer_added = Signal(str, int)  # layer id, index
    layer_removed = Signal(str)
    layer_moved = Signal(str, int)
    layer_changed = Signal(str, str)  # layer id, field ("name" | "visible" | "style")
    crs_changed = Signal(str)
    section_changed = Signal()
    cleared = Signal()

    def __init__(self, project: Project, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self._unsubscribe = project.subscribe(self._on_event)

    def detach(self) -> None:
        self._unsubscribe()

    def _on_event(self, event: ProjectEvent) -> None:
        match event.kind:
            case EventKind.LAYER_ADDED:
                self.layer_added.emit(event.layer_id, event.index)
            case EventKind.LAYER_REMOVED:
                self.layer_removed.emit(event.layer_id)
            case EventKind.LAYER_MOVED:
                self.layer_moved.emit(event.layer_id, event.index)
            case EventKind.LAYER_CHANGED:
                self.layer_changed.emit(event.layer_id, event.field)
            case EventKind.CRS_CHANGED:
                self.crs_changed.emit(self.project.crs)
            case EventKind.SECTION_CHANGED:
                self.section_changed.emit()
            case EventKind.CLEARED:
                self.cleared.emit()
