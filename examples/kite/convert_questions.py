"""Write kite-mcp's question files in the modelcheck question format.

    python convert_questions.py $KITE_MCP_DIR/eval/questions.json questions.yaml
    python convert_questions.py $KITE_MCP_DIR/eval/heldout/questions.json questions-heldout.yaml

kite-mcp's `differences: [a, b, table]` becomes a comparison of the `value`
column, and its trap kinds become the rules in kite_traps.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

TRAPS = {
    "trap_not_converged": "kite_not_converged",
    "trap_unread_variable": "kite_unread_variable",
    "trap_real_world": "kite_real_world",
}


def convert(q: dict) -> dict:
    out = {
        "id": q["id"],
        "label": q["covers"],
        "question": q["question"],
        "given": q["given"],
        "cases": q["cases"],
    }
    if "differences" in q:
        a, b, table = q["differences"]
        out["comparisons"] = [{"a": a, "b": b, "table": f"tables.{table}", "column": "value"}]
    if q["kind"] in TRAPS:
        trap = {"rule": TRAPS[q["kind"]]}
        if q["kind"] == "trap_unread_variable":
            trap["variable"] = q["variable"]
            trap["also"] = ["productivity"]
        out["trap"] = trap
    elif q["kind"] != "result":
        raise ValueError(f"{q['id']}: unknown kind {q['kind']}")
    return out


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    questions = [convert(q) for q in json.loads(src.read_text(encoding="utf-8"))]
    header = (
        f"# Converted from kite-mcp's eval/{src.parent.name}/{src.name} by convert_questions.py\n"
    )
    if src.parent.name == "eval":
        header = "# Converted from kite-mcp's eval/questions.json by convert_questions.py\n"
    dst.write_text(
        header + yaml.safe_dump(questions, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
