"""Main application window: menus, toolbar, layer dock, map canvas and status bar."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from petrogis import __version__
from petrogis.analysis.processing import algorithms
from petrogis.core.attributes import IdentifyHit, identify
from petrogis.core.crs import DEFAULT_PROJECT_CRS, crs_equal, crs_label, lonlat_area, normalize_crs
from petrogis.core.extent import Extent
from petrogis.core.layers import FeatureLayer, Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.core.logs import match_logs
from petrogis.core.project import Project
from petrogis.core.styles import VECTOR_PALETTE
from petrogis.io.delimited_io import (
    DELIMITED_FILE_FILTER,
    DelimitedImportResult,
    build_delimited_layer,
    read_delimited_table,
)
from petrogis.io.errors import DataReadError, MissingCRSError
from petrogis.io.las_io import LAS_FILE_FILTER, LasBatch, read_las_files
from petrogis.io.project_io import (
    PROJECT_FILE_FILTER,
    LoadContext,
    LoadedProject,
    MissingLayer,
    autosave_path,
    find_recovery,
    load_layer_entry,
    load_project,
    reload_layers,
    repaired_entry,
    save_project,
    write_project_file,
)
from petrogis.io.raster_io import RASTER_FILE_FILTER, load_raster_layer, sample_raster
from petrogis.io.vector_io import VECTOR_FILE_FILTER, list_layers, load_vector_layer
from petrogis.io.wells_io import (
    WELL_TABLE_FILE_FILTER,
    ColumnMapping,
    WellImportResult,
    WellTable,
    build_well_layer,
    list_sheets,
    read_well_table,
)
from petrogis.render.base import MouseMode
from petrogis.render.pg.canvas import PgMapCanvas
from petrogis.ui.attribute_table import AttributeTable
from petrogis.ui.correlation import CorrelationPanel
from petrogis.ui.dialogs.column_mapping import ask_column_mapping
from petrogis.ui.dialogs.crs_picker import pick_crs
from petrogis.ui.dialogs.delimited_text import ask_delimited_columns
from petrogis.ui.dialogs.layer_picker import pick_layers
from petrogis.ui.dialogs.repair_paths import ask_repair_paths
from petrogis.ui.dialogs.transformations import TransformationPair, choose_transformations
from petrogis.ui.errors import show_error, show_warning
from petrogis.ui.identify import IdentifyDialog
from petrogis.ui.layer_panel import LayerPanel
from petrogis.ui.legend import LegendPanel
from petrogis.ui.log_viewer import LogViewer
from petrogis.ui.map_controller import MapController
from petrogis.ui.processing import HistoryDialog, open_algorithm
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.recent import RecentProjects
from petrogis.ui.style_editors import edit_style
from petrogis.ui.text import as_rich_text
from petrogis.ui.well_data import WellDataImporter
from petrogis.ui.workers import TaskRunner

log = logging.getLogger(__name__)

MULTI_LAYER_SUFFIXES = {".gpkg", ".kml"}


AUTOSAVE_INTERVAL_MS = 120_000


def ask_crs(parent: QWidget, message: str, default: str) -> str | None:
    """Ask the user for a CRS with the searchable picker. Returns ``"EPSG:..."`` or ``None``."""
    return pick_crs(parent, default, message)


def default_autosave_dir() -> Path:
    """Where autosaves of untitled projects go (saved projects autosave next to their file)."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "autosave"


def ask_restore_autosave(parent: QWidget, autosave: Path) -> bool:
    """Ask whether to restore work found in an autosave file."""
    answer = QMessageBox.question(
        parent,
        "Recover Unsaved Work",
        as_rich_text(
            f"PetroGIS found unsaved changes from a previous session ({autosave.name}).\n"
            "Restore them?"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    return answer == QMessageBox.StandardButton.Yes


def ask_save_changes(parent: QWidget) -> str:
    """Ask whether to save unsaved changes. Returns "save", "discard" or "cancel"."""
    buttons = (
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    answer = QMessageBox.question(
        parent,
        "Unsaved Changes",
        "The project has unsaved changes. Save them first?",
        buttons,
        QMessageBox.StandardButton.Save,
    )
    return {
        QMessageBox.StandardButton.Save: "save",
        QMessageBox.StandardButton.Discard: "discard",
    }.get(answer, "cancel")


def format_coord(value: float) -> str:
    """Format a coordinate with thin-space thousands grouping and two decimals."""
    return f"{value:,.2f}".replace(",", "\N{THIN SPACE}")


class MainWindow(QMainWindow):
    def __init__(
        self,
        project: Project | None = None,
        parent: QWidget | None = None,
        recent: RecentProjects | None = None,
    ) -> None:
        super().__init__(parent)
        self.resize(1280, 800)
        self.recent = recent or RecentProjects()

        self.project = project or Project()
        self.bridge = ProjectBridge(self.project, self)
        self.tasks = TaskRunner(self)

        self.canvas = PgMapCanvas()
        self.setCentralWidget(self.canvas)
        self.map_controller = MapController(
            self.project, self.bridge, self.canvas, self.tasks, self
        )

        self.layer_panel = LayerPanel(self.project, self.bridge)
        self.layer_dock = QDockWidget("Layers", self)
        self.layer_dock.setObjectName("LayersDock")
        self.layer_dock.setWidget(self.layer_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layer_dock)
        self.layer_panel.zoom_to_layer_requested.connect(self.zoom_to_layer)
        self.layer_panel.style_requested.connect(self.edit_layer_style)
        self.layer_panel.remove_requested.connect(self.remove_layer)

        self.legend = LegendPanel(self.project, self.bridge)
        self.legend_dock = QDockWidget("Legend", self)
        self.legend_dock.setObjectName("LegendDock")
        self.legend_dock.setWidget(self.legend)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.legend_dock)

        self.log_viewer = LogViewer()
        self.log_dock = QDockWidget("Logs", self)
        self.log_dock.setObjectName("LogsDock")
        self.log_dock.setWidget(self.log_viewer)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.log_dock)
        self.log_dock.hide()
        self.canvas.clicked.connect(self.on_map_clicked)

        self.attribute_table = AttributeTable()
        self.attribute_dock = QDockWidget("Attributes", self)
        self.attribute_dock.setObjectName("AttributesDock")
        self.attribute_dock.setWidget(self.attribute_table)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.attribute_dock)
        self.attribute_dock.hide()

        self.correlation = CorrelationPanel(self.project, self.bridge)
        self.correlation_dock = QDockWidget("Correlation", self)
        self.correlation_dock.setObjectName("CorrelationDock")
        self.correlation_dock.setWidget(self.correlation)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.correlation_dock)
        self.tabifyDockWidget(self.attribute_dock, self.correlation_dock)
        self.correlation_dock.hide()
        self.bridge.section_changed.connect(self._on_section_changed)
        self.attribute_table.selection_changed.connect(self._highlight_table_selection)
        self.attribute_table.zoom_requested.connect(self._zoom_to_table_rows)
        self.layer_panel.attribute_table_requested.connect(self.open_attribute_table)
        self.bridge.layer_removed.connect(self._on_layer_removed)
        self.bridge.layer_changed.connect(self._on_layer_changed)
        self.bridge.cleared.connect(lambda: self._on_layer_removed(self.attribute_table.layer_id))

        self.map_controller.raster_read_failed.connect(
            lambda name, message: self.statusBar().showMessage(
                f"Could not draw raster {name}: {message}", 8000
            )
        )
        # Bumped whenever the project is replaced, so background results that belong to the
        # previous project are dropped instead of leaking into the new one.
        self._generation = 0

        self.autosave_dir = default_autosave_dir()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self.autosave_now)
        self.autosave_timer.start()

        self.identify_dialog = IdentifyDialog(self)
        self.identify_dialog.hit_selected.connect(self._highlight_hit)
        self._identify_token = 0

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self.canvas.cursor_moved.connect(self._show_cursor)
        for signal in (
            self.bridge.layer_added,
            self.bridge.layer_removed,
            self.bridge.layer_moved,
            self.bridge.layer_changed,
            self.bridge.crs_changed,
            self.bridge.cleared,
        ):
            signal.connect(self._update_title)
        self.bridge.cleared.connect(self.log_viewer.clear)
        self._update_title()
        self.bridge.crs_changed.connect(self._show_crs)

    # --- construction ------------------------------------------------------------------------
    def _action(
        self, text: str, slot: object, shortcut: QKeySequence | str | None = None
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.exit_action = self._action("E&xit", self.close, QKeySequence.StandardKey.Quit)
        self.new_project_action = self._action(
            "&New Project", self.new_project, QKeySequence.StandardKey.New
        )
        self.open_project_action = self._action(
            "&Open Project…", lambda: self.open_project(), QKeySequence.StandardKey.Open
        )
        self.save_project_action = self._action(
            "&Save Project", self.save_project, QKeySequence.StandardKey.Save
        )
        self.save_project_as_action = self._action(
            "Save Project &As…", lambda: self.save_project_as(), QKeySequence.StandardKey.SaveAs
        )
        self.add_vector_action = self._action(
            "Add &Vector Layer…", self.open_vector_dialog, "Ctrl+Shift+V"
        )
        self.add_raster_action = self._action(
            "Add &Raster Layer…", self.open_raster_dialog, "Ctrl+Shift+R"
        )
        self.import_wells_action = self._action(
            "Import &Wells…", self.open_wells_dialog, "Ctrl+Shift+W"
        )
        self.import_las_action = self._action(
            "Import &LAS Logs…", self.open_las_dialog, "Ctrl+Shift+G"
        )
        self.well_data = WellDataImporter(self)
        self.import_surveys_action = self._action(
            "Import Deviation &Surveys…", self.well_data.open_surveys_dialog
        )
        self.import_tops_action = self._action(
            "Import Formation &Tops…", self.well_data.open_tops_dialog
        )
        self.tops_layer_action = self._action(
            "Create Tops &Map Layer…", self.well_data.create_tops_layer
        )
        self.correlate_action = self._action(
            "&Correlate Selected Wells", self.correlate_selected_wells, "Ctrl+Shift+K"
        )
        self.add_delimited_action = self._action(
            "Add &Delimited Text Layer…", self.open_delimited_dialog, "Ctrl+Shift+T"
        )
        self.project_crs_action = self._action("Project &CRS…", self.choose_project_crs)
        self.transformations_action = self._action(
            "&Datum Transformations…", self.edit_transformations
        )
        self.remove_layer_action = self._action(
            "&Remove Layer", lambda: self._with_current(self.remove_layer)
        )
        self.style_action = self._action(
            "Layer &Style…", lambda: self._with_current(self.edit_layer_style)
        )
        self.zoom_layer_action = self._action(
            "Zoom to &Layer", lambda: self._with_current(self.zoom_to_layer), "Ctrl+Shift+L"
        )
        self.zoom_full_action = self._action(
            "Zoom to &Full Extent", self.zoom_to_full_extent, "Ctrl+Shift+F"
        )
        self.zoom_in_action = self._action(
            "Zoom &In", lambda: self.canvas.zoom_by(2.0), QKeySequence.StandardKey.ZoomIn
        )
        self.zoom_out_action = self._action(
            "Zoom &Out", lambda: self.canvas.zoom_by(0.5), QKeySequence.StandardKey.ZoomOut
        )

        self.pan_action = self._action(
            "&Pan", lambda: self.canvas.set_mouse_mode(MouseMode.PAN), "P"
        )
        self.zoom_box_action = self._action(
            "&Zoom Box", lambda: self.canvas.set_mouse_mode(MouseMode.ZOOM_BOX), "Z"
        )
        self.identify_action = self._action(
            "&Identify", lambda: self.canvas.set_mouse_mode(MouseMode.IDENTIFY), "I"
        )
        self.attribute_table_action = self._action(
            "Open &Attribute Table", lambda: self._with_current(self.open_attribute_table), "F6"
        )
        self.clear_highlight_action = self._action(
            "Clear Selection", lambda: self.canvas.set_highlight(None), "Esc"
        )
        mode_group = QActionGroup(self)
        for action in (self.pan_action, self.zoom_box_action, self.identify_action):
            action.setCheckable(True)
            mode_group.addAction(action)
        self.pan_action.setChecked(True)
        self.about_action = self._action("&About PetroGIS", self._show_about)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addActions([self.new_project_action, self.open_project_action])
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._build_recent_menu)
        file_menu.addSeparator()
        file_menu.addActions([self.save_project_action, self.save_project_as_action])
        file_menu.addSeparator()
        file_menu.addActions([self.project_crs_action, self.transformations_action])
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        layers_menu = bar.addMenu("&Layers")
        layers_menu.addActions(
            [
                self.add_vector_action,
                self.add_raster_action,
                self.add_delimited_action,
                self.import_wells_action,
                self.import_las_action,
                self.import_surveys_action,
                self.import_tops_action,
                self.tops_layer_action,
                self.correlate_action,
            ]
        )
        layers_menu.addSeparator()
        layers_menu.addActions(
            [
                self.style_action,
                self.attribute_table_action,
                self.zoom_layer_action,
                self.remove_layer_action,
            ]
        )

        self.algorithm_dialogs: dict[str, Any] = {}
        processing_menu = bar.addMenu("&Processing")
        groups: dict[str, Any] = {}
        for algorithm in algorithms():
            if algorithm.group not in groups:
                groups[algorithm.group] = processing_menu.addMenu(algorithm.group)
            action = groups[algorithm.group].addAction(algorithm.name)
            action.setStatusTip(algorithm.description)
            action.triggered.connect(lambda _=False, a=algorithm: open_algorithm(self, a))
        processing_menu.addSeparator()
        self.history_action = processing_menu.addAction("&History…", self.show_processing_history)

        view_menu = bar.addMenu("&View")
        view_menu.addActions([self.pan_action, self.zoom_box_action, self.identify_action])
        view_menu.addAction(self.clear_highlight_action)
        view_menu.addSeparator()
        view_menu.addActions(
            [
                self.zoom_in_action,
                self.zoom_out_action,
                self.zoom_full_action,
                self.zoom_layer_action,
            ]
        )
        view_menu.addSeparator()
        view_menu.addAction(self.layer_dock.toggleViewAction())
        view_menu.addAction(self.legend_dock.toggleViewAction())
        view_menu.addAction(self.log_dock.toggleViewAction())
        view_menu.addAction(self.attribute_dock.toggleViewAction())
        view_menu.addAction(self.correlation_dock.toggleViewAction())

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Map", self)
        toolbar.setObjectName("MapToolbar")
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.addActions(
            [
                self.add_vector_action,
                self.add_raster_action,
                self.import_wells_action,
                self.import_las_action,
            ]
        )
        toolbar.addSeparator()
        toolbar.addActions([self.pan_action, self.zoom_box_action, self.identify_action])
        toolbar.addSeparator()
        toolbar.addActions([self.zoom_full_action, self.zoom_layer_action])
        self.addToolBar(toolbar)

    def _build_status_bar(self) -> None:
        self.coord_label = QLabel("X: —  Y: —")
        self.coord_label.setMinimumWidth(260)
        self.crs_label = QToolButton()
        self.crs_label.setAutoRaise(True)
        self.crs_label.clicked.connect(self.choose_project_crs)
        status = self.statusBar()
        status.addPermanentWidget(self.coord_label)
        status.addPermanentWidget(self.crs_label)
        self._show_crs(self.project.crs)

    def _show_cursor(self, x: float, y: float) -> None:
        self.coord_label.setText(f"X: {format_coord(x)}  Y: {format_coord(y)}")

    def _show_crs(self, crs: str) -> None:
        self.crs_label.setText(crs)
        self.crs_label.setToolTip(
            as_rich_text(f"{crs_label(crs)}\nClick to change the project CRS")
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About PetroGIS",
            f"PetroGIS {__version__}\nDesktop GIS for petroleum engineering and reservoir geology.",
        )

    # --- loading -----------------------------------------------------------------------------
    def open_vector_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Vector Layer", "", VECTOR_FILE_FILTER)
        for path in paths:
            self.add_vector_file(path)

    def open_raster_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Raster Layer", "", RASTER_FILE_FILTER)
        for path in paths:
            self.load_raster(path)

    def open_wells_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import Well Headers", "", WELL_TABLE_FILE_FILTER
        )
        for path in paths:
            self.import_wells(path)

    def import_wells(self, path: str | Path, sheet: str | None = None) -> None:
        """Read a well-header table, confirm the column mapping if needed, then build the layer."""
        path = Path(path)
        if sheet is None and path.suffix.lower() in {".xlsx", ".xlsm"}:
            try:
                sheets = list_sheets(path)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                show_error(self, "Cannot import wells", str(exc), exc)
                return
            if len(sheets) > 1:
                sheet, ok = QInputDialog.getItem(
                    self,
                    "Select Sheet",
                    as_rich_text(f"{path.name} has several sheets:"),
                    sheets,
                    0,
                    False,
                )
                if not ok:
                    return
        self.statusBar().showMessage(f"Reading {path.name}…")
        self.tasks.submit(
            read_well_table,
            path,
            sheet,
            on_result=self._current(self._on_well_table),
            on_error=self._current(lambda exc: self._on_load_failed(path, exc)),
        )

    def _on_well_table(self, table: WellTable) -> None:
        self.statusBar().clearMessage()
        if table.mapping.is_complete:
            mapping: ColumnMapping = table.mapping
            source_crs = table.suggested_crs or self.project.crs
        else:
            answer = ask_column_mapping(table, self.project.crs, self)
            if answer is None:
                return
            mapping, source_crs = answer
        self.tasks.submit(
            build_well_layer,
            table,
            mapping,
            source_crs,
            self.project.crs,
            self._transformation_lookup(),
            on_result=self._current(self._on_wells_built),
            on_error=self._current(lambda exc: self._on_load_failed(table.path, exc)),
        )

    def _on_wells_built(self, result: WellImportResult) -> None:
        self._on_layer_loaded(result.layer)
        if result.warnings:
            show_warning(
                self, f"Imported {result.layer.name} with warnings", "\n".join(result.warnings)
            )

    # --- delimited text ----------------------------------------------------------------------
    def open_delimited_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Delimited Text Layer", "", DELIMITED_FILE_FILTER
        )
        for path in paths:
            self.import_delimited(path)

    def import_delimited(self, path: str | Path) -> None:
        """Read a CSV/TXT table, confirm its X/Y columns and CRS, then build a point layer."""
        path = Path(path)
        self.statusBar().showMessage(f"Reading {path.name}…")
        self.tasks.submit(
            read_delimited_table,
            path,
            on_result=self._current(self._on_delimited_table),
            on_error=self._current(lambda exc: self._on_load_failed(path, exc)),
        )

    def _on_delimited_table(self, table: WellTable) -> None:
        self.statusBar().clearMessage()
        answer = ask_delimited_columns(table, self.project.crs, self)
        if answer is None:
            return
        x_column, y_column, source_crs = answer
        self.tasks.submit(
            build_delimited_layer,
            table,
            x_column,
            y_column,
            source_crs,
            self.project.crs,
            self._transformation_lookup(),
            on_result=self._current(self._on_delimited_built),
            on_error=self._current(lambda exc: self._on_load_failed(table.path, exc)),
        )

    def _on_delimited_built(self, result: DelimitedImportResult) -> None:
        self._on_layer_loaded(result.layer)
        if result.warnings:
            show_warning(
                self, f"Imported {result.layer.name} with warnings", "\n".join(result.warnings)
            )

    # --- project CRS and datum transformations -------------------------------------------------
    def _project_base(self) -> Path:
        return Path(self.project.path).parent if self.project.path else Path.cwd()

    def choose_project_crs(self) -> None:
        crs = ask_crs(self, "Choose the project CRS. Every layer is shown in it.", self.project.crs)
        if crs:
            self.set_project_crs(crs)

    def set_project_crs(self, crs: str) -> None:
        """Change the project CRS, re-reading every layer in it (in the background)."""
        crs = normalize_crs(crs)
        if crs_equal(crs, self.project.crs):
            return
        # Checked before the empty-project shortcut: a layer still loading would otherwise arrive
        # in the old CRS and be added to a project that is now in the new one.
        if self.tasks.pending:
            self.statusBar().showMessage("Wait for loading to finish, then try again", 5000)
            return
        if len(self.project) == 0:
            self.project.set_crs(crs)
            return
        self._reload_all(crs, f"Reprojecting {len(self.project)} layer(s) to {crs}…")

    def _reload_all(self, crs: str, message: str) -> None:
        """Re-read every layer into ``crs`` and swap them in atomically (all or nothing)."""
        if self.tasks.pending:
            self.statusBar().showMessage("Wait for loading to finish, then try again", 5000)
            return
        old_crs, old_view = self.project.crs, self.canvas.view_extent()
        self.statusBar().showMessage(message)
        self._generation += 1
        self.tasks.submit(
            reload_layers,
            list(self.project.layers),
            crs,
            self.project.transformations,
            self._project_base(),
            on_result=self._current(
                lambda result: self._on_reloaded(crs, result, old_crs, old_view)
            ),
            on_error=self._current(
                lambda exc: show_error(self, "Cannot change projection", str(exc), exc)
            ),
        )

    def _on_reloaded(
        self, crs: str, result: tuple[list[Layer], list[str]], old_crs: str, old_view: Extent
    ) -> None:
        layers, warnings = result
        self.project.replace_contents(crs, layers)
        self.statusBar().showMessage(f"Project CRS is now {crs}", 5000)
        view = _transform_extent(old_view, old_crs, crs)
        if view is not None:
            self.canvas.set_view_extent(view)
        else:
            self.zoom_to_full_extent()
        if warnings:
            show_warning(self, "Reprojected with warnings", "\n".join(warnings))

    def transformation_pairs(self) -> list[TransformationPair]:
        """Each distinct (layer source CRS → project CRS) pair, with a lon/lat area of interest."""
        target = self.project.crs
        areas: dict[str, Extent] = {}
        for layer in self.project.layers:
            source = getattr(layer, "source_crs", None)
            if not source or crs_equal(source, target) or layer.extent is None:
                continue
            key = normalize_crs(source)
            areas[key] = areas[key].union(layer.extent) if key in areas else layer.extent
        return [
            TransformationPair(
                source,
                target,
                lonlat_area(target, extent.as_tuple()),
                self.project.transformation_for(source, target),
            )
            for source, extent in sorted(areas.items())
        ]

    def edit_transformations(self) -> None:
        pairs = self.transformation_pairs()
        choices = choose_transformations(pairs, self)
        if choices is None:
            return
        changed = False
        for (source, target), pipeline in choices.items():
            if self.project.transformation_for(source, target) != pipeline:
                self.project.set_transformation(source, target, pipeline)
                changed = True
        if changed:
            self._reload_all(self.project.crs, "Applying datum transformations…")

    # --- broken paths --------------------------------------------------------------------------
    def repair_missing_layers(self, missing: list[MissingLayer], positions: dict[str, int]) -> None:
        """Ask for the new location of each missing source, then load the repaired layers."""
        fixed = ask_repair_paths(missing, self)
        if not fixed:
            return
        entries = [
            (item.position, repaired_entry(item.entry, fixed[item.position]))
            for item in missing
            if item.position in fixed
        ]
        context = LoadContext(
            self._project_base(), self.project.crs, [], self.project.transformations
        )
        self.tasks.submit(
            _load_repaired,
            entries,
            context,
            on_result=self._current(lambda loaded: self._on_repaired(loaded, positions)),
        )

    def _on_repaired(
        self, loaded: tuple[list[tuple[int, Layer]], list[str]], positions: dict[str, int]
    ) -> None:
        layers, problems = loaded
        for position, layer in sorted(layers, key=lambda item: item[0]):
            index = sum(
                1 for other in self.project.layers if positions.get(other.id, -1) < position
            )
            self.project.add_layer(layer, index=index)
            positions[layer.id] = position
        if layers:
            self.statusBar().showMessage(f"Repaired {len(layers)} layer(s)", 5000)
        if problems:
            show_warning(self, "Some layers could not be repaired", "\n".join(problems))

    # --- logs --------------------------------------------------------------------------------
    def _target_well_layer(self) -> WellLayer | None:
        """The selected well layer, otherwise the topmost well layer in the project."""
        current = self.layer_panel.current_layer_id()
        if current is not None and isinstance(layer := self.project.get(current), WellLayer):
            return layer
        return next((lyr for lyr in self.project.layers if isinstance(lyr, WellLayer)), None)

    def open_las_dialog(self) -> None:
        if self._target_well_layer() is None:
            show_warning(self, "Import LAS Logs", "Import well headers first, then their logs.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Import LAS Logs", "", LAS_FILE_FILTER)
        if paths:
            self.import_las(paths)

    def import_las(self, paths: list[str | Path]) -> None:
        """Read LAS files in the background and attach them to the target well layer."""
        target = self._target_well_layer()
        if target is None:
            show_warning(self, "Import LAS Logs", "Import well headers first, then their logs.")
            return
        layer_id = target.id
        self.statusBar().showMessage(f"Reading {len(paths)} LAS file(s)…")
        self.tasks.submit(
            read_las_files,
            list(paths),
            on_result=self._current(lambda batch: self._on_las_read(layer_id, batch)),
            on_error=self._current(lambda exc: self._on_load_failed(Path(paths[0]), exc)),
        )

    def _on_las_read(self, layer_id: str, batch: LasBatch) -> None:
        try:
            layer = self.project.get(layer_id)
        except KeyError:
            self.statusBar().showMessage("LAS import discarded: the well layer was removed", 5000)
            return
        if not isinstance(layer, WellLayer):
            log.error("LAS import target %s is not a well layer", layer_id)
            return
        match = match_logs(batch.logs, layer)
        added = self.project.attach_logs(layer_id, match.matched)
        summary = f"Attached {added} LAS file(s) to {len(match.matched)} well(s) in {layer.name}"
        self.statusBar().showMessage(summary, 5000)
        problems = [
            f"No matching well for {Path(log.source).name} (WELL = {log.well_name!r})"
            for log in match.unmatched
        ] + [f"{name}: {reason}" for name, reason in batch.failures]
        if problems:
            show_warning(self, "LAS import", summary + ".\n\n" + "\n".join(problems))

    def on_map_clicked(self, x: float, y: float) -> None:
        """Identify features in identify mode. Otherwise open the logs of a clicked well, or with
        Ctrl (Cmd on macOS) held, add it to or remove it from the correlation section."""
        if self.canvas.mouse_mode is MouseMode.IDENTIFY:
            self.identify_at(x, y)
            return
        toggle = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        tolerance = 8.0 * self.canvas.map_units_per_pixel()
        for layer in self.project.layers:
            if not (isinstance(layer, WellLayer) and layer.visible):
                continue
            hits = layer.features_at(x, y, tolerance)
            if not hits:
                continue
            xy = layer.xy()[hits]
            nearest = hits[int(((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2).argmin())]
            if toggle:
                self.correlation.toggle_well(layer, layer.key_at(nearest))
            else:
                self.show_well_logs(layer, nearest)
            return

    def show_processing_history(self) -> HistoryDialog:
        dialog = HistoryDialog(self)
        dialog.show()
        return dialog

    def correlate_selected_wells(self) -> None:
        """Open the correlation panel on the wells selected in the attribute table."""
        layer_id = self.attribute_table.layer_id
        layer = self.project.get(layer_id) if layer_id is not None else None
        positions = self.attribute_table.selected_positions() if layer is not None else []
        if not isinstance(layer, WellLayer) or not len(positions):
            show_warning(
                self,
                "Correlate Selected Wells",
                "Select wells in the attribute table of a well layer first, or use Ctrl+click "
                "on the map.",
            )
            return
        self.correlation.set_wells(layer, tuple(layer.key_at(int(p)) for p in positions))

    def _on_section_changed(self) -> None:
        if self.project.section is not None and self.project.section.well_keys:
            self.correlation_dock.show()
            self.correlation_dock.raise_()

    def show_well_logs(self, layer: WellLayer, position: int, reveal: bool = True) -> None:
        row = layer.gdf.iloc[position]
        logs = layer.logs_for(position)
        uwi = "" if row["uwi"] is None or row["uwi"] != row["uwi"] else str(row["uwi"])
        self._shown_well = (layer.id, layer.key_at(position))
        self.log_viewer.show_well(
            str(row["name"]),
            logs,
            uwi=uwi,
            kb=layer.kb_at(position),
            trajectory=layer.trajectory_for(position),
            tops=layer.tops_for(position),
        )
        if not reveal:
            return
        self.log_dock.show()
        if not logs:
            self.statusBar().showMessage(f"No logs loaded for {row['name']}", 4000)

    # --- identify and attributes -------------------------------------------------------------
    def identify_at(self, x: float, y: float) -> None:
        hits = identify(self.project, x, y, 6.0 * self.canvas.map_units_per_pixel())
        self.identify_dialog.show_results(x, y, hits)
        if not hits:
            self.canvas.set_highlight(None)
        self.identify_dialog.show()
        self.identify_dialog.raise_()
        # Raster values are sampled in the background. A later click drops stale results.
        self._identify_token += 1
        token = self._identify_token
        for layer in self.project.layers:
            if not (isinstance(layer, RasterLayer) and layer.visible and layer.extent):
                continue
            e = layer.extent
            if not (e.xmin <= x <= e.xmax and e.ymin <= y <= e.ymax):
                continue
            layer_id, name = layer.id, layer.name

            def on_value(value: float | None, layer_id: str = layer_id, name: str = name) -> None:
                if token == self._identify_token:
                    self.identify_dialog.add_raster_value(layer_id, name, value)

            self.tasks.submit(
                sample_raster,
                layer.source,
                self.project.crs,
                x,
                y,
                layer.band,
                assume_crs=layer.assumed_crs,
                transformation=self.project.transformation_for(layer.source_crs, self.project.crs),
                on_result=on_value,
                on_error=lambda exc, s=layer.source: log.error(
                    "Sampling %s failed", s, exc_info=exc
                ),
            )

    def _highlight_hit(self, hit: IdentifyHit) -> None:
        layer = self.project.get(hit.layer_id)
        if isinstance(layer, FeatureLayer) and layer.gdf is not None:
            self.canvas.set_highlight(layer.gdf.geometry.values[[hit.position]])

    def open_attribute_table(self, layer_id: str) -> None:
        layer = self.project.get(layer_id)
        if not isinstance(layer, FeatureLayer):
            self.statusBar().showMessage(f"{layer.name} has no attribute table", 3000)
            return
        self.attribute_table.set_layer(layer)
        self.attribute_dock.setWindowTitle(f"Attributes – {layer.name}")
        self.attribute_dock.show()

    def _table_layer(self) -> FeatureLayer | None:
        layer_id = self.attribute_table.layer_id
        if layer_id is None:
            return None
        layer = self.project.get(layer_id)
        return layer if isinstance(layer, FeatureLayer) and layer.gdf is not None else None

    def _highlight_table_selection(self, positions: object) -> None:
        layer = self._table_layer()
        if layer is not None:
            self.canvas.set_highlight(layer.gdf.geometry.values[positions])

    def _zoom_to_table_rows(self, positions: object) -> None:
        layer = self._table_layer()
        if layer is None or not len(positions):  # type: ignore[arg-type]
            return
        import shapely

        bounds = shapely.total_bounds(layer.gdf.geometry.values[positions])
        self.canvas.set_view_extent(Extent.from_bounds(bounds).buffered(0.2, minimum=250.0))

    def _on_layer_removed(self, layer_id: str | None) -> None:
        if layer_id is not None and layer_id == self.attribute_table.layer_id:
            self.attribute_table.set_layer(None)
            self.attribute_dock.setWindowTitle("Attributes")
            self.canvas.set_highlight(None)

    def _on_layer_changed(self, layer_id: str, field: str) -> None:
        if field == "name" and layer_id == self.attribute_table.layer_id:
            self.attribute_dock.setWindowTitle(f"Attributes – {self.project.get(layer_id).name}")
        shown = getattr(self, "_shown_well", None)
        if field in ("logs", "trajectories", "tops") and shown and shown[0] == layer_id:
            # New data for the well in the log viewer: show it straight away.
            layer = self.project.get(layer_id)
            if isinstance(layer, WellLayer) and (position := layer.find(shown[1])) is not None:
                self.show_well_logs(layer, position, reveal=False)

    def add_vector_file(self, path: str | Path) -> None:
        """Load a vector file, asking which layers to load when it has more than one."""
        path = Path(path)
        layers: list[str | None] = [None]
        if path.suffix.lower() in MULTI_LAYER_SUFFIXES:
            try:
                names = list_layers(path)
            except Exception as exc:  # noqa: BLE001 - shown to the user
                show_error(self, "Cannot open layer", str(exc), exc)
                return
            if len(names) > 1:
                layers = list(pick_layers(path.name, names, self))
            elif names:
                layers = [names[0]]
        for layer in layers:
            self.load_vector(path, layer)

    def _transformation_lookup(self) -> Callable[[str], str | None]:
        """Snapshot of the project's datum-transformation choices, safe to use from a worker."""
        target = self.project.crs
        chosen = self.project.transformations

        def lookup(source_crs: str) -> str | None:
            return chosen.get((normalize_crs(source_crs), target))

        return lookup

    def load_vector(
        self, path: str | Path, layer: str | None = None, assume_crs: str | None = None
    ) -> None:
        self.statusBar().showMessage(f"Loading {Path(path).name}…")
        self.tasks.submit(
            load_vector_layer,
            path,
            self.project.crs,
            layer=layer,
            assume_crs=assume_crs,
            transformation=self._transformation_lookup(),
            on_result=self._current(self._on_layer_loaded),
            on_error=self._current(lambda exc: self._on_vector_failed(path, layer, exc)),
        )

    def load_raster(self, path: str | Path, assume_crs: str | None = None) -> None:
        self.statusBar().showMessage(f"Loading {Path(path).name}…")
        self.tasks.submit(
            load_raster_layer,
            path,
            self.project.crs,
            assume_crs=assume_crs,
            transformation=self._transformation_lookup(),
            on_result=self._current(self._on_layer_loaded),
            on_error=self._current(lambda exc: self._on_raster_failed(path, exc)),
        )

    def _on_raster_failed(self, path: str | Path, exc: BaseException) -> None:
        if not isinstance(exc, MissingCRSError):
            self._on_load_failed(path, exc)
            return
        self.statusBar().clearMessage()
        crs = ask_crs(self, f"{exc}. Choose the CRS the raster is in.", self.project.crs)
        if crs:
            self.load_raster(path, assume_crs=crs)

    def _current(self, fn: Callable[..., None]) -> Callable[..., None]:
        """Wrap a background callback so it is ignored if the project was replaced meanwhile."""
        generation = self._generation

        def guarded(*args: object) -> None:
            if generation == self._generation:
                fn(*args)
            else:
                log.info("Dropped a background result that belonged to a previous project")

        return guarded

    def _on_layer_loaded(self, layer: Layer) -> None:
        first = len(self.project) == 0
        if isinstance(layer, VectorLayer):
            n_vectors = sum(isinstance(other, VectorLayer) for other in self.project.layers)
            layer.style = replace(
                layer.style, color=VECTOR_PALETTE[n_vectors % len(VECTOR_PALETTE)]
            )
        self.project.add_layer(layer, index=self._insert_index(layer))
        self.statusBar().showMessage(f"Loaded {layer.name}", 3000)
        if first:
            self.zoom_to_full_extent()

    def _insert_index(self, layer: Layer) -> int:
        """New rasters go above existing rasters but below every vector and well layer."""
        if not isinstance(layer, RasterLayer):
            return 0
        layers = self.project.layers
        return next(
            (i for i, other in enumerate(layers) if isinstance(other, RasterLayer)), len(layers)
        )

    def _on_vector_failed(self, path: str | Path, layer: str | None, exc: BaseException) -> None:
        if not isinstance(exc, MissingCRSError):
            self._on_load_failed(path, exc)
            return
        self.statusBar().clearMessage()
        crs = ask_crs(self, f"{exc}. Choose the CRS the data is in.", self.project.crs)
        if crs:
            self.load_vector(path, layer, assume_crs=crs)

    def _on_load_failed(self, path: str | Path, exc: BaseException) -> None:
        self.statusBar().clearMessage()
        show_error(self, "Cannot load layer", f"{Path(path).name}: {exc}", exc)

    # --- layer actions -----------------------------------------------------------------------
    def _with_current(self, fn: object) -> None:
        layer_id = self.layer_panel.current_layer_id()
        if layer_id is None:
            self.statusBar().showMessage("Select a layer first", 3000)
            return
        fn(layer_id)  # type: ignore[operator]

    def remove_layer(self, layer_id: str) -> None:
        self.project.remove_layer(layer_id)

    def edit_layer_style(self, layer_id: str) -> None:
        style = edit_style(self.project.get(layer_id), self)
        if style is not None:
            self.project.set_style(layer_id, style)
            if self.canvas.has_layer(layer_id):
                error = self.canvas.graphics(layer_id).error
                if error:
                    self.statusBar().showMessage(f"Style not fully applied: {error}", 10000)

    def zoom_to_layer(self, layer_id: str) -> None:
        extent = self.project.get(layer_id).extent
        if extent is not None:
            self.canvas.set_view_extent(extent.buffered())

    def zoom_to_full_extent(self) -> None:
        extent = self.project.extent()
        if extent is not None:
            self.canvas.set_view_extent(extent.buffered())

    # --- project files -------------------------------------------------------------------------
    def _update_title(self, *_args: object) -> None:
        name = Path(self.project.path).name if self.project.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.project.dirty else ''} – PetroGIS")

    def maybe_save(self) -> bool:
        """Offer to save unsaved changes. Returns False if the user cancelled."""
        if not self.project.dirty or (len(self.project) == 0 and self.project.path is None):
            return True
        answer = ask_save_changes(self)
        if answer == "cancel":
            return False
        if answer == "save":
            return self.save_project()
        return True

    # --- autosave and recovery -------------------------------------------------------------------
    def autosave_now(self) -> Path | None:
        """Write unsaved changes to the autosave sidecar. Returns its path, or None if skipped."""
        if not self.project.dirty or (len(self.project) == 0 and self.project.path is None):
            return None
        target = autosave_path(self.project.path, self.autosave_dir)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_project_file(self.project, target, view=self.canvas.view_extent())
        except (OSError, DataReadError) as exc:
            # Autosave runs on a timer, so a dialog every two minutes would be worse than useless.
            log.warning("Autosave to %s failed: %s", target, exc)
            self.statusBar().showMessage(f"Autosave failed: {exc}", 8000)
            return None
        return target

    def _discard_autosave(self, project_path: str | Path | None) -> None:
        target = autosave_path(project_path, self.autosave_dir)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove autosave %s: %s", target, exc)

    def offer_untitled_recovery(self) -> None:
        """At startup, offer to restore an untitled project left behind by a crash."""
        recovery = find_recovery(None, self.autosave_dir)
        if recovery is None:
            return
        if not ask_restore_autosave(self, recovery):
            self._discard_autosave(None)
            return
        self._load_project_file(recovery, recovered=True, target=None)

    def new_project(self) -> None:
        if not self.maybe_save():
            return
        self._discard_autosave(self.project.path)
        self._generation += 1
        self.project.clear()
        self.project.set_crs(DEFAULT_PROJECT_CRS)
        self.project.dirty = False
        self._update_title()

    def save_project(self) -> bool:
        if self.project.path is None:
            return self.save_project_as()
        return self._write_project(self.project.path)

    def save_project_as(self, path: str | Path | None = None) -> bool:
        if path is None:
            start = self.project.path or "untitled.pgis"
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save Project As", start, PROJECT_FILE_FILTER
            )
            if not chosen:
                return False
            path = chosen
        return self._write_project(path)

    def _write_project(self, path: str | Path) -> bool:
        previous_path = self.project.path
        try:
            saved = save_project(self.project, path, view=self.canvas.view_extent())
        except Exception as exc:  # noqa: BLE001 - shown to the user
            show_error(self, "Cannot save project", str(exc), exc)
            return False
        self._discard_autosave(previous_path)
        self._discard_autosave(saved)
        self.recent.add(saved)
        self._update_title()
        self.statusBar().showMessage(f"Saved {saved.name}", 3000)
        return True

    def open_project(self, path: str | Path | None = None) -> None:
        if not self.maybe_save():
            return
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(self, "Open Project", "", PROJECT_FILE_FILTER)
            if not chosen:
                return
            path = chosen
        path = Path(path)
        self._discard_autosave(self.project.path)
        recovery = find_recovery(path, self.autosave_dir)
        if recovery is not None:
            if ask_restore_autosave(self, recovery):
                self._load_project_file(recovery, recovered=True, target=path)
                return
            self._discard_autosave(path)
        self._load_project_file(path)

    def _load_project_file(
        self, source: Path, recovered: bool = False, target: Path | None = None
    ) -> None:
        """Load ``source`` in the background. A recovered autosave takes ``target`` as its path."""
        self.statusBar().showMessage(f"Opening {source.name}…")
        self._generation += 1  # layers still loading belong to the project being replaced
        self.tasks.submit(
            load_project,
            source,
            on_result=self._current(
                lambda loaded: self._on_project_loaded(loaded, recovered=recovered, target=target)
            ),
            on_error=self._current(lambda exc: self._on_project_failed(source, exc)),
        )

    def _on_project_loaded(
        self, loaded: LoadedProject, recovered: bool = False, target: Path | None = None
    ) -> None:
        self.project.clear()
        self.project.set_crs(loaded.project.crs)
        for layer in reversed(loaded.project.layers):
            self.project.add_layer(layer)
        if loaded.view is not None:
            self.canvas.set_view_extent(loaded.view)
        else:
            self.zoom_to_full_extent()
        if recovered:
            # The restored state is unsaved work: keep the original identity and mark it dirty.
            self.project.path = None if target is None else str(target)
            self.project.dirty = True
            self.statusBar().showMessage("Recovered unsaved changes. Save to keep them.", 8000)
        else:
            self.project.path = loaded.project.path
            self.project.dirty = False
            if self.project.path is not None:
                self.recent.add(self.project.path)
                self.statusBar().showMessage(f"Opened {Path(self.project.path).name}", 3000)
        self._update_title()
        if loaded.missing:
            self.repair_missing_layers(loaded.missing, dict(loaded.layer_positions))
        if loaded.warnings:
            show_warning(self, "Project opened with problems", "\n".join(loaded.warnings))

    def _on_project_failed(self, path: Path, exc: BaseException) -> None:
        self.statusBar().clearMessage()
        if not path.exists():
            self.recent.remove(path)
        show_error(self, "Cannot open project", str(exc), exc)

    def _build_recent_menu(self) -> None:
        self.recent_menu.clear()
        paths = self.recent.paths()
        for entry in paths:
            action = self.recent_menu.addAction(Path(entry).name)
            action.setToolTip(as_rich_text(entry))
            action.setStatusTip(entry)
            action.triggered.connect(lambda _checked=False, p=entry: self.open_project(p))
        if paths:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Recent", self.recent.clear)
        else:
            self.recent_menu.addAction("No recent projects").setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.maybe_save():
            event.ignore()
            return
        # The user saved or chose to discard, so the autosave is no longer wanted.
        self._discard_autosave(self.project.path)
        self.autosave_timer.stop()
        self.tasks.shutdown()
        self.bridge.detach()
        super().closeEvent(event)


def _transform_extent(extent: Extent, source: str, target: str) -> Extent | None:
    """``extent`` re-expressed in ``target`` (densified edges), or ``None`` if that fails."""
    from pyproj.exceptions import ProjError
    from pyproj.transformer import Transformer

    try:
        transformer = Transformer.from_crs(source, target, always_xy=True)
        bounds = transformer.transform_bounds(*extent.as_tuple(), densify_pts=21)
    except (ProjError, ValueError):
        return None
    result = Extent.from_bounds(bounds)
    return result if result.is_valid() and result.width > 0 else None


def _load_repaired(
    entries: list[tuple[int, dict[str, Any]]], context: LoadContext
) -> tuple[list[tuple[int, Layer]], list[str]]:
    """Load repaired layer entries (runs in a worker). Failures are returned as messages."""
    layers: list[tuple[int, Layer]] = []
    problems: list[str] = []
    for position, entry in entries:
        try:
            layers.append((position, load_layer_entry(entry, context)))
        except (DataReadError, ValueError, TypeError) as exc:
            problems.append(f"{entry.get('name', '?')}: {exc}")
    problems.extend(context.warnings)
    return layers, problems
