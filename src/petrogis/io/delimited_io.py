"""Point layers from delimited text files (CSV/TXT) with X and Y columns.

Column detection, delimiter sniffing and locale-robust number parsing are shared with the well
header importer (``io.wells_io`` and ``io.tables``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from petrogis.core.crs import normalize_crs
from petrogis.core.layers import DelimitedLayer
from petrogis.io.errors import DataReadError
from petrogis.io.reproject import TransformationLookup, reproject_frame, resolve_transformation
from petrogis.io.tables import to_numbers
from petrogis.io.wells_io import WellTable, read_well_table

DELIMITED_EXTENSIONS = {".csv", ".txt", ".tsv"}
DELIMITED_FILE_FILTER = "Delimited text (*.csv *.txt *.tsv)"


@dataclass
class DelimitedImportResult:
    layer: DelimitedLayer
    warnings: list[str] = field(default_factory=list)


def read_delimited_table(path: str | Path) -> WellTable:
    """Read the table and guess the X/Y columns (a table without a name column is fine here)."""
    path = Path(path)
    if path.suffix.lower() not in DELIMITED_EXTENSIONS:
        raise DataReadError(f"Unsupported delimited text format: {path.suffix or path.name}")
    return read_well_table(path)


def build_delimited_layer(
    table: WellTable,
    x_column: str,
    y_column: str,
    source_crs: str,
    project_crs: str,
    transformation: str | TransformationLookup | None = None,
) -> DelimitedImportResult:
    """Points from ``x_column``/``y_column``. Rows without both coordinates become a warning."""
    import geopandas as gpd

    missing = sorted({x_column, y_column} - set(table.data.columns))
    if missing:
        raise DataReadError(f"Columns not found in {table.path.name}: {', '.join(missing)}")
    if x_column == y_column:
        raise DataReadError("X and Y must be different columns")
    df = table.data
    x = to_numbers(df[x_column])
    y = to_numbers(df[y_column])
    valid = (x.notna() & y.notna()).to_numpy()
    messages = list(table.read_warnings)
    if not valid.any():
        raise DataReadError(f"{table.path.name}: no rows with numeric X and Y values")
    if not valid.all():
        rows = [int(i) + 2 for i in np.flatnonzero(~valid)[:10]]  # +2: header row and 1-based
        more = "…" if (~valid).sum() > 10 else ""
        messages.append(
            f"Skipped {int((~valid).sum())} row(s) without numeric X/Y "
            f"(file rows {', '.join(map(str, rows))}{more})"
        )
    source_crs = normalize_crs(source_crs)
    attributes = df.loc[valid].reset_index(drop=True)
    gdf = gpd.GeoDataFrame(
        attributes,
        geometry=gpd.points_from_xy(x[valid], y[valid]),
        crs=source_crs,
    )
    gdf = reproject_frame(gdf, project_crs, resolve_transformation(transformation, source_crs))
    layer = DelimitedLayer(
        name=table.path.stem,
        source=str(table.path),
        gdf=gdf,
        source_crs=source_crs,
        import_options={"x": x_column, "y": y_column, "source_crs": source_crs},
    )
    _ = layer.paths
    return DelimitedImportResult(layer=layer, warnings=messages)


def load_delimited_layer(
    path: str | Path,
    project_crs: str,
    x_column: str | None = None,
    y_column: str | None = None,
    source_crs: str | None = None,
    transformation: str | TransformationLookup | None = None,
) -> DelimitedImportResult:
    """One-shot import, for scripts and project loading. Raises if X/Y can't be determined."""
    table = read_delimited_table(path)
    x_column = x_column or table.mapping.x
    y_column = y_column or table.mapping.y
    if x_column is None or y_column is None:
        raise DataReadError(f"Could not detect the X and Y columns in {Path(path).name}")
    crs = source_crs or table.suggested_crs or project_crs
    return build_delimited_layer(table, x_column, y_column, crs, project_crs, transformation)
