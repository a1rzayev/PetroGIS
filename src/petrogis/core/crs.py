"""Coordinate reference system defaults and helpers.

pyproj is imported inside functions so that importing this module stays cheap at startup.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
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


# --- CRS catalogue ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CRSEntry:
    code: str
    """``"EPSG:<code>"``."""
    name: str
    kind: str
    """``"projected"`` or ``"geographic"``."""
    area: str
    """Area of use, as named by EPSG."""


@lru_cache(maxsize=1)
def list_crs() -> tuple[CRSEntry, ...]:
    """Every non-deprecated EPSG projected and geographic 2D CRS (about 6,000; cached)."""
    from pyproj.database import query_crs_info
    from pyproj.enums import PJType

    kinds = {PJType.PROJECTED_CRS: "projected", PJType.GEOGRAPHIC_2D_CRS: "geographic"}
    infos = query_crs_info(auth_name="EPSG", pj_types=list(kinds))
    return tuple(
        CRSEntry(
            code=f"EPSG:{info.code}",
            name=info.name,
            kind=kinds[info.type],
            area=info.area_of_use.name if info.area_of_use else "",
        )
        for info in infos
        if not info.deprecated
    )


_WORD = re.compile(r"\w+")


def _word_matches(query_word: str, words: list[str]) -> bool:
    """Numbers must match a whole word ("9" is not inside "1942"); text matches a word prefix."""
    if query_word.isdigit():
        return query_word in words
    return any(word.startswith(query_word) for word in words)


def search_crs(text: str, limit: int = 500) -> list[CRSEntry]:
    """CRSs matching every word of ``text`` in their code, name or area (case-insensitive).

    An exact code (``"32639"`` or ``"EPSG:32639"``) comes first. Then come CRSs whose *name*
    matches every word, shortest (most specific) name first, then those matching partly via
    their area of use.
    """
    query = _WORD.findall(text.lower().replace("epsg:", " "))
    if not query:
        return list(list_crs()[:limit])
    exact: list[CRSEntry] = []
    ranked: list[tuple[int, int, CRSEntry]] = []
    for entry in list_crs():
        code = entry.code.split(":")[1]
        if len(query) == 1 and query[0] == code:
            exact.append(entry)
            continue
        name_words = [*_WORD.findall(entry.name.lower()), code]
        area_words = _WORD.findall(entry.area.lower())
        in_name = [_word_matches(w, name_words) for w in query]
        if all(m or _word_matches(w, area_words) for w, m in zip(query, in_name, strict=True)):
            ranked.append((-sum(in_name), len(name_words), entry))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].name))
    return (exact + [entry for _, _, entry in ranked])[:limit]


# --- datum transformations -------------------------------------------------------------------
@dataclass(frozen=True)
class TransformOption:
    description: str
    accuracy: float | None
    """Accuracy in metres as published by EPSG, or ``None`` when unknown."""
    pipeline: str
    """PROJ pipeline (x/y axis order). Empty for operations that are not available."""
    available: bool
    missing_grids: tuple[str, ...] = ()


def lonlat_area(crs: Any, bounds: tuple[float, float, float, float]) -> tuple[float, ...] | None:
    """``bounds`` (in ``crs``) as a lon/lat area of interest, or ``None`` if not computable."""
    from pyproj.exceptions import ProjError
    from pyproj.transformer import Transformer

    try:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        west, south, east, north = transformer.transform_bounds(*bounds, densify_pts=21)
    except (ProjError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        return None
    return (west, south, east, north)


def transformation_options(
    source: Any, target: Any, area: tuple[float, float, float, float] | None = None
) -> list[TransformOption]:
    """Candidate operations from ``source`` to ``target``, in PROJ's preference order.

    Available operations come first. Operations that need a grid which isn't installed are
    listed after them with ``available=False``. ``area`` is a lon/lat area of interest
    (west, south, east, north) that narrows the candidates.
    """
    from pyproj.transformer import AreaOfInterest, TransformerGroup

    aoi = AreaOfInterest(*area) if area is not None else None
    group = TransformerGroup(source, target, always_xy=True, area_of_interest=aoi)
    options = [
        TransformOption(
            description=transformer.description,
            accuracy=transformer.accuracy if transformer.accuracy >= 0 else None,
            pipeline=transformer.to_proj4(),
            available=True,
        )
        for transformer in group.transformers
    ]
    options.extend(
        TransformOption(
            description=operation.name,
            accuracy=operation.accuracy if operation.accuracy >= 0 else None,
            pipeline="",
            available=False,
            missing_grids=tuple(grid.short_name for grid in operation.grids if not grid.available),
        )
        for operation in group.unavailable_operations
    )
    return options


def is_known_pipeline(source: Any, target: Any, pipeline: str) -> bool:
    """True if ``pipeline`` is one of PROJ's available operations for this CRS pair.

    Pipelines read from project files go through this check, so a crafted file can't make PROJ
    run an arbitrary pipeline (for example one that points at a local file as a grid).
    """
    return any(
        o.pipeline == pipeline for o in transformation_options(source, target) if o.available
    )


def best_transformation_warning(
    source: Any, target: Any, area: tuple[float, float, float, float] | None = None
) -> str | None:
    """A message when the most accurate operation needs a grid that isn't installed."""
    from pyproj.transformer import AreaOfInterest, TransformerGroup

    aoi = AreaOfInterest(*area) if area is not None else None
    group = TransformerGroup(source, target, always_xy=True, area_of_interest=aoi)
    if group.best_available:
        return None
    grids = sorted(
        {g.short_name for op in group.unavailable_operations for g in op.grids if not g.available}
    )
    return (
        f"The most accurate transformation from {normalize_crs(source)} to "
        f"{normalize_crs(target)} needs grid files that are not installed "
        f"({', '.join(grids) or 'unknown'}). A less accurate transformation is used instead."
    )
