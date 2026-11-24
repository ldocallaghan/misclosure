"""Trap rules: checks for questions whose right answer is a refusal or a warning.

A rule is a function that takes a `TrapContext` and returns (passed, note).
Rules are registered by name with `@trap_rule("name")`, in misclosure or in
a plugin file the evaluation file lists under `traps.plugins`. A question
names its rule with `trap: {rule: name, ...params}`.

    from misclosure.score.traps import TrapContext, trap_rule

    @trap_rule("says_synthetic")
    def says_synthetic(ctx: TrapContext) -> tuple[bool, str]:
        ok = "synthetic" in ctx.answer.lower()
        return ok, f"says the data are synthetic: {'yes' if ok else 'no'}"
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from misclosure.score.numbers import Shown
from misclosure.score.support import table_rows

TrapRule = Callable[["TrapContext"], tuple[bool, str]]
RULES: dict[str, TrapRule] = {}


def trap_rule(name: str) -> Callable[[TrapRule], TrapRule]:
    def register(fn: TrapRule) -> TrapRule:
        RULES[name] = fn
        return fn

    return register


@dataclass
class TrapContext:
    question: dict[str, Any]
    answer: str
    numbers: list[Shown]
    reference: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)

    def case(self, i: int = 0) -> Any:
        """The reference node of the question's i-th case."""
        return self.reference[self.question["cases"][i]]

    def column(self, case: Any, table: str, column: str = "value") -> list[float]:
        return [vals[column] for _, vals in table_rows(case["tables"][table]) if column in vals]

    @staticmethod
    def shown_matching(shown: list[Shown], values: list[float]) -> list[Shown]:
        return [
            s
            for s in shown
            if any(
                abs(v - s.value) <= s.tolerance or abs(abs(v) - abs(s.value)) <= s.tolerance
                for v in values
            )
        ]


def load_plugins(paths: list[Path]) -> None:
    for p in paths:
        spec = importlib.util.spec_from_file_location(f"misclosure_plugin_{p.stem}", p)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load trap plugin {p}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


def run(ctx: TrapContext) -> tuple[bool, str]:
    name = ctx.params.get("rule")
    if name not in RULES:
        raise ValueError(f"unknown trap rule {name!r}; known: {', '.join(sorted(RULES))}")
    return RULES[name](ctx)


# ---- general rules ------------------------------------------------------------------


@trap_rule("regex")
def regex_rule(ctx: TrapContext) -> tuple[bool, str]:
    """Passes when every pattern in `must` matches and none in `must_not` does."""
    must = [re.compile(p, re.I) for p in ctx.params.get("must", [])]
    must_not = [re.compile(p, re.I) for p in ctx.params.get("must_not", [])]
    missing = [p.pattern for p in must if not p.search(ctx.answer)]
    present = [p.pattern for p in must_not if p.search(ctx.answer)]
    note = []
    if missing:
        note.append("missing: " + "; ".join(missing))
    if present:
        note.append("present: " + "; ".join(present))
    return not missing and not present, ", ".join(note) or "all patterns as required"
