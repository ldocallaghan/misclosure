"""The proof against kite-mcp. Runs only when MISCLOSURE_KITE=1.

Needs KITE_MCP_DIR (a kite-mcp checkout with its evaluation files),
KITE_MCP_BIN (a built kite-mcp binary), and R with KITE at commit 5995dd1.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from misclosure.conform import report
from misclosure.conform.runner import CaseFile, Runner
from misclosure.score.scorer import EvalFile, score, to_markdown

ROOT = Path(__file__).resolve().parent.parent
KITE = ROOT / "examples" / "kite"

pytestmark = pytest.mark.skipif(
    os.environ.get("MISCLOSURE_KITE") != "1",
    reason="set MISCLOSURE_KITE=1, KITE_MCP_DIR and KITE_MCP_BIN to run",
)

sys.path.insert(0, str(KITE))
from compare_results import compare  # noqa: E402


def test_conformance_reproduces_the_kite_mcp_count(tmp_path):
    r = anyio.run(Runner(CaseFile.load(KITE / "cases.yaml"), workdir=tmp_path).run)
    assert r.passed, report.to_markdown(r)
    t = r.totals()
    # tests/conformance.rs in kite-mcp compares 17,394 cells through get_table
    assert t.cells_by_call["get_table"] == 17394
    assert r.client_checked > 0

    # misclosure's R encoder writes the same bits as kite-mcp's own reference script
    theirs = tmp_path / "kite_mcp_reference.json"
    kite = Path(os.environ["KITE_MCP_DIR"])
    subprocess.run(
        ["Rscript", "--vanilla", str(kite / "tests/conformance/reference.R"), str(theirs)],
        check=True,
        capture_output=True,
    )
    a = json.loads((tmp_path / "reference.json").read_text())["values"]
    b = json.loads(theirs.read_text())
    assert a.keys() == b.keys()
    for case in a:
        assert a[case]["tables"] == b[case]["tables"], case
        assert a[case]["info"] == b[case]["info"], case
        assert a[case]["warnings"] == b[case]["warnings"], case


@pytest.mark.parametrize(
    ("eval_file", "results"),
    [("eval.yaml", "eval/results.md"), ("eval-heldout.yaml", "eval/heldout/results.md")],
)
def test_scores_reproduce_kite_mcp_results(tmp_path, eval_file, results):
    ef = EvalFile.load(KITE / eval_file)
    ours = tmp_path / "results.md"
    ours.write_text(to_markdown(score(ef), ef))
    diffs = compare(Path(os.environ["KITE_MCP_DIR"]) / results, ours)
    assert diffs == [], "\n".join(diffs)
