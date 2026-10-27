"""Axis-aligned map extents in project CRS units."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Extent:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @classmethod
    def from_bounds(cls, bounds: Iterable[float]) -> Extent:
        """Build from (minx, miny, maxx, maxy), as returned by shapely/geopandas/rasterio."""
        xmin, ymin, xmax, ymax = (float(v) for v in bounds)
        return cls(xmin, ymin, xmax, ymax)

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def is_valid(self) -> bool:
        """True for finite extents with non-negative size (a single point is valid)."""
        values = (self.xmin, self.ymin, self.xmax, self.ymax)
        return all(math.isfinite(v) for v in values) and self.width >= 0 and self.height >= 0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    def union(self, other: Extent) -> Extent:
        return Extent(
            min(self.xmin, other.xmin),
            min(self.ymin, other.ymin),
            max(self.xmax, other.xmax),
            max(self.ymax, other.ymax),
        )

    def intersection(self, other: Extent) -> Extent | None:
        xmin, ymin = max(self.xmin, other.xmin), max(self.ymin, other.ymin)
        xmax, ymax = min(self.xmax, other.xmax), min(self.ymax, other.ymax)
        if xmin > xmax or ymin > ymax:
            return None
        return Extent(xmin, ymin, xmax, ymax)

    def intersects(self, other: Extent) -> bool:
        return self.intersection(other) is not None

    def buffered(self, fraction: float = 0.05, minimum: float = 1.0) -> Extent:
        """Grow by ``fraction`` of the larger side (at least ``minimum``) on every edge."""
        pad = max(max(self.width, self.height) * fraction, minimum)
        return Extent(self.xmin - pad, self.ymin - pad, self.xmax + pad, self.ymax + pad)


def union_all(extents: Iterable[Extent | None]) -> Extent | None:
    result: Extent | None = None
    for extent in extents:
        if extent is None or not extent.is_valid():
            continue
        result = extent if result is None else result.union(extent)
    return result
