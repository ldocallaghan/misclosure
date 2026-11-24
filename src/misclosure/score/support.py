"""Which values support a number in an answer.

A number is supported when, within the rounding it shows, it matches a
value in the support set of its question:

1. the reference values: every finite double and every integer in the
   question's reference cases;
2. comparison values: for each comparison the question declares, the
   derivations the evaluation file gives for that table, such as b - a or
   b / a, row by row between two cases;
3. each value of 1 and 2 passed through every `each_value` derivation, such
   as x, (x - 1) * 100 or x * 1000;
4. the numbers given in the question;
5. the constants the evaluation file declares.

Derivations are arithmetic expressions in x (each value) or a and b (the
two cases of a comparison), with + - * / ** and parentheses.
"""

from __future__ import annotations

import ast
import fnmatch
import math
import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from misclosure.encoding import decode_double, is_scalar_node, is_table_node
from misclosure.score.numbers import Shown

_OPS: dict[type, Callable[..., float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, *_OPS)


class Expression:
    """An arithmetic expression over named variables, without eval."""

    def __init__(self, text: str, names: Sequence[str]) -> None:
        self.text = text
        tree = ast.parse(text, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in names:
                raise ValueError(f"{text!r}: unknown name {node.id!r}; use {', '.join(names)}")
            if not isinstance(node, _ALLOWED):
                raise ValueError(f"{text!r}: only numbers, names and + - * / ** are allowed")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValueError(f"{text!r}: constants must be numbers")
        self.tree = tree.body

    def __call__(self, **values: float) -> float | None:
        try:
            r = self._eval(self.tree, values)
        except (ZeroDivisionError, OverflowError):
            return None
        return r if math.isfinite(r) else None

    def _eval(self, node: ast.AST, values: Mapping[str, float]) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            return _OPS[type(node.op)](self._eval(node.operand, values))
        if isinstance(node, ast.BinOp):
            return _OPS[type(node.op)](
                self._eval(node.left, values), self._eval(node.right, values)
            )
        raise ValueError(f"cannot evaluate {ast.dump(node)}")


@dataclass
class ComparisonRule:
    tables: list[str]
    derive: dict[str, Expression]

    def applies(self, table: str) -> bool:
        return any(fnmatch.fnmatchcase(table, p) for p in self.tables)


@dataclass
class Derivations:
    each_value: list[Expression]
    comparisons: list[ComparisonRule]

    @classmethod
    def parse(cls, spec: Mapping[str, Any] | None) -> Derivations:
        spec = spec or {}
        each = [Expression(e, ["x"]) for e in spec.get("each_value", ["x"])]
        rules = []
        for r in spec.get("comparisons", []):
            tables = r.get("tables", ["*"])
            rules.append(
                ComparisonRule(
                    [tables] if isinstance(tables, str) else list(tables),
                    {name: Expression(e, ["a", "b"]) for name, e in r["derive"].items()},
                )
            )
        return cls(each, rules)

    def for_table(self, table: str, only: Sequence[str] | None = None) -> dict[str, Expression]:
        """The first comparison rule whose table patterns match, limited to `only`."""
        for r in self.comparisons:
            if r.applies(table):
                if only is None:
                    return dict(r.derive)
                unknown = set(only) - set(r.derive)
                if unknown:
                    raise ValueError(f"table {table}: derivations {sorted(unknown)} not declared")
                return {k: v for k, v in r.derive.items() if k in only}
        return {}


def numeric_values(node: Any) -> list[float]:
    """Every finite double and every integer under a reference node."""
    out: list[float] = []
    if is_table_node(node):
        for col in node["columns"]:
            if col["type"] == "double":
                for v in col["values"]:
                    x = decode_double(v)
                    if math.isfinite(x):
                        out.append(x)
            elif col["type"] == "integer":
                out.extend(float(v) for v in col["values"] if v is not None)
    elif is_scalar_node(node):
        ((t, v),) = node.items()
        if t == "double":
            x = decode_double(v)
            if math.isfinite(x):
                out.append(x)
        elif t == "integer" and v is not None:
            out.append(float(v))
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(numeric_values(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(numeric_values(v))
    return out


def table_rows(node: Any) -> list[tuple[tuple[Any, ...], dict[str, float]]]:
    """(key, {double column: value}) for each row; the key is the non-double columns."""
    if not is_table_node(node):
        raise ValueError("not a table")
    cols = node["columns"]
    n = len(cols[0]["values"]) if cols else 0
    out = []
    for i in range(n):
        key = tuple(c["values"][i] for c in cols if c["type"] != "double")
        vals = {c["name"]: decode_double(c["values"][i]) for c in cols if c["type"] == "double"}
        out.append((key, vals))
    return out


def comparison_values(
    a: Any, b: Any, derive: Mapping[str, Expression], column: str | None = None
) -> list[float]:
    """Derivations between two tables, row by matching key, column by column."""
    rows_b = dict(table_rows(b))
    out: list[float] = []
    for key, va in table_rows(a):
        vb = rows_b.get(key)
        if vb is None:
            continue
        for name, x in va.items():
            if column is not None and name != column:
                continue
            y = vb.get(name)
            if y is None or not (math.isfinite(x) and math.isfinite(y)):
                continue
            for expr in derive.values():
                r = expr(a=x, b=y)
                if r is not None:
                    out.append(r)
    return out


@dataclass
class Support:
    values: list[float]
    ignore_sign: bool = True

    def matches(self, s: Shown) -> bool:
        if self.ignore_sign:
            return any(
                abs(v - s.value) <= s.tolerance or abs(abs(v) - abs(s.value)) <= s.tolerance
                for v in self.values
            )
        return any(abs(v - s.value) <= s.tolerance for v in self.values)
