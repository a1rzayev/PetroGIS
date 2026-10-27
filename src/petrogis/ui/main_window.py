"""Main application window: menus, docks, central map area and status bar."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTreeWidget,
    QWidget,
)

from petrogis import __version__
from petrogis.core.crs import DEFAULT_PROJECT_CRS


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PetroGIS")
        self.resize(1280, 800)

        # Placeholder until the map canvas lands in M3.
        central = QLabel("Map canvas")
        central.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(central)

        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderHidden(True)
        self.layer_dock = QDockWidget("Layers", self)
        self.layer_dock.setObjectName("LayersDock")
        self.layer_dock.setWidget(self.layer_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layer_dock)

        self._build_menus()
        self._build_status_bar()

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.layers_menu = bar.addMenu("&Layers")

        view_menu = bar.addMenu("&View")
        view_menu.addAction(self.layer_dock.toggleViewAction())

        help_menu = bar.addMenu("&Help")
        about_action = QAction("&About PetroGIS", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_status_bar(self) -> None:
        self.coord_label = QLabel("X: —  Y: —")
        self.crs_label = QLabel(DEFAULT_PROJECT_CRS)
        status = self.statusBar()
        status.addPermanentWidget(self.coord_label)
        status.addPermanentWidget(self.crs_label)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About PetroGIS",
            f"PetroGIS {__version__}\nDesktop GIS for petroleum engineering and reservoir geology.",
        )
