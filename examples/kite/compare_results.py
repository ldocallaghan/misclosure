"""Compare a modelcheck results table with kite-mcp's results.md, row by row.

    python compare_results.py $KITE_MCP_DIR/eval/results.md modelcheck-out/eval/results.md

Compares every column: question, covers, tools called, numbers checked,
unsupported numbers and trap outcome. Exits with status 1 on any difference.
"""

from __future__ import annotations

import sys
from pathlib import Path


def rows(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("# Answers"):
            break
        if not line.startswith("| ") or line.startswith("| Question") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        out[cells[0]] = cells
    return out


def compare(kite: Path, ours: Path) -> list[str]:
    a, b = rows(kite), rows(ours)
    diffs = []
    for q in sorted(set(a) | set(b)):
        if q not in a or q not in b:
            diffs.append(f"{q}: only in {'kite-mcp' if q in a else 'modelcheck'}")
        elif a[q] != b[q]:
            diffs.append(f"{q}:\n  kite-mcp   {a[q]}\n  modelcheck {b[q]}")
    return diffs


def main() -> int:
    diffs = compare(Path(sys.argv[1]), Path(sys.argv[2]))
    n = len(rows(Path(sys.argv[1])))
    if diffs:
        print("\n".join(diffs))
        return 1
    print(f"{n} rows identical in every column")
    return 0


if __name__ == "__main__":
    sys.exit(main())
