"""Well identity helpers shared by header import, LAS matching and (later) tops and trajectories."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def normalize_well_name(name: object) -> str:
    """Canonical key for matching well names across sources.

    Case, punctuation and whitespace are ignored, and leading zeros in numbers are dropped:
    ``"AB-01"``, ``"AB 1"``, ``"ab_01"`` and ``"AB1"`` all give ``"AB1"``. Letter suffixes
    are kept (``"AB-4A"`` gives ``"AB4A"``), so sidetracks stay distinct.
    """
    tokens = _TOKEN.findall(str(name))
    return "".join(str(int(t)) if t.isdigit() else t.upper() for t in tokens)


class WellStatus(StrEnum):
    OIL = "oil"
    GAS = "gas"
    OIL_GAS = "oil_gas"
    WATER = "water"
    WATER_INJECTOR = "water_injector"
    GAS_INJECTOR = "gas_injector"
    DRY = "dry"
    ABANDONED = "abandoned"
    SUSPENDED = "suspended"
    PLANNED = "planned"
    OBSERVATION = "observation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WellSymbol:
    label: str
    color: str
    shape: str
    """Renderer-neutral shape name: ``circle``, ``sun``, ``half``, ``injector``, ``open``,
    ``crossed``, ``barred`` or ``diamond``."""


WELL_SYMBOLS: dict[WellStatus, WellSymbol] = {
    WellStatus.OIL: WellSymbol("Oil", "#1a9641", "circle"),
    WellStatus.GAS: WellSymbol("Gas", "#d7191c", "sun"),
    WellStatus.OIL_GAS: WellSymbol("Oil and gas", "#1a9641", "half"),
    WellStatus.WATER: WellSymbol("Water", "#2b83ba", "circle"),
    WellStatus.WATER_INJECTOR: WellSymbol("Water injector", "#2b83ba", "injector"),
    WellStatus.GAS_INJECTOR: WellSymbol("Gas injector", "#d7191c", "injector"),
    WellStatus.DRY: WellSymbol("Dry", "#7f7f7f", "crossed"),
    WellStatus.ABANDONED: WellSymbol("Plugged and abandoned", "#000000", "crossed"),
    WellStatus.SUSPENDED: WellSymbol("Suspended / shut in", "#e66101", "barred"),
    WellStatus.PLANNED: WellSymbol("Planned", "#000000", "open"),
    WellStatus.OBSERVATION: WellSymbol("Observation", "#7b3294", "diamond"),
}
"""Conventional map symbols per status. ``UNKNOWN`` uses the layer's own colour and a circle."""

_STATUS_ALIASES: dict[str, WellStatus] = {}
for _status, _words in {
    WellStatus.OIL: "oil oilproducer oilwell producer prod production neft neftquyusu нефть "
    "нефтяная нефтедобывающая добывающая",
    WellStatus.GAS: "gas gasproducer gaswell condensate gascondensate gaz qaz qazquyusu газ "
    "газовая газоконденсатная",
    WellStatus.OIL_GAS: "oilgas oilandgas oilgasproducer нефтегазовая",
    WellStatus.WATER: "water watersource waterproducer waterwell su suquyusu водозаборная вода",
    WellStatus.WATER_INJECTOR: "injector inj injection wi waterinjector waterinjection "
    "wateri нагнетательная нагнетательнаяводы",
    WellStatus.GAS_INJECTOR: "gasinjector gasinjection gi нагнетательнаягаза",
    WellStatus.DRY: "dry dryhole da dryandabandoned quru сухая",
    WellStatus.ABANDONED: "abandoned pa plugged pluggedandabandoned plugandabandon ləğv "
    "ləğvedilmiş ликвидирована ликвидированная ликвидация",
    WellStatus.SUSPENDED: "suspended shutin shut si ta temporarilyabandoned idle inactive "
    "консервация законсервирована бездействующая",
    WellStatus.PLANNED: "planned proposed location loc prospect proposal layihə проект "
    "проектная планируемая",
    WellStatus.OBSERVATION: "observation obs monitor monitoring müşahidə наблюдательная "
    "пьезометрическая контрольная",
}.items():
    for _word in _words.split():
        _STATUS_ALIASES[_word] = _status

_STATUS_COLUMNS = (
    "status", "wellstatus", "currentstatus", "welltype", "type", "category", "purpose",
    "статус", "состояние", "категория", "назначение", "vəziyyət", "status1",
)  # fmt: skip
_LETTERS = re.compile(r"[\W\d_]+")


def _status_key(value: object) -> str:
    return _LETTERS.sub("", str(value)).casefold()


def classify_status(value: object) -> WellStatus:
    """Map a free-text status ("OIL", "Water injector", "P&A", "нефтяная") to a ``WellStatus``.

    Tries an exact alias first, then the longest alias the text starts with, so that
    "Oil producer (ESP)" is oil and "Gas injector" is a gas injector, not gas.
    """
    if value is None or (isinstance(value, float) and value != value):
        return WellStatus.UNKNOWN
    key = _status_key(value)
    if not key:
        return WellStatus.UNKNOWN
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    best = max(
        (a for a in _STATUS_ALIASES if len(a) >= 3 and key.startswith(a)), key=len, default=""
    )
    return _STATUS_ALIASES[best] if best else WellStatus.UNKNOWN


def classify_statuses(values: Iterable[object]) -> list[WellStatus]:
    """``classify_status`` for many values, computed once per distinct value."""
    cache: dict[object, WellStatus] = {}
    out = []
    for value in values:
        try:
            status = cache[value]
        except KeyError:
            status = cache[value] = classify_status(value)
        except TypeError:  # unhashable
            status = classify_status(value)
        out.append(status)
    return out


def detect_status_column(columns: Iterable[object]) -> str | None:
    """The column that most likely holds the well status, by name."""
    by_key = {}
    for column in columns:
        by_key.setdefault(_status_key(column), column)
    for candidate in _STATUS_COLUMNS:
        if candidate in by_key:
            return str(by_key[candidate])
    return None
