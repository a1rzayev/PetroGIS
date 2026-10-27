"""Well log data model and log-to-well matching. This module has no Qt dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from petrogis.core.mnemonics import CurveFamily, resolve
from petrogis.core.wells import normalize_well_name

if TYPE_CHECKING:
    from petrogis.core.layers import WellLayer


@dataclass(frozen=True)
class LogCurve:
    mnemonic: str
    unit: str
    description: str
    values: NDArray[np.float64]
    """Same length as the log's depth array, with NaN where null."""


@dataclass
class WellLog:
    """One LAS file: a depth index plus curves. Depth is MD in ``depth_unit``.

    Phase 1 treats wells as vertical. Trajectories (Phase 2) will map MD to TVD and XY.
    """

    well_name: str
    uwi: str
    source: str
    depth: NDArray[np.float64]
    depth_unit: str
    curves: dict[str, LogCurve] = field(default_factory=dict)
    _families: dict[CurveFamily, str] | None = field(default=None, init=False, repr=False)

    @property
    def name_key(self) -> str:
        return normalize_well_name(self.well_name)

    @property
    def families(self) -> dict[CurveFamily, str]:
        """Curve family to mnemonic, resolved through the alias table (cached)."""
        if self._families is None:
            self._families = resolve(self.curves)
        return self._families

    def curve_for(self, family: CurveFamily) -> LogCurve | None:
        mnemonic = self.families.get(family)
        return None if mnemonic is None else self.curves[mnemonic]

    @property
    def depth_range(self) -> tuple[float, float]:
        finite = self.depth[np.isfinite(self.depth)]
        if finite.size == 0:
            return (float("nan"), float("nan"))
        return float(finite.min()), float(finite.max())


@dataclass
class LogMatch:
    matched: dict[str, list[WellLog]] = field(default_factory=dict)
    """Normalised well name to the logs attached to that well."""
    unmatched: list[WellLog] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(len(logs) for logs in self.matched.values())


def match_logs(logs: list[WellLog], wells: WellLayer) -> LogMatch:
    """Attach each log to a well by normalised WELL name, then UWI, then file name."""
    result = LogMatch()
    uwi_index: dict[str, str] = {}
    if wells.gdf is not None and "uwi" in wells.gdf:
        for uwi, key in zip(wells.gdf["uwi"], wells.gdf["name_key"], strict=True):
            if isinstance(uwi, str) and uwi.strip():
                uwi_index.setdefault(uwi.strip().upper(), str(key))

    for log in logs:
        uwi = log.uwi.strip().upper()
        stem = Path(log.source).stem
        key: str | None = None
        if log.well_name and wells.find(log.well_name) is not None:
            key = normalize_well_name(log.well_name)
        elif uwi and uwi in uwi_index:
            key = uwi_index[uwi]
        elif wells.find(stem) is not None:
            key = normalize_well_name(stem)
        if key is None:
            result.unmatched.append(log)
        else:
            result.matched.setdefault(key, []).append(log)
    return result


FEET_UNITS = {"F", "FT", "FEET", "FOOT", "'"}
METRES_PER_FOOT = 0.3048


def metres_per_unit(unit: str) -> float:
    """Conversion factor for a LAS depth unit: 0.3048 for feet, 1 for metres (or unknown)."""
    return METRES_PER_FOOT if unit.strip().upper().rstrip(".") in FEET_UNITS else 1.0


def minmax_decimate(
    depth: NDArray[np.float64], values: NDArray[np.float64], max_bins: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Reduce a curve to at most ``2 * max_bins`` points without losing spikes.

    Samples are split into ``max_bins`` consecutive runs, and each run keeps its minimum and
    maximum value, in depth order, so a thin bed still shows at full amplitude. Curves with fewer
    samples are returned unchanged. Bins whose values are all NaN keep one NaN, so gaps stay gaps.
    """
    n = depth.size
    if n <= 2 * max_bins or max_bins < 1:
        return depth, values
    edges = np.linspace(0, n, max_bins + 1).astype(int)
    size = int(np.diff(edges).max())
    # Pad every run to the same length so the min/max search is one vectorised call.
    index = edges[:-1, None] + np.arange(size)[None, :]
    valid = index < edges[1:, None]
    index = np.minimum(index, n - 1)
    block = np.where(valid, values[index], np.nan)
    all_nan = np.isnan(block).all(axis=1)
    filled_low = np.where(np.isnan(block), np.inf, block)
    filled_high = np.where(np.isnan(block), -np.inf, block)
    low = index[np.arange(max_bins), filled_low.argmin(axis=1)]
    high = index[np.arange(max_bins), filled_high.argmax(axis=1)]
    first, second = np.minimum(low, high), np.maximum(low, high)
    picks = np.column_stack([first, second]).ravel()
    out_values = values[picks].astype(np.float64)
    out_values[np.repeat(all_nan, 2)] = np.nan
    return depth[picks], out_values
