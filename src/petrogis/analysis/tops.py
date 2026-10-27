"""Formation tops as map points: the input to gridding a surface. No Qt dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from petrogis.core.layers import WellLayer
from petrogis.core.tops import surface_order

if TYPE_CHECKING:
    import geopandas as gpd


def surfaces(layer: WellLayer) -> list[str]:
    """Every surface picked in the layer, in stratigraphic order."""
    return surface_order(t for picks in layer.tops.values() for t in picks)


def tops_points(layer: WellLayer, surface: str, crs: str) -> gpd.GeoDataFrame:
    """One point per well that picks ``surface``, at the pick's position along the well path.

    Columns: ``well``, ``surface``, ``md``, ``tvd``, ``tvdss`` (TVD below sea level, using the
    header KB; NaN without one), ``x`` and ``y``. Wells without a survey are vertical.
    """
    import geopandas as gpd
    import pandas as pd

    rows = []
    if layer.gdf is not None:
        heads = layer.xy()
        for position, key in enumerate(layer.gdf["name_key"]):
            pick = next((t for t in layer.tops.get(str(key), []) if t.surface == surface), None)
            if pick is None:
                continue
            trajectory = layer.trajectories.get(str(key))
            if trajectory is None:
                tvd, dx, dy = pick.md, 0.0, 0.0
            else:
                tvd = float(trajectory.md_to_tvd(pick.md)[0])
                ex, ny = trajectory.md_to_xy(pick.md)
                dx, dy = float(ex[0]), float(ny[0])
            kb = layer.kb_at(position)
            x, y = heads[position][0] + dx, heads[position][1] + dy
            rows.append(
                {
                    "well": str(layer.gdf["name"].iloc[position]),
                    "surface": surface,
                    "md": pick.md,
                    "tvd": tvd,
                    "tvdss": tvd - kb if kb is not None else np.nan,
                    "x": x,
                    "y": y,
                }
            )
    columns = ["well", "surface", "md", "tvd", "tvdss", "x", "y"]
    frame = pd.DataFrame(rows, columns=columns)
    return gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(frame["x"], frame["y"]), crs=crs)
