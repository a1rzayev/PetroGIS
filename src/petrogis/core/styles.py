"""Layer styles: plain data, serialisable to and from dicts for the project file."""

from __future__ import annotations

import math
import re
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Any, Self

from petrogis.core.classify import METHODS, RAMPS

COLORMAPS = ("viridis", "plasma", "inferno", "magma", "cividis", "turbo", "gray")
POINT_SYMBOLS = ("o", "s", "t", "d", "+", "x")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
RENDERERS = ("single", "categorized", "graduated")
MAX_CLASSES = 256
"""Most categories or classes one style may hold. Bounds the graphics items a style creates."""
MAX_LABEL_TEXT = 500
VECTOR_PALETTE = ("#e41a1c", "#000000", "#ff7f00", "#f781bf", "#984ea3", "#a65628", "#377eb8")
"""Default colours for new vector layers, picked to stand out on the raster colormaps."""


def _check_color(value: object) -> None:
    if not isinstance(value, str) or not _HEX_COLOR.match(value):
        raise ValueError(f"Colour must be '#rrggbb', got {value!r}")


def _check_number(name: str, value: object, minimum: float | None = None) -> None:
    """Reject non-numbers, booleans, NaN/inf and values below ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value!r}")


def _check_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false, got {value!r}")


def _check_opacity(name: str, value: object) -> None:
    _check_number(name, value, minimum=0.0)
    if value > 1.0:  # type: ignore[operator]
        raise ValueError(f"{name} must be between 0 and 1")


def _check_text(name: str, value: object, limit: int = MAX_LABEL_TEXT) -> None:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name} must be text of at most {limit} characters")


def _check_expression(name: str, value: object) -> None:
    """Empty, or an expression our parser accepts. Never evaluated here."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if value.strip():
        from petrogis.core.expressions import ExpressionError, parse_expression

        try:
            parse_expression(value)
        except ExpressionError as exc:
            raise ValueError(f"{name}: {exc}") from exc


def _items(cls: type, data: object, name: str) -> list[Any]:
    if not isinstance(data, list):
        raise ValueError(f"{name} must be a list")
    if len(data) > MAX_CLASSES:
        raise ValueError(f"{name} can hold at most {MAX_CLASSES} entries")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError(f"every entry of {name} must be an object")
    return [cls.from_dict(item) for item in data]  # type: ignore[attr-defined]


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from a dict, ignoring unknown keys so older/newer project files still load."""
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        missing = [
            f.name
            for f in fields(cls)  # type: ignore[arg-type]
            if f.name not in data and f.default is MISSING and f.default_factory is MISSING
        ]
        if missing:
            raise ValueError(f"{cls.__name__} is missing {', '.join(missing)}")
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Category(_Serializable):
    """One value of a categorized style. ``value`` ``None`` matches NULL."""

    value: str | int | float | bool | None
    color: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.value is not None and not isinstance(self.value, str | int | float | bool):
            raise ValueError(f"Category value must be text, a number or null, got {self.value!r}")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Category value must be finite")
        if isinstance(self.value, str):
            _check_text("category value", self.value)
        _check_color(self.color)
        _check_text("category label", self.label)

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        return "NULL" if self.value is None else str(self.value)


@dataclass
class ClassBreak(_Serializable):
    """One class of a graduated style, covering ``lower < value <= upper``."""

    lower: float
    upper: float
    color: str
    label: str = ""

    def __post_init__(self) -> None:
        _check_number("lower", self.lower)
        _check_number("upper", self.upper)
        if self.lower > self.upper:
            raise ValueError("A class's lower bound must not exceed its upper bound")
        _check_color(self.color)
        _check_text("class label", self.label)

    @property
    def display_label(self) -> str:
        from petrogis.core.classify import format_break

        return self.label or f"{format_break(self.lower)} – {format_break(self.upper)}"


@dataclass
class LabelStyle(_Serializable):
    """Map labels for a vector layer: the text is a field or expression."""

    enabled: bool = False
    expression: str = ""
    size: float = 9.0
    """Font size in points."""
    color: str = "#000000"

    def __post_init__(self) -> None:
        _check_bool("enabled", self.enabled)
        _check_expression("label expression", self.expression)
        _check_number("label size", self.size, minimum=1.0)
        _check_color(self.color)


@dataclass
class VectorStyle(_Serializable):
    color: str = "#1f77b4"
    """Single-symbol colour, and the colour of features no category or class matches."""
    line_width: float = 1.5
    point_size: float = 7.0
    point_symbol: str = "o"
    opacity: float = 1.0
    fill_opacity: float = 0.0
    """Opacity of polygon fills, relative to ``opacity``. 0 draws outlines only."""
    renderer: str = "single"
    """``single``, ``categorized`` or ``graduated``."""
    classify_by: str = ""
    """Field or expression the categorized and graduated renderers classify on."""
    categories: list[Category] = field(default_factory=list)
    breaks: list[ClassBreak] = field(default_factory=list)
    ramp: str = "viridis"
    """Ramp last used to colour the graduated classes (kept for re-classifying)."""
    method: str = "equal_interval"
    labels: LabelStyle = field(default_factory=LabelStyle)

    def __post_init__(self) -> None:
        _check_color(self.color)
        _check_number("line_width", self.line_width, minimum=0.1)
        _check_number("point_size", self.point_size, minimum=0.1)
        if self.point_symbol not in POINT_SYMBOLS:
            raise ValueError(f"Unknown point symbol {self.point_symbol!r}")
        _check_opacity("opacity", self.opacity)
        _check_opacity("fill_opacity", self.fill_opacity)
        if self.renderer not in RENDERERS:
            raise ValueError(f"Unknown renderer {self.renderer!r}")
        _check_expression("classify_by", self.classify_by)
        if self.renderer != "single" and not self.classify_by.strip():
            raise ValueError(f"A {self.renderer} style needs a field or expression")
        if self.ramp not in RAMPS:
            raise ValueError(f"Unknown colour ramp {self.ramp!r}")
        if self.method not in METHODS:
            raise ValueError(f"Unknown classification method {self.method!r}")
        if len(self.categories) > MAX_CLASSES or len(self.breaks) > MAX_CLASSES:
            raise ValueError(f"A style can hold at most {MAX_CLASSES} classes")
        if not all(isinstance(c, Category) for c in self.categories):
            raise ValueError("categories must be Category objects")
        if not all(isinstance(b, ClassBreak) for b in self.breaks):
            raise ValueError("breaks must be ClassBreak objects")
        if not isinstance(self.labels, LabelStyle):
            raise ValueError("labels must be a LabelStyle")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        if "categories" in values:
            values["categories"] = _items(Category, values["categories"], "categories")
        if "breaks" in values:
            values["breaks"] = _items(ClassBreak, values["breaks"], "breaks")
        if "labels" in values:
            if not isinstance(values["labels"], dict):
                raise ValueError("labels must be an object")
            values["labels"] = LabelStyle.from_dict(values["labels"])
        return cls(**values)


@dataclass
class WellStyle(_Serializable):
    color: str = "#000000"
    symbol_size: float = 9.0
    show_labels: bool = True
    label_size: float = 9.0
    """Label font size in points."""
    label_color: str = "#000000"
    symbol_by_status: bool = True
    """Draw each well with the conventional symbol for its status (oil, gas, dry, injector…)."""
    status_field: str = ""
    """Column holding the well status. Empty means detect it from the column names."""
    label_expression: str = ""
    """Label text as a field or expression. Empty means the well name."""
    show_paths: bool = True
    """Draw deviated well paths and bottom-hole locations."""

    def __post_init__(self) -> None:
        _check_color(self.color)
        _check_color(self.label_color)
        _check_number("symbol_size", self.symbol_size, minimum=0.1)
        _check_number("label_size", self.label_size, minimum=0.1)
        _check_bool("show_labels", self.show_labels)
        _check_bool("symbol_by_status", self.symbol_by_status)
        _check_bool("show_paths", self.show_paths)
        _check_text("status_field", self.status_field)
        _check_expression("label_expression", self.label_expression)


@dataclass
class RasterStyle(_Serializable):
    colormap: str = "viridis"
    vmin: float | None = None
    """Lower end of the colour range. ``None`` means use the layer's default (2nd percentile)."""
    vmax: float | None = None
    """Upper end of the colour range. ``None`` means use the layer's default (98th percentile)."""
    invert: bool = False
    opacity: float = 1.0

    def __post_init__(self) -> None:
        if self.colormap not in COLORMAPS:
            raise ValueError(f"Unknown colormap {self.colormap!r}")
        for name in ("vmin", "vmax"):
            if getattr(self, name) is not None:
                _check_number(name, getattr(self, name))
        if self.vmin is not None and self.vmax is not None and self.vmin >= self.vmax:
            raise ValueError("vmin must be less than vmax")
        _check_bool("invert", self.invert)
        _check_opacity("opacity", self.opacity)
