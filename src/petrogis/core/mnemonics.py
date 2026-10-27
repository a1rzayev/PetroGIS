"""Resolve vendor-specific LAS curve mnemonics into curve families through an alias table.

To support a new tool or vendor, add its mnemonic to the family's tuple. Aliases are listed in
priority order, so when a file has several curves in one family (for example both RT and ILD),
the earlier alias wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class CurveFamily(StrEnum):
    GR = "GR"
    RES_DEEP = "RES_DEEP"
    RES_MEDIUM = "RES_MEDIUM"
    RES_SHALLOW = "RES_SHALLOW"
    NPHI = "NPHI"
    RHOB = "RHOB"
    DT = "DT"
    CALI = "CALI"
    SP = "SP"
    PEF = "PEF"


ALIASES: dict[CurveFamily, tuple[str, ...]] = {
    CurveFamily.GR: ("GR", "GR_EDTC", "SGR", "GRC", "GRD", "ECGR", "HGR", "HSGR", "CGR", "GRGC",
                     "GR_CAL", "GRS", "GAM", "GAMMA"),
    CurveFamily.RES_DEEP: ("RT", "ILD", "LLD", "RDEP", "RD", "AT90", "AHT90", "AF90", "M2R9",
                           "RILD", "LLDC", "RLA5", "HDRS", "P40H", "RES_DEEP", "DEEP"),
    CurveFamily.RES_MEDIUM: ("ILM", "RM", "RMED", "AT30", "AHT30", "M2R3", "RILM", "HMRS"),
    CurveFamily.RES_SHALLOW: ("MSFL", "LLS", "SFLU", "SFL", "RS", "RXO", "AT10", "AHT10",
                              "M2R1", "RXOZ", "SHALLOW"),
    CurveFamily.NPHI: ("NPHI", "TNPH", "NPOR", "NPHS", "NPHIL", "CNPOR", "CNC", "CN", "NPL",
                       "PHIN", "NPHI_LS", "TNPH_LS", "NEU"),
    CurveFamily.RHOB: ("RHOB", "RHOZ", "DEN", "ZDEN", "DENS", "RHOM", "HDEN", "DENC", "RHO"),
    CurveFamily.DT: ("DT", "DTC", "DTCO", "AC", "DT24", "DTLN", "SONIC"),
    CurveFamily.CALI: ("CALI", "CAL", "HCAL", "C1", "CALS", "CAL1", "LCAL"),
    CurveFamily.SP: ("SP", "SPBR", "SSP"),
    CurveFamily.PEF: ("PEF", "PEFZ", "PE", "PEDF"),
}  # fmt: skip

_DUPLICATE_SUFFIX = re.compile(r":\d+$")


def normalize_mnemonic(mnemonic: str) -> str:
    """Uppercase and strip lasio's duplicate suffix (``GR:1`` becomes ``GR``)."""
    return _DUPLICATE_SUFFIX.sub("", mnemonic.strip().upper())


_LOOKUP: dict[str, tuple[CurveFamily, int]] = {
    alias: (family, rank)
    for family, aliases in ALIASES.items()
    for rank, alias in enumerate(aliases)
}


def family_of(mnemonic: str) -> CurveFamily | None:
    entry = _LOOKUP.get(normalize_mnemonic(mnemonic))
    return None if entry is None else entry[0]


def resolve(mnemonics: Iterable[str]) -> dict[CurveFamily, str]:
    """Map each family to the best-ranked mnemonic present. Returns the original spellings."""
    best: dict[CurveFamily, tuple[int, str]] = {}
    for mnemonic in mnemonics:
        entry = _LOOKUP.get(normalize_mnemonic(mnemonic))
        if entry is None:
            continue
        family, rank = entry
        if family not in best or rank < best[family][0]:
            best[family] = (rank, mnemonic)
    return {family: mnemonic for family, (_rank, mnemonic) in best.items()}
