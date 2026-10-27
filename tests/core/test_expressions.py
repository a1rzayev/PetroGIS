"""Expression engine: grammar, NULL semantics, functions, limits and fuzzing.

Expression text comes from project and style files, so it is untrusted. For any text, parsing
either succeeds or raises ``ExpressionError``; for any parsed expression and frame, evaluation
either returns one value per row or raises ``ExpressionError``. Anything else is a bug.
"""

import re

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from shapely import LineString, Point, Polygon, box

from petrogis.core.expressions import (
    MAX_DEPTH,
    MAX_LENGTH,
    MAX_NODES,
    Expression,
    ExpressionError,
    format_values,
    quote_field,
)
from petrogis.core.expressions.evaluate import FUNCTIONS, prepare_column
from petrogis.core.expressions.parser import tokenize

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@pytest.fixture
def frame():
    return gpd.GeoDataFrame(
        {
            "name": ["AB-1", "AB-2", None, "x-9"],
            "kb": [28.3, 21.0, None, 5.0],
            "td": [1764, 1700, 1600, 0],
            "Status": ["OIL", "DRY", "OIL", None],
            "Well Name": ["a", "b", "c", "d"],
            "flag": [True, False, True, False],
        },
        geometry=[Point(1, 2), LineString([(0, 0), (3, 4)]), box(0, 0, 2, 2), None],
    )


def ev(text, frame):
    return Expression(text).evaluate(frame).tolist()


def na_list(values):
    return [None if v is pd.NA else v for v in values]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("2 ^ 3 ^ 2", 512.0),  # right associative
        ("-2 ^ 2", 4.0),  # unary minus binds tighter, as in QGIS
        ("7 / 2", 3.5),
        ("7 // 2", 3),
        ("-7 % 3", 2),
        ("10 - 2 - 3", 5),
        ("'a' || 'b' || 1", "ab1"),
        ("'it''s'", "it's"),
        ("'tab\\tend'", "tab\tend"),
        ("1e3 + .5", 1000.5),
        ("TRUE AND NOT FALSE", True),
        ("1 < 2 AND 2 <= 2 AND 3 > 2 AND 3 >= 3 AND 1 <> 2 AND 1 != 2 AND 1 = 1", True),
        ("NULL IS NULL", True),
        ("1 IS NOT NULL", True),
        ("2 IN (1, 2, 3)", True),
        ("5 NOT IN (1, 2)", True),
        ("'abc' LIKE 'a%'", True),
        ("'abc' NOT LIKE 'a_c'", False),
        ("'ABC' ILIKE 'a%c'", True),
        ("'a%b' LIKE 'a\\%b'", True),
        ("'axb' LIKE 'a\\%b'", False),
        ("CASE WHEN 1 > 2 THEN 'x' WHEN 2 > 1 THEN 'y' ELSE 'z' END", "y"),
        ("CASE WHEN FALSE THEN 1 END", None),
        ("'2' + 3", 5),  # numeric text converts, as in QGIS
        ("'a' + 'b'", "ab"),
        ("9223372036854775807 + 1", 9223372036854775808.0),  # no silent int64 wrap
        ("99999999999999999999", 1e20),
    ],
)
def test_grammar_and_operators(text, expected, frame):
    result = na_list(ev(text, frame))
    assert result == [expected] * len(frame)


def test_null_semantics(frame):
    assert na_list(ev("NULL + 1", frame)) == [None] * 4
    assert na_list(ev("NULL AND FALSE", frame)) == [False] * 4  # Kleene logic
    assert na_list(ev("NULL OR TRUE", frame)) == [True] * 4
    assert na_list(ev("NULL AND TRUE", frame)) == [None] * 4
    assert na_list(ev("NULL = NULL", frame)) == [None] * 4
    assert na_list(ev("1 / 0", frame)) == [None] * 4
    assert na_list(ev("5 // 0", frame)) == [None] * 4
    assert na_list(ev("5 % 0", frame)) == [None] * 4
    assert na_list(ev("sqrt(-1)", frame)) == [None] * 4
    assert na_list(ev("ln(0)", frame)) == [None] * 4
    assert na_list(ev("10 ^ 400", frame)) == [None] * 4
    assert na_list(ev("kb * 2", frame)) == [56.6, 42.0, None, 10.0]
    assert na_list(ev("Status IN ('OIL', 'GAS')", frame)) == [True, False, True, None]


def test_fields(frame):
    assert ev('"Well Name"', frame) == ["a", "b", "c", "d"]
    assert ev("status = 'OIL'", frame)[:2] == [True, False]  # case-insensitive fallback
    assert ev("flag AND td > 1650", frame) == [True, False, False, False]
    expr = Expression('"td" + kb + length(name)')
    assert expr.fields == {"td", "kb", "name"}
    assert expr.field_name is None
    assert Expression('"Status"').field_name == "Status"
    with pytest.raises(ExpressionError, match="not found"):
        Expression('"nope"').evaluate(frame)
    with pytest.raises(ExpressionError, match="not found"):
        Expression("nope + 1").check_fields(frame)
    with pytest.raises(ExpressionError, match="geometr"):
        Expression("geometry").evaluate(frame)
    assert quote_field('a "b"') == '"a ""b"""'
    assert ev(quote_field("Well Name"), frame)[0] == "a"


def test_geometry_variables(frame):
    assert na_list(ev("$area", frame)) == [0.0, 0.0, 4.0, None]
    assert na_list(ev("$length", frame)) == [0.0, 5.0, 8.0, None]
    assert na_list(ev("$perimeter", frame)) == [None, None, 8.0, None]
    assert na_list(ev("$x", frame)) == [1.0, None, None, None]
    assert na_list(ev("$y", frame)) == [2.0, None, None, None]
    assert ev("$id", frame) == [0, 1, 2, 3]
    plain = pd.DataFrame({"a": [1]})
    with pytest.raises(ExpressionError, match="geometry"):
        Expression("$area").evaluate(plain)
    assert Expression("$id + a").evaluate(plain).tolist() == [1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abs(-3)", 3),
        ("round(2.5)", 3.0),
        ("round(-2.5)", -3.0),
        ("round(3.14159, 2)", 3.14),
        ("round(1234, -2)", 1200.0),
        ("floor(2.7) + ceil(2.1)", 5.0),
        ("sqrt(16) + exp(0) + log10(100) + ln(1)", 7.0),
        ("degrees(pi())", 180.0),
        ("round(radians(180), 5)", 3.14159),
        ("round(sin(0) + cos(0) + tan(0) + asin(0) + acos(1) + atan(0) + atan2(0, 1), 6)", 1.0),
        ("min(3, 1, 2) + max(3, 1, 2)", 4.0),
        ("min(NULL, 4)", 4.0),
        ("clamp(0, 15, 10)", 10.0),
        ("to_int('3.7')", 3),
        ("to_int(-3.7)", -3),
        ("to_real('2,5' = '2,5')", 1.0),
        ("to_string(2.0)", "2"),
        ("to_string(2.5)", "2.5"),
        ("to_string(TRUE)", "true"),
        ("upper('ab') || lower('CD') || title('ef gh')", "ABcdEf Gh"),
        ("trim('  x  ')", "x"),
        ("length('héllo')", 5),
        ("substr('HelloWorld', 2, 3)", "ell"),
        ("substr('HelloWorld', -5)", "World"),
        ("substr('HelloWorld', 1, -5)", "Hello"),
        ("left('abcdef', 2) || right('abcdef', 2)", "abef"),
        ("replace('a-b-c', '-', '_')", "a_b_c"),
        ("replace('abc', '', 'x')", "abc"),
        ("strpos('abcdef', 'cd')", 3),
        ("strpos('abc', 'z')", 0),
        ("lpad('7', 3, '0') || rpad('x', 3, 'ab')", "007xab"),
        ("lpad('abcdef', 3, '0')", "abc"),
        ("concat('a', NULL, 1)", "a1"),
        ("format_number(1234567.891, 2)", "1,234,567.89"),
        ("if(1 > 2, 'yes', 'no')", "no"),
        ("if(TRUE, 1, 2.5)", 1.0),
        ("coalesce(NULL, NULL, 'x')", "x"),
        ("nullif(3, 3)", None),
        ("nullif(3, 4)", 3),
    ],
)
def test_functions(text, expected, frame):
    assert na_list(ev(text, frame)) == [expected] * len(frame)


def test_string_functions_follow_rows(frame):
    assert na_list(ev("substr(name, 1, 2)", frame)) == ["AB", "AB", None, "x-"]
    assert na_list(ev("left(name, td // 1000)", frame)) == ["A", "A", None, ""]
    assert na_list(ev("replace(name, Status, 'q')", frame)) == ["AB-1", "AB-2", None, None]
    assert na_list(ev("name LIKE Status", frame)) == [False, False, None, None]
    assert na_list(ev("coalesce(name, Status, 'none')", frame)) == ["AB-1", "AB-2", "OIL", "x-9"]
    assert na_list(ev("CASE WHEN kb > 25 THEN td ELSE kb END", frame)) == [1764.0, 21.0, None, 5.0]


def test_type_errors_are_expression_errors(frame):
    for text in ("'a' * 2", "Status + 1", "NOT 'x'", "round(kb, td)", "lpad('a', 5000, 'x')",
                 "format_number(1, 99)", "if('x', 1, 2)"):  # fmt: skip
        with pytest.raises(ExpressionError):
            Expression(text).evaluate(frame)
    with pytest.raises(ExpressionError, match="condition"):
        Expression("name").evaluate_mask(frame)


def test_mask_treats_null_as_false(frame):
    np.testing.assert_array_equal(
        Expression("Status = 'OIL'").evaluate_mask(frame), [True, False, True, False]
    )
    np.testing.assert_array_equal(Expression("kb").evaluate_mask(frame), [True, True, False, True])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("1 +", "expected a value"),
        ("(1", "expected ')'"),
        ("1)", "unexpected text"),
        ("foo(1)", "Unknown function"),
        ("abs()", "takes 1"),
        ("round(1, 2, 3)", "1 to 2"),
        ("concat()", "at least 1"),
        ("'open", "Unclosed quote"),
        ('"open', "Unclosed quote"),
        ("$nope", "Unknown variable"),
        ("@layer", "Unexpected character"),
        ("a NOT b", "unexpected text"),
        ("CASE END", "expected WHEN"),
        ("CASE WHEN 1 THEN 2", "expected END"),
        ("CASE WHEN 1 2 END", "expected THEN"),
        ("1 IN ()", "expected a value"),
        ("x IN 1", "expected '('"),
    ],
)
def test_syntax_errors(text, message):
    with pytest.raises(ExpressionError, match=re.escape(message)):
        Expression(text)


def test_limits():
    with pytest.raises(ExpressionError, match="too long"):
        Expression("1" * (MAX_LENGTH + 1))
    with pytest.raises(ExpressionError, match="nested too deeply"):
        Expression("(" * (MAX_DEPTH + 5) + "1" + ")" * (MAX_DEPTH + 5))
    with pytest.raises(ExpressionError, match="nested too deeply"):
        Expression("-" * (MAX_DEPTH + 5) + "1")
    with pytest.raises(ExpressionError, match="nested too deeply"):
        Expression(" + ".join(["1"] * (MAX_DEPTH + 5)))  # long left-deep chains count too
    with pytest.raises(ExpressionError, match="too complex"):
        Expression("x IN (" + ", ".join(["1"] * MAX_NODES) + ")")
    with pytest.raises(ExpressionError, match="must be text"):
        Expression(123)  # type: ignore[arg-type]
    Expression("x IN (" + ", ".join(["1"] * 1000) + ")")  # wide but shallow is fine


def test_like_is_linear_on_hostile_patterns():
    import time

    frame = pd.DataFrame({"t": ["a" * 5000]})
    start = time.perf_counter()
    assert ev("t LIKE '" + "%a" * 30 + "b'", frame) == [False]
    assert time.perf_counter() - start < 2.0


def test_prepare_column_dtypes():
    assert str(prepare_column(pd.Series([1, 2])).dtype) == "Int64"
    assert str(prepare_column(pd.Series([1.5, np.nan])).dtype) == "Float64"
    assert str(prepare_column(pd.Series([True, False])).dtype) == "boolean"
    assert str(prepare_column(pd.Series([1, None], dtype=object)).dtype) == "Int64"
    assert str(prepare_column(pd.Series(["a", 1], dtype=object)).dtype) == "string"
    assert str(prepare_column(pd.Series(pd.to_datetime(["2024-01-02"]))).dtype) == "string"
    assert str(prepare_column(pd.Series([2**64 - 1], dtype="uint64")).dtype) == "Float64"


def test_format_values():
    assert format_values(pd.Series([1.0, 2.5, np.nan, 4475000.25])) == [
        "1",
        "2.5",
        None,
        "4475000.25",
    ]
    assert format_values(pd.Series([1, None], dtype="Int64")) == ["1", None]
    assert format_values(pd.Series([True, None], dtype="boolean")) == ["true", None]
    assert format_values(pd.Series(["a", None], dtype=object)) == ["a", None]
    assert format_values(pd.Series([[1, 2]], dtype=object)) == ["[1, 2]"]


def test_empty_frame_and_tokens():
    empty = gpd.GeoDataFrame({"a": pd.Series([], dtype=float)}, geometry=[])
    assert Expression("a * 2 + $area").evaluate(empty).tolist() == []
    assert Expression("pi()").evaluate(empty).tolist() == []
    kinds = [t.kind for t in tokenize("$x + \"f\" + g('s', 1.5) AND NULL")]
    assert kinds == ["var", "op", "field", "op", "ident", "op", "str", "op", "num", "op",
                     "kw", "kw", "end"]  # fmt: skip


def test_hundred_thousand_rows_is_fast():
    import time

    n = 100_000
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"v": rng.uniform(0, 100, n), "s": rng.choice(["OIL", "DRY", "GAS"], n)})
    start = time.perf_counter()
    result = Expression(
        "CASE WHEN s = 'OIL' AND v > 50 THEN round(v * 2, 1) WHEN s LIKE 'G%' THEN v ELSE NULL END"
    ).evaluate(frame)
    assert len(result) == n
    assert time.perf_counter() - start < 1.5


# --- fuzzing -------------------------------------------------------------------------------
ALPHABET = st.sampled_from(
    [*"abcxyz019 .,()+-*/%^=<>!|'\"$_\\\n", "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE",
    "CASE", "WHEN", "THEN", "ELSE", "END", "abs", "round", "kb", "Status", "td"]
)  # fmt: skip


@SETTINGS
@given(st.lists(ALPHABET, max_size=40).map("".join) | st.text(max_size=60))
def test_fuzz_text_only_raises_expression_error(text):
    frame = gpd.GeoDataFrame(
        {"kb": [1.5, None], "Status": ["OIL", None], "td": [10, 0]},
        geometry=[Point(0, 0), Polygon([(0, 0), (1, 0), (1, 1)])],
    )
    try:
        expression = Expression(text)
    except ExpressionError:
        return
    try:
        result = expression.evaluate(frame)
    except ExpressionError:
        return
    assert len(result) == len(frame)


_atoms = st.sampled_from(
    ["kb", "td", '"Status"', "NULL", "TRUE", "FALSE", "0", "1", "-2.5", "'OIL'", "''", "$area",
     "$x", "$id", "pi()"]
)  # fmt: skip


def _expressions(children):
    binary = st.tuples(
        children,
        st.sampled_from(
            [
                "+",
                "-",
                "*",
                "/",
                "//",
                "%",
                "^",
                "||",
                "=",
                "<>",
                "<",
                ">=",
                "AND",
                "OR",
                "LIKE",
                "ILIKE",
                "IS",
                "IS NOT",
            ]
        ),
        children,
    ).map(lambda t: f"({t[0]} {t[1]} {t[2]})")
    unary = st.tuples(st.sampled_from(["-", "NOT "]), children).map(lambda t: f"{t[0]}({t[1]})")
    names = sorted(FUNCTIONS)
    call = st.tuples(st.sampled_from(names), st.lists(children, min_size=0, max_size=3)).map(
        lambda t: f"{t[0]}({', '.join(t[1])})"
    )
    in_list = st.tuples(children, st.lists(children, min_size=1, max_size=3)).map(
        lambda t: f"({t[0]} IN ({', '.join(t[1])}))"
    )
    case = st.tuples(children, children, children).map(
        lambda t: f"CASE WHEN {t[0]} THEN {t[1]} ELSE {t[2]} END"
    )
    return binary | unary | call | in_list | case


@SETTINGS
@given(st.recursive(_atoms, _expressions, max_leaves=12))
def test_fuzz_grammar_evaluation_only_raises_expression_error(text):
    frame = gpd.GeoDataFrame(
        {"kb": [1.5, None, 0.0], "Status": ["OIL", None, "DRY"], "td": [10, 0, -3]},
        geometry=[Point(0, 0), None, Polygon([(0, 0), (1, 0), (1, 1)])],
    )
    try:
        result = Expression(text).evaluate(frame)
    except ExpressionError:
        return
    assert len(result) == len(frame)
