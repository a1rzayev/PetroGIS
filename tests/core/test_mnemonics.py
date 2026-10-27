import pytest

from petrogis.core.mnemonics import ALIASES, CurveFamily, family_of, normalize_mnemonic, resolve


@pytest.mark.parametrize(
    ("mnemonic", "family"),
    [
        ("GR", CurveFamily.GR),
        ("GR_EDTC", CurveFamily.GR),
        ("sgr", CurveFamily.GR),
        ("ILD", CurveFamily.RES_DEEP),
        ("LLD", CurveFamily.RES_DEEP),
        ("RDEP", CurveFamily.RES_DEEP),
        ("TNPH", CurveFamily.NPHI),
        ("NPOR", CurveFamily.NPHI),
        ("RHOZ", CurveFamily.RHOB),
        ("DEN", CurveFamily.RHOB),
        ("GR:1", CurveFamily.GR),
        ("FOO", None),
    ],
)
def test_family_of(mnemonic, family):
    assert family_of(mnemonic) == family


def test_resolve_prefers_higher_priority_alias_and_keeps_spelling():
    resolved = resolve(["DEPT", "ild", "RT", "GR_EDTC", "TNPH", "XYZ"])
    assert resolved == {
        CurveFamily.RES_DEEP: "RT",
        CurveFamily.GR: "GR_EDTC",
        CurveFamily.NPHI: "TNPH",
    }


def test_normalize_mnemonic():
    assert normalize_mnemonic(" gr:2 ") == "GR"


def test_aliases_are_unique_across_families():
    seen = [alias for aliases in ALIASES.values() for alias in aliases]
    assert len(seen) == len(set(seen))
