"""Choose one or more layers from a multi-layer source (GeoPackage, KML)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)


class LayerPickerDialog(QDialog):
    def __init__(self, source_name: str, layers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Layers")
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.addItems(layers)
        self.list.selectAll()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{source_name} contains several layers:"))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [item.text() for item in self.list.selectedItems()]


def pick_layers(source_name: str, layers: list[str], parent: QWidget | None = None) -> list[str]:
    dialog = LayerPickerDialog(source_name, layers, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.selected()
    return []
