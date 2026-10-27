"""Log viewer: depth tracks for GR, deep resistivity (log scale) and a neutron-density overlay.

All tracks share one depth axis, inverted so depth increases downwards. Scrolling zooms and
dragging pans in depth only. The depth axis can show MD, TVD or TVDSS (through the well's
trajectory, or vertical without one) in metres or feet. LAS data stays in MD: only the y values
passed to the curves change. Formation tops are drawn across every track.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from petrogis.core.logs import METRES_PER_FOOT, LogCurve, WellLog, metres_per_unit, minmax_decimate
from petrogis.core.mnemonics import CurveFamily
from petrogis.core.tops import Top, surface_colors, surface_order
from petrogis.core.trajectory import Trajectory


@dataclass(frozen=True)
class TrackScale:
    family: CurveFamily
    lo: float
    hi: float
    color: str
    reversed: bool = False
    log: bool = False


GR_SCALE = TrackScale(CurveFamily.GR, 0.0, 150.0, "#2ca02c")
RES_SCALE = TrackScale(CurveFamily.RES_DEEP, 0.2, 2000.0, "#000000", log=True)
NPHI_SCALE = TrackScale(CurveFamily.NPHI, -0.15, 0.45, "#1f77b4", reversed=True)
RHOB_SCALE = TrackScale(CurveFamily.RHOB, 1.95, 2.95, "#d62728")
DEFAULT_SCALES = {s.family: s for s in (GR_SCALE, RES_SCALE, NPHI_SCALE, RHOB_SCALE)}
GR_SAND_CUTOFF = 75.0
EMPTY_TEXT = "Click a well on the map to show its logs."
DEPTH_REFERENCES = ("MD", "TVD", "TVDSS")
DEPTH_UNITS = ("m", "ft")
MAX_DISPLAY_BINS = 4000
"""Longer curves are min-max decimated to twice this many points for drawing."""
SAND_BRUSH = "#f2d16b"
CROSSOVER_BRUSH = "#f4a259"


def _plot_values(curve: LogCurve | None, scale: TrackScale) -> np.ndarray | None:
    if curve is None:
        return None
    values = curve.values
    if scale.log:
        values = np.where(values > 0, values, np.nan)
    return values


class ShadeItem(pg.GraphicsObject):
    """Fills the area between two curves ``x1(y)`` and ``x2(y)``, broken at NaN gaps.

    The whole fill is one path, built with numpy, so it costs the same as drawing one curve.
    """

    def __init__(self, color: str) -> None:
        super().__init__()
        brush = QColor(color)
        brush.setAlpha(150)
        self._brush = pg.mkBrush(brush)
        self._path = QPainterPath()

    def set_data(self, x1: np.ndarray | None, x2: np.ndarray | None, y: np.ndarray | None) -> None:
        self.prepareGeometryChange()
        self._path = QPainterPath()
        if x1 is None or x2 is None or y is None or y.size < 2:
            self.update()
            return
        ok = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
        # Split into runs of valid samples; each run becomes a closed polygon (x1 down, x2 up).
        edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        connect: list[np.ndarray] = []
        for start, stop in zip(edges[::2], edges[1::2], strict=True):
            if stop - start < 2:
                continue
            xs.append(np.concatenate([x1[start:stop], x2[start:stop][::-1]]))
            ys.append(np.concatenate([y[start:stop], y[start:stop][::-1]]))
            run = np.ones(2 * (stop - start), dtype=bool)
            run[-1] = False
            connect.append(run)
        if xs:
            self._path = pg.arrayToQPath(
                np.concatenate(xs), np.concatenate(ys), connect=np.concatenate(connect)
            )
        self.update()

    def boundingRect(self) -> QRectF:
        return self._path.boundingRect()

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._brush)
        painter.drawPath(self._path)


class TrackScalesDialog(QDialog):
    """Edit each track's value range and the GR sand cut-off."""

    def __init__(
        self, scales: dict[CurveFamily, TrackScale], cutoff: float, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Track Scales")
        self._scales = scales
        self.spins: dict[CurveFamily, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        grid = QGridLayout()
        grid.addWidget(QLabel("Left"), 0, 1)
        grid.addWidget(QLabel("Right"), 0, 2)
        for row, (family, scale) in enumerate(scales.items(), start=1):
            low, high = (QDoubleSpinBox(minimum=-1e6, maximum=1e6, decimals=3) for _ in range(2))
            # Shown left to right as they appear on screen; reversed tracks run high to low.
            left, right = (scale.hi, scale.lo) if scale.reversed else (scale.lo, scale.hi)
            low.setValue(left)
            high.setValue(right)
            grid.addWidget(QLabel(family.value), row, 0)
            grid.addWidget(low, row, 1)
            grid.addWidget(high, row, 2)
            self.spins[family] = (low, high)
        self.cutoff = QDoubleSpinBox(minimum=0.0, maximum=1000.0, value=cutoff)
        grid.addWidget(QLabel("GR sand cut-off"), len(scales) + 1, 0)
        grid.addWidget(self.cutoff, len(scales) + 1, 1)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        restore = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        restore.clicked.connect(self.restore_defaults)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def restore_defaults(self) -> None:
        for family, (low, high) in self.spins.items():
            scale = DEFAULT_SCALES[family]
            left, right = (scale.hi, scale.lo) if scale.reversed else (scale.lo, scale.hi)
            low.setValue(left)
            high.setValue(right)
        self.cutoff.setValue(GR_SAND_CUTOFF)

    def result_scales(self) -> dict[CurveFamily, TrackScale]:
        """New scales. Raises ``ValueError`` if a range is empty or invalid for a log track."""
        out = {}
        for family, (low, high) in self.spins.items():
            scale = self._scales[family]
            left, right = low.value(), high.value()
            lo, hi = (right, left) if scale.reversed else (left, right)
            if hi <= lo:
                raise ValueError(f"{family.value}: the range must not be empty or inverted")
            if scale.log and lo <= 0:
                raise ValueError(f"{family.value}: a logarithmic scale needs positive values")
            out[family] = replace(scale, lo=lo, hi=hi)
        return out

    def accept(self) -> None:
        try:
            self.result_scales()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


def ask_track_scales(
    parent: QWidget, scales: dict[CurveFamily, TrackScale], cutoff: float
) -> tuple[dict[CurveFamily, TrackScale], float] | None:
    """Module-level so tests can replace it. Returns new scales and cut-off, or ``None``."""
    dialog = TrackScalesDialog(scales, cutoff, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.result_scales(), dialog.cutoff.value()
    return None


class LogViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.logs: list[WellLog] = []
        self.current: WellLog | None = None
        self.scales: dict[CurveFamily, TrackScale] = dict(DEFAULT_SCALES)
        self.sand_cutoff = GR_SAND_CUTOFF
        self.trajectory: Trajectory | None = None
        self.tops: list[Top] = []
        self._kb: float | None = None
        self._well: tuple[str, str, float | None] | None = None
        self._top_lines: list[tuple[pg.PlotItem, pg.InfiniteLine]] = []

        self.header = QLabel(EMPTY_TEXT)
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.source_combo = QComboBox()
        self.source_combo.setVisible(False)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        top = QHBoxLayout()
        top.addWidget(self.header, 1)
        top.addWidget(self.source_combo)

        self.depth_combo = QComboBox()
        self.depth_combo.addItems(DEPTH_REFERENCES)
        self.depth_combo.setToolTip("Depth reference: measured, true vertical, or below sea level")
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(DEPTH_UNITS)
        self.sand_check = QCheckBox("Sand")
        self.sand_check.setToolTip("Shade GR below the sand cut-off")
        self.sand_check.setChecked(True)
        self.crossover_check = QCheckBox("Crossover")
        self.crossover_check.setToolTip("Shade neutron–density crossover")
        self.crossover_check.setChecked(True)
        self.scales_button = QPushButton("Scales…")
        self.scales_button.clicked.connect(self.edit_scales)
        for widget in (self.depth_combo, self.unit_combo):
            widget.currentIndexChanged.connect(self._redraw)
        for check in (self.sand_check, self.crossover_check):
            check.toggled.connect(self._redraw)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Depth"))
        controls.addWidget(self.depth_combo)
        controls.addWidget(self.unit_combo)
        controls.addStretch(1)
        controls.addWidget(self.sand_check)
        controls.addWidget(self.crossover_check)
        controls.addWidget(self.scales_button)

        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground("w")
        self.gr_plot = self._make_track(0, GR_SCALE)
        self.res_plot = self._make_track(1, RES_SCALE)
        self.nd_plot = self._make_track(2, NPHI_SCALE)
        for plot in (self.res_plot, self.nd_plot):
            plot.setYLink(self.gr_plot)
            plot.getAxis("left").setStyle(showValues=False)
            plot.getAxis("left").setWidth(4)
        self.gr_plot.getAxis("left").setLabel("MD")

        self.sand_shade = ShadeItem(SAND_BRUSH)
        self.gr_plot.addItem(self.sand_shade)
        self.crossover_shade = ShadeItem(CROSSOVER_BRUSH)
        self.nd_plot.addItem(self.crossover_shade)
        self.gr_curve = self._add_curve(self.gr_plot, GR_SCALE)
        self.res_curve = self._add_curve(self.res_plot, RES_SCALE)
        self.nphi_curve = self._add_curve(self.nd_plot, NPHI_SCALE, dashed=True)

        # Density shares the neutron track but has its own x scale, drawn on the bottom axis.
        self.rhob_view = pg.ViewBox(enableMouse=False)
        self.nd_plot.scene().addItem(self.rhob_view)
        self.nd_plot.showAxis("bottom")
        bottom = self.nd_plot.getAxis("bottom")
        bottom.linkToView(self.rhob_view)
        bottom.setPen(pg.mkPen(RHOB_SCALE.color))
        bottom.setTextPen(pg.mkPen(RHOB_SCALE.color))
        self.rhob_view.setYLink(self.nd_plot.getViewBox())
        self.rhob_curve = pg.PlotDataItem(
            pen=pg.mkPen(RHOB_SCALE.color, width=1.2), connect="finite"
        )
        self.rhob_view.addItem(self.rhob_curve)
        self.nd_plot.getViewBox().sigResized.connect(self._sync_rhob_geometry)
        self._apply_scales()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addLayout(controls)
        layout.addWidget(self.graphics, 1)
        self.setMinimumWidth(380)

    # --- construction ------------------------------------------------------------------------
    def _make_track(self, col: int, scale: TrackScale) -> pg.PlotItem:
        plot: pg.PlotItem = self.graphics.addPlot(row=0, col=col)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.invertY(True)
        plot.setMouseEnabled(x=False, y=True)
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.showAxis("top")
        plot.hideAxis("bottom")
        for name in ("top", "bottom", "left"):
            plot.getAxis(name).enableAutoSIPrefix(False)
        top = plot.getAxis("top")
        top.setPen(pg.mkPen(scale.color))
        top.setTextPen(pg.mkPen(scale.color))
        if scale.log:
            plot.setLogMode(x=True, y=False)
        plot.getViewBox().invertX(scale.reversed)
        return plot

    @staticmethod
    def _add_curve(plot: pg.PlotItem, scale: TrackScale, dashed: bool = False) -> pg.PlotDataItem:
        pen = pg.mkPen(
            scale.color, width=1.2, style=Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        )
        curve = pg.PlotDataItem(pen=pen, connect="finite")
        plot.addItem(curve)
        return curve

    def _sync_rhob_geometry(self) -> None:
        self.rhob_view.setGeometry(self.nd_plot.getViewBox().sceneBoundingRect())

    def _apply_scales(self) -> None:
        for plot, family in (
            (self.gr_plot, CurveFamily.GR),
            (self.res_plot, CurveFamily.RES_DEEP),
            (self.nd_plot, CurveFamily.NPHI),
        ):
            scale = self.scales[family]
            if scale.log:
                plot.setXRange(math.log10(scale.lo), math.log10(scale.hi), padding=0)
            else:
                plot.setXRange(scale.lo, scale.hi, padding=0)
        rhob = self.scales[CurveFamily.RHOB]
        self.rhob_view.setXRange(rhob.lo, rhob.hi, padding=0)

    def edit_scales(self) -> None:
        answer = ask_track_scales(self, self.scales, self.sand_cutoff)
        if answer is not None:
            self.scales, self.sand_cutoff = answer
            self._apply_scales()
            self._redraw()

    # --- data --------------------------------------------------------------------------------
    def show_well(
        self,
        well_name: str,
        logs: list[WellLog],
        uwi: str = "",
        kb: float | None = None,
        trajectory: Trajectory | None = None,
        tops: list[Top] | None = None,
    ) -> None:
        """Show ``logs`` for a well. Several LAS files for one well can be picked from a combo."""
        self.logs = list(logs)
        self._well = (well_name, uwi, kb)
        self._kb = kb if kb is not None and math.isfinite(kb) else None
        self.trajectory = trajectory
        self.tops = list(tops or [])
        # TVDSS needs a KB elevation.
        tvdss_item = self.depth_combo.model().item(DEPTH_REFERENCES.index("TVDSS"))
        tvdss_item.setEnabled(self._kb is not None)
        if self._kb is None and self.depth_reference == "TVDSS":
            self.depth_combo.setCurrentText("TVD")
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for log in self.logs:
            self.source_combo.addItem(Path(log.source).name)
        self.source_combo.setVisible(len(self.logs) > 1)
        self.source_combo.blockSignals(False)
        if self.logs:
            self.set_log(self.logs[0])
        else:
            self.clear(f"<b>{escape(well_name)}</b>: no logs loaded for this well.")
            self._draw_tops()
            self._fit_depth_axis(self.display_depth(np.array([t.md for t in self.tops])))

    @property
    def depth_reference(self) -> str:
        return self.depth_combo.currentText()

    @property
    def depth_unit(self) -> str:
        return self.unit_combo.currentText()

    def display_depth(self, md_metres: np.ndarray) -> np.ndarray:
        """Convert measured depths (metres) to the selected reference and unit."""
        reference = self.depth_reference
        depth = np.asarray(md_metres, dtype=np.float64)
        if reference in ("TVD", "TVDSS") and self.trajectory is not None:
            depth = self.trajectory.md_to_tvd(depth)
        if reference == "TVDSS" and self._kb is not None:
            depth = depth - self._kb
        return depth / METRES_PER_FOOT if self.depth_unit == "ft" else depth

    def _on_source_changed(self, index: int) -> None:
        if 0 <= index < len(self.logs):
            self.set_log(self.logs[index])

    def _redraw(self) -> None:
        if self.current is not None:
            self.set_log(self.current)
        else:
            self._draw_tops()
            self._fit_depth_axis(self.display_depth(np.array([t.md for t in self.tops])))

    def _fit_depth_axis(self, depth: np.ndarray) -> None:
        self.gr_plot.getAxis("left").setLabel(f"{self.depth_reference} ({self.depth_unit})")
        finite = depth[np.isfinite(depth)]
        if finite.size:
            pad = max(10.0, 0.05 * float(np.ptp(finite)))
            self.gr_plot.setYRange(float(finite.min()) - pad, float(finite.max()) + pad, padding=0)

    def set_log(self, log: WellLog) -> None:
        self.current = log
        well_name, uwi, kb = self._well or (log.well_name, log.uwi, None)
        # Rich-text header built from data-file values: escape every part.
        details = [f"<b>{escape(well_name)}</b>"]
        if uwi:
            details.append(f"UWI {escape(uwi)}")
        if kb is not None and math.isfinite(kb):
            details.append(f"KB {kb:.1f}")
        if self.trajectory is not None and not self.trajectory.is_vertical:
            details.append(f"deviated, max {float(self.trajectory.inc.max()):.0f}°")
        details.append(escape(Path(log.source).name))
        self.header.setText(" · ".join(details))

        depth = self.display_depth(log.depth * metres_per_unit(log.depth_unit))
        curves = {}
        for plot, curve, family in (
            (self.gr_plot, self.gr_curve, CurveFamily.GR),
            (self.res_plot, self.res_curve, CurveFamily.RES_DEEP),
            (self.nd_plot, self.nphi_curve, CurveFamily.NPHI),
        ):
            scale = self.scales[family]
            source = log.curve_for(family)
            curves[family] = _plot_values(source, scale)
            self._set_curve(curve, depth, curves[family])
            plot.getAxis("top").setLabel(self._title(scale, source))
        rhob_scale = self.scales[CurveFamily.RHOB]
        rhob = log.curve_for(CurveFamily.RHOB)
        curves[CurveFamily.RHOB] = _plot_values(rhob, rhob_scale)
        self._set_curve(self.rhob_curve, depth, curves[CurveFamily.RHOB])
        self.nd_plot.getAxis("bottom").setLabel(self._title(rhob_scale, rhob))
        self._draw_shading(depth, curves)
        self._draw_tops()

        self._fit_depth_axis(depth)
        self._sync_rhob_geometry()

    def _draw_shading(
        self, depth: np.ndarray, curves: dict[CurveFamily, np.ndarray | None]
    ) -> None:
        gr = curves[CurveFamily.GR]
        if self.sand_check.isChecked() and gr is not None:
            cutoff = np.full(gr.shape, self.sand_cutoff)
            with np.errstate(invalid="ignore"):
                self.sand_shade.set_data(np.minimum(gr, cutoff), cutoff, depth)
        else:
            self.sand_shade.set_data(None, None, None)
        nphi, rhob = curves[CurveFamily.NPHI], curves[CurveFamily.RHOB]
        if self.crossover_check.isChecked() and nphi is not None and rhob is not None:
            n_scale, r_scale = self.scales[CurveFamily.NPHI], self.scales[CurveFamily.RHOB]
            # Express density in the neutron track's units: same position on screen.
            fraction = (rhob - r_scale.lo) / (r_scale.hi - r_scale.lo)
            if n_scale.reversed:
                rhob_as_nphi = n_scale.hi - fraction * (n_scale.hi - n_scale.lo)
                with np.errstate(invalid="ignore"):
                    edge = np.maximum(rhob_as_nphi, nphi)
            else:
                rhob_as_nphi = n_scale.lo + fraction * (n_scale.hi - n_scale.lo)
                with np.errstate(invalid="ignore"):
                    edge = np.minimum(rhob_as_nphi, nphi)
            self.crossover_shade.set_data(nphi, edge, depth)
        else:
            self.crossover_shade.set_data(None, None, None)

    def _draw_tops(self) -> None:
        for plot, line in self._top_lines:
            plot.removeItem(line)
        self._top_lines = []
        if not self.tops:
            return
        colors = surface_colors(surface_order(self.tops))
        depths = self.display_depth(np.array([t.md for t in self.tops]))
        for top, depth in zip(self.tops, depths, strict=True):
            if not math.isfinite(depth):
                continue
            pen = pg.mkPen(colors[top.surface], width=1.5, style=Qt.PenStyle.DashLine)
            for plot in (self.gr_plot, self.res_plot, self.nd_plot):
                line = pg.InfiniteLine(pos=float(depth), angle=0, pen=pen, movable=False)
                if plot is self.gr_plot:
                    # InfLineLabel sets plain text (TextItem.setText), so names from the data
                    # file are shown literally; braces are doubled because it formats the text.
                    name = top.surface.replace("{", "{{").replace("}", "}}")
                    label = pg.InfLineLabel(
                        line, text=name, position=0.05, anchors=[(0, 1), (0, 1)]
                    )
                    label.setColor(colors[top.surface])
                plot.addItem(line)
                self._top_lines.append((plot, line))

    @staticmethod
    def _set_curve(curve: pg.PlotDataItem, depth: np.ndarray, values: np.ndarray | None) -> None:
        if values is None:
            curve.clear()
            return
        # pyqtgraph's own downsampling assumes x is the independent axis, so decimate by depth.
        shown_depth, shown = minmax_decimate(depth, values, MAX_DISPLAY_BINS)
        curve.setData(x=shown, y=shown_depth)

    @staticmethod
    def _title(scale: TrackScale, curve: LogCurve | None) -> str:
        if curve is None:
            return f"{scale.family.value} (missing)"
        unit = f" [{curve.unit}]" if curve.unit else ""
        return escape(f"{curve.mnemonic}{unit}")  # pyqtgraph axis labels are HTML

    def clear(self, message: str = EMPTY_TEXT) -> None:
        self.current = None
        self.header.setText(message)
        for curve in (self.gr_curve, self.res_curve, self.nphi_curve, self.rhob_curve):
            curve.clear()
        self.sand_shade.set_data(None, None, None)
        self.crossover_shade.set_data(None, None, None)
        for plot, line in self._top_lines:
            plot.removeItem(line)
        self._top_lines = []
