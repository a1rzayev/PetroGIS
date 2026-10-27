"""Import formation tops from CSV or Excel, in long or wide layout.

- Long: one row per pick, with well, surface and MD columns.
- Wide: one row per well, with one MD column per surface (the column header is the surface name).

Detection picks the layout. The UI shows the mapping dialog only when it cannot.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from petrogis.core.tops import Top
from petrogis.core.wells import normalize_well_name
from petrogis.io.errors import DataReadError
from petrogis.io.survey_io import SURVEY_ALIASES, depth_factor, row_wells
from petrogis.io.tables import detect_fields, header_depth_unit, read_table, to_numbers

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

TOPS_FILE_FILTER = "Formation tops (*.csv *.txt *.xlsx *.xlsm)"
TOPS_FIELDS = ("well", "surface", "md")
TOPS_LABELS = {"well": "Well name", "surface": "Surface / formation", "md": "Measured depth"}
TOPS_ALIASES: dict[str, tuple[str, ...]] = {
    "well": SURVEY_ALIASES["well"],
    "surface": ("surface", "surfacename", "top", "topname", "formation", "formationname",
                "horizon", "marker", "pick", "pickname", "zone", "горизонт", "пласт", "свита",
                "lay", "horizont"),
    "md": ("md", "measureddepth", "topmd", "mdtop", "pickmd", "depth", "depthmd", "dept",
           "глубина", "кровля", "dərinlik"),
}  # fmt: skip


@dataclass
class TopsMapping:
    well: str | None = None
    surface: str | None = None
    md: str | None = None

    @property
    def is_wide(self) -> bool:
        """No surface and MD columns: every other numeric column is a surface."""
        return self.surface is None and self.md is None

    @property
    def is_complete(self) -> bool:
        if self.is_wide:
            return self.well is not None
        return self.surface is not None and self.md is not None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TopsMapping:
        return cls(**{f: data.get(f) for f in TOPS_FIELDS})


@dataclass
class TopsTable:
    path: Path
    data: pd.DataFrame
    sheet: str | None
    mapping: TopsMapping
    depth_unit: str
    read_warnings: list[str] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self.data.columns]

    def wide_surfaces(self, mapping: TopsMapping) -> list[str]:
        """Columns read as surfaces in the wide layout: numeric and not the well column."""
        import pandas as pd

        return [
            str(c)
            for c in self.data.columns
            if c != mapping.well and pd.api.types.is_numeric_dtype(self.data[c])
        ]


@dataclass
class TopsImport:
    tops: dict[str, list[Top]]
    """Picks per normalised well name, shallowest first."""
    names: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return sum(len(t) for t in self.tops.values())


def read_tops_table(path: str | Path, sheet: str | None = None) -> TopsTable:
    path = Path(path)
    data, read_warnings = read_table(path, sheet, what="tops")
    found = detect_fields(list(data.columns), TOPS_ALIASES, ("well", "surface", "md"))
    mapping = TopsMapping(**found)
    if mapping.surface is None and mapping.md is not None and mapping.well is not None:
        # A lone depth column without surface names is not a usable long table: try wide.
        mapping = TopsMapping(well=mapping.well)
    unit = header_depth_unit(mapping.md) if mapping.md else None
    return TopsTable(path, data, sheet, mapping, unit or "m", read_warnings)


def build_tops(
    table: TopsTable,
    mapping: TopsMapping,
    depth_unit: str | None = None,
    default_well: str | None = None,
) -> TopsImport:
    """Picks per well. Rows with a missing well, surface or depth become a warning."""
    import pandas as pd

    if not mapping.is_complete:
        raise DataReadError("Column mapping is missing the well, surface or depth column")
    used = {c for c in (mapping.well, mapping.surface, mapping.md) if c}
    absent = sorted(used - set(table.data.columns))
    if absent:
        raise DataReadError(f"Columns not found in {table.path.name}: {', '.join(absent)}")
    unit = depth_unit or table.depth_unit
    factor = depth_factor(unit)
    data = table.data
    wells = row_wells(data, mapping.well, default_well, table.path)
    if mapping.is_wide:
        surfaces_in = table.wide_surfaces(mapping)
        if not surfaces_in:
            raise DataReadError(f"{table.path.name}: no surface depth columns found")
        long = pd.DataFrame(
            {
                "well": np.repeat(wells.to_numpy(dtype=object), len(surfaces_in)),
                "surface": np.tile(surfaces_in, len(data)),
                "md": data[surfaces_in].to_numpy(dtype=float).ravel(),
            }
        )
        long = long[np.isfinite(long["md"])]  # wide tables leave cells blank where absent
        well_col, surfaces, md = (
            long["well"].astype("string"),
            long["surface"].astype("string"),
            long["md"].to_numpy(),
        )
    else:
        well_col = wells
        surfaces = data[mapping.surface].astype("string").str.strip()
        md = to_numbers(data[mapping.md]).to_numpy()
    md = md * factor
    warnings = list(table.read_warnings)
    bad = (
        (well_col.isna() | (well_col == "")).to_numpy()
        | (surfaces.isna() | (surfaces == "")).to_numpy()
        | ~np.isfinite(md)
    )
    if bad.any() and not mapping.is_wide:
        rows = [int(i) + 2 for i in np.flatnonzero(bad)[:10]]
        warnings.append(
            f"Skipped {int(bad.sum())} row(s) with a missing well, surface or depth "
            f"(file rows {', '.join(map(str, rows))}{'…' if bad.sum() > 10 else ''})"
        )
    tops: dict[str, list[Top]] = {}
    names: dict[str, str] = {}
    source = str(table.path)
    for well, surface, depth in zip(
        well_col.to_numpy(dtype=object)[~bad],
        surfaces.to_numpy(dtype=object)[~bad],
        md[~bad],
        strict=True,
    ):
        key = normalize_well_name(well)
        if not key:
            continue
        names.setdefault(key, str(well))
        tops.setdefault(key, []).append(Top(str(well), key, str(surface), float(depth), source))
    for key, picks in tops.items():
        seen: dict[str, Top] = {}
        for pick in picks:
            if pick.surface in seen:
                warnings.append(
                    f"{pick.well}: surface {pick.surface!r} picked twice; the first pick is used"
                )
                continue
            seen[pick.surface] = pick
        tops[key] = sorted(seen.values(), key=lambda t: t.md)
    if not tops:
        raise DataReadError(f"{table.path.name}: no valid formation tops")
    for message in warnings:
        log.warning("%s: %s", table.path.name, message)
    options = {
        "mapping": mapping.to_dict(),
        "depth_unit": unit,
        "sheet": table.sheet,
        "default_well": default_well,
    }
    return TopsImport(tops, names, warnings, options)


def load_tops(
    path: str | Path,
    mapping: TopsMapping | None = None,
    depth_unit: str | None = None,
    sheet: str | None = None,
    default_well: str | None = None,
) -> TopsImport:
    """One-shot import, for scripts and project loading. Raises if detection is incomplete."""
    table = read_tops_table(path, sheet)
    mapping = mapping or table.mapping
    if not mapping.is_complete:
        raise DataReadError(
            f"Could not detect the well, surface and depth columns in {Path(path).name}"
        )
    return build_tops(table, mapping, depth_unit, default_well)
