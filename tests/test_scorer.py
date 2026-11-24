import json
from pathlib import Path

import pytest

from misclosure.encoding import encode_table
from misclosure.score import transcript as tx
from misclosure.score.numbers import numbers, remove_code
from misclosure.score.scorer import EvalFile, score
from misclosure.score.support import Derivations, Expression, Support, comparison_values

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval"


def texts(s, **kw):
    return [x.text for x in numbers(s, **kw)]


def test_numbers_skip_identifiers_and_list_markers():
    # the case of kite-mcp's scorer test
    s = numbers(
        "1. c1 loses 14.84 % under caliendo_parro_2015; welfare_change 0.8517 after 22 "
        "iterations (9.757e-05) in r0a1b2c3d4e5"
    )
    assert [x.text for x in s] == ["14.84 %", "0.8517", "22", "9.757e-05"]
    assert abs(s[0].tolerance - 0.005) < 1e-9
    assert abs(s[3].tolerance - 0.5e-8) < 1e-12


def test_signs_separators_and_scientific_notation():
    assert texts(chr(0x2212) + "2.269 %, +3, 1,234,567.5 and 6.02e23") == [
        "-2.269 %",
        "+3",
        "1,234,567.5",
        "6.02e23",
    ]
    s = numbers("1,234,567.5")[0]
    assert s.value == 1234567.5 and s.significant == 8
    assert texts("a 10x rise, the 3rd run, v1.2, path/2015, x_3") == ["10"]


def test_ranges():
    en_dash = chr(0x2013)
    assert texts(f"between 0.5{en_dash}0.8") == ["0.5", "0.8"]
    assert texts("between 0.5-0.8") == ["0.5", "0.8"]
    assert texts("between 0.5-0.8", hyphen_ranges=False) == ["0.5"]
    assert texts("from -1.4 to -2.3") == ["-1.4", "-2.3"]
    # a date is not a range of decimals
    assert texts("on 2026-11-15") == ["2026"]


def test_code_blocks():
    text = "value 0.5\n```r\nx <- runif(3, 0.3, 1)\n```\n```text\n0.7\n```\nand 0.25"
    assert texts(text) == ["0.5", "0.25"]
    assert texts(text, skip_code=["r"]) == ["0.5", "0.7", "0.25"]
    assert "0.7" not in remove_code("~~~\n0.7\n~~~", None)


def test_rounding_decides_support():
    sup = Support([0.851_633_3, -14.836_67])
    n = numbers("0.8516 and 0.852 and 0.8517 and 0.851 and -14.84% and 14.8%")
    assert [sup.matches(s) for s in n] == [True, True, False, False, True, True]
    assert not Support([-14.836_67], ignore_sign=False).matches(numbers("14.84%")[0])


def test_expressions_are_arithmetic_only():
    assert Expression("(x - 1) * 100", ["x"])(x=1.05) == pytest.approx(5.0)
    assert Expression("b / a", ["a", "b"])(a=0.0, b=1.0) is None
    for bad in ["__import__('os')", "x.real", "y + 1", "[x]", "'a'"]:
        with pytest.raises(ValueError):
            Expression(bad, ["x"])


def test_comparisons_pair_rows_by_key():
    a = encode_table({"country": ["c1", "c2"], "value": [0.85, 0.92]})
    b = encode_table({"country": ["c2", "c1"], "value": [0.90, 0.84]})
    d = Derivations.parse({"comparisons": [{"tables": ["*"], "derive": {"r": "b / a"}}]})
    got = comparison_values(a, b, d.for_table("t"))
    assert got == pytest.approx([0.84 / 0.85, 0.90 / 0.92])
    with pytest.raises(ValueError):
        d.for_table("t", ["difference"])


def test_claude_stream_json_adapter():
    t = tx.read(FIXTURES / "claude" / "g01.jsonl", "claude-stream-json")
    assert t.model == "fixture"
    assert t.tools_called() == ["grow"]
    mine = tx.read(FIXTURES / "transcripts" / "g01.jsonl")
    assert t.answer == mine.answer
    out = FIXTURES.parent / "_converted.jsonl"
    try:
        tx.convert(FIXTURES / "claude" / "g01.jsonl", out, "claude-stream-json")
        back = tx.read(out)
        assert back.answer == mine.answer and back.tools_called() == ["grow"]
        assert json.loads(out.read_text().split("\n")[0])["format"] == "misclosure-transcript"
    finally:
        out.unlink(missing_ok=True)


def test_an_invented_number_is_flagged():
    r = score(EvalFile.load(FIXTURES / "eval.yaml"))
    by_id = {q.id: q for q in r.questions}
    g01 = by_id["g01"]
    assert [s.text for s in g01.unsupported] == ["141.7"]
    assert len(g01.shown) == 7  # the number in the python block is not read
    assert not g01.passed
    assert by_id["g02"].trap == (True, "all patterns as required") and by_id["g02"].passed
    assert by_id["g03"].passed  # 104.9 is a declared comparison: b - a
    assert r.failing == 1


def test_transcripts_from_claude_code_score_the_same(tmp_path):
    ef = EvalFile.load(FIXTURES / "eval.yaml")
    ef.data["transcripts"] = {"dir": str(FIXTURES / "claude"), "format": "claude-stream-json"}
    r = score(ef)
    g01 = next(q for q in r.questions if q.id == "g01")
    assert [s.text for s in g01.unsupported] == ["141.7"]
    # g02 and g03 have no Claude Code transcript here
    assert sum(q.answer is None for q in r.questions) == 2
