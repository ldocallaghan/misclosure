import os
import random
import struct
from typing import Any

import mcp_types as types
import pytest
from pydantic import TypeAdapter

from modelcheck import jsonlit
from modelcheck.encoding import float_to_hex
from modelcheck.jsonlit import Num
from modelcheck.selectors import SelectorError, parse, select, select_all, substitute


def test_literals_keep_their_text():
    tree = jsonlit.loads('{"a": 0.1000, "b": -0, "c": [1, 2.5e-3], "d": "x"}')
    assert tree["a"] == Num("0.1000")
    assert tree["a"].significant_digits() == 4
    assert float_to_hex(tree["b"].as_float()) == "8000000000000000"
    assert tree["c"][0].is_integer_literal and tree["c"][0].plain() == 1
    assert jsonlit.plain(tree) == {"a": 0.1, "b": 0, "c": [1, 0.0025], "d": "x"}
    assert Num("0.00648016").significant_digits() == 6
    assert Num("1.2345678901234567e-300").significant_digits() == 17


def test_sdk_parses_numbers_to_the_correctly_rounded_double():
    """The SDK's JSON parser and Python's give the same bits for every literal.

    The conformance runner relies on this when it compares the SDK's view of a
    result with its own; this test fixes the assumption for the SDK version
    installed.
    """
    rng = random.Random(7)
    adapter = TypeAdapter(Any)
    literals = ["4.9406564584124654e-324", "2.2250738585072011e-308", "1.7976931348623157e308"]
    for _ in range(20000):
        x = struct.unpack(">d", struct.pack(">Q", rng.getrandbits(64)))[0]
        if x == x and abs(x) != float("inf"):
            literals += [repr(x), f"{x:.17g}", f"{x:.25e}"]
        literals.append(f"{rng.randint(1, 999999)}e{rng.randint(-330, 310)}")
    for s in literals:
        want = float_to_hex(float(s))
        assert float_to_hex(float(adapter.validate_json(s))) == want, s
    msg = '{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"x":0.1}}}'
    parsed = types.jsonrpc_message_adapter.validate_json(msg, by_name=False)
    assert float_to_hex(parsed.result["structuredContent"]["x"]) == "3fb999999999999a"


def test_selectors():
    tree = {"info": {"criterion": 1}, "rows": [{"a": 1}, {"a": 2}], "t": {"x.y": 3}}
    assert select(tree, "info.criterion") == 1
    assert select(tree, "rows[1].a") == 2
    assert [v for _, v in select_all(tree, "rows[*].a")] == [1, 2]
    assert select(tree, 't["x.y"]') == 3
    assert parse('a.b[0][*]["c.d"]')[0:2] == ["a", "b"]
    with pytest.raises(SelectorError):
        select(tree, "info.missing")
    with pytest.raises(SelectorError):
        select(tree, "rows[*].a")  # two values where one is needed
    with pytest.raises(ValueError):
        parse("a..b")


def test_templates():
    ctx = {"run": {"run_id": "r1", "n": Num("22")}, "table": "welfare"}
    assert substitute("${run.run_id}", ctx) == "r1"
    assert substitute("${run.n}", ctx) == 22
    assert substitute({"x": ["id ${run.run_id}/${table}"]}, ctx) == {"x": ["id r1/welfare"]}
    os.environ["MODELCHECK_TEST_VAR"] = "v"
    assert substitute("${env.MODELCHECK_TEST_VAR}", ctx) == "v"
    with pytest.raises(SelectorError):
        substitute("${nothing}", ctx)
