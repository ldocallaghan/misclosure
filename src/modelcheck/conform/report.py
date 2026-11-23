"""Conformance reports: JSON, a Markdown summary and JUnit XML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from modelcheck.conform.runner import RunResult

KIND_TEXT = {
    "value": "a different value",
    "json_precision": "bits lost in JSON: the number written is the reference rounded",
    "type": "the wrong type",
    "missing": "missing",
    "extra": "extra",
    "duplicate": "duplicate key",
    "error": "tool error",
    "client": "the SDK read a number to other bits",
}


def to_json(r: RunResult) -> dict[str, Any]:
    t = r.totals()
    return {
        "case_file": r.case_file,
        "passed": r.passed,
        "server": r.server,
        "protocol": r.negotiation,
        "reference": r.reference.describe(),
        "totals": {
            "cases": len(r.cases),
            "cases_failed": sum(not c.passed for c in r.cases),
            "values_compared": t.values,
            "table_cells": t.table_cells,
            "table_cells_by_call": t.cells_by_call,
            "scalars": t.scalars,
            "other_checks": t.other,
            "within_tolerance": t.within_tolerance,
            "client_numbers_checked": r.client_checked,
            "failures": sum(len(c.failures) for c in r.cases),
            "failures_by_kind": r.failures_by_kind(),
        },
        "tolerances": [
            {
                "case": u.case,
                "field": u.field,
                "abs": u.tolerance.abs,
                "rel": u.tolerance.rel,
                "reason": u.tolerance.reason,
                "values_within_tolerance_not_exact": u.values_within,
            }
            for u in r.tolerances
        ],
        "cases": [
            {
                "id": c.id,
                "passed": c.passed,
                "calls": c.calls,
                "table_cells": c.tally.table_cells,
                "scalars": c.tally.scalars,
                "other_checks": c.tally.other,
                "error": c.error,
                "failures": [f.to_json() for f in c.failures],
            }
            for c in r.cases
        ],
    }


def to_markdown(r: RunResult, max_failures: int = 50) -> str:
    t = r.totals()
    lines = [f"# Conformance: {'pass' if r.passed else 'FAIL'}", ""]
    model = r.reference.model
    lines.append(f"Case file `{Path(r.case_file).name}`.")
    m = ", ".join(f"{k} {v}" for k, v in model.items())
    lines.append(f"Reference: {m or 'no model given'}; SHA-256 `{r.reference.sha256[:16]}...`.")
    lines.append(f"Protocol version: {r.negotiation.get('sdk_protocol_version')}.")
    lines.append("")
    lines.append("| Case | Calls | Table cells | Scalars | Other checks | Failures |")
    lines.append("|---|---|---|---|---|---|")
    for c in r.cases:
        fails = len(c.failures) + (1 if c.error else 0)
        lines.append(
            f"| {c.id} | {c.calls} | {c.tally.table_cells} | {c.tally.scalars} "
            f"| {c.tally.other} | {fails} |"
        )
    lines.append(
        f"| **total** | {sum(c.calls for c in r.cases)} | {t.table_cells} | {t.scalars} "
        f"| {t.other} | {sum(len(c.failures) + (1 if c.error else 0) for c in r.cases)} |"
    )
    lines.append("")
    lines.append(
        f"Values compared: {t.values} ({t.table_cells} table cells, {t.scalars} scalars, "
        f"{t.other} other checks). Numbers whose SDK parse was checked: {r.client_checked}."
    )
    if t.cells_by_call:
        by = ", ".join(f"{n} from `{k}`" for k, n in sorted(t.cells_by_call.items()))
        lines.append(f"Table cells by call: {by}.")
    lines.append("")
    if r.tolerances:
        lines.append("## Tolerances used")
        lines.append("")
        lines.append("| Case | Field | Tolerance | Values within it, not exact | Reason |")
        lines.append("|---|---|---|---|---|")
        for u in r.tolerances:
            lines.append(
                f"| {u.case} | `{u.field}` | {u.tolerance.describe()} | {u.values_within} "
                f"| {u.tolerance.reason} |"
            )
        lines.append("")
    else:
        lines.append("No tolerances: every double was compared by its bits.")
        lines.append("")
    by_kind = r.failures_by_kind()
    if by_kind:
        lines.append("## Failures")
        lines.append("")
        for k, n in sorted(by_kind.items()):
            lines.append(f"- {k} ({KIND_TEXT.get(k, k)}): {n}")
        lines.append("")
        shown = 0
        for c in r.cases:
            if c.error:
                lines.append(f"- **{c.id}**: {c.error}")
            for f in c.failures:
                if shown >= max_failures:
                    break
                bits = ""
                if f.output or f.reference:
                    bits = f" (output `{f.output}`, reference `{f.reference}`)"
                lines.append(f"- **{c.id}** {f.kind}: {f.where}: {f.message}{bits}")
                shown += 1
        total = sum(len(c.failures) for c in r.cases)
        if total > shown:
            lines.append(f"- ... {total - shown} more in the JSON report")
        lines.append("")
    return "\n".join(lines)


def to_junit(r: RunResult) -> str:
    name = Path(r.case_file).stem
    failed = sum(not c.passed for c in r.cases)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<testsuite name={quoteattr('modelcheck conform ' + name)} "
        f'tests="{len(r.cases)}" failures="{failed}" errors="0">',
    ]
    for c in r.cases:
        out.append(f"  <testcase classname={quoteattr(name)} name={quoteattr(c.id)}>")
        if not c.passed:
            msgs = ([c.error] if c.error else []) + [
                f"{f.kind}: {f.where}: {f.message}"
                + (f" (output {f.output}, reference {f.reference})" if f.output else "")
                for f in c.failures
            ]
            first = quoteattr((msgs[0] if msgs else "failed")[:500])
            body = escape("\n".join(msgs))
            out.append(f"    <failure message={first}>{body}</failure>")
        out.append("  </testcase>")
    out.append("</testsuite>")
    return "\n".join(out) + "\n"


def write(r: RunResult, out_dir: Path, junit: Path | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "conformance.json",
        "markdown": out_dir / "conformance.md",
        "junit": junit or out_dir / "conformance-junit.xml",
    }
    paths["json"].write_text(json.dumps(to_json(r), indent=1) + "\n", encoding="utf-8")
    paths["markdown"].write_text(to_markdown(r), encoding="utf-8")
    paths["junit"].write_text(to_junit(r), encoding="utf-8")
    return paths
