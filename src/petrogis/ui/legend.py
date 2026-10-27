"""Legend panel: each visible layer with the symbols its style draws.

It redraws from project events, like the layer panel. Labels come from data (category values)
and are set as plain item text, which Qt never interprets as markup.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget

from petrogis.core.legend import legend_entries
from petrogis.core.project import Project
from petrogis.render.pg.legend_icons import ICON_HEIGHT, ICON_WIDTH, legend_icon
from petrogis.ui.project_bridge import ProjectBridge


class LegendPanel(QTreeWidget):
    def __init__(
        self, project: Project, bridge: ProjectBridge, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setIconSize(QSize(ICON_WIDTH, ICON_HEIGHT))
        # Several events often arrive together (load, restyle, reorder): rebuild once after them.
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self.rebuild)
        for signal in (
            bridge.layer_added,
            bridge.layer_removed,
            bridge.layer_moved,
            bridge.layer_changed,
            bridge.cleared,
            bridge.crs_changed,
        ):
            signal.connect(self._pending.start)
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        for layer in self.project.layers:
            if not layer.visible:
                continue
            entries = legend_entries(layer)
            top = QTreeWidgetItem([layer.name])
            if len(entries) == 1 and not entries[0].label:
                top.setIcon(0, legend_icon(entries[0]))
            else:
                for entry in entries:
                    child = QTreeWidgetItem([entry.label])
                    child.setIcon(0, legend_icon(entry))
                    top.addChild(child)
            self.addTopLevelItem(top)
            top.setExpanded(True)

    def entry_labels(self) -> list[tuple[str, list[str]]]:
        """(layer name, entry labels) for each row, for tests."""
        rows = []
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            rows.append((top.text(0), [top.child(j).text(0) for j in range(top.childCount())]))
        return rows
