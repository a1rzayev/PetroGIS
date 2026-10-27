"""Coordinate reference system defaults and helpers.

pyproj is imported inside functions so that importing this module stays cheap at startup.
"""

from __future__ import annotations

from typing import Any

DEFAULT_PROJECT_CRS = "EPSG:32639"
"""WGS 84 / UTM zone 39N, which covers the Absheron peninsula and the Caspian region."""


class CRSError(ValueError):
    """Raised for CRS definitions that pyproj cannot parse."""


def normalize_crs(crs: Any) -> str:
    """Return a canonical string for any CRS input (``"EPSG:32639"``, an int, WKT, a pyproj CRS).

    The result is ``"EPSG:<code>"`` when an EPSG code exists, otherwise WKT2.
    """
    from pyproj import CRS
    from pyproj.exceptions import CRSError as PyprojCRSError

    try:
        parsed = CRS.from_user_input(crs)
    except PyprojCRSError as exc:
        raise CRSError(f"Unrecognised CRS: {crs!r}") from exc
    epsg = parsed.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else parsed.to_wkt()


def crs_equal(a: Any, b: Any) -> bool:
    from pyproj import CRS

    return CRS.from_user_input(a) == CRS.from_user_input(b)


def crs_label(crs: Any) -> str:
    """Short human-readable label, e.g. ``"EPSG:32639 - WGS 84 / UTM zone 39N"``."""
    from pyproj import CRS

    parsed = CRS.from_user_input(crs)
    epsg = parsed.to_epsg()
    return f"EPSG:{epsg} - {parsed.name}" if epsg is not None else parsed.name
