"""Well rendering: one scatter for all symbols plus one item that paints every visible label."""

from __future__ import annotations

import logging

import pyqtgraph as pg
from PySide6.QtGui import QColor

from petrogis.core.layers import WellLayer
from petrogis.core.wells import WELL_SYMBOLS, WellStatus
from petrogis.render.pg.items import LayerGraphics
from petrogis.render.pg.label_items import LabelItem
from petrogis.render.pg.symbols import marker_shape

log = logging.getLogger(__name__)
TRANSPARENT = QColor(0, 0, 0, 0)


class WellGraphics(LayerGraphics):
    """Symbols by status (one scatter with per-well symbols) and labels (one painter item)."""

    def __init__(self, layer: WellLayer) -> None:
        self._xy = layer.xy()
        self.paths = pg.PlotCurveItem(connect="finite")
        self.bottoms = pg.ScatterPlotItem(pxMode=True, symbol="s", size=5)
        self.symbols = pg.ScatterPlotItem(pxMode=True, symbol="o")
        self.labels = LabelItem(self._xy, [], layer.style.label_size, layer.style.label_color)
        self._visible = layer.visible
        self._show_labels = layer.style.show_labels
        self._show_paths = layer.style.show_paths
        # Paths under symbols under labels.
        super().__init__([self.paths, self.bottoms, self.symbols, self.labels])
        self.apply_style(layer)

    def _symbol_arrays(self, layer: WellLayer) -> tuple[list, list, list]:
        from petrogis.core.renderers import classify_wells

        n = len(self._xy)
        style = layer.style
        symbols: list = ["o"] * n
        brushes: list = [None] * n
        pens: list = [None] * n
        for group in classify_wells(style, layer.gdf):
            if group.status is WellStatus.UNKNOWN:
                color, shape_name = QColor(style.color), "circle"
            else:
                symbol = WELL_SYMBOLS[group.status]
                color, shape_name = QColor(symbol.color), symbol.shape
            shape = marker_shape(shape_name)
            brush = pg.mkBrush(color if shape.filled else TRANSPARENT)
            pen = pg.mkPen(color, width=1.6) if shape.stroked else pg.mkPen("#ffffff", width=1)
            for row in group.rows:
                symbols[row] = shape.symbol
                brushes[row] = brush
                pens[row] = pen
        return symbols, brushes, pens

    def apply_style(self, layer: WellLayer) -> None:
        style = layer.style
        self.error = None
        if len(self._xy):
            symbols, brushes, pens = self._symbol_arrays(layer)
            self.symbols.setData(
                pos=self._xy, symbol=symbols, brush=brushes, pen=pens, size=style.symbol_size
            )
        self._set_paths(layer)
        self.labels.set_labels(self._xy, self._label_texts(layer))
        self.labels.set_appearance(style.label_size, style.label_color, style.symbol_size)
        self._show_labels = style.show_labels
        self._show_paths = style.show_paths
        self.set_visible(self._visible)

    def _set_paths(self, layer: WellLayer) -> None:
        paths, bottoms = layer.well_paths()
        color = QColor(layer.style.color)
        self.paths.setPen(pg.mkPen(color, width=1.5, cosmetic=True))
        if paths.size:
            self.paths.setData(paths[:, 0], paths[:, 1])
            self.bottoms.setData(pos=bottoms, brush=pg.mkBrush(color), pen=pg.mkPen("#ffffff"))
        else:
            self.paths.clear()
            self.bottoms.clear()

    def _label_texts(self, layer: WellLayer) -> list[str | None]:
        expression = layer.style.label_expression.strip()
        if not expression or layer.gdf is None:
            return list(layer.names)
        from petrogis.core.expressions import ExpressionError
        from petrogis.core.renderers import label_texts

        try:
            texts = label_texts(expression, layer.gdf)
        except ExpressionError as exc:
            self.error = f"Labels: {exc}"
            log.warning("Well labels for %s: %s", layer.name, exc)
            return list(layer.names)
        return texts

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        self.symbols.setVisible(visible)
        self.labels.setVisible(visible and self._show_labels)
        self.paths.setVisible(visible and self._show_paths)
        self.bottoms.setVisible(visible and self._show_paths)
