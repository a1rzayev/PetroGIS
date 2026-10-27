"""Correlation panel: wells side by side on one depth axis, with tops picking and flattening.

Each well gets one plot per template track, all sharing (linked) one inverted depth axis. Between
neighbouring wells, a narrow gap plot draws correlation lines joining the same surface in both
wells. Curves are drawn in each track's normalised 0–1 width (``core.correlation``).

Picking: switch on Pick, choose or type a surface, then click a track at the depth of the top.
Shift+click removes that surface's pick from the well. Picks go through the ``Project``, so the
log viewer, the tops map layer and the project file all see them.
"""

from __future__ import annotations

import math
from dataclasses import replace
from html import escape
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.correlation import (
    BUILTIN_TEMPLATES,
    DATUMS,
    DEFAULT_TEMPLATE,
    MAX_SECTION_WELLS,
    MAX_TRACKS,
    SHADINGS,
    CorrelationSection,
    CurveSpec,
    TrackSpec,
    TrackTemplate,
    WellDatum,
    md_at,
    normalize_curve,
    section_depth,
    well_datum,
)
from petrogis.core.layers import WellLayer
from petrogis.core.logs import WellLog, metres_per_unit, minmax_decimate
from petrogis.core.mnemonics import CurveFamily
from petrogis.core.project import Project
from petrogis.core.tops import surface_colors, surface_order
from petrogis.ui.log_viewer import CROSSOVER_BRUSH, SAND_BRUSH, ShadeItem
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.text import plain_label

NO_FLATTEN = "(none)"
MAX_BINS = 2000
"""Curves are min-max decimated to twice this many points per well for drawing."""
GAP_WIDTH = 34
FAMILY_COLORS = {
    CurveFamily.GR: "#2ca02c",
    CurveFamily.RES_DEEP: "#000000",
    CurveFamily.RES_MEDIUM: "#7f7f7f",
    CurveFamily.RES_SHALLOW: "#bcbd22",
    CurveFamily.NPHI: "#1f77b4",
    CurveFamily.RHOB: "#d62728",
    CurveFamily.DT: "#9467bd",
    CurveFamily.CALI: "#8c564b",
    CurveFamily.SP: "#17becf",
    CurveFamily.PEF: "#e377c2",
}
DEFAULT_SCALES = {
    CurveFamily.GR: (0.0, 150.0),
    CurveFamily.RES_DEEP: (0.2, 2000.0),
    CurveFamily.RES_MEDIUM: (0.2, 2000.0),
    CurveFamily.RES_SHALLOW: (0.2, 2000.0),
    CurveFamily.NPHI: (0.45, -0.15),
    CurveFamily.RHOB: (1.95, 2.95),
    CurveFamily.DT: (140.0, 40.0),
    CurveFamily.CALI: (6.0, 16.0),
    CurveFamily.SP: (-100.0, 50.0),
    CurveFamily.PEF: (0.0, 10.0),
}


def _brace_safe(text: str) -> str:
    """InfLineLabel formats its text with str.format: keep data-derived braces literal."""
    return text.replace("{", "{{").replace("}", "}}")


# --- dialogs -----------------------------------------------------------------------------------
class SectionWellsDialog(QDialog):
    """Choose the wells of a section and their left-to-right order (drag to reorder)."""

    def __init__(
        self, layer: WellLayer, keys: tuple[str, ...], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Section Wells – {layer.name}")
        self.resize(320, 460)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        names = [] if layer.gdf is None else [str(n) for n in layer.gdf["name"]]
        all_keys = [] if layer.gdf is None else [str(k) for k in layer.gdf["name_key"]]
        by_key = dict(zip(all_keys, names, strict=True))
        order = [k for k in keys if k in by_key] + [k for k in all_keys if k not in keys]
        for key in dict.fromkeys(order):
            item = QListWidgetItem(by_key[key])  # plain item text: never markup
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.error = plain_label()
        self.error.setStyleSheet("color: #b00020")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tick the wells to show; drag to set the order."))
        layout.addWidget(self.list)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def keys(self) -> tuple[str, ...]:
        items = [self.list.item(i) for i in range(self.list.count())]
        return tuple(
            str(i.data(Qt.ItemDataRole.UserRole))
            for i in items
            if i.checkState() == Qt.CheckState.Checked
        )

    def accept(self) -> None:
        if len(self.keys()) > MAX_SECTION_WELLS:
            self.error.setText(f"Choose at most {MAX_SECTION_WELLS} wells")
            return
        super().accept()


def ask_section_wells(
    layer: WellLayer, keys: tuple[str, ...], parent: QWidget | None = None
) -> tuple[str, ...] | None:
    dialog = SectionWellsDialog(layer, keys, parent)
    return dialog.keys() if dialog.exec() == QDialog.DialogCode.Accepted else None


NONE_CURVE = "(none)"
COLUMNS = (
    "Name", "Curve 1", "Left", "Right", "Curve 2", "Left", "Right", "Log", "Shading", "Cut-off"
)  # fmt: skip


class TemplateDialog(QDialog):
    """Edit a track template: up to two curves per track, scales, log scale and shading."""

    def __init__(self, template: TrackTemplate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Track Template")
        self.resize(900, 360)
        self._name = template.name
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        for track in template.tracks:
            self.add_track(track)
        add = QPushButton("Add Track")
        add.clicked.connect(lambda: self.add_track())
        remove = QPushButton("Remove Track")
        remove.clicked.connect(self.remove_track)
        self.error = plain_label()
        self.error.setStyleSheet("color: #b00020")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.table)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(minimum=-1e6, maximum=1e6, decimals=3)
        spin.setValue(value)
        return spin

    def _curve_combo(self, family: CurveFamily | None, allow_none: bool) -> QComboBox:
        combo = QComboBox()
        if allow_none:
            combo.addItem(NONE_CURVE, None)
        for f in CurveFamily:
            combo.addItem(f.value, f)
        combo.setCurrentIndex(combo.findData(family) if family is not None else 0)
        return combo

    def add_track(self, track: TrackSpec | None = None) -> None:
        if self.table.rowCount() >= MAX_TRACKS:
            self.error.setText(f"A template holds at most {MAX_TRACKS} tracks")
            return
        track = track or TrackSpec("Track", (CurveSpec(CurveFamily.GR, 0.0, 150.0, "#2ca02c"),))
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(track.name))
        first = track.curves[0]
        second = track.curves[1] if len(track.curves) > 1 else None
        widgets = [
            self._curve_combo(first.family, False),
            self._spin(first.left),
            self._spin(first.right),
            self._curve_combo(second.family if second else None, True),
            self._spin(second.left if second else 0.0),
            self._spin(second.right if second else 1.0),
        ]
        log = QCheckBox()
        log.setChecked(track.log)
        shading = QComboBox()
        shading.addItems(list(SHADINGS))
        shading.setCurrentText(track.shading)
        cutoff = self._spin(track.cutoff)
        for column, widget in enumerate([*widgets, log, shading, cutoff], start=1):
            self.table.setCellWidget(row, column, widget)
        # Picking a curve fills in its usual scale.
        for combo, left, right in (widgets[0:3], widgets[3:6]):
            combo.currentIndexChanged.connect(
                lambda _i, c=combo, lo=left, hi=right: self._default_scale(c, lo, hi)
            )

    @staticmethod
    def _default_scale(combo: QComboBox, left: QDoubleSpinBox, right: QDoubleSpinBox) -> None:
        family = combo.currentData()
        if family in DEFAULT_SCALES:
            lo, hi = DEFAULT_SCALES[family]
            left.setValue(lo)
            right.setValue(hi)

    def remove_track(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row >= 0 and self.table.rowCount() > 1:
            self.table.removeRow(row)

    def _cell(self, row: int, column: int) -> Any:
        return self.table.cellWidget(row, column)

    def result_template(self) -> TrackTemplate:
        """The edited template. Raises ``ValueError`` with a user-facing message if invalid."""
        tracks = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            name = item.text().strip() if item is not None else ""
            curves = []
            for combo_col, left_col, right_col in ((1, 2, 3), (4, 5, 6)):
                raw = self._cell(row, combo_col).currentData()
                if raw is None:
                    continue
                family = CurveFamily(raw)  # Qt hands enum data back as a plain string
                curves.append(
                    CurveSpec(
                        family,
                        self._cell(row, left_col).value(),
                        self._cell(row, right_col).value(),
                        FAMILY_COLORS.get(family, "#000000"),
                        dashed=bool(curves),
                    )
                )
            tracks.append(
                TrackSpec(
                    name or f"Track {row + 1}",
                    tuple(curves),
                    self._cell(row, 7).isChecked(),
                    self._cell(row, 8).currentText(),
                    self._cell(row, 9).value(),
                )
            )
        name = self._name if self._name not in {t.name for t in BUILTIN_TEMPLATES} else "Custom"
        return TrackTemplate(name, tuple(tracks))

    def accept(self) -> None:
        try:
            self.result_template()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


def ask_template(template: TrackTemplate, parent: QWidget | None = None) -> TrackTemplate | None:
    dialog = TemplateDialog(template, parent)
    return dialog.result_template() if dialog.exec() == QDialog.DialogCode.Accepted else None


# --- the panel ---------------------------------------------------------------------------------
class _WellColumn:
    """The plots of one well and how its depths map to the section."""

    def __init__(self, info: WellDatum, plots: list[pg.PlotItem]) -> None:
        self.info = info
        self.plots = plots


class CorrelationPanel(QWidget):
    def __init__(
        self, project: Project, bridge: ProjectBridge, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.columns: list[_WellColumn] = []
        self.gaps: list[pg.PlotItem] = []
        self.notes: list[str] = []
        self._syncing = False

        self.wells_button = QPushButton("Wells…")
        self.wells_button.clicked.connect(self.choose_wells)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(lambda: self.project.set_section(None))
        self.datum_combo = QComboBox()
        self.datum_combo.addItems(list(DATUMS))
        self.flatten_combo = QComboBox()
        self.template_combo = QComboBox()
        self.template_button = QPushButton("Edit…")
        self.template_button.clicked.connect(self.edit_template)
        self.pick_button = QPushButton("Pick")
        self.pick_button.setCheckable(True)
        self.pick_button.setToolTip("Click a track to pick the surface; Shift+click removes it")
        self.surface_combo = QComboBox()
        self.surface_combo.setEditable(True)
        self.surface_combo.setMinimumWidth(140)
        self.surface_combo.lineEdit().setPlaceholderText("Surface to pick")
        for combo in (self.datum_combo, self.flatten_combo, self.template_combo):
            combo.currentIndexChanged.connect(self._on_controls_changed)

        row1 = QHBoxLayout()
        row1.addWidget(self.wells_button)
        row1.addWidget(self.clear_button)
        row1.addSpacing(12)
        row1.addWidget(QLabel("Depth"))
        row1.addWidget(self.datum_combo)
        row1.addWidget(QLabel("Flatten on"))
        row1.addWidget(self.flatten_combo)
        row1.addSpacing(12)
        row1.addWidget(QLabel("Template"))
        row1.addWidget(self.template_combo)
        row1.addWidget(self.template_button)
        row1.addStretch(1)
        row1.addWidget(self.pick_button)
        row1.addWidget(self.surface_combo)
        self.status = plain_label(
            "Choose wells to correlate (Wells…, Ctrl+click on the map, "
            "or from the attribute table)."
        )
        self.status.setWordWrap(True)

        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground("w")
        self.graphics.scene().sigMouseClicked.connect(self._on_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(row1)
        layout.addWidget(self.status)
        layout.addWidget(self.graphics, 1)

        bridge.section_changed.connect(self.rebuild)
        bridge.cleared.connect(self.rebuild)
        bridge.layer_removed.connect(self._on_layer_removed)
        bridge.layer_changed.connect(self._on_layer_changed)
        self.rebuild()

    # --- state ---------------------------------------------------------------------------------
    @property
    def section(self) -> CorrelationSection | None:
        return self.project.section

    def layer(self) -> WellLayer | None:
        section = self.section
        if section is None:
            return None
        try:
            layer = self.project.get(section.layer_id)
        except KeyError:
            return None
        return layer if isinstance(layer, WellLayer) else None

    def target_layer(self) -> WellLayer | None:
        return self.layer() or next(
            (lyr for lyr in self.project.layers if isinstance(lyr, WellLayer)), None
        )

    def set_wells(self, layer: WellLayer, keys: tuple[str, ...]) -> None:
        """Show ``keys`` (normalised names) from ``layer``, keeping template and datum."""
        current = self.section
        keys = tuple(dict.fromkeys(k for k in keys if layer.find(k) is not None))[
            :MAX_SECTION_WELLS
        ]
        if not keys:
            self.project.set_section(None)
            return
        if current is not None and current.layer_id == layer.id:
            self.project.set_section(replace(current, well_keys=keys))
        else:
            template = current.template if current else DEFAULT_TEMPLATE
            self.project.set_section(CorrelationSection(layer.id, keys, template))

    def toggle_well(self, layer: WellLayer, key: str) -> None:
        current = self.section
        keys = current.well_keys if current is not None and current.layer_id == layer.id else ()
        keys = tuple(k for k in keys if k != key) if key in keys else (*keys, key)
        self.set_wells(layer, keys)

    def choose_wells(self) -> None:
        layer = self.target_layer()
        if layer is None:
            self.status.setText("Import well headers first.")
            return
        current = self.section
        keys = current.well_keys if current is not None and current.layer_id == layer.id else ()
        chosen = ask_section_wells(layer, keys, self)
        if chosen is not None:
            self.set_wells(layer, chosen)

    def edit_template(self) -> None:
        section = self.section
        template = section.template if section else DEFAULT_TEMPLATE
        edited = ask_template(template, self)
        if edited is not None and section is not None:
            self.project.set_section(replace(section, template=edited))

    def _on_controls_changed(self) -> None:
        section = self.section
        if self._syncing or section is None:
            return
        flatten = self.flatten_combo.currentText()
        template = section.template
        chosen = self.template_combo.currentText()
        builtin = {t.name: t for t in BUILTIN_TEMPLATES}
        if chosen in builtin:
            template = builtin[chosen]
        self.project.set_section(
            replace(
                section,
                datum=self.datum_combo.currentText(),
                flatten_on=None if flatten in ("", NO_FLATTEN) else flatten,
                template=template,
            )
        )

    def _on_layer_removed(self, layer_id: str) -> None:
        if self.section is not None and self.section.layer_id == layer_id:
            self.project.set_section(None)

    def _on_layer_changed(self, layer_id: str, field: str) -> None:
        section = self.section
        if section is None or section.layer_id != layer_id:
            return
        if field in ("tops", "logs", "trajectories", "name"):
            self.rebuild()

    # --- drawing -------------------------------------------------------------------------------
    def _sync_controls(self, layer: WellLayer | None) -> None:
        self._syncing = True
        try:
            section = self.section
            surfaces = (
                []
                if layer is None
                else surface_order(t for picks in layer.tops.values() for t in picks)
            )
            self.flatten_combo.clear()
            self.flatten_combo.addItems([NO_FLATTEN, *surfaces])
            self.template_combo.clear()
            names = [t.name for t in BUILTIN_TEMPLATES]
            if section is not None and section.template.name not in names:
                names.append(section.template.name)
            self.template_combo.addItems(names)
            current_surface = self.surface_combo.currentText()
            self.surface_combo.clear()
            self.surface_combo.addItems(surfaces)
            self.surface_combo.setCurrentText(current_surface or (surfaces[0] if surfaces else ""))
            if section is not None:
                self.datum_combo.setCurrentText(section.datum)
                self.flatten_combo.setCurrentText(section.flatten_on or NO_FLATTEN)
                self.template_combo.setCurrentText(section.template.name)
            enabled = section is not None
            for widget in (self.datum_combo, self.flatten_combo, self.template_combo,
                           self.template_button, self.pick_button, self.clear_button):  # fmt: skip
                widget.setEnabled(enabled)
        finally:
            self._syncing = False

    def rebuild(self) -> None:
        """Redraw the whole section from the project (cheap: one plot per well track)."""
        self.graphics.clear()
        self.columns = []
        self.gaps = []
        self.notes = []
        layer = self.layer()
        section = self.section
        self._sync_controls(layer)
        if layer is None or section is None or not section.well_keys:
            self.status.setText(
                "Choose wells to correlate (Wells…, Ctrl+click on the map, or from the "
                "attribute table)."
            )
            return
        infos = [well_datum(layer, k, section.datum, section.flatten_on) for k in section.well_keys]
        infos = [i for i in infos if i is not None]
        all_tops = [t for i in infos for t in layer.tops_for(i.position)]
        colors = surface_colors(surface_order(all_tops))
        master: pg.PlotItem | None = None
        depth_range: list[float] = []
        col = 0
        for index, info in enumerate(infos):
            if index:
                gap = self._add_gap(col)
                col += 1
                if master is not None:
                    gap.setYLink(master)
            header = escape(info.name)
            if info.flattened:
                header += " <span style='color:#888'>(flattened)</span>"
            self.graphics.addLabel(
                header,
                row=0,
                col=col,
                colspan=len(section.template.tracks),
                color="#000000",
                bold=True,
            )
            plots = []
            for track in section.template.tracks:
                plot = self._add_track(col, track, first=master is None)
                if master is None:
                    master = plot
                else:
                    plot.setYLink(master)
                plots.append(plot)
                col += 1
            column = _WellColumn(info, plots)
            self.columns.append(column)
            depth_range.extend(self._draw_well(layer, column, section, colors))
            self.notes.extend(f"{info.name}: {w}" for w in info.warnings)
        self._draw_correlation_lines(layer, section, colors)
        if master is not None:
            master.getAxis("left").setLabel(self._axis_label(section))
            finite = [d for d in depth_range if math.isfinite(d)]
            if finite:
                pad = max(5.0, 0.03 * (max(finite) - min(finite)))
                master.setYRange(min(finite) - pad, max(finite) + pad, padding=0)
        message = f"{len(infos)} well(s) · {section.template.name} template"
        if self.notes:
            message += " · " + "; ".join(self.notes)
        self.status.setText(message)

    @staticmethod
    def _axis_label(section: CorrelationSection) -> str:
        label = section.datum
        if section.flatten_on:
            label += f" relative to {section.flatten_on}"
        return escape(f"{label} (m)")  # axis labels are HTML

    def _add_gap(self, col: int) -> pg.PlotItem:
        gap: pg.PlotItem = self.graphics.addPlot(row=1, col=col)
        gap.hideAxis("left")
        gap.hideAxis("bottom")
        gap.setMenuEnabled(False)
        gap.hideButtons()
        gap.setMouseEnabled(x=False, y=True)
        gap.invertY(True)
        gap.setXRange(0, 1, padding=0)
        gap.setMaximumWidth(GAP_WIDTH)
        self.gaps.append(gap)
        return gap

    def _add_track(self, col: int, track: TrackSpec, first: bool) -> pg.PlotItem:
        plot: pg.PlotItem = self.graphics.addPlot(row=1, col=col)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.invertY(True)
        plot.setMouseEnabled(x=False, y=True)
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.hideAxis("bottom")
        plot.getAxis("left").enableAutoSIPrefix(False)
        if not first:
            plot.getAxis("left").setStyle(showValues=False)
            plot.getAxis("left").setWidth(4)
        plot.setXRange(0, 1, padding=0)
        # Header: each curve's name and scale in its colour (HTML, so escape every part).
        parts = [
            f"<span style='color:{c.color}'>{escape(c.family.value)} {c.left:g}–{c.right:g}</span>"
            for c in track.curves
        ]
        plot.setTitle("<br>".join(parts), size="8pt")
        return plot

    def _logs(self, layer: WellLayer, info: WellDatum) -> list[WellLog]:
        return layer.logs_for(info.position)

    def _draw_well(
        self,
        layer: WellLayer,
        column: _WellColumn,
        section: CorrelationSection,
        colors: dict[str, str],
    ) -> list[float]:
        info = column.info
        extent: list[float] = []
        logs = self._logs(layer, info)
        for plot, track in zip(column.plots, section.template.tracks, strict=True):
            drawn: list[tuple[np.ndarray, np.ndarray, WellLog] | None] = []
            for curve in track.curves:
                log = next((lg for lg in logs if lg.curve_for(curve.family) is not None), None)
                if log is None:
                    drawn.append(None)
                    continue
                md = log.depth * metres_per_unit(log.depth_unit)
                depth = section_depth(layer, info, md, section.datum)
                values = log.curve_for(curve.family).values  # type: ignore[union-attr]
                x = normalize_curve(values, curve, track.log)
                drawn.append((x, depth, log))
                shown_depth, shown = minmax_decimate(depth, x, MAX_BINS)
                pen = pg.mkPen(
                    curve.color,
                    width=1.1,
                    style=Qt.PenStyle.DashLine if curve.dashed else Qt.PenStyle.SolidLine,
                )
                plot.addItem(pg.PlotDataItem(x=shown, y=shown_depth, pen=pen, connect="finite"))
                finite = depth[np.isfinite(depth)]
                if finite.size:
                    extent.extend((float(finite.min()), float(finite.max())))
            self._draw_shading(plot, track, drawn)
        for top in layer.tops_for(info.position):
            depth = float(section_depth(layer, info, np.array([top.md]), section.datum)[0])
            if not math.isfinite(depth):
                continue
            extent.append(depth)
            pen = pg.mkPen(colors.get(top.surface, "#000000"), width=1.6)
            for t_index, plot in enumerate(column.plots):
                line = pg.InfiniteLine(pos=depth, angle=0, pen=pen, movable=False)
                if t_index == 0:
                    label = pg.InfLineLabel(
                        line, text=_brace_safe(top.surface), position=0.02, anchors=[(0, 1), (0, 1)]
                    )
                    label.setColor(colors.get(top.surface, "#000000"))
                plot.addItem(line)
        return extent

    @staticmethod
    def _draw_shading(
        plot: pg.PlotItem,
        track: TrackSpec,
        drawn: list[tuple[np.ndarray, np.ndarray, WellLog] | None],
    ) -> None:
        first = drawn[0] if drawn else None
        if first is None:
            return
        x, depth, log = first
        if track.shading == "sand":
            edge = normalize_curve(np.array([track.cutoff]), track.curves[0], track.log)[0]
            cutoff = np.full(x.shape, edge)
            # Sand is the low-value side of the cut-off, whichever way the scale runs.
            reversed_scale = track.curves[0].left > track.curves[0].right
            with np.errstate(invalid="ignore"):
                low = np.maximum(x, cutoff) if reversed_scale else np.minimum(x, cutoff)
            shade = ShadeItem(SAND_BRUSH)
            shade.set_data(low, cutoff, depth)
            plot.addItem(shade)
        elif track.shading == "crossover" and len(drawn) > 1 and drawn[1] is not None:
            second, _, second_log = drawn[1]
            if second_log is not log:  # different depth samples: nothing to compare
                return
            with np.errstate(invalid="ignore"):
                edge = np.minimum(second, x)  # where the second curve lies left of the first
            shade = ShadeItem(CROSSOVER_BRUSH)
            shade.set_data(x, edge, depth)
            plot.addItem(shade)

    def _draw_correlation_lines(
        self, layer: WellLayer, section: CorrelationSection, colors: dict[str, str]
    ) -> None:
        pairs = zip(self.columns, self.columns[1:], strict=False)
        for gap, (left, right) in zip(self.gaps, pairs, strict=True):
            left_tops = {t.surface: t for t in layer.tops_for(left.info.position)}
            for top in layer.tops_for(right.info.position):
                other = left_tops.get(top.surface)
                if other is None:
                    continue
                y0 = float(section_depth(layer, left.info, np.array([other.md]), section.datum)[0])
                y1 = float(section_depth(layer, right.info, np.array([top.md]), section.datum)[0])
                pen = pg.mkPen(colors.get(top.surface, "#000000"), width=1.6)
                gap.addItem(pg.PlotDataItem(x=[0.0, 1.0], y=[y0, y1], pen=pen))

    # --- picking -------------------------------------------------------------------------------
    def column_at(self, plot: pg.PlotItem) -> _WellColumn | None:
        return next((c for c in self.columns if plot in c.plots), None)

    def pick_at(self, column: _WellColumn, depth: float, delete: bool = False) -> str | None:
        """Pick (or with ``delete``, remove) the current surface in ``column``'s well.

        Returns a message for the status line, or ``None`` when nothing happened.
        """
        layer, section = self.layer(), self.section
        surface = self.surface_combo.currentText().strip()
        if layer is None or section is None:
            return None
        if not surface:
            return "Type or choose a surface to pick first."
        info = column.info
        if delete:
            removed = self.project.delete_pick(layer.id, info.key, surface)
            return f"Removed {surface} from {info.name}" if removed else None
        md = md_at(layer, info, depth, section.datum)
        if md is None:
            return f"{info.name}: no measured depth at that position"
        self.project.set_pick(layer.id, info.key, surface, md)
        return f"Picked {surface} in {info.name} at MD {md:.1f} m"

    def _on_click(self, event: Any) -> None:
        if not self.pick_button.isChecked() or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        for column in self.columns:
            for plot in column.plots:
                box = plot.getViewBox()
                if box.sceneBoundingRect().contains(pos):
                    depth = box.mapSceneToView(pos).y()
                    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    message = self.pick_at(column, depth, delete=shift)
                    if message:
                        self.status.setText(message)
                    event.accept()
                    return
