"""Comparison of server output with reference values.

Doubles compare by bit pattern unless a check declares a tolerance. NA and
other NaNs compare by class, because JSON cannot carry a NaN payload.
Strings, integers and logicals compare exactly. Tables are matched by key
columns in any row order; missing or extra rows and columns are failures.
"""

from __future__ import annotations

import fnmatch
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from misclosure.encoding import (
    decode_double,
    float_to_hex,
    is_scalar_node,
    is_table_node,
    label,
)
from misclosure.jsonlit import Num

DEFAULT_TOKENS = {"NA": "NA", "NaN": "NaN", "Inf": "Inf", "-Inf": "-Inf"}


@dataclass(frozen=True)
class Tolerance:
    abs: float | None
    rel: float | None
    reason: str

    @classmethod
    def parse(cls, spec: Mapping[str, Any], where: str) -> Tolerance:
        reason = str(spec.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"{where}: a tolerance must give a reason")
        a, r = spec.get("abs"), spec.get("rel")
        if a is None and r is None:
            raise ValueError(f"{where}: a tolerance needs `abs` or `rel`")
        unknown = set(spec) - {"abs", "rel", "reason"}
        if unknown:
            raise ValueError(f"{where}: unknown tolerance keys {sorted(unknown)}")
        return cls(None if a is None else float(a), None if r is None else float(r), reason)

    def allows(self, out: float, ref: float) -> bool:
        if not (math.isfinite(out) and math.isfinite(ref)):
            return False
        d = abs(out - ref)
        if self.abs is not None and d <= self.abs:
            return True
        return self.rel is not None and d <= self.rel * abs(ref)

    def describe(self) -> str:
        parts = []
        if self.abs is not None:
            parts.append(f"abs {self.abs:g}")
        if self.rel is not None:
            parts.append(f"rel {self.rel:g}")
        return " or ".join(parts)


@dataclass
class Failure:
    kind: str
    """value, json_precision, type, missing, extra, duplicate, error, client"""
    where: str
    message: str
    output: str | None = None
    reference: str | None = None
    literal: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Tally:
    table_cells: int = 0
    scalars: int = 0
    other: int = 0
    within_tolerance: int = 0
    by_tolerance: dict[int, int] = field(default_factory=dict)
    """Values that differ in bits but are within a tolerance, by id of the Tolerance."""
    cells_by_call: dict[str, int] = field(default_factory=dict)
    """Table cells, by the tool or resource whose output held them."""

    def add(self, other: Tally) -> None:
        self.table_cells += other.table_cells
        self.scalars += other.scalars
        self.other += other.other
        self.within_tolerance += other.within_tolerance
        for k, v in other.by_tolerance.items():
            self.by_tolerance[k] = self.by_tolerance.get(k, 0) + v
        for k, n in other.cells_by_call.items():
            self.cells_by_call[k] = self.cells_by_call.get(k, 0) + n

    @property
    def values(self) -> int:
        return self.table_cells + self.scalars + self.other


@dataclass
class Numbers:
    """How the server writes non-finite doubles."""

    tokens: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOKENS))
    null: str | None = None
    """None: JSON null is a missing value; or a token, such as "NA"."""

    def value_of(self, node: Any) -> float | None:
        if isinstance(node, Num):
            return node.as_float()
        if isinstance(node, str) and node in self.tokens:
            return decode_double(self.tokens[node])
        if node is None and self.null is not None:
            return decode_double(self.null)
        return None


def _shown(node: Any) -> str:
    if isinstance(node, Num):
        return node.text
    return repr(node)


def _lost_in_json(literal: Num, ref: float) -> bool:
    """True when the literal is the reference rounded to the digits the server wrote."""
    if not math.isfinite(ref):
        return False
    digits = literal.significant_digits()
    if digits >= 17:
        return False
    rounded = float(f"{ref:.{max(digits, 1) - 1}e}")
    return float_to_hex(rounded) == float_to_hex(literal.as_float())


def compare_double(
    out: Any,
    ref: str,
    where: str,
    numbers: Numbers,
    tolerance: Tolerance | None,
    tally: Tally,
) -> Failure | None:
    want = decode_double(ref)
    got = numbers.value_of(out)
    if got is None:
        return Failure(
            "type",
            where,
            f"expected a double, got {_shown(out)}",
            output=_shown(out),
            reference=label(want),
        )
    if label(got) == label(want):
        return None
    if tolerance is not None and tolerance.allows(got, want):
        tally.within_tolerance += 1
        tally.by_tolerance[id(tolerance)] = tally.by_tolerance.get(id(tolerance), 0) + 1
        return None
    written = out.text if isinstance(out, Num) else repr(out)
    if isinstance(out, Num) and _lost_in_json(out, want):
        kind = "json_precision"
        message = f"server wrote {written}: the reference rounded to the digits written"
    else:
        kind = "value"
        message = f"server wrote {written}: a different double"
    return Failure(
        kind,
        where,
        message,
        output=label(got),
        reference=label(want),
        literal=out.text if isinstance(out, Num) else None,
    )


def compare_exact(out: Any, ref: Any, type_: str, where: str) -> Failure | None:
    """Integers, logicals and strings: exactly equal, and of the same JSON type."""
    if ref is None:
        if out is None:
            return None
        return Failure("value", where, f"expected a missing value, got {_shown(out)}", _shown(out))
    if type_ == "integer":
        ok = isinstance(out, Num) and out.is_integer_literal and int(out.text) == ref
    elif type_ == "logical":
        ok = isinstance(out, bool) and out == ref
    else:
        ok = isinstance(out, str) and out == ref
    if ok:
        return None
    return Failure(
        "value" if out is not None else "missing",
        where,
        f"expected {type_} {ref!r}, got {_shown(out)}",
        output=_shown(out),
        reference=repr(ref),
    )


def compare_scalar(
    out: Any,
    ref_node: Any,
    where: str,
    numbers: Numbers,
    tolerance: Tolerance | None,
    tally: Tally,
) -> Failure | None:
    tally.scalars += 1
    if ref_node is None:
        if out is None:
            return None
        return Failure("value", where, f"expected no value, got {_shown(out)}", _shown(out))
    if not is_scalar_node(ref_node):
        raise ValueError(f"{where}: the reference is not a scalar: {ref_node!r}")
    ((type_, ref),) = ref_node.items()
    if type_ == "double":
        return compare_double(out, ref, where, numbers, tolerance, tally)
    return compare_exact(out, ref, type_, where)


def _key_of(
    row: Mapping[str, Any], names: Sequence[str], types: Sequence[str], numbers: Numbers
) -> tuple[Any, ...]:
    out: list[Any] = []
    for n, t in zip(names, types, strict=True):
        v = row.get(n)
        if t == "double":
            x = numbers.value_of(v)
            out.append(("d", label(x)) if x is not None else ("?", repr(v)))
        elif t == "integer" and isinstance(v, Num) and v.is_integer_literal:
            out.append(("i", int(v.text)))
        else:
            out.append(("v", v))
    return tuple(out)


def _ref_key(values: Sequence[Any], types: Sequence[str]) -> tuple[Any, ...]:
    out: list[Any] = []
    for v, t in zip(values, types, strict=True):
        if t == "double":
            out.append(("d", label(decode_double(v))))
        elif t == "integer" and v is not None:
            out.append(("i", v))
        else:
            out.append(("v", v))
    return tuple(out)


def compare_table(
    rows: Any,
    ref_node: Any,
    where: str,
    numbers: Numbers,
    keys: Sequence[str] | None,
    columns: Mapping[str, str] | None,
    ignore: Sequence[str],
    tolerances: Mapping[str, Tolerance],
    tally: Tally,
    reference_columns: Sequence[str] | None = None,
) -> list[Failure]:
    """Compare a list of row objects with a reference table.

    `columns` maps output column names to reference column names; by default
    each reference column is read from the output column of the same name.
    `keys` are reference column names; by default every column that is not
    of type double. Output columns that are neither mapped nor matched by a
    pattern in `ignore` are failures. `reference_columns` limits the
    comparison to those reference columns, for output that holds part of a
    table.
    """
    if not is_table_node(ref_node):
        raise ValueError(f"{where}: the reference is not a table")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return [Failure("type", where, "expected a list of row objects")]
    ref_cols = ref_node["columns"]
    if reference_columns is not None:
        unknown = set(reference_columns) - {c["name"] for c in ref_cols}
        if unknown:
            raise ValueError(f"{where}: reference_columns {sorted(unknown)} not in the table")
        ref_cols = [c for c in ref_cols if c["name"] in reference_columns]
    ref_names = [c["name"] for c in ref_cols]
    ref_types = {c["name"]: c["type"] for c in ref_cols}
    out_for_ref = {r: r for r in ref_names}
    if columns:
        unknown = set(columns.values()) - set(ref_names)
        if unknown:
            raise ValueError(
                f"{where}: `columns` names reference columns {sorted(unknown)} not in the table"
            )
        for out_name, ref_name in columns.items():
            out_for_ref[ref_name] = out_name
    key_names = list(keys) if keys else [c["name"] for c in ref_cols if c["type"] != "double"]
    for k in key_names:
        if k not in ref_types:
            raise ValueError(f"{where}: key column {k!r} is not in the reference table")
    n = len(ref_cols[0]["values"]) if ref_cols else 0
    failures: list[Failure] = []

    # columns: extra and missing
    mapped = set(out_for_ref.values())
    seen: dict[str, int] = {}
    for r in rows:
        for c in r:
            seen[c] = seen.get(c, 0) + 1
    for c in sorted(seen):
        if c not in mapped and not any(fnmatch.fnmatchcase(c, p) for p in ignore):
            failures.append(
                Failure("extra", f"{where}.{c}", f"column {c!r} is not in the reference")
            )
    for ref_name in ref_names:
        out_name = out_for_ref[ref_name]
        missing = sum(1 for r in rows if out_name not in r)
        if missing:
            failures.append(
                Failure(
                    "missing",
                    f"{where}.{out_name}",
                    f"column missing from {missing} of {len(rows)} rows",
                )
            )

    # rows: by key, or by position when the table has no key columns
    ref_rows = [[c["values"][i] for c in ref_cols] for i in range(n)]
    out_key_names = [out_for_ref[k] for k in key_names]
    pairs: list[tuple[int, dict[str, Any]]] = []
    if key_names:
        key_types = [ref_types[k] for k in key_names]
        index: dict[tuple[Any, ...], list[int]] = {}
        for i, r in enumerate(rows):
            index.setdefault(_key_of(r, out_key_names, key_types, numbers), []).append(i)
        used: set[int] = set()
        for i, rr in enumerate(ref_rows):
            kv = [rr[ref_names.index(k)] for k in key_names]
            hits = index.get(_ref_key(kv, key_types), [])
            desc = ", ".join(f"{k}={v}" for k, v in zip(key_names, kv, strict=True))
            if not hits:
                failures.append(
                    Failure("missing", f"{where}[{desc}]", "row missing from the output")
                )
            elif len(hits) > 1:
                failures.append(
                    Failure(
                        "duplicate", f"{where}[{desc}]", f"{len(hits)} output rows have this key"
                    )
                )
                used.update(hits)
            else:
                pairs.append((i, rows[hits[0]]))
                used.add(hits[0])
        for j, r in enumerate(rows):
            if j not in used:
                desc = ", ".join(f"{k}={r.get(k)}" for k in out_key_names)
                failures.append(Failure("extra", f"{where}[{desc}]", "row not in the reference"))
    else:
        if len(rows) != n:
            failures.append(
                Failure(
                    "missing" if len(rows) < n else "extra",
                    where,
                    f"{len(rows)} rows; the reference has {n}",
                )
            )
        pairs = [(i, rows[i]) for i in range(min(n, len(rows)))]

    for i, r in pairs:
        rr = ref_rows[i]
        desc = ", ".join(f"{k}={rr[ref_names.index(k)]}" for k in key_names) or f"row {i}"
        for ci, ref_name in enumerate(ref_names):
            out_name = out_for_ref[ref_name]
            if out_name not in r:
                continue
            tally.table_cells += 1
            cell = f"{where}[{desc}].{out_name}"
            t = ref_types[ref_name]
            if t == "double":
                f = compare_double(
                    r[out_name], rr[ci], cell, numbers, tolerances.get(ref_name), tally
                )
            else:
                f = compare_exact(r[out_name], rr[ci], t, cell)
            if f:
                failures.append(f)
    return failures
