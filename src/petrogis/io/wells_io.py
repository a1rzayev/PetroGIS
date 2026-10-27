"""Import well headers from CSV or Excel, with automatic column detection.

The import runs in two steps so the UI can step in between:

1. :func:`read_well_table` reads the table and guesses which column is which (a worker thread).
2. :func:`build_well_layer` turns the table into a :class:`WellLayer` using a confirmed
   :class:`ColumnMapping` and source CRS (also a worker thread).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from petrogis.core.crs import normalize_crs
from petrogis.core.layers import WellLayer
from petrogis.core.wells import normalize_well_name
from petrogis.io.errors import DataReadError
from petrogis.io.reproject import TransformationLookup, reproject_frame, resolve_transformation
from petrogis.io.tables import (
    TABLE_EXTENSIONS,
    detect_fields,
    normalize_header,
    read_table,
    to_numbers,
)

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

WELL_TABLE_EXTENSIONS = TABLE_EXTENSIONS
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
    "name": ("well", "wellname", "name", "wellid", "borehole", "boreholename", "wellbore", "bore",
             "quyu", "quyuadi", "quyununadi", "quyunomresi",  # Azerbaijani
             "скважина", "скв", "номерскважины", "skvazhina", "skv"),  # Russian
    "uwi": ("uwi", "api", "apinumber", "uwiapi", "wellidentifier"),
    "x": ("x", "easting", "east", "xcoord", "xcoordinate", "surfacex", "xsurface", "eastings",
          "lon", "long", "longitude"),
    "y": ("y", "northing", "north", "ycoord", "ycoordinate", "surfacey", "ysurface", "northings",
          "lat", "latitude"),
    "kb": ("kb", "kbelev", "kbelevation", "elevkb", "rkb", "kellybushing", "elevation", "elev",
           "datum", "datumelevation", "z", "альтитуда", "альтитударотора", "altituda", "altitud"),
    "td": ("td", "totaldepth", "tdmd", "mdtd", "drillertd", "забой", "глубиназабоя", "zaboy"),
}  # fmt: skip
_GEOGRAPHIC_HEADERS = {"lon", "long", "longitude", "lat", "latitude"}
__all__ = ["normalize_header"]  # re-exported: older callers import it from here


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
    read_warnings: list[str] = field(default_factory=list)
    """Problems noticed while reading (for example ragged rows), shown with the import result."""
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
    found = detect_fields(columns, COLUMN_ALIASES, ("uwi", "name", "x", "y", "kb", "td"))
    mapping = ColumnMapping(**found)
    if mapping.name is None and mapping.uwi is not None:
        mapping.name = mapping.uwi  # A UWI is better than no identifier at all.
    return mapping


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
    path = Path(path)
    data, read_warnings = read_table(path, sheet, what="well header")
    mapping = detect_columns(list(data.columns))
    return WellTable(path, data, sheet, mapping, _suggest_crs(data, mapping), read_warnings)


def _suggest_crs(data: pd.DataFrame, mapping: ColumnMapping) -> str | None:

    if mapping.x is None or mapping.y is None:
        return None
    if {normalize_header(mapping.x), normalize_header(mapping.y)} & _GEOGRAPHIC_HEADERS:
        return GEOGRAPHIC_CRS
    x = to_numbers(data[mapping.x]).dropna()
    y = to_numbers(data[mapping.y]).dropna()
    if len(x) and len(y) and x.abs().max() <= 180 and y.abs().max() <= 90:
        return GEOGRAPHIC_CRS
    return None


def build_well_layer(
    table: WellTable,
    mapping: ColumnMapping,
    source_crs: str,
    project_crs: str,
    transformation: str | TransformationLookup | None = None,
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
    messages: list[str] = list(table.read_warnings)
    names = df[mapping.name].astype("string").str.strip()
    x = to_numbers(df[mapping.x])
    y = to_numbers(df[mapping.y])
    valid = names.notna() & (names != "") & x.notna() & y.notna()
    if not valid.all():
        # Positional, so the report is right whatever index the frame carries (+2: header, 1-based).
        bad_rows = [int(i) + 2 for i in np.flatnonzero(~valid.to_numpy())[:10]]
        messages.append(
            f"Skipped {int((~valid).sum())} row(s) with a missing name or coordinates "
            f"(file rows {', '.join(map(str, bad_rows))}{'…' if (~valid).sum() > 10 else ''})"
        )
    if not valid.any():
        raise DataReadError(f"{table.path.name}: no rows with a valid name and coordinates")

    def numeric(col: str | None) -> pd.Series:
        if col is None:
            return pd.Series(float("nan"), index=df.index)
        return to_numbers(df[col])

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
        messages.append(f"Duplicate well names (the first one is used for matching): {listed}")

    source_crs = normalize_crs(source_crs)
    gdf = gpd.GeoDataFrame(
        out.reset_index(drop=True),
        geometry=gpd.points_from_xy(x[valid], y[valid]),
        crs=source_crs,
    )
    gdf = reproject_frame(gdf, project_crs, resolve_transformation(transformation, source_crs))
    for message in messages:
        log.warning("%s: %s", table.path.name, message)

    layer = WellLayer(
        name=table.path.stem,
        source=str(table.path),
        gdf=gdf,
        source_crs=source_crs,
        import_options={
            "mapping": mapping.to_dict(),
            "source_crs": source_crs,
            "sheet": table.sheet,
        },
    )
    return WellImportResult(layer=layer, warnings=messages)


def load_well_layer(
    path: str | Path,
    project_crs: str,
    mapping: ColumnMapping | None = None,
    source_crs: str | None = None,
    sheet: str | None = None,
    transformation: str | TransformationLookup | None = None,
) -> WellImportResult:
    """One-shot import, for scripts and project loading. Raises if detection is incomplete."""
    table = read_well_table(path, sheet)
    mapping = mapping or table.mapping
    if not mapping.is_complete:
        raise DataReadError(
            f"Could not detect the well name and coordinate columns in {Path(path).name}"
        )
    return build_well_layer(
        table,
        mapping,
        source_crs or table.suggested_crs or project_crs,
        project_crs,
        transformation,
    )
