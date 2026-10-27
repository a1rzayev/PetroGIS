"""Processing framework: an algorithm registry with typed parameters, progress and cancel.

An algorithm declares its parameters (layers, numbers, choices, a CRS, output files). The UI
builds a dialog from them and runs the algorithm in a worker thread, so ``run`` must only read
its inputs and write its outputs. Results are always written to files and loaded back as layers,
so project files keep storing references only. No Qt dependency.

Cancel is cooperative: an algorithm calls ``feedback.check()`` between steps, which raises
:class:`Canceled` once the user has asked to stop. A cancelled run deletes partial outputs.
"""

from __future__ import annotations

import math
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from petrogis.core.crs import CRSError, normalize_crs
from petrogis.core.layers import Layer, LayerKind


class ParameterError(ValueError):
    """A parameter value is missing or invalid. The message is shown to the user."""


class Canceled(Exception):
    """The user cancelled the run."""


# --- parameters --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    optional: bool = False

    def validate(self, value: Any, context: ValidationContext) -> Any:
        raise NotImplementedError

    def to_json(self, value: Any) -> Any:
        """A JSON-safe form of a validated value, for the history log."""
        return value


@dataclass(frozen=True)
class LayerParam(Parameter):
    kinds: tuple[LayerKind, ...] = ()
    """Accepted layer kinds (empty: any)."""
    geometry: str | None = None
    """``"polygon"`` or ``"point"`` to require that geometry type (vector layers)."""

    def accepts(self, layer: Layer) -> bool:
        if self.kinds and layer.kind not in self.kinds:
            return False
        if self.geometry is None:
            return True
        gdf = getattr(layer, "gdf", None)
        if gdf is None or gdf.empty:
            return False
        types = set(gdf.geometry.geom_type.dropna())
        wanted = {"polygon": {"Polygon", "MultiPolygon"}, "point": {"Point", "MultiPoint"}}
        return bool(types) and types <= wanted[self.geometry]

    def validate(self, value: Any, context: ValidationContext) -> Any:
        if value is None:
            if self.optional:
                return None
            raise ParameterError(f"{self.label}: choose a layer")
        layer = value if isinstance(value, Layer) else context.layer(value)
        if layer is None:
            raise ParameterError(f"{self.label}: the layer is no longer in the project")
        if not self.accepts(layer):
            raise ParameterError(f"{self.label}: {layer.name!r} is not a suitable layer")
        return layer

    def to_json(self, value: Any) -> Any:
        return None if value is None else {"id": value.id, "name": value.name}


@dataclass(frozen=True)
class NumberParam(Parameter):
    default: float = 0.0
    minimum: float = -1e12
    maximum: float = 1e12
    integer: bool = False

    def validate(self, value: Any, context: ValidationContext) -> Any:
        if value is None:
            value = self.default
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ParameterError(f"{self.label}: must be a number")
        if not math.isfinite(value) or not self.minimum <= value <= self.maximum:
            raise ParameterError(
                f"{self.label}: must be between {self.minimum:g} and {self.maximum:g}"
            )
        return int(value) if self.integer else float(value)


@dataclass(frozen=True)
class ChoiceParam(Parameter):
    choices: tuple[str, ...] = ()
    default: str = ""

    def validate(self, value: Any, context: ValidationContext) -> Any:
        value = self.default if value is None else value
        if value not in self.choices:
            raise ParameterError(f"{self.label}: choose one of {', '.join(self.choices)}")
        return value


@dataclass(frozen=True)
class BoolParam(Parameter):
    default: bool = False

    def validate(self, value: Any, context: ValidationContext) -> Any:
        value = self.default if value is None else value
        if not isinstance(value, bool):
            raise ParameterError(f"{self.label}: must be true or false")
        return value


@dataclass(frozen=True)
class CrsParam(Parameter):
    def validate(self, value: Any, context: ValidationContext) -> Any:
        if not value:
            raise ParameterError(f"{self.label}: choose a coordinate reference system")
        try:
            return normalize_crs(value)
        except CRSError as exc:
            raise ParameterError(f"{self.label}: {exc}") from exc


@dataclass(frozen=True)
class OutputParam(Parameter):
    extension: str = ".gpkg"
    """``.gpkg`` for vector outputs, ``.tif`` for rasters."""

    def validate(self, value: Any, context: ValidationContext) -> Any:
        if not value or not str(value).strip():
            raise ParameterError(f"{self.label}: choose where to save the result")
        path = Path(str(value)).expanduser()
        if path.suffix.lower() != self.extension:
            path = path.with_suffix(self.extension)
        if not path.parent.is_dir():
            raise ParameterError(f"{self.label}: folder {path.parent} does not exist")
        return path

    def to_json(self, value: Any) -> Any:
        return str(value)


@dataclass
class ValidationContext:
    layers: dict[str, Layer]
    """Project layers by id (and, as a fallback for history re-runs, by name)."""

    def layer(self, reference: Any) -> Layer | None:
        """A layer by id, else by unique name (history entries store both)."""
        if isinstance(reference, dict):
            ids, names = [reference.get("id")], [reference.get("name")]
        else:
            ids, names = [reference], [reference]
        for layer_id in ids:
            if isinstance(layer_id, str) and layer_id in self.layers:
                return self.layers[layer_id]
        for name in names:
            if isinstance(name, str):
                by_name = [lyr for lyr in self.layers.values() if lyr.name == name]
                if len(by_name) == 1:
                    return by_name[0]
        return None


# --- running -----------------------------------------------------------------------------------
class Feedback:
    """Progress, messages and cancel, shared between the worker and the UI. Thread-safe."""

    def __init__(self, on_progress: Callable[[float], None] | None = None) -> None:
        self._canceled = threading.Event()
        self._on_progress = on_progress
        self._lock = threading.Lock()
        self.messages: list[str] = []

    def cancel(self) -> None:
        self._canceled.set()

    @property
    def canceled(self) -> bool:
        return self._canceled.is_set()

    def check(self) -> None:
        """Raise :class:`Canceled` if the user asked to stop. Call between steps."""
        if self._canceled.is_set():
            raise Canceled

    def progress(self, fraction: float) -> None:
        self.check()
        if self._on_progress is not None:
            self._on_progress(max(0.0, min(1.0, float(fraction))))

    def log(self, message: str) -> None:
        with self._lock:
            self.messages.append(message)


@dataclass
class RunContext:
    project_crs: str
    transformations: dict[tuple[str, str], str] = field(default_factory=dict)

    def transformation(self, source_crs: str | None) -> str | None:
        if source_crs is None:
            return None
        return self.transformations.get((normalize_crs(source_crs), self.project_crs))


@dataclass
class AlgorithmResult:
    outputs: dict[str, Path]
    """Written files by output parameter name, to load back as layers."""
    summary: str = ""


class Algorithm(ABC):
    id: str = ""
    name: str = ""
    group: str = ""
    description: str = ""

    @property
    @abstractmethod
    def parameters(self) -> tuple[Parameter, ...]: ...

    @abstractmethod
    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        """Do the work on validated ``values``. Runs in a worker thread."""

    def validate(self, values: dict[str, Any], context: ValidationContext) -> dict[str, Any]:
        """Check every parameter. Raises :class:`ParameterError` on the first problem."""
        out = {}
        for parameter in self.parameters:
            out[parameter.name] = parameter.validate(values.get(parameter.name), context)
        self.check(out)
        return out

    def check(self, values: dict[str, Any]) -> None:  # noqa: B027 - optional hook
        """Cross-parameter checks (override when needed)."""

    def to_json(self, values: dict[str, Any]) -> dict[str, Any]:
        return {p.name: p.to_json(values.get(p.name)) for p in self.parameters}

    def execute(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        """Run, deleting partial output files if the run fails or is cancelled."""
        outputs = [
            v for p in self.parameters if isinstance(p, OutputParam) and (v := values.get(p.name))
        ]
        existed = {Path(o): Path(o).exists() for o in outputs}
        try:
            result = self.run(values, context, feedback)
            feedback.progress(1.0)  # a cancel that lands here still discards the output
        except BaseException:
            for path, was_there in existed.items():
                if not was_there:
                    path.unlink(missing_ok=True)
            raise
        return result


_REGISTRY: dict[str, Algorithm] = {}


def register_algorithm(algorithm: Algorithm) -> None:
    if not algorithm.id or algorithm.id in _REGISTRY:
        raise ValueError(f"Algorithm id {algorithm.id!r} is empty or already registered")
    _REGISTRY[algorithm.id] = algorithm


def algorithms() -> list[Algorithm]:
    """Every registered algorithm, sorted by group then name."""
    _load_builtins()
    return sorted(_REGISTRY.values(), key=lambda a: (a.group, a.name))


def get_algorithm(algorithm_id: str) -> Algorithm:
    _load_builtins()
    try:
        return _REGISTRY[algorithm_id]
    except KeyError:
        raise ParameterError(f"Unknown algorithm {algorithm_id!r}") from None


def _load_builtins() -> None:
    from petrogis.analysis import algorithms as _builtins  # noqa: F401 - registers on import
