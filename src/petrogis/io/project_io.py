"""Save and load PetroGIS project files (``.pgis``, JSON).

A project file stores references, not data: layer sources, styles, order, visibility, the CRS,
the import options for well headers, attached LAS files and the map view. Source paths are
stored relative to the project file, so a project folder can be moved, with an absolute path
kept as a fallback.

Loading re-reads every source through the normal readers and keeps going past broken layers:
each problem becomes a warning, and the rest of the project still opens.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from petrogis.core.crs import CRSError
from petrogis.core.extent import Extent
from petrogis.core.layers import Layer, LayerKind, RasterLayer, VectorLayer, WellLayer
from petrogis.core.logs import match_logs
from petrogis.core.project import Project
from petrogis.core.styles import RasterStyle, VectorStyle, WellStyle
from petrogis.io.errors import DataReadError

log = logging.getLogger(__name__)

PROJECT_FORMAT = "petrogis-project"
PROJECT_VERSION = 1
PROJECT_EXTENSION = ".pgis"
PROJECT_FILE_FILTER = "PetroGIS project (*.pgis)"


@dataclass
class LoadedProject:
    project: Project
    view: Extent | None = None
    warnings: list[str] = field(default_factory=list)


# --- paths ---------------------------------------------------------------------------------
def _relative(source: str, base: Path) -> str:
    try:
        return Path(os.path.relpath(Path(source).resolve(), base.resolve())).as_posix()
    except ValueError:  # different drive on Windows
        return Path(source).resolve().as_posix()


def _object(value: Any, what: str) -> dict[str, Any]:
    """``value`` as a JSON object (``None`` becomes empty). Anything else is a malformed file."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DataReadError(f"{what} must be an object")
    return value


def _text(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataReadError(f"{what} must be text")
    return value


def _resolve(entry: dict[str, Any], key: str, base: Path) -> Path:
    """Find a stored source: relative to the project file first, then the absolute fallback."""
    relative = _text(entry.get(key), key)
    absolute = _text(entry.get(f"{key}_abs"), f"{key}_abs")
    candidates = []
    if relative:
        candidates.append(base / relative)
    if absolute:
        candidates.append(Path(absolute))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    shown = relative or absolute or "?"
    raise DataReadError(f"Source not found: {shown}")


def _source_fields(source: str, base: Path, key: str = "source") -> dict[str, str]:
    return {key: _relative(source, base), f"{key}_abs": Path(source).resolve().as_posix()}


# --- serialisation -------------------------------------------------------------------------
def _layer_to_dict(layer: Layer, base: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": layer.id,
        "kind": layer.kind.value,
        "name": layer.name,
        "visible": layer.visible,
        **_source_fields(layer.source, base),
    }
    if isinstance(layer, VectorLayer):
        entry["source_layer"] = layer.source_layer
        entry["assume_crs"] = layer.assumed_crs
        entry["style"] = layer.style.to_dict()
    elif isinstance(layer, RasterLayer):
        entry["band"] = layer.band
        entry["style"] = layer.style.to_dict()
    elif isinstance(layer, WellLayer):
        entry["import_options"] = layer.import_options
        entry["style"] = layer.style.to_dict()
        entry["logs"] = [_source_fields(src, base, "path") for src in layer.log_sources]
    return entry


def project_to_dict(
    project: Project, path: str | Path, view: Extent | None = None
) -> dict[str, Any]:
    base = Path(path).resolve().parent
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "crs": project.crs,
        "view": None if view is None else list(view.as_tuple()),
        "layers": [_layer_to_dict(layer, base) for layer in project.layers],
    }


def write_project_file(project: Project, path: str | Path, view: Extent | None = None) -> Path:
    """Write ``project`` to ``path`` atomically, without changing the project's path or dirty flag.

    Autosave uses this directly. :func:`save_project` adds the backup and the state changes.
    """
    path = Path(path)
    data = project_to_dict(project, path, view)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on the same filesystem: never leaves a half-written project
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise DataReadError(f"Cannot save project to {path}: {exc}") from exc
    return path


def backup_path(path: str | Path) -> Path:
    """Where the previous version of a project is kept when it is overwritten."""
    path = Path(path)
    return path.with_name(path.name + ".bak")


def save_project(project: Project, path: str | Path, view: Extent | None = None) -> Path:
    """Save ``project`` atomically and mark it clean. Returns the final path.

    If ``path`` already exists, the previous version is kept as ``<name>.pgis.bak`` first.
    """
    path = Path(path)
    if path.suffix.lower() != PROJECT_EXTENSION:
        path = path.with_suffix(PROJECT_EXTENSION)
    if path.exists():
        try:
            shutil.copy2(path, backup_path(path))
        except OSError as exc:
            raise DataReadError(f"Cannot back up {path.name} before saving: {exc}") from exc
    write_project_file(project, path, view)
    project.path = str(path)
    project.dirty = False
    log.info("Saved project %s (%d layers)", path, len(project))
    return path


# --- autosave and recovery -----------------------------------------------------------------
AUTOSAVE_SUFFIX = ".autosave"
UNTITLED_AUTOSAVE = "untitled.pgis" + AUTOSAVE_SUFFIX


def autosave_path(project_path: str | Path | None, autosave_dir: str | Path) -> Path:
    """Sidecar autosave file: next to a saved project, or in ``autosave_dir`` for an untitled one.

    Keeping it next to the project means its relative source paths resolve the same way.
    """
    if project_path is None:
        return Path(autosave_dir) / UNTITLED_AUTOSAVE
    project_path = Path(project_path)
    return project_path.with_name(project_path.name + AUTOSAVE_SUFFIX)


def find_recovery(project_path: str | Path | None, autosave_dir: str | Path) -> Path | None:
    """An autosave newer than the project file (or any untitled autosave), else ``None``."""
    candidate = autosave_path(project_path, autosave_dir)
    if not candidate.is_file():
        return None
    project_file = None if project_path is None else Path(project_path)
    if project_file is not None and project_file.is_file():
        is_stale = candidate.stat().st_mtime <= project_file.stat().st_mtime
        return None if is_stale else candidate
    return candidate


# --- loading -------------------------------------------------------------------------------
def read_project_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataReadError(f"Project not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DataReadError(f"Cannot read project {path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != PROJECT_FORMAT:
        raise DataReadError(f"{path.name} is not a PetroGIS project")
    version = data.get("version")
    if not isinstance(version, int) or version > PROJECT_VERSION:
        raise DataReadError(
            f"{path.name} was written by a newer PetroGIS (format version {version}). "
            "Please update the application."
        )
    if not isinstance(data.get("layers"), list):
        raise DataReadError(f"{path.name} has no layer list")
    return data


def _load_layer(entry: dict[str, Any], base: Path, crs: str, warnings: list[str]) -> Layer:
    from petrogis.io.las_io import read_las_files
    from petrogis.io.raster_io import load_raster_layer
    from petrogis.io.vector_io import load_vector_layer
    from petrogis.io.wells_io import ColumnMapping, load_well_layer

    try:
        kind = LayerKind(entry.get("kind"))
    except ValueError as exc:
        raise DataReadError(f"unknown layer kind {entry.get('kind')!r}") from exc
    source = _resolve(entry, "source", base)
    style = _object(entry.get("style"), "style")
    layer: Layer
    if kind is LayerKind.VECTOR:
        layer = load_vector_layer(
            source,
            crs,
            layer=_text(entry.get("source_layer"), "source_layer"),
            assume_crs=_text(entry.get("assume_crs"), "assume_crs"),
        )
        layer.style = VectorStyle.from_dict(style)
    elif kind is LayerKind.RASTER:
        band = entry.get("band", 1)
        if isinstance(band, bool) or not isinstance(band, int) or band < 1:
            raise DataReadError(f"band must be a positive integer, got {band!r}")
        layer = load_raster_layer(source, crs, band=band)
        layer.style = RasterStyle.from_dict(style)
    else:
        options = _object(entry.get("import_options"), "import_options")
        mapping = _object(options.get("mapping"), "column mapping")
        result = load_well_layer(
            source,
            crs,
            mapping=ColumnMapping.from_dict(
                {k: _text(v, f"mapping.{k}") for k, v in mapping.items()}
            ),
            source_crs=_text(options.get("source_crs"), "source_crs"),
            sheet=_text(options.get("sheet"), "sheet"),
        )
        warnings.extend(f"{source.name}: {w}" for w in result.warnings)
        layer = result.layer
        layer.style = WellStyle.from_dict(style)
        las_paths: list[Path] = []
        log_entries = entry.get("logs") or []
        if not isinstance(log_entries, list):
            raise DataReadError("logs must be a list")
        for log_entry in log_entries:
            try:
                las_paths.append(_resolve(_object(log_entry, "log entry"), "path", base))
            except DataReadError as exc:
                warnings.append(f"{entry.get('name', '?')}: {exc}")
        batch = read_las_files(las_paths)
        warnings.extend(f"{name}: {reason}" for name, reason in batch.failures)
        match = match_logs(batch.logs, layer)
        for key, logs in match.matched.items():
            layer.logs.setdefault(key, []).extend(logs)
        warnings.extend(f"No matching well for {Path(lg.source).name}" for lg in match.unmatched)
    layer.id = str(entry.get("id") or layer.id)
    layer.name = str(entry.get("name") or layer.name)
    visible = entry.get("visible", True)
    layer.visible = visible if isinstance(visible, bool) else True
    return layer


def _read_view(view: Any) -> Extent | None:
    """The saved map view, or ``None`` when missing or malformed (a bad view is not worth a warning)."""
    if not (isinstance(view, list) and len(view) == 4):
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in view):
        return None
    extent = Extent(*(float(v) for v in view))
    return extent if extent.is_valid() else None


def load_project(path: str | Path) -> LoadedProject:
    """Read a project file and all its sources. Runs synchronously (call it from a worker)."""
    path = Path(path).resolve()
    data = read_project_file(path)
    try:
        project = Project(crs=_text(data.get("crs"), "crs") or "EPSG:32639")
    except CRSError as exc:
        raise DataReadError(f"{path.name}: {exc}") from exc
    result = LoadedProject(project=project)
    base = path.parent
    for entry in reversed(data["layers"]):  # stored top-first, and add_layer inserts on top
        name = entry.get("name", "?") if isinstance(entry, dict) else "?"
        try:
            if not isinstance(entry, dict):
                raise DataReadError("layer entry must be an object")
            project.add_layer(_load_layer(entry, base, project.crs, result.warnings))
        except (DataReadError, ValueError, TypeError) as exc:
            log.warning("Project %s: layer %r skipped: %s", path.name, name, exc)
            result.warnings.append(f"Layer {name!r} skipped: {exc}")
    result.view = _read_view(data.get("view"))
    project.path = str(path)
    project.dirty = False
    return result
