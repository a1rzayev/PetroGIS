"""Layer styles: plain data, serialisable to and from dicts for the project file."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Self

COLORMAPS = ("viridis", "plasma", "inferno", "magma", "cividis", "turbo", "gray")
POINT_SYMBOLS = ("o", "s", "t", "d", "+", "x")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _check_color(value: str) -> None:
    if not _HEX_COLOR.match(value):
        raise ValueError(f"Colour must be '#rrggbb', got {value!r}")


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from a dict, ignoring unknown keys so older/newer project files still load."""
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class VectorStyle(_Serializable):
    color: str = "#1f77b4"
    line_width: float = 1.5
    point_size: float = 7.0
    point_symbol: str = "o"
    opacity: float = 1.0

    def __post_init__(self) -> None:
        _check_color(self.color)
        if self.point_symbol not in POINT_SYMBOLS:
            raise ValueError(f"Unknown point symbol {self.point_symbol!r}")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("Opacity must be between 0 and 1")


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
        if self.vmin is not None and self.vmax is not None and self.vmin >= self.vmax:
            raise ValueError("vmin must be less than vmax")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("Opacity must be between 0 and 1")
