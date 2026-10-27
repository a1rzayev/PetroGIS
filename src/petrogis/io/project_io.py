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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from petrogis.core.crs import CRSError, is_known_pipeline, normalize_crs
from petrogis.core.extent import Extent
from petrogis.core.layers import DelimitedLayer, Layer, RasterLayer, VectorLayer, WellLayer
from petrogis.core.logs import match_logs
from petrogis.core.project import Project
from petrogis.core.styles import RasterStyle, VectorStyle, WellStyle
from petrogis.io.errors import DataReadError

log = logging.getLogger(__name__)

PROJECT_FORMAT = "petrogis-project"
PROJECT_VERSION = 3
"""Version 2 added well ``surveys`` and ``tops``; version 3 added interactive ``picks`` and the
``correlation`` section. Older versions are still read."""
MAX_PICKS = 100_000
"""Most interactive picks read from one well entry, so a hostile file cannot exhaust memory."""
PROJECT_EXTENSION = ".pgis"
PROJECT_FILE_FILTER = "PetroGIS project (*.pgis)"


class SourceNotFoundError(DataReadError):
    """A layer's data file is missing. The UI offers to locate it (repair broken paths)."""


@dataclass(frozen=True)
class MissingLayer:
    """A layer that couldn't be loaded because its source file is missing."""

    position: int
    """Index in the stored layer list (top-first), so a repaired layer goes back in its place."""
    entry: dict[str, Any]
    """The layer's entry from the project file; repair updates its source and re-loads it."""
    name: str
    stored_path: str


@dataclass
class LoadedProject:
    project: Project
    view: Extent | None = None
    warnings: list[str] = field(default_factory=list)
    missing: list[MissingLayer] = field(default_factory=list)
    layer_positions: dict[str, int] = field(default_factory=dict)
    """Stored position (top-first) of each loaded layer, by id, for re-inserting repairs."""


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
    raise SourceNotFoundError(f"Source not found: {shown}")


def _source_fields(source: str, base: Path, key: str = "source") -> dict[str, str]:
    return {key: _relative(source, base), f"{key}_abs": Path(source).resolve().as_posix()}


# --- layer codecs ------------------------------------------------------------------------------
_S = TypeVar("_S", VectorStyle, RasterStyle, WellStyle)


@dataclass
class LoadContext:
    base: Path
    """Directory of the project file; relative sources resolve against it."""
    crs: str
    """Project CRS that layers are loaded into."""
    warnings: list[str]
    transformations: dict[tuple[str, str], str] = field(default_factory=dict)

    def transformation(self, source_crs: str) -> str | None:
        return self.transformations.get((normalize_crs(source_crs), self.crs))

    def style(self, style_type: type[_S], entry: dict[str, Any], source: Path) -> _S:
        """The entry's style, or the default style plus a warning if it is invalid.

        A broken style (bad colour, unparsable expression, too many classes) must not cost the
        user the layer itself.
        """
        try:
            return style_type.from_dict(_object(entry.get("style"), "style"))
        except (DataReadError, ValueError, TypeError) as exc:
            self.warnings.append(f"{source.name}: invalid style replaced by the default ({exc})")
            return style_type()


@dataclass(frozen=True)
class LayerCodec:
    """How one layer kind is written to, and read from, a project file.

    ``to_dict`` returns only the kind-specific fields. ``load`` receives the resolved source path
    and the whole entry. Common fields (id, name, visibility, source) are handled for every kind.
    """

    kind: str
    to_dict: Callable[[Any, Path], dict[str, Any]]
    load: Callable[[Path, dict[str, Any], LoadContext], Layer]


_CODECS: dict[str, LayerCodec] = {}


def register_layer_codec(codec: LayerCodec) -> None:
    """Make a layer kind serialisable. Phase 2 layer kinds and plugins register here."""
    _CODECS[codec.kind] = codec


def layer_to_dict(layer: Layer, base: Path) -> dict[str, Any]:
    codec = _CODECS.get(layer.kind.value)
    if codec is None:
        raise DataReadError(f"Layer kind {layer.kind.value!r} can't be saved")
    return {
        "id": layer.id,
        "kind": layer.kind.value,
        "name": layer.name,
        "visible": layer.visible,
        **_source_fields(layer.source, base),
        **codec.to_dict(layer, base),
    }


def load_layer_entry(entry: dict[str, Any], context: LoadContext) -> Layer:
    """Load one layer entry. Raises ``DataReadError`` (or ``SourceNotFoundError``) on failure."""
    kind = entry.get("kind")
    codec = _CODECS.get(kind) if isinstance(kind, str) else None
    if codec is None:
        raise DataReadError(f"unknown layer kind {kind!r}")
    source = _resolve(entry, "source", context.base)
    layer = codec.load(source, entry, context)
    layer.id = str(entry.get("id") or layer.id)
    layer.name = str(entry.get("name") or layer.name)
    visible = entry.get("visible", True)
    layer.visible = visible if isinstance(visible, bool) else True
    return layer


def _vector_to_dict(layer: VectorLayer, _base: Path) -> dict[str, Any]:
    return {
        "source_layer": layer.source_layer,
        "assume_crs": layer.assumed_crs,
        "encoding": layer.encoding,
        "style": layer.style.to_dict(),
    }


def _vector_load(source: Path, entry: dict[str, Any], ctx: LoadContext) -> Layer:
    from petrogis.io.vector_io import load_vector_layer

    layer = load_vector_layer(
        source,
        ctx.crs,
        layer=_text(entry.get("source_layer"), "source_layer"),
        assume_crs=_text(entry.get("assume_crs"), "assume_crs"),
        encoding=_text(entry.get("encoding"), "encoding"),
        transformation=ctx.transformation,
    )
    layer.style = ctx.style(VectorStyle, entry, source)
    return layer


def _delimited_to_dict(layer: DelimitedLayer, _base: Path) -> dict[str, Any]:
    return {"import_options": layer.import_options, "style": layer.style.to_dict()}


def _delimited_load(source: Path, entry: dict[str, Any], ctx: LoadContext) -> Layer:
    from petrogis.io.delimited_io import load_delimited_layer

    options = _object(entry.get("import_options"), "import_options")
    result = load_delimited_layer(
        source,
        ctx.crs,
        x_column=_text(options.get("x"), "x column"),
        y_column=_text(options.get("y"), "y column"),
        source_crs=_text(options.get("source_crs"), "source_crs"),
        transformation=ctx.transformation,
    )
    ctx.warnings.extend(f"{source.name}: {w}" for w in result.warnings)
    result.layer.style = ctx.style(VectorStyle, entry, source)
    return result.layer


def _raster_to_dict(layer: RasterLayer, _base: Path) -> dict[str, Any]:
    return {"band": layer.band, "assume_crs": layer.assumed_crs, "style": layer.style.to_dict()}


def _raster_load(source: Path, entry: dict[str, Any], ctx: LoadContext) -> Layer:
    from petrogis.io.raster_io import load_raster_layer

    band = entry.get("band", 1)
    if isinstance(band, bool) or not isinstance(band, int) or band < 1:
        raise DataReadError(f"band must be a positive integer, got {band!r}")
    layer = load_raster_layer(
        source,
        ctx.crs,
        band=band,
        assume_crs=_text(entry.get("assume_crs"), "assume_crs"),
        transformation=ctx.transformation,
    )
    layer.style = ctx.style(RasterStyle, entry, source)
    return layer


def _wells_to_dict(layer: WellLayer, base: Path) -> dict[str, Any]:
    return {
        "import_options": layer.import_options,
        "style": layer.style.to_dict(),
        "logs": [_source_fields(src, base, "path") for src in layer.log_sources],
        "surveys": [_import_record(r, base) for r in layer.survey_imports],
        "tops": [_import_record(r, base) for r in layer.tops_imports],
        "picks": [
            {"well": key, "surface": surface, "md": None if top is None else top.md}
            for (key, surface), top in layer.pick_overrides.items()
        ],
    }


def _apply_picks(layer: WellLayer, entry: dict[str, Any], ctx: LoadContext) -> None:
    """Re-apply interactive pick edits (format version 3) on top of the tops files."""
    from petrogis.core.tops import PICKED, Top

    name = entry.get("name", "?")
    picks = entry.get("picks") or []
    if not isinstance(picks, list):
        ctx.warnings.append(f"{name}: picks must be a list")
        return
    if len(picks) > MAX_PICKS:
        ctx.warnings.append(f"{name}: only the first {MAX_PICKS:,} picks were read")
        picks = picks[:MAX_PICKS]
    skipped = 0
    for pick in picks:
        try:
            pick = _object(pick, "pick")
            well = _text(pick.get("well"), "pick well")
            surface = _text(pick.get("surface"), "pick surface")
            md = pick.get("md")
            if not well or not surface or not surface.strip() or len(surface) > 200:
                raise DataReadError("pick needs a well and a surface")
            if md is not None and (
                isinstance(md, bool) or not isinstance(md, int | float) or not 0 <= md < 1e6
            ):
                raise DataReadError(f"invalid pick depth {md!r}")
        except DataReadError:
            skipped += 1
            continue
        position = layer.find(well)
        if position is None:
            skipped += 1
            continue
        key = layer.key_at(position)
        kept = [t for t in layer.tops.get(key, []) if t.surface != surface]
        if md is None:
            layer.pick_overrides[(key, surface)] = None
            if kept:
                layer.tops[key] = kept
            else:
                layer.tops.pop(key, None)
        else:
            top = Top(str(layer.gdf["name"].iloc[position]), key, surface, float(md), PICKED)  # type: ignore[index]
            layer.pick_overrides[(key, surface)] = top
            layer.tops[key] = sorted([*kept, top], key=lambda t: t.md)
    if skipped:
        ctx.warnings.append(f"{name}: skipped {skipped} invalid or unmatched pick(s)")


def _import_record(record: dict[str, Any], base: Path) -> dict[str, Any]:
    return {
        **_source_fields(str(record["path"]), base, "path"),
        "import_options": record.get("import_options", {}),
    }


def _import_options(entry: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Validated survey/tops import options from an untrusted project entry."""
    options = _object(entry.get("import_options"), "import_options")
    mapping = _object(options.get("mapping"), "column mapping")
    return {
        "mapping": {f: _text(mapping.get(f), f"mapping.{f}") for f in fields},
        "depth_unit": _text(options.get("depth_unit"), "depth_unit"),
        "sheet": _text(options.get("sheet"), "sheet"),
        "default_well": _text(options.get("default_well"), "default_well"),
    }


def _load_well_extras(layer: WellLayer, entry: dict[str, Any], ctx: LoadContext) -> None:
    """Re-read the survey and tops files of a well entry (format version 2) and attach them."""
    from petrogis.io.survey_io import SURVEY_FIELDS, SurveyMapping, load_surveys
    from petrogis.io.tops_io import TOPS_FIELDS, TopsMapping, load_tops

    name = entry.get("name", "?")
    for key, fields in (("surveys", SURVEY_FIELDS), ("tops", TOPS_FIELDS)):
        records = entry.get(key) or []
        if not isinstance(records, list):
            ctx.warnings.append(f"{name}: {key} must be a list")
            continue
        for record in records:
            try:
                record = _object(record, f"{key} entry")
                path = _resolve(record, "path", ctx.base)
                options = _import_options(record, fields)
                common = {k: options[k] for k in ("depth_unit", "sheet", "default_well")}
                if key == "surveys":
                    result = load_surveys(
                        path, SurveyMapping.from_dict(options["mapping"]), **common
                    )
                    found = result.trajectories
                    layer.trajectories.update(
                        {k: v for k, v in found.items() if layer.find(result.names[k]) is not None}
                    )
                    layer.survey_imports.append(
                        {"path": str(path), "import_options": result.options}
                    )
                else:
                    result = load_tops(path, TopsMapping.from_dict(options["mapping"]), **common)
                    found = result.tops
                    for k, picks in found.items():
                        if layer.find(result.names[k]) is not None:
                            layer.tops[k] = picks
                    layer.tops_imports.append({"path": str(path), "import_options": result.options})
            except DataReadError as exc:
                ctx.warnings.append(f"{name}: {exc}")
                continue
            ctx.warnings.extend(f"{path.name}: {w}" for w in result.warnings)
            unmatched = sorted(
                result.names[k] for k in found if layer.find(result.names[k]) is None
            )
            if unmatched:
                ctx.warnings.append(f"{path.name}: no matching well for {', '.join(unmatched)}")


def _wells_load(source: Path, entry: dict[str, Any], ctx: LoadContext) -> Layer:
    from petrogis.io.las_io import read_las_files
    from petrogis.io.wells_io import ColumnMapping, load_well_layer

    options = _object(entry.get("import_options"), "import_options")
    mapping = _object(options.get("mapping"), "column mapping")
    result = load_well_layer(
        source,
        ctx.crs,
        mapping=ColumnMapping.from_dict({k: _text(v, f"mapping.{k}") for k, v in mapping.items()}),
        source_crs=_text(options.get("source_crs"), "source_crs"),
        sheet=_text(options.get("sheet"), "sheet"),
        transformation=ctx.transformation,
    )
    ctx.warnings.extend(f"{source.name}: {w}" for w in result.warnings)
    layer = result.layer
    layer.style = ctx.style(WellStyle, entry, source)
    las_paths: list[Path] = []
    log_entries = entry.get("logs") or []
    if not isinstance(log_entries, list):
        raise DataReadError("logs must be a list")
    for log_entry in log_entries:
        try:
            las_paths.append(_resolve(_object(log_entry, "log entry"), "path", ctx.base))
        except DataReadError as exc:
            ctx.warnings.append(f"{entry.get('name', '?')}: {exc}")
    batch = read_las_files(las_paths)
    ctx.warnings.extend(f"{name}: {reason}" for name, reason in batch.failures)
    match = match_logs(batch.logs, layer)
    for key, logs in match.matched.items():
        layer.logs.setdefault(key, []).extend(logs)
    ctx.warnings.extend(f"No matching well for {Path(lg.source).name}" for lg in match.unmatched)
    _load_well_extras(layer, entry, ctx)
    _apply_picks(layer, entry, ctx)
    return layer


for _codec in (
    LayerCodec("vector", _vector_to_dict, _vector_load),
    LayerCodec("delimited", _delimited_to_dict, _delimited_load),
    LayerCodec("raster", _raster_to_dict, _raster_load),
    LayerCodec("wells", _wells_to_dict, _wells_load),
):
    register_layer_codec(_codec)


def project_to_dict(
    project: Project, path: str | Path, view: Extent | None = None
) -> dict[str, Any]:
    base = Path(path).resolve().parent
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "crs": project.crs,
        "view": None if view is None else list(view.as_tuple()),
        "transformations": [
            {"source": source, "target": target, "pipeline": pipeline}
            for (source, target), pipeline in sorted(project.transformations.items())
        ],
        "layers": [layer_to_dict(layer, base) for layer in project.layers],
        "correlation": None if project.section is None else project.section.to_dict(),
    }


def _read_section(data: Any, project: Project, warnings: list[str]) -> None:
    """Restore the correlation section, dropping wells or layers that did not load."""
    from dataclasses import replace

    from petrogis.core.correlation import CorrelationSection

    if data is None:
        return
    try:
        if not isinstance(data, dict):
            raise ValueError("must be an object")
        section = CorrelationSection.from_dict(data)
    except (ValueError, TypeError) as exc:
        warnings.append(f"Correlation section ignored: {exc}")
        return
    try:
        layer = project.get(section.layer_id)
    except KeyError:
        warnings.append("Correlation section ignored: its well layer did not load")
        return
    if not isinstance(layer, WellLayer):
        warnings.append("Correlation section ignored: its layer is not a well layer")
        return
    present = tuple(k for k in section.well_keys if layer.find(k) is not None)
    if len(present) < len(section.well_keys):
        missing = len(section.well_keys) - len(present)
        warnings.append(f"Correlation section: {missing} well(s) no longer in {layer.name}")
    project.set_section(replace(section, well_keys=present))


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


def _read_view(view: Any) -> Extent | None:
    """The saved map view, or ``None`` when missing or malformed (not worth a warning)."""
    if not (isinstance(view, list) and len(view) == 4):
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in view):
        return None
    extent = Extent(*(float(v) for v in view))
    return extent if extent.is_valid() else None


def _read_transformations(
    value: Any, project_crs: str, warnings: list[str]
) -> dict[tuple[str, str], str]:
    """Stored datum transformations, keeping only pipelines PROJ itself offers for the pair.

    A project file is untrusted, so an unknown pipeline is dropped with a warning instead of being
    handed to PROJ (it could, for example, reference a local file as a grid).
    """
    if not isinstance(value, list):
        return {}
    chosen: dict[tuple[str, str], str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        source, target, pipeline = item.get("source"), item.get("target"), item.get("pipeline")
        if not all(isinstance(v, str) and v for v in (source, target, pipeline)):
            continue
        try:
            pair = (normalize_crs(source), normalize_crs(target))
            known = pair[1] == project_crs and is_known_pipeline(*pair, pipeline)
        except (CRSError, ValueError) as exc:
            warnings.append(f"Ignored a datum transformation: {exc}")
            continue
        if known:
            chosen[pair] = pipeline
        else:
            warnings.append(
                f"Ignored an unrecognised datum transformation for {pair[0]} → {pair[1]}"
            )
    return chosen


def load_project(path: str | Path) -> LoadedProject:
    """Read a project file and all its sources. Runs synchronously (call it from a worker)."""
    path = Path(path).resolve()
    data = read_project_file(path)
    try:
        project = Project(crs=_text(data.get("crs"), "crs") or "EPSG:32639")
    except CRSError as exc:
        raise DataReadError(f"{path.name}: {exc}") from exc
    result = LoadedProject(project=project)
    transformations = _read_transformations(
        data.get("transformations"), project.crs, result.warnings
    )
    for (source, target), pipeline in transformations.items():
        project.set_transformation(source, target, pipeline)
    context = LoadContext(path.parent, project.crs, result.warnings, transformations)
    entries = data["layers"]
    # Stored top-first; add_layer inserts on top, so load bottom-up.
    for position in reversed(range(len(entries))):
        entry = entries[position]
        name = entry.get("name", "?") if isinstance(entry, dict) else "?"
        try:
            if not isinstance(entry, dict):
                raise DataReadError("layer entry must be an object")
            layer = load_layer_entry(entry, context)
            project.add_layer(layer)
            result.layer_positions[layer.id] = position
        except SourceNotFoundError as exc:
            stored = entry.get("source") or entry.get("source_abs") or "?"
            result.missing.insert(0, MissingLayer(position, entry, str(name), str(stored)))
            result.warnings.append(f"Layer {name!r}: {exc}")
        except (DataReadError, ValueError, TypeError) as exc:
            log.warning("Project %s: layer %r skipped: %s", path.name, name, exc)
            result.warnings.append(f"Layer {name!r} skipped: {exc}")
    _read_section(data.get("correlation"), project, result.warnings)
    result.view = _read_view(data.get("view"))
    project.path = str(path)
    project.dirty = False
    return result


def reload_layers(
    layers: list[Layer],
    project_crs: str,
    transformations: dict[tuple[str, str], str],
    base: Path,
) -> tuple[list[Layer], list[str]]:
    """Re-read ``layers`` (top-first) into ``project_crs``, keeping ids, names, styles and logs.

    All or nothing: if any layer fails, ``DataReadError`` is raised and the caller keeps the old
    layers. Used to change the project CRS or a datum transformation with layers loaded.
    """
    warnings: list[str] = []
    context = LoadContext(base, normalize_crs(project_crs), warnings, dict(transformations))
    reloaded: list[Layer] = []
    for layer in layers:
        try:
            reloaded.append(load_layer_entry(layer_to_dict(layer, base), context))
        except (DataReadError, ValueError, TypeError) as exc:
            raise DataReadError(f"Could not re-read layer {layer.name!r}: {exc}") from exc
    return reloaded, warnings


def repaired_entry(entry: dict[str, Any], new_source: Path) -> dict[str, Any]:
    """``entry`` pointing at ``new_source`` (absolute), for re-loading a repaired layer."""
    fixed = dict(entry)
    fixed.pop("source", None)
    fixed["source_abs"] = Path(new_source).resolve().as_posix()
    return fixed


MAX_SEARCH_FILES = 50_000


def find_files_by_name(
    folder: str | Path, names: set[str], max_files: int = MAX_SEARCH_FILES
) -> dict[str, Path]:
    """Search ``folder`` recursively for files named like ``names`` (case-insensitive).

    Returns the first match per name. Stops after ``max_files`` entries so a huge drive can't hang
    the search. Symlinked directories are not followed.
    """
    wanted = {name.lower(): name for name in names}
    found: dict[str, Path] = {}
    seen = 0
    for root, _dirs, files in os.walk(folder, followlinks=False):
        for filename in files:
            seen += 1
            original = wanted.get(filename.lower())
            if original is not None and original not in found:
                found[original] = Path(root) / filename
            if seen >= max_files or len(found) == len(wanted):
                return found
    return found
