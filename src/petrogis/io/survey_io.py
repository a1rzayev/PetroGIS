"""Import deviation surveys (MD, inclination, azimuth) from CSV or Excel.

Same two-step flow as well headers: :func:`read_survey_table` reads and detects columns, the UI
shows the mapping dialog only when detection is incomplete, then :func:`build_surveys` computes a
minimum-curvature trajectory per well. A file holds either many wells (with a well column) or one
well (named after the file, or chosen by the user).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from petrogis.core.trajectory import Trajectory
from petrogis.core.wells import normalize_well_name
from petrogis.io.errors import DataReadError
from petrogis.io.tables import detect_fields, header_depth_unit, read_table, to_numbers
from petrogis.io.wells_io import COLUMN_ALIASES as WELL_ALIASES

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

SURVEY_FILE_FILTER = "Deviation surveys (*.csv *.txt *.xlsx *.xlsm)"
FEET = 0.3048
DEPTH_UNITS = ("m", "ft")
SURVEY_FIELDS = ("well", "md", "inc", "azi")
SURVEY_REQUIRED = ("md", "inc", "azi")
SURVEY_LABELS = {
    "well": "Well name",
    "md": "Measured depth",
    "inc": "Inclination (°)",
    "azi": "Azimuth (°)",
}
SURVEY_ALIASES: dict[str, tuple[str, ...]] = {
    "well": (*WELL_ALIASES["name"], "uwi"),
    "md": ("md", "measureddepth", "mdm", "depth", "dept", "depthmd", "глубина",
           "глубинапостволу", "dərinlik"),
    "inc": ("inc", "incl", "inclination", "dev", "deviation", "angle", "incdeg", "зенитныйугол",
            "зенит", "угол", "zenit"),
    "azi": ("azi", "azim", "azimuth", "az", "dir", "direction", "azimuthgrid", "gridazimuth",
            "азимут", "azimut"),
}  # fmt: skip


def depth_factor(unit: str) -> float:
    """Metres per ``unit`` (``m`` or ``ft``)."""
    if unit not in DEPTH_UNITS:
        raise DataReadError(f"Unknown depth unit {unit!r}")
    return FEET if unit == "ft" else 1.0


@dataclass
class SurveyMapping:
    well: str | None = None
    md: str | None = None
    inc: str | None = None
    azi: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(getattr(self, f) is not None for f in SURVEY_REQUIRED)

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurveyMapping:
        return cls(**{f: data.get(f) for f in SURVEY_FIELDS})


@dataclass
class SurveyTable:
    path: Path
    data: pd.DataFrame
    sheet: str | None
    mapping: SurveyMapping
    depth_unit: str
    """Detected from the MD header ("MD (ft)"), else metres."""
    read_warnings: list[str] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self.data.columns]


@dataclass
class SurveyImport:
    trajectories: dict[str, Trajectory]
    """Keyed by normalised well name."""
    names: dict[str, str]
    """Well name as written in the file, per key."""
    warnings: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    """How the file was read, so a project file can read it again."""


def read_survey_table(path: str | Path, sheet: str | None = None) -> SurveyTable:
    path = Path(path)
    data, read_warnings = read_table(path, sheet, what="survey")
    found = detect_fields(list(data.columns), SURVEY_ALIASES, ("md", "inc", "azi", "well"))
    mapping = SurveyMapping(**found)
    unit = header_depth_unit(mapping.md) if mapping.md else None
    return SurveyTable(path, data, sheet, mapping, unit or "m", read_warnings)


def row_wells(
    data: pd.DataFrame, column: str | None, default_well: str | None, path: Path
) -> pd.Series:
    """Well name per row: from ``column``, else ``default_well``, else the file name."""
    import pandas as pd

    if column is not None:
        return data[column].astype("string").str.strip().fillna("")
    return pd.Series(default_well or path.stem, index=data.index, dtype="string")


def _skipped(mask: np.ndarray, what: str) -> str:
    rows = [int(i) + 2 for i in np.flatnonzero(mask)[:10]]
    more = "…" if mask.sum() > 10 else ""
    listed = ", ".join(map(str, rows))
    return f"Skipped {int(mask.sum())} row(s) with {what} (file rows {listed}{more})"


def build_surveys(
    table: SurveyTable,
    mapping: SurveyMapping,
    depth_unit: str | None = None,
    default_well: str | None = None,
) -> SurveyImport:
    """One trajectory per well. Bad rows and invalid surveys become warnings."""
    if not mapping.is_complete:
        missing = [SURVEY_LABELS[f] for f in SURVEY_REQUIRED if getattr(mapping, f) is None]
        raise DataReadError(f"Column mapping is missing: {', '.join(missing)}")
    used = {c for c in (mapping.well, mapping.md, mapping.inc, mapping.azi) if c}
    absent = sorted(used - set(table.data.columns))
    if absent:
        raise DataReadError(f"Columns not found in {table.path.name}: {', '.join(absent)}")
    unit = depth_unit or table.depth_unit
    factor = depth_factor(unit)
    data = table.data
    warnings = list(table.read_warnings)
    wells = row_wells(data, mapping.well, default_well, table.path)
    md = to_numbers(data[mapping.md]).to_numpy() * factor
    inc = to_numbers(data[mapping.inc]).to_numpy()
    azi = to_numbers(data[mapping.azi]).to_numpy()
    bad = (wells.isna() | (wells == "")).to_numpy() | ~np.isfinite(md + inc + azi)
    if bad.any():
        warnings.append(_skipped(bad, "a missing well, depth or angle"))

    keys = wells.map(normalize_well_name, na_action="ignore").to_numpy(dtype=object)
    trajectories: dict[str, Trajectory] = {}
    names: dict[str, str] = {}
    for key in dict.fromkeys(keys[~bad]):  # file order
        if not key:
            continue
        rows = np.flatnonzero(~bad & (keys == key))
        name = str(wells.iloc[rows[0]])
        try:
            trajectories[key] = Trajectory.from_survey(
                md[rows], inc[rows], azi[rows], source=str(table.path)
            )
            names[key] = name
        except ValueError as exc:
            warnings.append(f"Survey for {name} ignored: {exc}")
    if not trajectories:
        raise DataReadError(f"{table.path.name}: no valid survey stations")
    for message in warnings:
        log.warning("%s: %s", table.path.name, message)
    options = {
        "mapping": mapping.to_dict(),
        "depth_unit": unit,
        "sheet": table.sheet,
        "default_well": default_well,
    }
    return SurveyImport(trajectories, names, warnings, options)


def load_surveys(
    path: str | Path,
    mapping: SurveyMapping | None = None,
    depth_unit: str | None = None,
    sheet: str | None = None,
    default_well: str | None = None,
) -> SurveyImport:
    """One-shot import, for scripts and project loading. Raises if detection is incomplete."""
    table = read_survey_table(path, sheet)
    mapping = mapping or table.mapping
    if not mapping.is_complete:
        raise DataReadError(
            f"Could not detect the MD, inclination and azimuth columns in {Path(path).name}"
        )
    return build_surveys(table, mapping, depth_unit, default_well)
