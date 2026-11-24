"""`misclosure score`: check every number in each answer against reference values.

The scorer checks where the numbers in an answer came from. It does not
check whether the prose around them is right.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from misclosure.conform.runner import read_structured_file
from misclosure.reference import Reference
from misclosure.reference import load as load_reference
from misclosure.score import transcript as tx
from misclosure.score import traps
from misclosure.score.numbers import Shown, numbers
from misclosure.score.support import Derivations, Support, comparison_values, numeric_values
from misclosure.selectors import parse, select, substitute

EVAL_FORMAT = 1


class EvalFileError(Exception):
    pass


@dataclass
class QuestionResult:
    id: str
    label: str
    tools: list[str]
    shown: list[Shown]
    unsupported: list[Shown]
    trap: tuple[bool, str] | None
    answer: str | None
    passed: bool


@dataclass
class ScoreResult:
    title: str
    model: str
    questions: list[QuestionResult] = field(default_factory=list)
    reference: Reference | None = None

    @property
    def failing(self) -> int:
        return sum(not q.passed for q in self.questions)


@dataclass
class EvalFile:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> EvalFile:
        p = path.resolve()
        data = read_structured_file(p)
        if not isinstance(data, dict) or data.get("misclosure") != EVAL_FORMAT:
            raise EvalFileError(f"{p}: set `misclosure: {EVAL_FORMAT}` at the top")
        for key in ("questions", "reference", "transcripts"):
            if key not in data:
                raise EvalFileError(f"{p}: no `{key}`")
        return cls(p, data)

    @property
    def base(self) -> Path:
        return self.path.parent


def _questions(ef: EvalFile, ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    q = ef.data["questions"]
    if isinstance(q, str):
        q = read_structured_file(ef.base / substitute(q, ctx))
    if not isinstance(q, list):
        raise EvalFileError("`questions` must be a list or a file holding one")
    ids = [x.get("id") for x in q]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
        raise EvalFileError("every question needs a unique string `id`")
    return q


def _support(
    q: Mapping[str, Any],
    ref: Mapping[str, Any],
    derivations: Derivations,
    constants: list[float],
    ignore_sign: bool,
) -> Support:
    base: list[float] = []
    for c in q.get("cases", []):
        if c not in ref:
            raise EvalFileError(f"question {q['id']}: case {c!r} is not in the reference")
        base.extend(numeric_values(ref[c]))
    for comp in q.get("comparisons", []):
        # `table` is a selector inside each case; rules match its last part
        a, b, table = comp["a"], comp["b"], comp["table"]
        name = [p for p in parse(table) if isinstance(p, str)][-1]
        derive = derivations.for_table(name, comp.get("derive"))
        base.extend(
            comparison_values(
                select(ref[a], table), select(ref[b], table), derive, comp.get("column")
            )
        )
    values: list[float] = []
    for x in base:
        for e in derivations.each_value:
            r = e(x=x)
            if r is not None:
                values.append(r)
    values.extend(float(g) for g in q.get("given", []))
    values.extend(constants)
    return Support(values, ignore_sign)


def score(
    ef: EvalFile, transcripts: Path | None = None, workdir: Path | None = None
) -> ScoreResult:
    ctx: dict[str, Any] = {"case_dir": str(ef.base), "workdir": str(workdir or ef.base)}
    d = ef.data
    reference = load_reference(d["reference"], ctx, ef.base)
    questions = _questions(ef, ctx)
    tspec = d["transcripts"]
    tdir = transcripts or (ef.base / substitute(tspec["dir"], ctx))
    tformat = tspec.get("format", "misclosure")
    strip = tspec.get("strip_prefix", "mcp__")
    nspec = d.get("numbers") or {}
    skip = nspec.get("skip_code_blocks", "all")
    skip_code = None if skip == "all" else list(skip)
    hyphen_ranges = bool(nspec.get("hyphen_ranges", True))
    ignore_sign = bool(nspec.get("ignore_sign", True))
    derivations = Derivations.parse(d.get("derivations"))
    constants = [float(c) for c in d.get("constants", [])]
    plugins = (d.get("traps") or {}).get("plugins", [])
    traps.load_plugins([ef.base / substitute(p, ctx) for p in plugins])
    bar = d.get("pass_bar") or {}
    max_unsupported = int(bar.get("max_unsupported", 0))

    result = ScoreResult(title=str(d.get("name", ef.path.stem)), model="", reference=reference)
    for q in questions:
        qid = q["id"]
        path = tdir / f"{qid}{tspec.get('suffix', '.jsonl')}"
        t = tx.read(path, tformat, strip) if path.exists() else None
        if t is not None and not result.model and t.model:
            result.model = t.model
        answer = t.answer if t else None
        if answer is None:
            result.questions.append(
                QuestionResult(qid, q.get("label", ""), [], [], [], None, None, False)
            )
            continue
        shown = numbers(answer, skip_code, hyphen_ranges)
        sup = _support(q, reference.values, derivations, constants, ignore_sign)
        unsupported = [s for s in shown if not sup.matches(s)]
        trap = None
        if q.get("trap"):
            tctx = traps.TrapContext(q, answer, shown, reference.values, dict(q["trap"]))
            trap = traps.run(tctx)
        tools: list[str] = []
        for name in t.tools_called():
            if not tools or tools[-1] != name:
                tools.append(name)
        passed = len(unsupported) <= max_unsupported and (trap is None or trap[0])
        result.questions.append(
            QuestionResult(qid, q.get("label", ""), tools, shown, unsupported, trap, answer, passed)
        )
    return result


def to_markdown(r: ScoreResult, ef: EvalFile) -> str:
    label_col = (ef.data.get("report") or {}).get("label_column")
    lines = [f"# {r.title}", ""]
    head = ["Question"] + ([label_col] if label_col else [])
    head += ["Tools called", "Numbers checked", "Unsupported", "Trap outcome"]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "---|" * len(head))
    for q in r.questions:
        cells = [q.id] + ([q.label] if label_col else [])
        if q.answer is None:
            cells += ["", "", "", "no transcript"]
        else:
            uns = (
                "0"
                if not q.unsupported
                else f"{len(q.unsupported)}: " + ", ".join(f"`{s.text}`" for s in q.unsupported)
            )
            trap = (
                "not a trap"
                if q.trap is None
                else f"{'pass' if q.trap[0] else 'FAIL'} ({q.trap[1]})"
            )
            cells += [", ".join(q.tools), str(len(q.shown)), uns, trap]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"Model: `{r.model}`. Questions failing the bar: {r.failing}.")
    lines.append("")
    lines.append("## Unsupported numbers")
    lines.append("")
    any_unsupported = False
    for q in r.questions:
        for s in q.unsupported:
            any_unsupported = True
            lines.append(f"- {q.id}, line {s.line}: `{s.text}` in: {s.context}")
    if not any_unsupported:
        lines.append("None.")
    lines.append("")
    lines.append(
        "The scorer checks that each number comes from the reference values. "
        "It does not check whether the prose around the numbers is right."
    )
    lines.append("")
    return "\n".join(lines)


def to_json(r: ScoreResult) -> dict[str, Any]:
    return {
        "title": r.title,
        "model": r.model,
        "failing": r.failing,
        "reference": r.reference.describe() if r.reference else None,
        "questions": [
            {
                "id": q.id,
                "label": q.label,
                "passed": q.passed,
                "tools": q.tools,
                "numbers_checked": len(q.shown),
                "unsupported": [
                    {"text": s.text, "line": s.line, "context": s.context} for s in q.unsupported
                ],
                "trap": None if q.trap is None else {"passed": q.trap[0], "note": q.trap[1]},
                "has_answer": q.answer is not None,
            }
            for q in r.questions
        ],
    }


def run_score(path: Path, out: Path | None = None, transcripts: Path | None = None) -> int:
    ef = EvalFile.load(path)
    r = score(ef, transcripts)
    out_dir = out or ef.base / (ef.data.get("report") or {}).get("out", "misclosure-out")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = to_markdown(r, ef)
    (out_dir / "results.md").write_text(md, encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(to_json(r), indent=1) + "\n", "utf-8")
    print(md.split("\n## Unsupported numbers")[0])
    print(f"reports in {out_dir}")
    max_failing = int((ef.data.get("pass_bar") or {}).get("max_failing_questions", 0))
    return 0 if r.failing <= max_failing else 1
