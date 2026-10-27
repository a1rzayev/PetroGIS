"""Shared helpers for delimited text and spreadsheet tables. No Qt dependency.

Field data often comes from European or Azerbaijani/Russian locales: semicolon separators, decimal
commas (``405123,45``), and spaces or apostrophes as thousands separators. These helpers parse such
values without guessing per cell.
"""

from __future__ import annotations

import csv
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from petrogis.io.errors import DataReadError

if TYPE_CHECKING:
    import pandas as pd

TABLE_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xlsm"}
_UNITS = re.compile(r"\(.*?\)|\[.*?\]")
_UNIT_TEXT = re.compile(r"[\(\[]\s*([^\)\]]*?)\s*[\)\]]")
# Letters of any script survive (Azerbaijani, Cyrillic headers); punctuation and spaces go.
_NON_ALNUM = re.compile(r"[\W_]+")
_FEET = {"ft", "feet", "foot", "f", "фут"}

_GROUPING = "[\\s\N{NO-BREAK SPACE}\N{THIN SPACE}\N{NARROW NO-BREAK SPACE}']"
_COMMA_THOUSANDS = r"-?\d{1,3}(,\d{3})+"


def sniff_delimiter(path: Path) -> str:
    """Guess the field delimiter (``,``, ``;``, tab or ``|``) from the start of the file."""
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def to_numbers(values: pd.Series) -> pd.Series:
    """Parse a column of numbers written in any common locale. Unparseable cells become NaN.

    - Spaces, no-break spaces and apostrophes are thousands separators and are removed.
    - When a value has both ``.`` and ``,``, the last one is the decimal separator.
    - Values with only commas use the comma as the decimal separator, unless every such value in
      the column looks like comma thousands grouping (``1,234,567``).
    """
    import pandas as pd

    if pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values):
        return pd.to_numeric(values, errors="coerce").astype("float64")
    text = values.astype("string").str.strip().str.replace(_GROUPING, "", regex=True)
    has_comma = text.str.contains(",", regex=False).fillna(False)
    has_dot = text.str.contains(".", regex=False).fillna(False)
    last_comma = text.str.rfind(",")
    last_dot = text.str.rfind(".")
    comma_decimal = has_comma & has_dot & (last_comma > last_dot)
    dot_decimal = has_comma & has_dot & (last_dot > last_comma)
    only_comma = has_comma & ~has_dot

    out = text.copy()
    out[comma_decimal] = (
        out[comma_decimal].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    )
    out[dot_decimal] = out[dot_decimal].str.replace(",", "", regex=False)
    if only_comma.any():
        grouping = text[only_comma].str.fullmatch(_COMMA_THOUSANDS).fillna(False)
        replacement = "" if grouping.all() else "."
        out[only_comma] = out[only_comma].str.replace(",", replacement, regex=False)
    return pd.to_numeric(out, errors="coerce").astype("float64")


def infer_numeric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns whose non-empty values all parse as numbers (any locale) to floats."""
    import pandas as pd

    out = frame.copy()
    for column in out.columns:
        series = out[column]
        # pandas 3 reads text as the "str" dtype, older versions as object: test by kind.
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        present = series.notna() & (series.astype("string").str.strip() != "")
        if not present.any():
            continue
        parsed = to_numbers(series)
        if parsed[present].notna().all():
            out[column] = parsed
    return out


def normalize_header(header: object) -> str:
    """Lowercase letters and digits only, with units in brackets removed: "MD (m)" -> "md"."""
    return _NON_ALNUM.sub("", _UNITS.sub("", str(header).lower()))


def header_depth_unit(header: object) -> str | None:
    """``"ft"`` or ``"m"`` when the header names a depth unit in brackets, else ``None``."""
    match = _UNIT_TEXT.search(str(header))
    if match is None:
        return None
    unit = match.group(1).strip().lower().rstrip(".")
    if unit in _FEET:
        return "ft"
    if unit in {"m", "metre", "metres", "meter", "meters", "м"}:
        return "m"
    return None


def detect_fields(
    columns: list[str], aliases: dict[str, tuple[str, ...]], order: tuple[str, ...]
) -> dict[str, str | None]:
    """Match headers to fields by normalised aliases, fields in ``order``, each column once."""
    normalized = {col: normalize_header(col) for col in columns}
    used: set[str] = set()
    found: dict[str, str | None] = dict.fromkeys(order)
    for field_name in order:
        for alias in aliases[field_name]:
            key = normalize_header(alias)
            match = next((c for c in columns if c not in used and normalized[c] == key), None)
            if match is not None:
                found[field_name] = match
                used.add(match)
                break
    return found


def read_table(
    path: str | Path, sheet: str | None = None, what: str = "table"
) -> tuple[pd.DataFrame, list[str]]:
    """Read a CSV/TXT or Excel table with stripped headers and locale-robust number columns.

    Returns the frame and read warnings (for example ragged rows). Raises ``DataReadError``.
    """
    import pandas as pd

    path = Path(path)
    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in TABLE_EXTENSIONS:
        raise DataReadError(f"Unsupported {what} format: {path.suffix or path.name}")
    read_warnings: list[str] = []
    try:
        if suffix in {".xlsx", ".xlsm"}:
            data = pd.read_excel(path, sheet_name=sheet or 0, engine="openpyxl")
        else:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", pd.errors.ParserWarning)
                # index_col=False: never turn surplus cells in ragged rows into a row index.
                data = pd.read_csv(
                    path, sep=sniff_delimiter(path), encoding="utf-8-sig", index_col=False
                )
            if any(issubclass(w.category, pd.errors.ParserWarning) for w in caught):
                read_warnings.append(
                    "Some rows have more cells than the header; the extra cells were ignored"
                )
    except Exception as exc:  # pandas/openpyxl parse errors vary
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc
    if data.empty:
        raise DataReadError(f"{path.name} contains no rows")
    data = data.reset_index(drop=True)
    data.columns = [str(c).strip() for c in data.columns]
    # Decimal-comma and other locale-formatted number columns become real numbers.
    return infer_numeric_columns(data), read_warnings
