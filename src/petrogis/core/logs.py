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
