"""Safe expressions over layer attributes: a QGIS-compatible subset with no Python evaluation.

Text is tokenized and parsed into an AST by our own parser (``parser.py``), then evaluated over
whole pandas columns at once (``evaluate.py``). Only whitelisted functions exist. Input size, AST
depth and node count are limited, so a hostile project or style file can neither run code nor make
the evaluator hang.
"""

from petrogis.core.expressions.evaluate import (
    Expression,
    format_values,
    parse_expression,
)
from petrogis.core.expressions.parser import (
    MAX_DEPTH,
    MAX_LENGTH,
    MAX_NODES,
    ExpressionError,
    quote_field,
)

__all__ = [
    "MAX_DEPTH",
    "MAX_LENGTH",
    "MAX_NODES",
    "Expression",
    "ExpressionError",
    "format_values",
    "parse_expression",
    "quote_field",
]
