"""Main application window: menus, toolbar, layer dock, map canvas and status bar."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from petrogis import __version__
from petrogis.core.attributes import IdentifyHit, identify
from petrogis.core.crs import CRSError, crs_label, normalize_crs
from petrogis.core.extent import Extent
from petrogis.core.layers import FeatureLayer, Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.core.logs import match_logs
from petrogis.core.project import Project
from petrogis.core.styles import VECTOR_PALETTE
from petrogis.io.errors import MissingCRSError
from petrogis.io.las_io import LAS_FILE_FILTER, LasBatch, read_las_files
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
from petrogis.ui.dialogs.column_mapping import ask_column_mapping
from petrogis.ui.dialogs.layer_picker import pick_layers
from petrogis.ui.dialogs.style_raster import edit_raster_style
from petrogis.ui.dialogs.style_vector import edit_vector_style
from petrogis.ui.dialogs.style_wells import edit_well_style
from petrogis.ui.errors import show_error, show_warning
from petrogis.ui.identify import IdentifyDialog
from petrogis.ui.layer_panel import LayerPanel
from petrogis.ui.log_viewer import LogViewer
from petrogis.ui.map_controller import MapController
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.workers import TaskRunner

log = logging.getLogger(__name__)

MULTI_LAYER_SUFFIXES = {".gpkg", ".kml"}


def format_coord(value: float) -> str:
    """Format a coordinate with thin-space thousands grouping and two decimals."""
    return f"{value:,.2f}".replace(",", "\N{THIN SPACE}")


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PetroGIS")
        self.resize(1280, 800)

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
        self.attribute_table.selection_changed.connect(self._highlight_table_selection)
        self.attribute_table.zoom_requested.connect(self._zoom_to_table_rows)
        self.layer_panel.attribute_table_requested.connect(self.open_attribute_table)
        self.bridge.layer_removed.connect(self._on_layer_removed)
        self.bridge.layer_changed.connect(self._on_layer_changed)
        self.bridge.cleared.connect(lambda: self._on_layer_removed(self.attribute_table.layer_id))

        self.identify_dialog = IdentifyDialog(self)
        self.identify_dialog.hit_selected.connect(self._highlight_hit)
        self._identify_token = 0

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self.canvas.cursor_moved.connect(self._show_cursor)
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
        file_menu.addAction(self.exit_action)

        layers_menu = bar.addMenu("&Layers")
        layers_menu.addActions(
            [
                self.add_vector_action,
                self.add_raster_action,
                self.import_wells_action,
                self.import_las_action,
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
        view_menu.addAction(self.log_dock.toggleViewAction())
        view_menu.addAction(self.attribute_dock.toggleViewAction())

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
        self.crs_label = QLabel()
        status = self.statusBar()
        status.addPermanentWidget(self.coord_label)
        status.addPermanentWidget(self.crs_label)
        self._show_crs(self.project.crs)

    def _show_cursor(self, x: float, y: float) -> None:
        self.coord_label.setText(f"X: {format_coord(x)}  Y: {format_coord(y)}")

    def _show_crs(self, crs: str) -> None:
        self.crs_label.setText(crs)
        self.crs_label.setToolTip(crs_label(crs))

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
                    self, "Select Sheet", f"{path.name} has several sheets:", sheets, 0, False
                )
                if not ok:
                    return
        self.statusBar().showMessage(f"Reading {path.name}…")
        self.tasks.submit(
            read_well_table,
            path,
            sheet,
            on_result=self._on_well_table,
            on_error=lambda exc: self._on_load_failed(path, exc),
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
            on_result=self._on_wells_built,
            on_error=lambda exc: self._on_load_failed(table.path, exc),
        )

    def _on_wells_built(self, result: WellImportResult) -> None:
        self._on_layer_loaded(result.layer)
        if result.warnings:
            show_warning(
                self, f"Imported {result.layer.name} with warnings", "\n".join(result.warnings)
            )

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
            on_result=lambda batch: self._on_las_read(layer_id, batch),
            on_error=lambda exc: self._on_load_failed(Path(paths[0]), exc),
        )

    def _on_las_read(self, layer_id: str, batch: LasBatch) -> None:
        layer = self.project.get(layer_id)
        assert isinstance(layer, WellLayer)
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
        """Identify features in identify mode. Otherwise open the logs of a clicked well."""
        if self.canvas.mouse_mode is MouseMode.IDENTIFY:
            self.identify_at(x, y)
            return
        tolerance = 8.0 * self.canvas.map_units_per_pixel()
        for layer in self.project.layers:
            if not (isinstance(layer, WellLayer) and layer.visible):
                continue
            hits = layer.features_at(x, y, tolerance)
            if not hits:
                continue
            xy = layer.xy()[hits]
            nearest = hits[int(((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2).argmin())]
            self.show_well_logs(layer, nearest)
            return

    def show_well_logs(self, layer: WellLayer, position: int) -> None:
        row = layer.gdf.iloc[position]
        logs = layer.logs_for(position)
        kb = float(row["kb"]) if row["kb"] == row["kb"] else None  # NaN check
        uwi = "" if row["uwi"] is None or row["uwi"] != row["uwi"] else str(row["uwi"])
        self.log_viewer.show_well(str(row["name"]), logs, uwi=uwi, kb=kb)
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
            on_result=self._on_layer_loaded,
            on_error=lambda exc: self._on_vector_failed(path, layer, exc),
        )

    def load_raster(self, path: str | Path) -> None:
        self.statusBar().showMessage(f"Loading {Path(path).name}…")
        self.tasks.submit(
            load_raster_layer,
            path,
            self.project.crs,
            on_result=self._on_layer_loaded,
            on_error=lambda exc: self._on_load_failed(path, exc),
        )

    def _on_layer_loaded(self, layer: Layer) -> None:
        first = len(self.project) == 0
        if isinstance(layer, VectorLayer):
            n_vectors = sum(isinstance(other, VectorLayer) for other in self.project.layers)
            layer.style = replace(
                layer.style, color=VECTOR_PALETTE[n_vectors % len(VECTOR_PALETTE)]
            )
        self.project.add_layer(layer)
        self.statusBar().showMessage(f"Loaded {layer.name}", 3000)
        if first:
            self.zoom_to_full_extent()

    def _on_vector_failed(self, path: str | Path, layer: str | None, exc: BaseException) -> None:
        if not isinstance(exc, MissingCRSError):
            self._on_load_failed(path, exc)
            return
        self.statusBar().clearMessage()
        text, ok = QInputDialog.getText(
            self,
            "Coordinate Reference System",
            f"{exc}\n\nEnter the CRS of the data (e.g. EPSG:32639 or EPSG:4326):",
            text=self.project.crs,
        )
        if not ok or not text.strip():
            return
        try:
            crs = normalize_crs(text.strip())
        except CRSError as crs_exc:
            show_error(self, "Invalid CRS", str(crs_exc), crs_exc)
            return
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
        layer = self.project.get(layer_id)
        if isinstance(layer, VectorLayer):
            style = edit_vector_style(layer.style, layer.name, self)
        elif isinstance(layer, RasterLayer) and layer.info is not None:
            style = edit_raster_style(layer.style, layer.info, layer.name, self)
        elif isinstance(layer, WellLayer):
            style = edit_well_style(layer.style, layer.name, self)
        else:
            return
        if style is not None:
            self.project.set_style(layer_id, style)

    def zoom_to_layer(self, layer_id: str) -> None:
        extent = self.project.get(layer_id).extent
        if extent is not None:
            self.canvas.set_view_extent(extent.buffered())

    def zoom_to_full_extent(self) -> None:
        extent = self.project.extent()
        if extent is not None:
            self.canvas.set_view_extent(extent.buffered())

    def closeEvent(self, event: QCloseEvent) -> None:
        self.bridge.detach()
        super().closeEvent(event)
