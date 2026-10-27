"""Import flows for well data attached to a well layer: deviation surveys and formation tops,
plus turning a surface's tops into a map layer.

Each import follows the well-header pattern: read and detect columns in a worker, ask for the
mapping only when detection is incomplete, build in a worker, then match wells by normalised name
and attach to the layer through the ``Project``. Prompts are module-level functions so tests can
replace them (``tests/conftest.py`` makes them cancel by default).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QFileDialog, QInputDialog, QWidget

from petrogis.core.layers import WellLayer
from petrogis.io.survey_io import (
    SURVEY_FILE_FILTER,
    SurveyImport,
    SurveyTable,
    build_surveys,
    read_survey_table,
)
from petrogis.io.tops_io import TOPS_FILE_FILTER, TopsImport, TopsTable, build_tops, read_tops_table
from petrogis.ui.dialogs.field_mapping import ask_survey_mapping, ask_tops_mapping
from petrogis.ui.errors import show_error, show_warning

if TYPE_CHECKING:
    from petrogis.ui.main_window import MainWindow

log = logging.getLogger(__name__)

NO_WELLS = "Import well headers first, then their surveys and tops."


def ask_tops_surface(parent: QWidget, surfaces: list[str]) -> str | None:
    """Which surface to turn into a map layer."""
    surface, ok = QInputDialog.getItem(parent, "Create Tops Layer", "Surface:", surfaces, 0, False)
    return str(surface) if ok else None


def ask_tops_layer_path(parent: QWidget, suggested: str) -> str | None:
    """Where to write the tops GeoPackage."""
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save Tops Layer", suggested, "GeoPackage (*.gpkg)"
    )
    return path or None


class WellDataImporter:
    """Survey and tops import for the main window (kept out of it to keep it readable)."""

    def __init__(self, window: MainWindow) -> None:
        self.window = window

    @property
    def project(self) -> Any:
        return self.window.project

    def _status(self, message: str, timeout: int = 0) -> None:
        self.window.statusBar().showMessage(message, timeout)

    def _target(self, title: str) -> WellLayer | None:
        layer = self.window._target_well_layer()
        if layer is None:
            show_warning(self.window, title, NO_WELLS)
        return layer

    def _failed(self, path: Path, exc: BaseException) -> None:
        self.window.statusBar().clearMessage()
        show_error(self.window, "Cannot import", f"{path.name}: {exc}", exc)

    # --- surveys -----------------------------------------------------------------------------
    def open_surveys_dialog(self) -> None:
        if self._target("Import Deviation Surveys") is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self.window, "Import Deviation Surveys", "", SURVEY_FILE_FILTER
        )
        for path in paths:
            self.import_surveys(path)

    def import_surveys(self, path: str | Path) -> None:
        target = self._target("Import Deviation Surveys")
        if target is None:
            return
        path, layer_id = Path(path), target.id
        self._status(f"Reading {path.name}…")
        self.window.tasks.submit(
            read_survey_table,
            path,
            on_result=self.window._current(lambda t: self._on_survey_table(layer_id, t)),
            on_error=self.window._current(lambda exc: self._failed(path, exc)),
        )

    def _on_survey_table(self, layer_id: str, table: SurveyTable) -> None:
        self.window.statusBar().clearMessage()
        mapping, unit, default_well = table.mapping, table.depth_unit, None
        if not mapping.is_complete or mapping.well is None:
            # Without a well column the user must confirm which well the file belongs to.
            answer = ask_survey_mapping(table, self.window)
            if answer is None:
                return
            mapping, unit, default_well = answer
        self.window.tasks.submit(
            build_surveys,
            table,
            mapping,
            unit,
            default_well,
            on_result=self.window._current(lambda r: self._on_surveys_built(layer_id, r)),
            on_error=self.window._current(lambda exc: self._failed(table.path, exc)),
        )

    def _on_surveys_built(self, layer_id: str, result: SurveyImport) -> None:
        layer = self._layer(layer_id, "Survey import")
        if layer is None:
            return
        matched = {
            k: t for k, t in result.trajectories.items() if layer.find(result.names[k]) is not None
        }
        unmatched = sorted(result.names[k] for k in result.trajectories if k not in matched)
        path = next(iter(result.trajectories.values())).source
        self.project.attach_trajectories(
            layer_id, matched, {"path": path, "import_options": result.options}
        )
        deviated = sum(not t.is_vertical for t in matched.values())
        summary = (
            f"Attached surveys to {len(matched)} well(s) in {layer.name} ({deviated} deviated)"
        )
        self._report(summary, result.warnings, unmatched)

    # --- tops --------------------------------------------------------------------------------
    def open_tops_dialog(self) -> None:
        if self._target("Import Formation Tops") is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self.window, "Import Formation Tops", "", TOPS_FILE_FILTER
        )
        for path in paths:
            self.import_tops(path)

    def import_tops(self, path: str | Path) -> None:
        target = self._target("Import Formation Tops")
        if target is None:
            return
        path, layer_id = Path(path), target.id
        self._status(f"Reading {path.name}…")
        self.window.tasks.submit(
            read_tops_table,
            path,
            on_result=self.window._current(lambda t: self._on_tops_table(layer_id, t)),
            on_error=self.window._current(lambda exc: self._failed(path, exc)),
        )

    def _on_tops_table(self, layer_id: str, table: TopsTable) -> None:
        self.window.statusBar().clearMessage()
        mapping, unit, default_well = table.mapping, table.depth_unit, None
        if not mapping.is_complete or mapping.well is None:
            answer = ask_tops_mapping(table, self.window)
            if answer is None:
                return
            mapping, unit, default_well = answer
        self.window.tasks.submit(
            build_tops,
            table,
            mapping,
            unit,
            default_well,
            on_result=self.window._current(lambda r: self._on_tops_built(layer_id, r, table.path)),
            on_error=self.window._current(lambda exc: self._failed(table.path, exc)),
        )

    def _on_tops_built(self, layer_id: str, result: TopsImport, path: Path) -> None:
        layer = self._layer(layer_id, "Tops import")
        if layer is None:
            return
        matched = {k: t for k, t in result.tops.items() if layer.find(result.names[k]) is not None}
        unmatched = sorted(result.names[k] for k in result.tops if k not in matched)
        count = self.project.attach_tops(
            layer_id, matched, {"path": str(path), "import_options": result.options}
        )
        summary = f"Attached {count} top(s) to {len(matched)} well(s) in {layer.name}"
        self._report(summary, result.warnings, unmatched)

    # --- tops map layer ----------------------------------------------------------------------
    def create_tops_layer(self) -> None:
        """Write one surface's tops (with TVDSS) to a GeoPackage and load it as a layer."""
        from petrogis.analysis.tops import surfaces

        layer = self._target("Create Tops Layer")
        if layer is None:
            return
        names = surfaces(layer)
        if not names:
            show_warning(self.window, "Create Tops Layer", "Import formation tops first.")
            return
        surface = ask_tops_surface(self.window, names)
        if surface is None:
            return
        base = Path(self.project.path).parent if self.project.path else Path(layer.source).parent
        safe = "".join(c if c.isalnum() else "_" for c in surface).strip("_") or "tops"
        target = ask_tops_layer_path(self.window, str(base / f"tops_{safe}.gpkg"))
        if target is None:
            return
        from petrogis.analysis.tops import tops_points

        points = tops_points(layer, surface, self.project.crs)  # a few rows per well: cheap
        self._status(f"Writing tops of {surface}…")
        self.window.tasks.submit(
            _write_tops_layer,
            points,
            surface,
            Path(target),
            on_result=self.window._current(self.window.load_vector),
            on_error=self.window._current(lambda exc: self._failed(Path(target), exc)),
        )

    # --- helpers -----------------------------------------------------------------------------
    def _layer(self, layer_id: str, what: str) -> WellLayer | None:
        try:
            layer = self.project.get(layer_id)
        except KeyError:
            self._status(f"{what} discarded: the well layer was removed", 5000)
            return None
        return layer if isinstance(layer, WellLayer) else None

    def _report(self, summary: str, warnings: list[str], unmatched: list[str]) -> None:
        self._status(summary, 5000)
        problems = list(warnings)
        if unmatched:
            problems.append(f"No matching well for: {', '.join(unmatched)}")
        if problems:
            show_warning(
                self.window,
                "Import finished with warnings",
                summary + ".\n\n" + "\n".join(problems),
            )


def _write_tops_layer(points: Any, surface: str, path: Path) -> Path:
    """Worker: write the tops points to a GeoPackage."""
    from petrogis.io.vector_io import write_geopackage

    safe = "".join(c if c.isalnum() else "_" for c in surface).strip("_") or "tops"
    return write_geopackage(points, path, layer=f"tops_{safe}")
