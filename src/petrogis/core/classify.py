"""Classification for categorized and graduated styles: class breaks, colour ramps, categories.

Everything here is vectorised and Qt-free. Natural breaks (Fisher–Jenks) is exact on a sample of
at most ``JENKS_SAMPLE`` values, which is what keeps it fast on 100k+ features.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

if TYPE_CHECKING:  # pandas is imported lazily: core.styles imports this module at startup
    import pandas as pd

RAMPS: dict[str, tuple[str, ...]] = {
    "viridis": ("#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779",
                "#6ece58", "#b5de2b", "#fde725"),
    "magma": ("#000004", "#1c1044", "#4f127b", "#812581", "#b5367a", "#e55064", "#fb8761",
              "#fec287", "#fcfdbf"),
    "blues": ("#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5",
              "#08519c", "#08306b"),
    "reds": ("#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d",
             "#a50f15", "#67000d"),
    "greens": ("#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45",
               "#006d2c", "#00441b"),
    "spectral": ("#9e0142", "#d53e4f", "#f46d43", "#fdae61", "#fee08b", "#ffffbf", "#e6f598",
                 "#abdda4", "#66c2a5", "#3288bd", "#5e4fa2"),
    "rdylgn": ("#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf", "#d9ef8b",
               "#a6d96a", "#66bd63", "#1a9850", "#006837"),
}  # fmt: skip
"""Colour ramps for graduated styles, as evenly spaced stops (ColorBrewer and matplotlib)."""

QUALITATIVE = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
    "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
)  # fmt: skip
"""Distinct colours for categories (tab20, strongest first), cycled when there are more."""

METHODS = ("equal_interval", "quantile", "natural_breaks")
METHOD_NAMES = {
    "equal_interval": "Equal interval",
    "quantile": "Quantile (equal count)",
    "natural_breaks": "Natural breaks (Jenks)",
}
MAX_AUTO_CATEGORIES = 100
"""Classify creates at most this many categories (the most frequent). The rest draw as "Other"."""
JENKS_SAMPLE = 1500


def _rgb(color: str) -> NDArray[np.float64]:
    return np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)


def ramp_colors(name: str, count: int, invert: bool = False) -> list[str]:
    """``count`` colours spread evenly along ramp ``name``, interpolated in RGB."""
    stops = np.array([_rgb(c) for c in RAMPS[name]])
    if invert:
        stops = stops[::-1]
    if count <= 0:
        return []
    last = len(stops) - 1
    positions = np.linspace(0, last, count) if count > 1 else np.array([last / 2])
    low = np.floor(positions).astype(int)
    high = np.minimum(low + 1, len(stops) - 1)
    frac = (positions - low)[:, None]
    rgb = np.rint(stops[low] * (1 - frac) + stops[high] * frac).astype(int)
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in rgb]


def category_colors(count: int) -> list[str]:
    return [QUALITATIVE[i % len(QUALITATIVE)] for i in range(count)]


def _finite_values(values: ArrayLike) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=float).ravel()
    return array[np.isfinite(array)]


def equal_interval(values: ArrayLike, count: int) -> list[float]:
    data = _finite_values(values)
    if data.size == 0:
        return []
    return [float(v) for v in np.linspace(data.min(), data.max(), count + 1)]


def quantile_breaks(values: ArrayLike, count: int) -> list[float]:
    data = _finite_values(values)
    if data.size == 0:
        return []
    return [float(v) for v in np.quantile(data, np.linspace(0, 1, count + 1))]


def natural_breaks(values: ArrayLike, count: int, sample: int = JENKS_SAMPLE) -> list[float]:
    """Fisher–Jenks optimal breaks (minimum within-class sum of squares).

    Exact dynamic programming on the sorted unique values, vectorised over one axis. Larger inputs
    are first reduced to ``sample`` evenly spaced order statistics, always keeping the extremes.
    """
    data = np.unique(_finite_values(values))
    if data.size == 0:
        return []
    if data.size > sample:
        data = data[np.linspace(0, data.size - 1, sample).round().astype(int)]
    n = data.size
    k = min(count, n)
    if k <= 1:
        return [float(data[0]), float(data[-1])]
    s1 = np.concatenate([[0.0], np.cumsum(data)])
    s2 = np.concatenate([[0.0], np.cumsum(data * data)])
    # ssd[j, i]: sum of squared deviations of data[j:i] (i exclusive), valid for j < i.
    j = np.arange(n + 1)[:, None]
    i = np.arange(n + 1)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (i - j).astype(float)
        ssd = s2[i] - s2[j] - (s1[i] - s1[j]) ** 2 / width
    ssd[i <= j] = np.inf
    cost = ssd[0].copy()  # one class covering data[0:i]
    choices = []
    for _ in range(1, k):
        total = cost[:, None] + ssd
        choice = np.argmin(total, axis=0)
        cost = total[choice, np.arange(n + 1)]
        choices.append(choice)
    # Walk back from the full range to recover where each class starts.
    starts = []
    end = n
    for choice in reversed(choices):
        end = int(choice[end])
        starts.append(end)
    starts.reverse()
    return [float(data[0])] + [float(data[s - 1]) for s in starts] + [float(data[-1])]


def class_breaks(values: ArrayLike, method: str, count: int) -> list[float]:
    """Ascending edges of up to ``count`` classes; duplicate edges are merged."""
    if method not in METHODS:
        raise ValueError(f"Unknown classification method {method!r}")
    count = max(1, int(count))
    fn = {"equal_interval": equal_interval, "quantile": quantile_breaks}.get(method)
    edges = fn(values, count) if fn else natural_breaks(values, count)
    if not edges:
        return []
    unique = sorted(set(edges))
    return unique if len(unique) > 1 else [unique[0], unique[0]]


def python_value(value: Any) -> Any:
    """A JSON-friendly Python scalar for a pandas/numpy value (NULL becomes ``None``)."""
    import pandas as pd

    if value is None or value is pd.NA:
        return None
    if isinstance(value, float | np.floating) and np.isnan(value):
        return None
    if isinstance(value, bool | np.bool_):
        return bool(value)
    if isinstance(value, int | np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        return float(value)
    return str(value)


def unique_categories(values: pd.Series, limit: int = MAX_AUTO_CATEGORIES) -> list[Any]:
    """Distinct values for a categorized style, sorted, with NULL last.

    When there are more than ``limit`` values, only the most frequent ``limit`` are kept.
    """
    counts = values.value_counts(dropna=True, sort=True)
    kept = [python_value(v) for v in counts.index[:limit]]
    has_null = bool(values.isna().any())
    kept.sort(key=lambda v: (isinstance(v, str), v if not isinstance(v, str) else v.casefold()))
    if has_null and len(kept) < limit:
        kept.append(None)
    return kept


def category_key(value: Any) -> tuple[str, Any] | None:
    """Comparable key for matching data values to category values (1 and 1.0 match)."""
    value = python_value(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, int | float):
        return ("n", float(value))
    return ("s", value)


def assign_categories(values: pd.Series, categories: list[Any]) -> NDArray[np.intp]:
    """Index of the matching category for each value, or -1 when none matches."""
    import pandas as pd

    lookup: dict[tuple[str, Any] | None, int] = {}
    for index, category in enumerate(categories):
        lookup.setdefault(category_key(category), index)
    codes, uniques = pd.factorize(values, use_na_sentinel=True)
    table = np.array([lookup.get(category_key(u), -1) for u in uniques] + [-1], dtype=np.intp)
    out = table[codes]  # code -1 (NULL) picks the trailing -1
    if None in lookup:
        out[codes == -1] = lookup[None]
    return out


def assign_breaks(values: ArrayLike, bounds: list[tuple[float, float]]) -> NDArray[np.intp]:
    """Index of the class holding each value, or -1 (NULL or out of range).

    A class covers ``lower < v <= upper``, and the first class also includes its lower bound,
    the same rule QGIS uses. When classes overlap, the first match wins.
    """
    data = np.asarray(values, dtype=float)
    out = np.full(data.shape, -1, dtype=np.intp)
    with np.errstate(invalid="ignore"):
        for index, (lower, upper) in enumerate(bounds):
            above = data >= lower if index == 0 else data > lower
            hit = (out == -1) & above & (data <= upper)
            out[hit] = index
    return out


def format_break(value: float) -> str:
    """Compact number for class labels: integers as-is, otherwise 4 significant digits."""
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    if abs(value) >= 1000:
        return f"{value:.1f}"
    return f"{value:.4g}"
