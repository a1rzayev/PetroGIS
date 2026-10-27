"""Import well headers from CSV or Excel, with automatic column detection.

The import runs in two steps so the UI can step in between:

1. :func:`read_well_table` reads the table and guesses which column is which (a worker thread).
2. :func:`build_well_layer` turns the table into a :class:`WellLayer` using a confirmed
   :class:`ColumnMapping` and source CRS (also a worker thread).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from petrogis.core.crs import crs_equal, normalize_crs
from petrogis.core.layers import WellLayer
from petrogis.core.wells import normalize_well_name
from petrogis.io.errors import DataReadError

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

WELL_TABLE_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xlsm"}
WELL_TABLE_FILE_FILTER = "Well headers (*.csv *.txt *.xlsx *.xlsm)"
GEOGRAPHIC_CRS = "EPSG:4326"

REQUIRED_FIELDS = ("name", "x", "y")
OPTIONAL_FIELDS = ("kb", "uwi", "td")
FIELD_LABELS = {
    "name": "Well name",
    "x": "X / Easting",
    "y": "Y / Northing",
    "kb": "KB elevation",
    "uwi": "UWI",
    "td": "Total depth",
}

# Normalised header aliases (lowercase, alphanumerics only, units in brackets removed), in
# priority order.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("well", "wellname", "name", "wellid", "borehole", "boreholename", "wellbore", "bore"),
    "uwi": ("uwi", "api", "apinumber", "uwiapi", "wellidentifier"),
    "x": ("x", "easting", "east", "xcoord", "xcoordinate", "surfacex", "xsurface", "eastings",
          "lon", "long", "longitude"),
    "y": ("y", "northing", "north", "ycoord", "ycoordinate", "surfacey", "ysurface", "northings",
          "lat", "latitude"),
    "kb": ("kb", "kbelev", "kbelevation", "elevkb", "rkb", "kellybushing", "elevation", "elev",
           "datum", "datumelevation", "z"),
    "td": ("td", "totaldepth", "tdmd", "mdtd", "drillertd"),
}  # fmt: skip
_GEOGRAPHIC_HEADERS = {"lon", "long", "longitude", "lat", "latitude"}
_UNITS = re.compile(r"\(.*?\)|\[.*?\]")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize_header(header: object) -> str:
    return _NON_ALNUM.sub("", _UNITS.sub("", str(header).lower()))


@dataclass
class ColumnMapping:
    """Source column for each well field. ``None`` means the field isn't in the table."""

    name: str | None = None
    x: str | None = None
    y: str | None = None
    kb: str | None = None
    uwi: str | None = None
    td: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(getattr(self, f) is not None for f in REQUIRED_FIELDS)

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnMapping:
        return cls(**{f: data.get(f) for f in (*REQUIRED_FIELDS, *OPTIONAL_FIELDS)})


@dataclass
class WellTable:
    path: Path
    data: pd.DataFrame
    sheet: str | None
    mapping: ColumnMapping
    """Auto-detected mapping. May be incomplete."""
    suggested_crs: str | None
    """``EPSG:4326`` when the headers or values look like longitude/latitude, else ``None``."""

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self.data.columns]


@dataclass
class WellImportResult:
    layer: WellLayer
    warnings: list[str] = field(default_factory=list)


def detect_columns(columns: list[str]) -> ColumnMapping:
    """Match headers to well fields by normalised aliases. Each column is used at most once."""
    normalized = {col: normalize_header(col) for col in columns}
    used: set[str] = set()
    mapping = ColumnMapping()
    for field_name in ("uwi", "name", "x", "y", "kb", "td"):
        for alias in COLUMN_ALIASES[field_name]:
            match = next(
                (c for c in columns if c not in used and normalized[c] == normalize_header(alias)),
                None,
            )
            if match is not None:
                setattr(mapping, field_name, match)
                used.add(match)
                break
    if mapping.name is None and mapping.uwi is not None:
        mapping.name = mapping.uwi  # A UWI is better than no identifier at all.
    return mapping


def _sniff_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def list_sheets(path: str | Path) -> list[str]:
    import openpyxl

    path = Path(path)
    try:
        workbook = openpyxl.load_workbook(path, read_only=True)
    except Exception as exc:  # openpyxl raises a range of zipfile/XML errors
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def read_well_table(path: str | Path, sheet: str | None = None) -> WellTable:
    import pandas as pd

    path = Path(path)
    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in WELL_TABLE_EXTENSIONS:
        raise DataReadError(f"Unsupported well header format: {path.suffix or path.name}")
    try:
        if suffix in {".xlsx", ".xlsm"}:
            data = pd.read_excel(path, sheet_name=sheet or 0, engine="openpyxl")
        else:
            data = pd.read_csv(path, sep=_sniff_delimiter(path), encoding="utf-8-sig")
    except Exception as exc:  # pandas/openpyxl parse errors vary
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc
    if data.empty:
        raise DataReadError(f"{path.name} contains no rows")
    data.columns = [str(c).strip() for c in data.columns]
    mapping = detect_columns(list(data.columns))
    return WellTable(path, data, sheet, mapping, _suggest_crs(data, mapping))


def _suggest_crs(data: pd.DataFrame, mapping: ColumnMapping) -> str | None:
    import pandas as pd

    if mapping.x is None or mapping.y is None:
        return None
    if {normalize_header(mapping.x), normalize_header(mapping.y)} & _GEOGRAPHIC_HEADERS:
        return GEOGRAPHIC_CRS
    x = pd.to_numeric(data[mapping.x], errors="coerce").dropna()
    y = pd.to_numeric(data[mapping.y], errors="coerce").dropna()
    if len(x) and len(y) and x.abs().max() <= 180 and y.abs().max() <= 90:
        return GEOGRAPHIC_CRS
    return None


def build_well_layer(
    table: WellTable, mapping: ColumnMapping, source_crs: str, project_crs: str
) -> WellImportResult:
    """Validate rows, reproject to ``project_crs`` and build the layer. Bad rows become warnings."""
    import geopandas as gpd
    import pandas as pd

    if not mapping.is_complete:
        missing = [FIELD_LABELS[f] for f in REQUIRED_FIELDS if getattr(mapping, f) is None]
        raise DataReadError(f"Column mapping is missing: {', '.join(missing)}")
    columns = {
        c for c in (mapping.name, mapping.x, mapping.y, mapping.kb, mapping.uwi, mapping.td) if c
    }
    absent = sorted(columns - set(table.data.columns))
    if absent:
        raise DataReadError(f"Columns not found in {table.path.name}: {', '.join(absent)}")

    df = table.data
    warnings: list[str] = []
    names = df[mapping.name].astype("string").str.strip()
    x = pd.to_numeric(df[mapping.x], errors="coerce")
    y = pd.to_numeric(df[mapping.y], errors="coerce")
    valid = names.notna() & (names != "") & x.notna() & y.notna()
    if not valid.all():
        bad_rows = [int(i) + 2 for i in df.index[~valid][:10]]  # +2: header row and 1-based
        warnings.append(
            f"Skipped {int((~valid).sum())} row(s) with a missing name or coordinates "
            f"(file rows {', '.join(map(str, bad_rows))}{'…' if (~valid).sum() > 10 else ''})"
        )
    if not valid.any():
        raise DataReadError(f"{table.path.name}: no rows with a valid name and coordinates")

    def numeric(col: str | None) -> pd.Series:
        if col is None:
            return pd.Series(float("nan"), index=df.index)
        return pd.to_numeric(df[col], errors="coerce")

    mapped = {mapping.name, mapping.x, mapping.y, mapping.kb, mapping.uwi, mapping.td}
    extras = df[[c for c in df.columns if c not in mapped]]
    out = pd.DataFrame(
        {
            "name": names,
            "uwi": df[mapping.uwi].astype("string") if mapping.uwi else pd.NA,
            "kb": numeric(mapping.kb),
            "td": numeric(mapping.td),
        },
        index=df.index,
    ).join(extras)
    out = out.loc[valid]
    out["name_key"] = out["name"].map(normalize_well_name)
    duplicates = out["name_key"][out["name_key"].duplicated()].unique()
    if len(duplicates):
        listed = ", ".join(map(str, duplicates[:10]))
        warnings.append(f"Duplicate well names (the first one is used for matching): {listed}")

    source_crs = normalize_crs(source_crs)
    gdf = gpd.GeoDataFrame(
        out.reset_index(drop=True),
        geometry=gpd.points_from_xy(x[valid], y[valid]),
        crs=source_crs,
    )
    if not crs_equal(source_crs, project_crs):
        gdf = gdf.to_crs(project_crs)
    for message in warnings:
        log.warning("%s: %s", table.path.name, message)

    layer = WellLayer(
        name=table.path.stem,
        source=str(table.path),
        gdf=gdf,
        import_options={
            "mapping": mapping.to_dict(),
            "source_crs": source_crs,
            "sheet": table.sheet,
        },
    )
    return WellImportResult(layer=layer, warnings=warnings)


def load_well_layer(
    path: str | Path,
    project_crs: str,
    mapping: ColumnMapping | None = None,
    source_crs: str | None = None,
    sheet: str | None = None,
) -> WellImportResult:
    """One-shot import, for scripts and project loading. Raises if detection is incomplete."""
    table = read_well_table(path, sheet)
    mapping = mapping or table.mapping
    if not mapping.is_complete:
        raise DataReadError(
            f"Could not detect the well name and coordinate columns in {Path(path).name}"
        )
    return build_well_layer(
        table, mapping, source_crs or table.suggested_crs or project_crs, project_crs
    )
