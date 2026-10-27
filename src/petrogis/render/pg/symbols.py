"""Marker shapes for well status symbols, as unit-size paths for pyqtgraph scatter plots."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

_R = 0.36  # circle radius inside the unit square used by pyqtgraph symbols


def _circle(path: QPainterPath, radius: float = _R) -> None:
    path.addEllipse(QPointF(0, 0), radius, radius)


def _line(path: QPainterPath, x0: float, y0: float, x1: float, y1: float) -> None:
    path.moveTo(x0, y0)
    path.lineTo(x1, y1)


@dataclass(frozen=True)
class MarkerShape:
    symbol: QPainterPath | str
    filled: bool
    """Fill with the symbol colour (``False`` leaves the inside transparent)."""
    stroked: bool
    """Outline in the symbol colour. Otherwise a thin white outline separates it from the map."""


@cache
def marker_shape(name: str) -> MarkerShape:
    """The marker for a ``core.wells.WellSymbol.shape`` name (a circle for unknown names)."""
    if name == "sun":
        path = QPainterPath()
        _circle(path, 0.26)
        for dx, dy in ((1, 0), (0.71, 0.71), (0, 1), (-0.71, 0.71)):
            _line(path, -0.5 * dx, -0.5 * dy, -0.34 * dx, -0.34 * dy)
            _line(path, 0.34 * dx, 0.34 * dy, 0.5 * dx, 0.5 * dy)
        return MarkerShape(path, filled=True, stroked=True)
    if name == "half":
        # Odd-even fill of a circle plus a half disc leaves exactly one half filled.
        path = QPainterPath()
        _circle(path)
        path.moveTo(0, -_R)
        path.arcTo(-_R, -_R, 2 * _R, 2 * _R, 90, 180)
        path.closeSubpath()
        return MarkerShape(path, filled=True, stroked=True)
    if name == "injector":
        path = QPainterPath()
        _circle(path, 0.28)
        _line(path, 0.2, -0.2, 0.5, -0.5)
        _line(path, 0.5, -0.5, 0.3, -0.5)
        _line(path, 0.5, -0.5, 0.5, -0.3)
        return MarkerShape(path, filled=True, stroked=True)
    if name == "crossed":
        path = QPainterPath()
        _circle(path)
        d = 0.48
        _line(path, -d, -d, d, d)
        _line(path, -d, d, d, -d)
        return MarkerShape(path, filled=False, stroked=True)
    if name == "barred":
        path = QPainterPath()
        _circle(path)
        _line(path, -0.5, 0, 0.5, 0)
        return MarkerShape(path, filled=True, stroked=True)
    if name == "open":
        return MarkerShape("o", filled=False, stroked=True)
    if name == "diamond":
        return MarkerShape("d", filled=True, stroked=False)
    return MarkerShape("o", filled=True, stroked=False)
