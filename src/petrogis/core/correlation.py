"""Well-to-well correlation: track templates, section state and depth datums. No Qt dependency.

A correlation section shows several wells side by side on one depth axis. Every well uses the
same track template. A track holds one or more curves, each with its own scale, drawn in the
track's normalised 0–1 width (the convention of correlation headers in Petra and Kingdom), so
neutron and density can share a track without a second axis.

Depths: TVDSS (default, needs a KB), TVD or MD, optionally flattened on a surface, so that the
surface's pick sits at depth 0 in every well that has it.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from petrogis.core.mnemonics import CurveFamily

if TYPE_CHECKING:
    from petrogis.core.layers import WellLayer

DATUMS = ("TVDSS", "TVD", "MD")
SHADINGS = ("none", "sand", "crossover")
MAX_TRACKS = 8
MAX_CURVES_PER_TRACK = 4
MAX_SECTION_WELLS = 50
MAX_NAME = 200
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _text(name: str, value: object, limit: int = MAX_NAME) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")
    return value


@dataclass(frozen=True)
class CurveSpec:
    """One curve in a track. ``left``/``right`` are the values at the track's edges, so a
    reversed scale (neutron porosity) simply has ``left > right``."""

    family: CurveFamily
    left: float
    right: float
    color: str = "#000000"
    dashed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.family, CurveFamily):
            raise ValueError(f"Unknown curve family {self.family!r}")
        _finite("left", self.left)
        _finite("right", self.right)
        if self.left == self.right:
            raise ValueError(f"{self.family.value}: the scale must not be empty")
        if not isinstance(self.color, str) or not _HEX.match(self.color):
            raise ValueError(f"Colour must be '#rrggbb', got {self.color!r}")
        if not isinstance(self.dashed, bool):
            raise ValueError("dashed must be true or false")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["family"] = self.family.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurveSpec:
        try:
            family = CurveFamily(data.get("family"))
        except ValueError as exc:
            raise ValueError(f"Unknown curve family {data.get('family')!r}") from exc
        return cls(
            family,
            data.get("left"),  # type: ignore[arg-type]  # validated in __post_init__
            data.get("right"),  # type: ignore[arg-type]
            data.get("color", "#000000"),
            data.get("dashed", False),
        )


@dataclass(frozen=True)
class TrackSpec:
    name: str
    curves: tuple[CurveSpec, ...]
    log: bool = False
    """Logarithmic scale (for resistivity). Every curve in the track must then be positive."""
    shading: str = "none"
    """``sand``: fill the first curve below ``cutoff``; ``crossover``: fill between the first two
    curves where the second lies left of the first."""
    cutoff: float = 75.0

    def __post_init__(self) -> None:
        _text("track name", self.name)
        if not 1 <= len(self.curves) <= MAX_CURVES_PER_TRACK:
            raise ValueError(f"A track holds 1 to {MAX_CURVES_PER_TRACK} curves")
        if not all(isinstance(c, CurveSpec) for c in self.curves):
            raise ValueError("curves must be CurveSpec objects")
        if not isinstance(self.log, bool):
            raise ValueError("log must be true or false")
        if self.log and any(min(c.left, c.right) <= 0 for c in self.curves):
            raise ValueError(f"{self.name}: a logarithmic track needs positive scales")
        if self.shading not in SHADINGS:
            raise ValueError(f"Unknown shading {self.shading!r}")
        if self.shading == "crossover" and len(self.curves) < 2:
            raise ValueError(f"{self.name}: crossover shading needs two curves")
        _finite("cutoff", self.cutoff)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "curves": [c.to_dict() for c in self.curves],
            "log": self.log,
            "shading": self.shading,
            "cutoff": self.cutoff,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackSpec:
        curves = data.get("curves")
        if not isinstance(curves, list) or not all(isinstance(c, dict) for c in curves):
            raise ValueError("curves must be a list of objects")
        if len(curves) > MAX_CURVES_PER_TRACK:
            raise ValueError(f"A track holds 1 to {MAX_CURVES_PER_TRACK} curves")
        return cls(
            data.get("name"),  # type: ignore[arg-type]
            tuple(CurveSpec.from_dict(c) for c in curves),
            data.get("log", False),
            data.get("shading", "none"),
            data.get("cutoff", 75.0),
        )


@dataclass(frozen=True)
class TrackTemplate:
    name: str
    tracks: tuple[TrackSpec, ...]

    def __post_init__(self) -> None:
        _text("template name", self.name)
        if not 1 <= len(self.tracks) <= MAX_TRACKS:
            raise ValueError(f"A template holds 1 to {MAX_TRACKS} tracks")
        if not all(isinstance(t, TrackSpec) for t in self.tracks):
            raise ValueError("tracks must be TrackSpec objects")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tracks": [t.to_dict() for t in self.tracks]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackTemplate:
        tracks = data.get("tracks")
        if not isinstance(tracks, list) or not all(isinstance(t, dict) for t in tracks):
            raise ValueError("tracks must be a list of objects")
        if len(tracks) > MAX_TRACKS:
            raise ValueError(f"A template holds 1 to {MAX_TRACKS} tracks")
        return cls(data.get("name"), tuple(TrackSpec.from_dict(t) for t in tracks))  # type: ignore[arg-type]


GR = CurveSpec(CurveFamily.GR, 0.0, 150.0, "#2ca02c")
RES = CurveSpec(CurveFamily.RES_DEEP, 0.2, 2000.0, "#000000")
NPHI = CurveSpec(CurveFamily.NPHI, 0.45, -0.15, "#1f77b4", dashed=True)
RHOB = CurveSpec(CurveFamily.RHOB, 1.95, 2.95, "#d62728")
BUILTIN_TEMPLATES: tuple[TrackTemplate, ...] = (
    TrackTemplate(
        "Standard",
        (
            TrackSpec("Gamma ray", (GR,), shading="sand"),
            TrackSpec("Resistivity", (RES,), log=True),
            TrackSpec("Neutron–density", (NPHI, RHOB), shading="crossover"),
        ),
    ),
    TrackTemplate("Gamma ray only", (TrackSpec("Gamma ray", (GR,), shading="sand"),)),
    TrackTemplate(
        "Lithology and porosity",
        (
            TrackSpec("Gamma ray", (GR,), shading="sand"),
            TrackSpec("Neutron–density", (NPHI, RHOB), shading="crossover"),
        ),
    ),
)
DEFAULT_TEMPLATE = BUILTIN_TEMPLATES[0]


@dataclass(frozen=True)
class CorrelationSection:
    """What the correlation panel shows. Saved in the project file."""

    layer_id: str
    well_keys: tuple[str, ...]
    """Normalised well names, left to right."""
    template: TrackTemplate = DEFAULT_TEMPLATE
    datum: str = "TVDSS"
    flatten_on: str | None = None
    """Surface to flatten on, or ``None``."""

    def __post_init__(self) -> None:
        _text("layer_id", self.layer_id)
        if len(self.well_keys) > MAX_SECTION_WELLS:
            raise ValueError(f"A section holds at most {MAX_SECTION_WELLS} wells")
        if not all(isinstance(k, str) and k and len(k) <= MAX_NAME for k in self.well_keys):
            raise ValueError("well keys must be non-empty text")
        if len(set(self.well_keys)) != len(self.well_keys):
            raise ValueError("A well can appear only once in a section")
        if not isinstance(self.template, TrackTemplate):
            raise ValueError("template must be a TrackTemplate")
        if self.datum not in DATUMS:
            raise ValueError(f"Unknown datum {self.datum!r}")
        if self.flatten_on is not None:
            _text("flatten_on", self.flatten_on)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "wells": list(self.well_keys),
            "template": self.template.to_dict(),
            "datum": self.datum,
            "flatten_on": self.flatten_on,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrelationSection:
        wells = data.get("wells")
        template = data.get("template")
        if not isinstance(wells, list):
            raise ValueError("wells must be a list")
        if template is not None and not isinstance(template, dict):
            raise ValueError("template must be an object")
        return cls(
            data.get("layer_id"),  # type: ignore[arg-type]
            tuple(wells),
            TrackTemplate.from_dict(template) if template else DEFAULT_TEMPLATE,
            data.get("datum", "TVDSS"),
            data.get("flatten_on"),
        )


# --- depth --------------------------------------------------------------------------------------
@dataclass
class WellDatum:
    """How one well's measured depths map to the section's depth axis."""

    position: int
    key: str
    name: str
    kb: float | None
    shift: float = 0.0
    """Subtracted after converting to the datum (the flattening surface's depth)."""
    flattened: bool = False
    """True when flattening was asked for and this well has the pick."""
    warnings: list[str] = field(default_factory=list)


def well_datum(layer: WellLayer, key: str, datum: str, flatten_on: str | None) -> WellDatum | None:
    """Datum info for well ``key`` in ``layer``, or ``None`` if the well isn't there."""
    position = layer.find(key)
    if position is None or layer.gdf is None:
        return None
    info = WellDatum(position, key, str(layer.gdf["name"].iloc[position]), layer.kb_at(position))
    if datum == "TVDSS" and info.kb is None:
        info.warnings.append("no KB elevation: shown as TVD")
    if flatten_on is not None:
        pick = next((t for t in layer.tops_for(position) if t.surface == flatten_on), None)
        if pick is None:
            info.warnings.append(f"no {flatten_on} pick: not flattened")
        else:
            info.shift = float(to_datum(layer, info, np.array([pick.md]), datum)[0])
            info.flattened = True
    return info


def to_datum(
    layer: WellLayer, info: WellDatum, md: NDArray[np.float64], datum: str
) -> NDArray[np.float64]:
    """Measured depths (metres) on the section's depth axis, before flattening."""
    trajectory = layer.trajectory_for(info.position)
    depth = np.asarray(md, dtype=np.float64)
    if datum in ("TVD", "TVDSS") and trajectory is not None:
        depth = trajectory.md_to_tvd(depth)
    if datum == "TVDSS" and info.kb is not None:
        depth = depth - info.kb
    return depth


def section_depth(
    layer: WellLayer, info: WellDatum, md: NDArray[np.float64], datum: str
) -> NDArray[np.float64]:
    """Measured depths (metres) to the section's (possibly flattened) depth axis."""
    return to_datum(layer, info, md, datum) - info.shift


def md_at(layer: WellLayer, info: WellDatum, depth: float, datum: str) -> float | None:
    """The measured depth at a section depth (inverse of :func:`section_depth`), for picking."""
    value = depth + info.shift
    if datum == "MD":
        return value if value >= 0 else None
    if datum == "TVDSS" and info.kb is not None:
        value += info.kb
    trajectory = layer.trajectory_for(info.position)
    if trajectory is None:
        return value if value >= 0 else None
    md = float(trajectory.tvd_to_md([value])[0])
    return md if math.isfinite(md) else None


def normalize_curve(
    values: NDArray[np.float64], curve: CurveSpec, log: bool
) -> NDArray[np.float64]:
    """Values as positions across the track (0 = left edge, 1 = right edge).

    Values outside the scale fall outside 0–1 (the plot clips them). Non-positive values on a
    log track become NaN.
    """
    values = np.asarray(values, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        if log:
            safe = np.where(values > 0, values, np.nan)
            return (np.log10(safe) - math.log10(curve.left)) / (
                math.log10(curve.right) - math.log10(curve.left)
            )
        return (values - curve.left) / (curve.right - curve.left)
