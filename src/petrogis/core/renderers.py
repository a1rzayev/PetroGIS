"""Which symbol each feature gets: the renderer-neutral half of styling.

``classify_features`` turns a vector style into groups of row positions with a colour and label,
and ``classify_wells`` does the same for well status symbols. The graphics layer draws one item
per group, and the legend lists the groups, so both always agree. Nothing here imports Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from petrogis.core.styles import VectorStyle, WellStyle
from petrogis.core.wells import WELL_SYMBOLS, WellStatus

if TYPE_CHECKING:
    import pandas as pd

OTHER_LABEL = "Other"


@dataclass(frozen=True)
class SymbolClass:
    color: str
    label: str
    rows: NDArray[np.intp]
    """Positional rows drawn with this symbol."""
    is_other: bool = False


@dataclass(frozen=True)
class Classification:
    classes: list[SymbolClass]
    """In drawing and legend order. Classes with no rows are kept so the legend shows them."""
    error: str | None = None
    """Why the style could not be applied (the layer then draws with its single colour)."""


def _group(index: NDArray[np.intp], count: int) -> list[NDArray[np.intp]]:
    """Rows per class 0..count-1 and, last, the rows with index -1, in one sort."""
    order = np.argsort(index, kind="stable")
    bounds = np.searchsorted(index[order], np.arange(-1, count + 1))
    groups = [order[bounds[i] : bounds[i + 1]] for i in range(count + 1)]
    return groups[1:] + groups[:1]


def _single(style: VectorStyle, n: int, error: str | None = None) -> Classification:
    rows = np.arange(n, dtype=np.intp)
    return Classification([SymbolClass(style.color, "", rows)], error)


def classify_features(style: VectorStyle, frame: pd.DataFrame | None) -> Classification:
    """Group the rows of ``frame`` by the style's categories or classes.

    Features no category or class matches are drawn with ``style.color`` as "Other". An
    expression that fails (for example a field that no longer exists) does not raise: the layer
    falls back to the single colour and ``error`` says why.
    """
    from petrogis.core.classify import assign_breaks, assign_categories
    from petrogis.core.expressions import Expression, ExpressionError
    from petrogis.core.expressions.evaluate import as_num

    n = 0 if frame is None else len(frame)
    if style.renderer == "single" or frame is None or n == 0:
        return _single(style, n)
    try:
        values = Expression(style.classify_by).evaluate(frame)
        if style.renderer == "categorized":
            index = assign_categories(values, [c.value for c in style.categories])
            symbols = [(c.color, c.display_label) for c in style.categories]
        else:
            numbers = as_num(values).to_numpy(dtype=float, na_value=np.nan)
            index = assign_breaks(numbers, [(b.lower, b.upper) for b in style.breaks])
            symbols = [(b.color, b.display_label) for b in style.breaks]
    except ExpressionError as exc:
        return _single(style, n, error=str(exc))
    groups = _group(index, len(symbols))
    classes = [
        SymbolClass(color, label, rows)
        for (color, label), rows in zip(symbols, groups[:-1], strict=True)
    ]
    if groups[-1].size:
        classes.append(SymbolClass(style.color, OTHER_LABEL, groups[-1], is_other=True))
    return Classification(classes)


@dataclass(frozen=True)
class StatusGroup:
    status: WellStatus
    rows: NDArray[np.intp]


def well_status_column(style: WellStyle, frame: pd.DataFrame | None) -> str | None:
    from petrogis.core.wells import detect_status_column

    if frame is None:
        return None
    if style.status_field:
        return style.status_field if style.status_field in frame.columns else None
    candidates = [c for c in frame.columns if c not in ("name", "uwi", "kb", "td", "name_key")]
    return detect_status_column(candidates)


def classify_wells(style: WellStyle, frame: pd.DataFrame | None) -> list[StatusGroup]:
    """Wells grouped by status, in ``WELL_SYMBOLS`` order with ``UNKNOWN`` last.

    With status symbols off, or no status column, every well is ``UNKNOWN``.
    """
    from petrogis.core.wells import classify_statuses

    n = 0 if frame is None else len(frame)
    column = well_status_column(style, frame) if style.symbol_by_status else None
    if frame is None or column is None:
        return [StatusGroup(WellStatus.UNKNOWN, np.arange(n, dtype=np.intp))] if n else []
    order = [*WELL_SYMBOLS, WellStatus.UNKNOWN]
    position = {status: i for i, status in enumerate(order)}
    statuses = classify_statuses(frame[column].tolist())
    index = np.fromiter((position[s] for s in statuses), dtype=np.intp, count=n)
    groups = _group(index, len(order))[:-1]
    return [StatusGroup(s, rows) for s, rows in zip(order, groups, strict=True) if rows.size]


def label_texts(expression: str, frame: pd.DataFrame) -> list[str | None]:
    """Label text per row (``None`` for no label). Raises ``ExpressionError``."""
    from petrogis.core.expressions import Expression, format_values

    return format_values(Expression(expression).evaluate(frame))
