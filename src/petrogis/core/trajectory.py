"""Well trajectories from deviation surveys, by the minimum-curvature method. No Qt dependency.

A survey is a list of stations (measured depth, inclination, azimuth). Between two stations the
well is assumed to follow a circular arc, which is what the minimum-curvature method computes
exactly. All depths and offsets are in metres. Azimuths are taken relative to grid north (no
convergence correction), offsets are east (``dx``) and north (``dy``) of the surface location, and
TVD is measured down from the same depth datum as MD (the KB for LAS logs).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]


def _as_array(values: ArrayLike) -> FloatArray:
    return np.atleast_1d(np.asarray(values, dtype=np.float64))


def minimum_curvature(
    md: ArrayLike, inc: ArrayLike, azi: ArrayLike
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """TVD, east and north offsets at each station (the first station is the origin).

    ``inc`` and ``azi`` are in degrees. Uses the ratio factor ``RF = 2/β · tan(β/2)``, where β is
    the dogleg angle, and its limit ``RF → 1`` for straight segments.
    """
    md, inc_deg, azi_deg = _as_array(md), _as_array(inc), _as_array(azi)
    i = np.radians(inc_deg)
    a = np.radians(azi_deg)
    i1, i2, a1, a2 = i[:-1], i[1:], a[:-1], a[1:]
    d_md = np.diff(md)
    cos_beta = np.cos(i2 - i1) - np.sin(i1) * np.sin(i2) * (1.0 - np.cos(a2 - a1))
    beta = np.arccos(np.clip(cos_beta, -1.0, 1.0))
    small = beta < 1e-7
    with np.errstate(divide="ignore", invalid="ignore"):
        rf = np.where(small, 1.0 + beta**2 / 12.0, 2.0 / beta * np.tan(beta / 2.0))
    half = d_md / 2.0 * rf
    d_tvd = half * (np.cos(i1) + np.cos(i2))
    d_east = half * (np.sin(i1) * np.sin(a1) + np.sin(i2) * np.sin(a2))
    d_north = half * (np.sin(i1) * np.cos(a1) + np.sin(i2) * np.cos(a2))
    start = np.zeros(1)
    return (
        np.concatenate([start, np.cumsum(d_tvd)]),
        np.concatenate([start, np.cumsum(d_east)]),
        np.concatenate([start, np.cumsum(d_north)]),
    )


@dataclass(frozen=True, eq=False)
class Trajectory:
    """Stations of one well path. Build with :meth:`from_survey`."""

    md: FloatArray
    inc: FloatArray
    azi: FloatArray
    tvd: FloatArray
    dx: FloatArray
    """East offset from the surface location."""
    dy: FloatArray
    """North offset from the surface location."""
    source: str = ""
    """File the survey came from."""

    @classmethod
    def from_survey(
        cls, md: ArrayLike, inc: ArrayLike, azi: ArrayLike, source: str = ""
    ) -> Trajectory:
        """Validate the stations and compute the path.

        Stations are sorted by MD. If the survey starts below the surface, a vertical tie-in
        station at MD 0 is added, the usual assumption for surveys that omit the surface.
        Raises ``ValueError`` with a user-facing message on invalid input.
        """
        md, inc, azi = _as_array(md), _as_array(inc), _as_array(azi)
        if not (md.size == inc.size == azi.size):
            raise ValueError("MD, inclination and azimuth must have the same length")
        if md.size == 0:
            raise ValueError("The survey has no stations")
        if not (np.isfinite(md).all() and np.isfinite(inc).all() and np.isfinite(azi).all()):
            raise ValueError("The survey has missing or non-numeric values")
        order = np.argsort(md, kind="stable")
        md, inc, azi = md[order], inc[order], azi[order]
        if (np.diff(md) == 0).any():
            raise ValueError(f"Duplicate survey depth {md[np.flatnonzero(np.diff(md) == 0)[0]]:g}")
        if md[0] < 0:
            raise ValueError("Measured depth cannot be negative")
        if ((inc < 0) | (inc > 180)).any():
            raise ValueError("Inclination must be between 0 and 180 degrees")
        azi = np.mod(azi, 360.0)
        if md[0] > 0:
            md = np.concatenate([[0.0], md])
            inc = np.concatenate([[0.0], inc])
            azi = np.concatenate([[azi[0]], azi])
        tvd, dx, dy = minimum_curvature(md, inc, azi)
        return cls(md, inc, azi, tvd, dx, dy, source)

    @classmethod
    def vertical(cls, total_depth: float, source: str = "") -> Trajectory:
        depth = max(float(total_depth), 1.0)
        return cls.from_survey([0.0, depth], [0.0, 0.0], [0.0, 0.0], source)

    @property
    def is_vertical(self) -> bool:
        return bool(np.all(self.inc == 0))

    @property
    def total_md(self) -> float:
        return float(self.md[-1])

    def _extend(self, md: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Straight-line extension beyond the last station, along its direction."""
        beyond = np.maximum(md - self.md[-1], 0.0)
        i, a = np.radians(self.inc[-1]), np.radians(self.azi[-1])
        return beyond * np.cos(i), beyond * np.sin(i) * np.sin(a), beyond * np.sin(i) * np.cos(a)

    def md_to_tvd(self, md: ArrayLike) -> FloatArray:
        """TVD at any MD. Interpolated linearly between stations (fine for station spacing of
        tens of metres), extended along the last station's direction below it."""
        md = _as_array(md)
        tvd = np.interp(md, self.md, self.tvd)
        tvd = tvd + self._extend(md)[0]
        return np.where(np.isfinite(md), tvd, np.nan)

    def md_to_xy(self, md: ArrayLike) -> tuple[FloatArray, FloatArray]:
        """East and north offsets at any MD."""
        md = _as_array(md)
        _, ext_e, ext_n = self._extend(md)
        dx = np.interp(md, self.md, self.dx) + ext_e
        dy = np.interp(md, self.md, self.dy) + ext_n
        bad = ~np.isfinite(md)
        return np.where(bad, np.nan, dx), np.where(bad, np.nan, dy)

    def tvd_to_md(self, tvd: ArrayLike) -> FloatArray:
        """The first MD at which the path reaches ``tvd``. NaN above the surface, and below the
        deepest point when the last station is horizontal or climbing."""
        tvd = _as_array(tvd)
        envelope = np.maximum.accumulate(self.tvd)
        # Strictly increasing copy of the envelope, so interpolation picks the first crossing.
        keep = np.concatenate([[True], np.diff(envelope) > 0])
        md = np.interp(tvd, envelope[keep], self.md[keep], left=np.nan, right=np.nan)
        below = tvd > envelope[-1]
        if below.any():
            cos_i = np.cos(np.radians(self.inc[-1]))
            if cos_i > 1e-9:
                md = np.where(below, self.md[-1] + (tvd - self.tvd[-1]) / cos_i, md)
        return md

    def path(self, step: float = 10.0) -> tuple[FloatArray, FloatArray]:
        """East and north offsets along the path, densified to about ``step`` metres."""
        if self.md.size < 2:
            return self.dx.copy(), self.dy.copy()
        dense = np.unique(np.concatenate([np.arange(0.0, self.total_md, step), self.md]))
        return self.md_to_xy(dense)
