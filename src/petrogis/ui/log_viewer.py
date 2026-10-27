"""Log viewer: depth tracks for GR, deep resistivity (log scale) and a neutron-density overlay.

All tracks share one depth axis, inverted so depth increases downwards. Scrolling zooms and
dragging pans in depth only, because each track has fixed, conventional value scales.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from petrogis.core.logs import LogCurve, WellLog
from petrogis.core.mnemonics import CurveFamily


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
EMPTY_TEXT = "Click a well on the map to show its logs."


def _plot_values(curve: LogCurve | None, scale: TrackScale) -> np.ndarray | None:
    if curve is None:
        return None
    values = curve.values
    if scale.log:
        values = np.where(values > 0, values, np.nan)
    return values


class LogViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.logs: list[WellLog] = []
        self.current: WellLog | None = None

        self.header = QLabel(EMPTY_TEXT)
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.source_combo = QComboBox()
        self.source_combo.setVisible(False)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        top = QHBoxLayout()
        top.addWidget(self.header, 1)
        top.addWidget(self.source_combo)

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
        self.rhob_view.setXRange(RHOB_SCALE.lo, RHOB_SCALE.hi, padding=0)
        self.rhob_curve = pg.PlotDataItem(
            pen=pg.mkPen(RHOB_SCALE.color, width=1.2), connect="finite"
        )
        self.rhob_view.addItem(self.rhob_curve)
        self.nd_plot.getViewBox().sigResized.connect(self._sync_rhob_geometry)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.graphics, 1)
        self.setMinimumWidth(360)

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
            plot.setXRange(math.log10(scale.lo), math.log10(scale.hi), padding=0)
        else:
            plot.setXRange(scale.lo, scale.hi, padding=0)
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

    # --- data --------------------------------------------------------------------------------
    def show_well(
        self, well_name: str, logs: list[WellLog], uwi: str = "", kb: float | None = None
    ) -> None:
        """Show ``logs`` for a well. Several LAS files for one well can be picked from a combo."""
        self.logs = list(logs)
        self._well = (well_name, uwi, kb)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for log in self.logs:
            self.source_combo.addItem(Path(log.source).name)
        self.source_combo.setVisible(len(self.logs) > 1)
        self.source_combo.blockSignals(False)
        if self.logs:
            self.set_log(self.logs[0])
        else:
            self.clear(f"<b>{well_name}</b>: no logs loaded for this well.")

    def _on_source_changed(self, index: int) -> None:
        if 0 <= index < len(self.logs):
            self.set_log(self.logs[index])

    def set_log(self, log: WellLog) -> None:
        self.current = log
        well_name, uwi, kb = getattr(self, "_well", (log.well_name, log.uwi, None))
        details = [f"<b>{well_name}</b>"]
        if uwi:
            details.append(f"UWI {uwi}")
        if kb is not None and math.isfinite(kb):
            details.append(f"KB {kb:.1f}")
        details.append(Path(log.source).name)
        self.header.setText(" · ".join(details))

        depth = log.depth
        for plot, curve, scale in (
            (self.gr_plot, self.gr_curve, GR_SCALE),
            (self.res_plot, self.res_curve, RES_SCALE),
            (self.nd_plot, self.nphi_curve, NPHI_SCALE),
        ):
            source = log.curve_for(scale.family)
            self._set_curve(curve, depth, _plot_values(source, scale))
            plot.getAxis("top").setLabel(self._title(scale, source))
        rhob = log.curve_for(RHOB_SCALE.family)
        self._set_curve(self.rhob_curve, depth, _plot_values(rhob, RHOB_SCALE))
        self.nd_plot.getAxis("bottom").setLabel(self._title(RHOB_SCALE, rhob))

        self.gr_plot.getAxis("left").setLabel(f"MD ({log.depth_unit})")
        top, bottom = log.depth_range
        if math.isfinite(top) and math.isfinite(bottom):
            self.gr_plot.setYRange(top, bottom, padding=0.01)
        self._sync_rhob_geometry()

    @staticmethod
    def _set_curve(curve: pg.PlotDataItem, depth: np.ndarray, values: np.ndarray | None) -> None:
        if values is None:
            curve.clear()
        else:
            curve.setData(x=values, y=depth)

    @staticmethod
    def _title(scale: TrackScale, curve: LogCurve | None) -> str:
        if curve is None:
            return f"{scale.family.value} (missing)"
        unit = f" [{curve.unit}]" if curve.unit else ""
        return f"{curve.mnemonic}{unit}"

    def clear(self, message: str = EMPTY_TEXT) -> None:
        self.current = None
        self.header.setText(message)
        for curve in (self.gr_curve, self.res_curve, self.nphi_curve, self.rhob_curve):
            curve.clear()
