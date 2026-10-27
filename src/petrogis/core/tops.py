"""Formation tops: picks of a named surface at a measured depth in a well. No Qt dependency."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from petrogis.core.classify import QUALITATIVE

PICKED = "picked"
"""``Top.source`` of picks made interactively in the correlation panel."""


@dataclass(frozen=True)
class Top:
    well: str
    """Well name as written in the source file."""
    name_key: str
    """Normalised well name (``core.wells.normalize_well_name``), the matching key."""
    surface: str
    md: float
    """Measured depth in metres, from the same datum as the well's logs and survey (KB)."""
    source: str = ""


def surface_order(tops: Iterable[Top]) -> list[str]:
    """Surface names in stratigraphic order: shallowest median MD first."""
    depths: dict[str, list[float]] = {}
    for top in tops:
        depths.setdefault(top.surface, []).append(top.md)
    return sorted(depths, key=lambda s: (float(np.median(depths[s])), s.casefold()))


def surface_colors(surfaces: list[str]) -> dict[str, str]:
    """A stable display colour per surface, in stratigraphic order."""
    return {s: QUALITATIVE[i % len(QUALITATIVE)] for i, s in enumerate(surfaces)}
