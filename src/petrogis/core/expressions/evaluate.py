"""Vectorised evaluation of parsed expressions over a (Geo)DataFrame.

Every node evaluates to a pandas Series with one value per row, using pandas' nullable dtypes:
``Int64``/``Float64`` for numbers, ``string`` for text and ``boolean`` for conditions. NULL is
``pd.NA`` and propagates the way SQL and QGIS do: ``NULL + 1`` is NULL, ``NULL AND FALSE`` is
FALSE (Kleene logic). Division by zero and invalid maths give NULL instead of failing.

The work per node is a handful of whole-column operations, so cost grows with rows times nodes and
both are bounded. Only the functions in ``FUNCTIONS`` can be called.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Sequence
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pandas.api import types as ptypes

from petrogis.core.expressions.parser import (
    Binary,
    Call,
    Case,
    ExpressionError,
    Field,
    InList,
    Literal,
    Node,
    Unary,
    Variable,
    parse,
    walk,
)

Values = pd.Series
MAX_PAD = 1000
"""Longest string ``lpad``/``rpad`` may produce, so an expression cannot exhaust memory."""


# --- value kinds and conversions ---------------------------------------------------------------
def _kind(s: Values) -> str:
    dtype = s.dtype
    if ptypes.is_bool_dtype(dtype):
        return "bool"
    if ptypes.is_numeric_dtype(dtype):
        return "num"
    if ptypes.is_string_dtype(dtype) and not ptypes.is_object_dtype(dtype):
        return "str"
    if s.isna().all():
        return "null"
    return "str"


def _null(n: int, dtype: str = "object") -> Values:
    return pd.Series(pd.NA, index=pd.RangeIndex(n), dtype=dtype)


def _const(value: Any, n: int) -> Values:
    if value is None:
        return _null(n)
    if isinstance(value, bool):
        return pd.Series(value, index=pd.RangeIndex(n), dtype="boolean")
    if isinstance(value, int):
        return pd.Series(value, index=pd.RangeIndex(n), dtype="Int64")
    if isinstance(value, float):
        return _finite(pd.Series(value, index=pd.RangeIndex(n), dtype="Float64"))
    return pd.Series(value, index=pd.RangeIndex(n), dtype="string")


def _finite(s: Values) -> Values:
    """Turn inf and NaN produced by maths into NULL."""
    if s.dtype != "Float64":
        return s
    values = s.to_numpy(dtype=float, na_value=np.nan)
    bad = ~np.isfinite(values)
    return s.mask(bad) if bad.any() else s


def _from_float(values: NDArray[np.float64]) -> Values:
    with np.errstate(invalid="ignore"):
        values = np.where(np.isfinite(values), values, np.nan)
    return pd.Series(values, dtype="Float64")


def _to_float(s: Values) -> NDArray[np.float64]:
    return as_num(s).to_numpy(dtype=float, na_value=np.nan)


def prepare_column(series: pd.Series) -> Values:
    """A source column converted to the nullable dtypes the evaluator works with."""
    s = series.reset_index(drop=True)
    dtype = s.dtype
    if getattr(dtype, "name", "") == "geometry":
        raise ExpressionError(
            f"Field '{series.name}' holds geometries; use $area, $length, $x or $y instead"
        )
    if ptypes.is_bool_dtype(dtype):
        return s.astype("boolean")
    if ptypes.is_integer_dtype(dtype):
        try:
            return s.astype("Int64")
        except (TypeError, ValueError, OverflowError):
            return s.astype("Float64")
    if ptypes.is_numeric_dtype(dtype):
        return _finite(s.astype("Float64"))
    if ptypes.is_datetime64_any_dtype(dtype):
        return s.astype("string")
    if ptypes.is_object_dtype(dtype):
        converted = s.convert_dtypes()
        if _kind(converted) in ("bool", "num") and not ptypes.is_object_dtype(converted.dtype):
            return converted
    return s.astype("string")


def as_num(s: Values, strict: bool = True) -> Values:
    kind = _kind(s)
    if kind == "num":
        return s
    if kind == "bool":
        return s.astype("Int64")
    if kind == "null":
        return _null(len(s), "Float64")
    text = s.astype("string")
    numbers = pd.to_numeric(text.str.strip(), errors="coerce")
    numbers = _finite(pd.Series(numbers, index=s.index).astype("Float64"))
    if strict:
        failed = numbers.isna() & text.notna() & (text.str.strip() != "")
        if failed.any():
            example = text[failed].iloc[0]
            raise ExpressionError(f"Cannot convert {example!r} to a number")
    return numbers


def as_str(s: Values) -> Values:
    kind = _kind(s)
    if kind == "str":
        return s.astype("string")
    if kind == "null":
        return _null(len(s), "string")
    return pd.Series(format_values(s), index=s.index, dtype="string")


def as_bool(s: Values) -> Values:
    kind = _kind(s)
    if kind == "bool":
        return s.astype("boolean")
    if kind == "null":
        return _null(len(s), "boolean")
    if kind == "num":
        return (s != 0).astype("boolean")
    raise ExpressionError("Text cannot be used as a condition; compare it with a value instead")


def _unify(values: Sequence[Values]) -> list[Values]:
    """Convert values to one common kind, for CASE branches, if() and coalesce()."""
    kinds = {_kind(v) for v in values} - {"null"}
    if not kinds:
        return list(values)
    if kinds == {"bool"}:
        return [as_bool(v) for v in values]
    if kinds <= {"num", "bool"}:
        nums = [as_num(v) for v in values]
        all_int = all(ptypes.is_integer_dtype(v.dtype) for v in nums if _kind(v) != "null")
        return [v.astype("Int64" if all_int else "Float64") for v in nums]
    return [as_str(v) for v in values]


def format_values(s: pd.Series) -> list[str | None]:
    """Display text for each value: integral numbers without ``.0``, NULL as ``None``."""
    if ptypes.is_bool_dtype(s.dtype):
        return [None if pd.isna(v) else ("true" if v else "false") for v in s]
    if ptypes.is_integer_dtype(s.dtype):
        return [None if pd.isna(v) else str(int(v)) for v in s]
    if ptypes.is_float_dtype(s.dtype):
        return [None if pd.isna(v) else _format_float(float(v)) for v in s]
    return [None if _is_missing(v) else str(v) for v in s]


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):  # list-like values
        return False


# --- operators ---------------------------------------------------------------------------------
_COMPARE: dict[str, Callable[[Any, Any], Any]] = {
    "=": operator.eq,
    "<>": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def _compare(op: str, a: Values, b: Values) -> Values:
    ka, kb = _kind(a), _kind(b)
    if "null" in (ka, kb):
        return _null(len(a), "boolean")
    if ka == kb == "str":
        x, y = a, b
    elif ka != "str" and kb != "str":
        x, y = as_num(a), as_num(b)
    else:  # text against a number: compare as numbers where the text is numeric
        x, y = as_num(a, strict=False), as_num(b, strict=False)
    return _COMPARE[op](x, y).astype("boolean")


def _arithmetic(op: str, a: Values, b: Values) -> Values:
    if op == "+" and _kind(a) == "str" and _kind(b) == "str":
        return a + b
    x, y = as_num(a), as_num(b)
    zero = (y == 0).fillna(False).to_numpy(dtype=bool)
    with np.errstate(all="ignore"):
        if op in ("+", "-", "*"):
            fn = {"+": operator.add, "-": operator.sub, "*": operator.mul}[op]
            result = fn(x, y)
            if ptypes.is_integer_dtype(result.dtype):
                # Int64 maths wraps on overflow, so fall back to floats when it would.
                as_float = fn(x.astype("Float64"), y.astype("Float64"))
                big = (as_float.abs() >= 2.0**63).fillna(False)
                if big.any():
                    result = as_float
        elif op == "/":
            result = x.astype("Float64") / y.astype("Float64")
        elif op == "//":
            result = x // y.mask(zero)
        elif op == "%":
            result = x % y.mask(zero)
        else:  # "^"
            result = x.astype("Float64") ** y.astype("Float64")
    if zero.any() and op in ("/", "//", "%"):
        result = result.mask(zero)
    return _finite(result)


_WILDCARD_ANY = object()
_WILDCARD_ONE = object()


def _like_tokens(pattern: str, fold: bool) -> list[object]:
    tokens: list[object] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            tokens.append(pattern[i + 1].casefold() if fold else pattern[i + 1])
            i += 2
            continue
        if ch == "%":
            if not tokens or tokens[-1] is not _WILDCARD_ANY:  # %% is the same as %
                tokens.append(_WILDCARD_ANY)
        elif ch == "_":
            tokens.append(_WILDCARD_ONE)
        else:
            tokens.append(ch.casefold() if fold else ch)
        i += 1
    return tokens


def _like_match(text: str, tokens: list[object]) -> bool:
    """SQL LIKE matching without regular expressions, so the cost is at most O(len * pattern).

    Greedy matching with backtracking to the last ``%`` only, which is linear for ordinary
    patterns and never exponential (unlike a regex translation, which can backtrack badly).
    """
    i = j = 0
    star = -1
    mark = 0
    while i < len(text):
        if j < len(tokens) and (tokens[j] is _WILDCARD_ONE or tokens[j] == text[i]):
            i += 1
            j += 1
        elif j < len(tokens) and tokens[j] is _WILDCARD_ANY:
            star = j
            mark = i
            j += 1
        elif star >= 0:
            j = star + 1
            mark += 1
            i = mark
        else:
            return False
    while j < len(tokens) and tokens[j] is _WILDCARD_ANY:
        j += 1
    return j == len(tokens)


def _like(a: Values, b: Values, fold: bool) -> Values:
    text, pattern = as_str(a), as_str(b)
    if fold:
        text = text.str.casefold()
    patterns = pattern.dropna().unique()
    if len(patterns) == 1 and not pattern.isna().any():
        tokens = _like_tokens(str(patterns[0]), fold)
        uniques = text.dropna().unique()
        lookup = {u: _like_match(str(u), tokens) for u in uniques}
        return text.map(lookup).astype("boolean")
    out = [
        pd.NA if _is_missing(t) or _is_missing(p) else _like_match(str(t), _like_tokens(p, fold))
        for t, p in zip(text, pattern, strict=True)
    ]
    return pd.Series(out, dtype="boolean")


def _is(a: Values, b: Values, negate: bool) -> Values:
    na_a = a.isna().to_numpy(dtype=bool)
    na_b = b.isna().to_numpy(dtype=bool)
    equal = _compare("=", a, b).fillna(False).to_numpy(dtype=bool)
    result = (na_a & na_b) | (~na_a & ~na_b & equal)
    return pd.Series(~result if negate else result, dtype="boolean")


# --- functions ---------------------------------------------------------------------------------
def _scalar_int(s: Values, what: str, low: int, high: int) -> int:
    values = as_num(s).dropna().unique()
    if len(values) != 1 or s.isna().any():
        raise ExpressionError(f"{what} must be a single number, not a field")
    value = float(values[0])
    if not low <= value <= high:
        raise ExpressionError(f"{what} must be between {low} and {high}")
    return int(value)


def _math(fn: Callable[..., Any]) -> Callable[[list[Values]], Values]:
    def apply(args: list[Values]) -> Values:
        with np.errstate(all="ignore"):
            return _from_float(fn(*[_to_float(a) for a in args]))

    return apply


def _round(args: list[Values]) -> Values:
    places = _scalar_int(args[1], "round() places", -15, 15) if len(args) > 1 else 0
    values = _to_float(args[0])
    scale = 10.0**places
    with np.errstate(all="ignore"):
        # Half away from zero, like QGIS (numpy's round is half to even).
        rounded = np.sign(values) * np.floor(np.abs(values) * scale + 0.5) / scale
    return _from_float(rounded)


def _to_int(args: list[Values]) -> Values:
    values = np.trunc(_to_float(args[0]))
    ok = np.isfinite(values) & (np.abs(values) < 2.0**63)
    out = pd.Series(np.where(ok, values, 0), dtype="float64").astype("Int64")
    return out.mask(~ok)


def _min_max(args: list[Values], take_max: bool) -> Values:
    frame = pd.concat([as_num(a).astype("Float64") for a in args], axis=1)
    return frame.max(axis=1) if take_max else frame.min(axis=1)


def _clamp(args: list[Values]) -> Values:
    low, value, high = (_to_float(a) for a in args)
    with np.errstate(invalid="ignore"):
        return _from_float(np.minimum(np.maximum(value, low), high))


def _per_row(fn: Callable[..., Any], args: list[Values], result_dtype: str) -> Values:
    """Apply ``fn`` row by row (only for string helpers whose arguments vary per row)."""
    columns = [list(a.astype(object)) for a in args]
    out = [
        pd.NA if any(_is_missing(v) for v in row) else fn(*row)
        for row in zip(*columns, strict=True)
    ]
    return pd.Series(out, dtype=result_dtype)


def _substr(text: str, start: int, length: int | None = None) -> str:
    # QGIS: 1-based start, negative start counts from the end, negative length drops from the end.
    begin = start - 1 if start > 0 else max(len(text) + start, 0)
    if length is None:
        return text[begin:]
    if length < 0:
        return text[begin:length] if length else ""
    return text[begin : begin + length]


def _fn_substr(args: list[Values]) -> Values:
    text = as_str(args[0])
    start = as_num(args[1])
    length = as_num(args[2]) if len(args) > 2 else None
    cols = [text, start] if length is None else [text, start, length]
    return _per_row(
        lambda t, s, n=None: _substr(t, int(s), None if n is None else int(n)), cols, "string"
    )


def _text_num(args: list[Values], fn: Callable[[str, int], str]) -> Values:
    return _per_row(lambda t, n: fn(t, int(n)), [as_str(args[0]), as_num(args[1])], "string")


def _strpos(args: list[Values]) -> Values:
    return _per_row(lambda h, n: h.find(n) + 1, [as_str(args[0]), as_str(args[1])], "Int64")


def _pad(args: list[Values], left: bool) -> Values:
    width = _scalar_int(args[1], "pad width", 0, MAX_PAD)
    text, fill = as_str(args[0]), as_str(args[2])

    def pad(t: str, f: str) -> str:
        if not f or len(t) >= width:
            return t[:width]
        padding = (f * (width // len(f) + 1))[: width - len(t)]
        return padding + t if left else t + padding

    return _per_row(pad, [text, fill], "string")


def _format_number(args: list[Values]) -> Values:
    places = _scalar_int(args[1], "format_number() places", 0, 15) if len(args) > 1 else 0
    values = _to_float(args[0])
    out = [None if not math.isfinite(v) else f"{v:,.{places}f}" for v in values]
    return pd.Series(out, dtype="string")


def _replace(args: list[Values]) -> Values:
    text, before, after = (as_str(a) for a in args)
    befores, afters = before.dropna().unique(), after.dropna().unique()
    if len(befores) == 1 and len(afters) == 1 and not (before.isna().any() or after.isna().any()):
        if befores[0] == "":
            return text
        return text.str.replace(str(befores[0]), str(afters[0]), regex=False)
    return _per_row(lambda t, b, a: t.replace(b, a) if b else t, [text, before, after], "string")


def _concat(args: list[Values]) -> Values:
    parts = [as_str(a).fillna("") for a in args]
    out = parts[0]
    for part in parts[1:]:
        out = out + part
    return out


def _coalesce(args: list[Values]) -> Values:
    values = _unify(args)
    out = values[0]
    for value in values[1:]:
        out = out.where(out.notna(), value)
    return out


def _if(args: list[Values]) -> Values:
    condition = as_bool(args[0]).fillna(False).to_numpy(dtype=bool)
    yes, no = _unify(args[1:])
    return yes.where(condition, no)


def _nullif(args: list[Values]) -> Values:
    return args[0].mask(_compare("=", args[0], args[1]).fillna(False).to_numpy(dtype=bool))


FUNCTIONS: dict[str, tuple[int, int | None, Callable[[list[Values]], Values]]] = {
    # maths
    "abs": (1, 1, lambda a: as_num(a[0]).abs()),
    "sqrt": (1, 1, _math(np.sqrt)),
    "exp": (1, 1, _math(np.exp)),
    "ln": (1, 1, _math(np.log)),
    "log10": (1, 1, _math(np.log10)),
    "sin": (1, 1, _math(np.sin)),
    "cos": (1, 1, _math(np.cos)),
    "tan": (1, 1, _math(np.tan)),
    "asin": (1, 1, _math(np.arcsin)),
    "acos": (1, 1, _math(np.arccos)),
    "atan": (1, 1, _math(np.arctan)),
    "atan2": (2, 2, _math(np.arctan2)),
    "degrees": (1, 1, _math(np.degrees)),
    "radians": (1, 1, _math(np.radians)),
    "floor": (1, 1, _math(np.floor)),
    "ceil": (1, 1, _math(np.ceil)),
    "round": (1, 2, _round),
    "pi": (0, 0, lambda a: pd.Series([math.pi], dtype="Float64")),
    "min": (1, None, lambda a: _min_max(a, take_max=False)),
    "max": (1, None, lambda a: _min_max(a, take_max=True)),
    "clamp": (3, 3, _clamp),
    # conversions
    "to_int": (1, 1, _to_int),
    "to_real": (1, 1, lambda a: as_num(a[0]).astype("Float64")),
    "to_string": (1, 1, lambda a: as_str(a[0])),
    # text
    "upper": (1, 1, lambda a: as_str(a[0]).str.upper()),
    "lower": (1, 1, lambda a: as_str(a[0]).str.lower()),
    "title": (1, 1, lambda a: as_str(a[0]).str.title()),
    "trim": (1, 1, lambda a: as_str(a[0]).str.strip()),
    "length": (1, 1, lambda a: as_str(a[0]).str.len().astype("Int64")),
    "substr": (2, 3, _fn_substr),
    "left": (2, 2, lambda a: _text_num(a, lambda t, n: t[: max(n, 0)])),
    "right": (2, 2, lambda a: _text_num(a, lambda t, n: t[len(t) - max(n, 0) :])),
    "replace": (3, 3, _replace),
    "strpos": (2, 2, _strpos),
    "lpad": (3, 3, lambda a: _pad(a, left=True)),
    "rpad": (3, 3, lambda a: _pad(a, left=False)),
    "concat": (1, None, _concat),
    "format_number": (1, 2, _format_number),
    # conditionals
    "if": (3, 3, _if),
    "coalesce": (1, None, _coalesce),
    "nullif": (2, 2, _nullif),
}  # fmt: skip
ARITY = {name: (low, high) for name, (low, high, _) in FUNCTIONS.items()}


# --- evaluation --------------------------------------------------------------------------------
class _Evaluator:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.n = len(frame)
        self._columns: dict[str, Values] = {}

    def column(self, name: str) -> Values:
        if name not in self._columns:
            self._columns[name] = prepare_column(self.frame[resolve_field(name, self.frame)])
        return self._columns[name]

    def geometry(self, variable: str) -> Any:
        geometry = getattr(self.frame, "geometry", None)
        if geometry is None or not hasattr(geometry, "area"):
            raise ExpressionError(f"${variable} needs a layer with geometry")
        return geometry.reset_index(drop=True)

    def variable(self, name: str) -> Values:
        if name == "id":
            return pd.Series(np.arange(self.n), dtype="Int64")
        import shapely

        geoms = np.asarray(self.geometry(name).values, dtype=object)
        with np.errstate(all="ignore"):
            if name == "area":
                values = shapely.area(geoms)
            elif name == "length":
                values = shapely.length(geoms)
            elif name == "perimeter":
                polygonal = np.isin(shapely.get_type_id(geoms), (3, 6))
                values = np.where(polygonal, shapely.length(geoms), np.nan)
            elif name == "x":
                values = shapely.get_x(geoms)
            else:
                values = shapely.get_y(geoms)
        return _from_float(np.asarray(values, dtype=float))

    def eval(self, node: Node) -> Values:
        match node:
            case Literal(value=value):
                return _const(value, self.n)
            case Field(name=name):
                return self.column(name)
            case Variable(name=name):
                return self.variable(name)
            case Unary(op="NOT", operand=operand):
                return ~as_bool(self.eval(operand))
            case Unary(op=op, operand=operand):
                value = as_num(self.eval(operand))
                return -value if op == "-" else value
            case Binary(op=op, left=left, right=right):
                return self.binary(op, self.eval(left), self.eval(right))
            case InList(operand=operand, items=items, negated=negated):
                value = self.eval(operand)
                result = pd.Series(False, index=pd.RangeIndex(self.n), dtype="boolean")
                for item in items:
                    result = result | _compare("=", value, self.eval(item))
                return ~result if negated else result
            case Call(name=name, args=args):
                result = FUNCTIONS[name][2]([self.eval(a) for a in args])
                if len(result) != self.n:  # constant functions such as pi()
                    result = pd.Series(
                        result.iloc[0] if len(result) else pd.NA,
                        index=pd.RangeIndex(self.n),
                        dtype=result.dtype,
                    )
                return result.reset_index(drop=True)
            case Case(whens=whens, otherwise=otherwise):
                return self.case(whens, otherwise)
        raise ExpressionError("Unsupported expression")  # pragma: no cover

    def binary(self, op: str, a: Values, b: Values) -> Values:
        if op == "AND":
            return as_bool(a) & as_bool(b)
        if op == "OR":
            return as_bool(a) | as_bool(b)
        if op in _COMPARE:
            return _compare(op, a, b)
        if op == "||":
            return as_str(a) + as_str(b)
        if op in ("LIKE", "ILIKE"):
            return _like(a, b, fold=op == "ILIKE")
        if op in ("IS", "IS NOT"):
            return _is(a, b, negate=op == "IS NOT")
        return _arithmetic(op, a, b)

    def case(self, whens: tuple[tuple[Node, Node], ...], otherwise: Node | None) -> Values:
        remaining = np.ones(self.n, dtype=bool)
        takes: list[NDArray[np.bool_]] = []
        values: list[Values] = []
        for condition, value in whens:
            hit = as_bool(self.eval(condition)).fillna(False).to_numpy(dtype=bool)
            takes.append(hit & remaining)
            values.append(self.eval(value))
            remaining &= ~hit
        takes.append(remaining)
        values.append(self.eval(otherwise) if otherwise is not None else _null(self.n))
        values = _unify(values)
        out = values[-1]
        for take, value in zip(reversed(takes[:-1]), reversed(values[:-1]), strict=True):
            out = value.where(take, out)
        return out


def resolve_field(name: str, frame: pd.DataFrame) -> str:
    """The column called ``name``: an exact match, else a unique case-insensitive one."""
    if name in frame.columns:
        return name
    folded = [c for c in frame.columns if isinstance(c, str) and c.casefold() == name.casefold()]
    if len(folded) == 1:
        return folded[0]
    raise ExpressionError(f"Field '{name}' not found")


class Expression:
    """A parsed expression. Parsing validates syntax, functions and argument counts."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.root = parse(text, ARITY)

    def __repr__(self) -> str:
        return f"Expression({self.text!r})"

    @cached_property
    def fields(self) -> frozenset[str]:
        return frozenset(n.name for n in walk(self.root) if isinstance(n, Field))

    @property
    def field_name(self) -> str | None:
        """The field name if the whole expression is a single field reference."""
        return self.root.name if isinstance(self.root, Field) else None

    def check_fields(self, frame: pd.DataFrame) -> None:
        """Raise ``ExpressionError`` if a referenced field is missing from ``frame``."""
        for name in sorted(self.fields):
            resolve_field(name, frame)

    def evaluate(self, frame: pd.DataFrame) -> Values:
        """One value per row of ``frame`` (positional index). Raises only ``ExpressionError``."""
        try:
            result = _Evaluator(frame).eval(self.root)
        except ExpressionError:
            raise
        except (TypeError, ValueError, ArithmeticError, KeyError, IndexError) as exc:
            raise ExpressionError(f"Could not evaluate {self.text!r}: {exc}") from exc
        return result.reset_index(drop=True)

    def evaluate_mask(self, frame: pd.DataFrame) -> NDArray[np.bool_]:
        """Rows where the expression is true. NULL counts as false, as in a QGIS filter."""
        values = self.evaluate(frame)
        try:
            return as_bool(values).fillna(False).to_numpy(dtype=bool)
        except (TypeError, ValueError) as exc:
            raise ExpressionError(f"{self.text!r} is not a condition") from exc


def parse_expression(text: str) -> Expression:
    return Expression(text)
