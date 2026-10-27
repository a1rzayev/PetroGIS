"""Tokenizer and Pratt parser for the expression language.

The grammar is the commonly used subset of QGIS expressions:

- literals: numbers, ``'text'`` (``''`` escapes a quote), ``TRUE``, ``FALSE``, ``NULL``
- fields: ``"Field Name"`` (``""`` escapes a quote) or a bare identifier such as ``status``
- geometry variables: ``$area``, ``$length``, ``$perimeter``, ``$x``, ``$y``, ``$id``
- operators, loosest first: ``OR``; ``AND``; ``NOT``; comparisons (``= <> != < <= > >=``,
  ``[NOT] LIKE``, ``[NOT] ILIKE``, ``IS [NOT]``, ``[NOT] IN (...)``); ``+ -``; ``* / // %``;
  ``^`` (right associative); ``||``; unary ``-`` and ``+``. This matches QGIS precedence.
- ``CASE WHEN cond THEN value ... [ELSE value] END`` and calls to whitelisted functions.

Parsing is the only place text is interpreted. Limits on length, depth and node count make the
cost of any input bounded, and every failure raises :class:`ExpressionError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_LENGTH = 20_000
"""Longest accepted expression, in characters."""
MAX_DEPTH = 200
"""Deepest accepted AST, counting chains like ``a + b + c`` as nesting too."""
MAX_NODES = 5_000
"""Most AST nodes in one expression. Evaluation cost is proportional to this times the rows."""

KEYWORDS = frozenset(
    {"AND", "OR", "NOT", "IN", "IS", "LIKE", "ILIKE", "NULL", "TRUE", "FALSE"}
    | {"CASE", "WHEN", "THEN", "ELSE", "END"}
)
VARIABLES = frozenset({"area", "length", "perimeter", "x", "y", "id"})
COMPARISONS = frozenset({"=", "<>", "!=", "<", "<=", ">", ">="})


class ExpressionError(ValueError):
    """Invalid expression text, or a value that cannot be evaluated. Safe to show to the user."""


# --- AST ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Literal:
    value: Any
    """int, float, str, bool or None (NULL)."""


@dataclass(frozen=True)
class Field:
    name: str


@dataclass(frozen=True)
class Variable:
    name: str
    """Lower-case name without the ``$``."""


@dataclass(frozen=True)
class Unary:
    op: str
    """``-``, ``+`` or ``NOT``."""
    operand: Node


@dataclass(frozen=True)
class Binary:
    op: str
    """An arithmetic or comparison operator, ``||``, ``AND``, ``OR``, ``LIKE``, ``ILIKE``,
    ``IS`` or ``IS NOT``."""
    left: Node
    right: Node


@dataclass(frozen=True)
class InList:
    operand: Node
    items: tuple[Node, ...]
    negated: bool


@dataclass(frozen=True)
class Call:
    name: str
    """Lower-case function name."""
    args: tuple[Node, ...]


@dataclass(frozen=True)
class Case:
    whens: tuple[tuple[Node, Node], ...]
    otherwise: Node | None


Node = Literal | Field | Variable | Unary | Binary | InList | Call | Case


# --- tokens ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Token:
    kind: str
    """``num``, ``str``, ``field``, ``ident``, ``var``, ``kw``, ``op`` or ``end``."""
    value: Any
    pos: int


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>'(?:[^'\\]|''|\\.)*')
  | (?P<field>"(?:[^"]|"")*")
  | (?P<var>\$[^\W\d]\w*)
  | (?P<ident>[^\W\d]\w*)
  | (?P<op>\|\||<=|>=|<>|!=|//|[-+*/%^=<>(),])
    """,
    re.VERBOSE,
)
_ESCAPES = {"'": "'", "\\": "\\", "n": "\n", "t": "\t"}
_INT64_MAX = 2**63 - 1


def _unescape_string(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            # Unknown escapes keep their backslash, so LIKE patterns can escape % and _.
            out.append(_ESCAPES.get(body[i + 1], "\\" + body[i + 1]))
            i += 2
        elif ch == "'" and body[i + 1 : i + 2] == "'":
            out.append("'")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _number(text: str) -> int | float:
    if re.fullmatch(r"\d+", text):
        value = int(text)
        # Larger integers would overflow int64 columns. QGIS turns them into doubles too.
        return value if value <= _INT64_MAX else float(value)
    return float(text)


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_LENGTH:
        raise ExpressionError(f"Expression is too long (over {MAX_LENGTH:,} characters)")
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            if text[pos] in "'\"":
                raise ExpressionError(f"Unclosed quote starting at position {pos + 1}")
            raise ExpressionError(f"Unexpected character {text[pos]!r} at position {pos + 1}")
        kind = match.lastgroup
        raw = match.group()
        if kind == "num":
            tokens.append(Token("num", _number(raw), pos))
        elif kind == "str":
            tokens.append(Token("str", _unescape_string(raw[1:-1]), pos))
        elif kind == "field":
            tokens.append(Token("field", raw[1:-1].replace('""', '"'), pos))
        elif kind == "var":
            tokens.append(Token("var", raw[1:].lower(), pos))
        elif kind == "ident":
            upper = raw.upper()
            tokens.append(
                Token("kw", upper, pos) if upper in KEYWORDS else Token("ident", raw, pos)
            )
        elif kind == "op":
            tokens.append(Token("op", raw, pos))
        pos = match.end()
    tokens.append(Token("end", None, len(text)))
    return tokens


def quote_field(name: str) -> str:
    """``name`` as a double-quoted field reference, safe for any field name."""
    return '"' + name.replace('"', '""') + '"'


# --- parser ------------------------------------------------------------------------------------
_OR, _AND, _NOT, _CMP, _ADD, _MUL, _POW, _CONCAT, _UNARY = range(1, 10)
_BINARY_POWER = {
    "OR": _OR,
    "AND": _AND,
    **dict.fromkeys(COMPARISONS, _CMP),
    "+": _ADD,
    "-": _ADD,
    "*": _MUL,
    "/": _MUL,
    "//": _MUL,
    "%": _MUL,
    "^": _POW,
    "||": _CONCAT,
}


class _Parser:
    def __init__(self, text: str, arity: dict[str, tuple[int, int | None]]) -> None:
        self.tokens = tokenize(text)
        self.i = 0
        self.depth = 0
        self.nodes = 0
        self.arity = arity

    # token helpers
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def is_kw(self, *words: str, offset: int = 0) -> bool:
        token = self.tokens[min(self.i + offset, len(self.tokens) - 1)]
        return token.kind == "kw" and token.value in words

    def is_op(self, op: str) -> bool:
        return self.tok.kind == "op" and self.tok.value == op

    def expect_op(self, op: str) -> None:
        if not self.is_op(op):
            self.fail(f"expected '{op}'")
        self.advance()

    def expect_kw(self, word: str) -> None:
        if not self.is_kw(word):
            self.fail(f"expected {word}")
        self.advance()

    def fail(self, message: str) -> None:
        token = self.tok
        where = "at the end" if token.kind == "end" else f"at position {token.pos + 1}"
        raise ExpressionError(f"Syntax error {where}: {message}")

    def node(self, node: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExpressionError(f"Expression is too complex (over {MAX_NODES:,} parts)")
        return node

    # grammar
    def parse(self) -> Node:
        if self.tok.kind == "end":
            raise ExpressionError("Expression is empty")
        node = self.expression(0)
        if self.tok.kind != "end":
            self.fail("unexpected text")
        return node

    def expression(self, min_power: int) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError(f"Expression is nested too deeply (over {MAX_DEPTH} levels)")
        left = self.prefix()
        while True:
            infix = self.infix(left, min_power)
            if infix is None:
                break
            left = infix
        self.depth -= 1
        return left

    def prefix(self) -> Node:
        token = self.tok
        if token.kind == "num" or token.kind == "str":
            self.advance()
            return self.node(Literal(token.value))
        if token.kind == "field":
            self.advance()
            return self.node(Field(token.value))
        if token.kind == "var":
            self.advance()
            if token.value not in VARIABLES:
                raise ExpressionError(f"Unknown variable ${token.value}")
            return self.node(Variable(token.value))
        if token.kind == "ident":
            self.advance()
            if self.is_op("("):
                return self.call(token)
            return self.node(Field(token.value))
        if token.kind == "kw":
            if token.value in ("TRUE", "FALSE", "NULL"):
                self.advance()
                value = {"TRUE": True, "FALSE": False, "NULL": None}[token.value]
                return self.node(Literal(value))
            if token.value == "NOT":
                self.advance()
                return self.node(Unary("NOT", self.expression(_NOT)))
            if token.value == "CASE":
                return self.case()
        if token.kind == "op":
            if token.value in ("-", "+"):
                self.advance()
                return self.node(Unary(token.value, self.expression(_UNARY)))
            if token.value == "(":
                self.advance()
                inner = self.expression(0)
                self.expect_op(")")
                return inner
        self.fail("expected a value")
        raise AssertionError  # unreachable: fail() raises

    def call(self, name_token: Token) -> Node:
        name = str(name_token.value).lower()
        if name not in self.arity:
            raise ExpressionError(f"Unknown function '{name_token.value}'")
        self.expect_op("(")
        args: list[Node] = []
        if not self.is_op(")"):
            args.append(self.expression(0))
            while self.is_op(","):
                self.advance()
                args.append(self.expression(0))
        self.expect_op(")")
        low, high = self.arity[name]
        if len(args) < low or (high is not None and len(args) > high):
            if low == high:
                expected = str(low)
            elif high is None:
                expected = f"at least {low}"
            else:
                expected = f"{low} to {high}"
            raise ExpressionError(
                f"Function {name}() takes {expected} argument(s), got {len(args)}"
            )
        return self.node(Call(name, tuple(args)))

    def case(self) -> Node:
        self.expect_kw("CASE")
        whens: list[tuple[Node, Node]] = []
        while self.is_kw("WHEN"):
            self.advance()
            condition = self.expression(0)
            self.expect_kw("THEN")
            whens.append((condition, self.expression(0)))
        if not whens:
            self.fail("expected WHEN")
        otherwise = None
        if self.is_kw("ELSE"):
            self.advance()
            otherwise = self.expression(0)
        self.expect_kw("END")
        return self.node(Case(tuple(whens), otherwise))

    def infix(self, left: Node, min_power: int) -> Node | None:
        token = self.tok
        if token.kind == "op" and token.value in _BINARY_POWER:
            power = _BINARY_POWER[token.value]
            if power <= min_power:
                return None
            self.advance()
            # ``^`` is right associative: the right side may contain another ``^``.
            right = self.expression(power - 1 if token.value == "^" else power)
            op = "<>" if token.value == "!=" else token.value
            return self.node(Binary(op, left, right))
        if token.kind != "kw":
            return None
        if token.value in ("AND", "OR"):
            power = _BINARY_POWER[token.value]
            if power <= min_power:
                return None
            self.advance()
            return self.node(Binary(token.value, left, self.expression(power)))
        if min_power >= _CMP:
            return None
        negated = False
        if token.value == "NOT" and self.is_kw("IN", "LIKE", "ILIKE", offset=1):
            self.advance()
            negated = True
        if self.is_kw("IN"):
            self.advance()
            return self.in_list(left, negated)
        if self.is_kw("LIKE", "ILIKE"):
            op = self.advance().value
            node = self.node(Binary(op, left, self.expression(_CMP)))
            return self.node(Unary("NOT", node)) if negated else node
        if self.is_kw("IS"):
            self.advance()
            op = "IS"
            if self.is_kw("NOT"):
                self.advance()
                op = "IS NOT"
            return self.node(Binary(op, left, self.expression(_CMP)))
        return None

    def in_list(self, operand: Node, negated: bool) -> Node:
        self.expect_op("(")
        items = [self.expression(0)]
        while self.is_op(","):
            self.advance()
            items.append(self.expression(0))
        self.expect_op(")")
        return self.node(InList(operand, tuple(items), negated))


def parse(text: str, arity: dict[str, tuple[int, int | None]]) -> Node:
    """Parse ``text`` into an AST. ``arity`` maps each allowed function to (min, max) arguments."""
    if not isinstance(text, str):
        raise ExpressionError("Expression must be text")
    root = _Parser(text, arity).parse()
    if tree_depth(root) > MAX_DEPTH:
        raise ExpressionError(f"Expression is nested too deeply (over {MAX_DEPTH} levels)")
    return root


def children(node: Node) -> tuple[Node, ...]:
    match node:
        case Unary(operand=operand):
            return (operand,)
        case Binary(left=left, right=right):
            return (left, right)
        case InList(operand=operand, items=items):
            return (operand, *items)
        case Call(args=args):
            return args
        case Case(whens=whens, otherwise=otherwise):
            parts = [part for pair in whens for part in pair]
            return (*parts, otherwise) if otherwise is not None else tuple(parts)
    return ()


def tree_depth(node: Node) -> int:
    """Depth of the tree, computed iteratively. Evaluation recurses this deep."""
    deepest = 0
    stack = [(node, 1)]
    while stack:
        current, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in children(current))
    return deepest


def walk(node: Node) -> list[Node]:
    """Every node of the tree, parents first (iterative, so depth is no concern)."""
    out: list[Node] = []
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(reversed(children(current)))
    return out
