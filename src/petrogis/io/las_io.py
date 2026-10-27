"""Read LAS 1.2/2.0 files into :class:`WellLog` objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from petrogis.core.logs import LogCurve, WellLog
from petrogis.io.errors import DataReadError

log = logging.getLogger(__name__)

LAS_EXTENSIONS = {".las"}
LAS_FILE_FILTER = "LAS logs (*.las *.LAS)"


@dataclass
class LasBatch:
    logs: list[WellLog] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    """(file name, reason) for each file that couldn't be read."""


def _header_value(las: object, mnemonic: str) -> str:
    try:
        value = las.well[mnemonic].value  # type: ignore[attr-defined]
    except KeyError:
        return ""
    return "" if value is None else str(value).strip()


def read_las(path: str | Path) -> WellLog:
    import lasio

    path = Path(path)
    if not path.exists():
        raise DataReadError(f"File not found: {path}")
    if path.suffix.lower() not in LAS_EXTENSIONS:
        raise DataReadError(f"Not a LAS file: {path.name}")
    try:
        las = lasio.read(path, ignore_header_errors=True)
    except Exception as exc:  # lasio raises a variety of parse errors
        raise DataReadError(f"Cannot read {path.name}: {exc}") from exc
    if not las.curves:
        raise DataReadError(f"{path.name} has no curves")

    depth = np.asarray(las.index, dtype=np.float64)
    if depth.size == 0:
        raise DataReadError(f"{path.name} has no data rows")
    index_curve = las.curves[0]
    curves = {
        curve.mnemonic: LogCurve(
            mnemonic=curve.mnemonic,
            unit=str(curve.unit or ""),
            description=str(curve.descr or ""),
            values=np.asarray(curve.data, dtype=np.float64),
        )
        for curve in las.curves[1:]
    }
    well_name = _header_value(las, "WELL") or path.stem
    return WellLog(
        well_name=well_name,
        uwi=_header_value(las, "UWI"),
        source=str(path),
        depth=depth,
        depth_unit=str(index_curve.unit or "").upper() or "M",
        curves=curves,
    )


def read_las_files(paths: list[str | Path]) -> LasBatch:
    """Read every file. Unreadable files are collected as failures instead of aborting the batch."""
    batch = LasBatch()
    for path in paths:
        try:
            batch.logs.append(read_las(path))
        except DataReadError as exc:
            log.warning("Skipping LAS file: %s", exc)
            batch.failures.append((Path(path).name, str(exc)))
    return batch
