"""Well identity helpers shared by header import, LAS matching and (later) tops and trajectories."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def normalize_well_name(name: object) -> str:
    """Canonical key for matching well names across sources.

    Case, punctuation and whitespace are ignored, and leading zeros in numbers are dropped:
    ``"AB-01"``, ``"AB 1"``, ``"ab_01"`` and ``"AB1"`` all give ``"AB1"``. Letter suffixes
    are kept (``"AB-4A"`` gives ``"AB4A"``), so sidetracks stay distinct.
    """
    tokens = _TOKEN.findall(str(name))
    return "".join(str(int(t)) if t.isdigit() else t.upper() for t in tokens)
