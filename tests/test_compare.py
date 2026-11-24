import math

import pytest

from misclosure.conform.compare import (
    Numbers,
    Tally,
    Tolerance,
    compare_double,
    compare_scalar,
    compare_table,
)
from misclosure.encoding import R_NA_HEX, encode_table, float_to_hex
from misclosure.jsonlit import Num, loads

N = Numbers()


def cmp(out, ref_hex, tol=None):
    return compare_double(out, ref_hex, "x", N, tol, Tally())


def test_doubles_compare_by_bits():
    x = 0.1 + 0.2
    assert cmp(Num(repr(x)), float_to_hex(x)) is None
    nudged = math.nextafter(x, 1.0)
    f = cmp(Num(repr(nudged)), float_to_hex(x))
    assert f is not None and f.kind == "value"
    assert f.output == float_to_hex(nudged) and f.reference == float_to_hex(x)
    # -0.0 and 0.0 differ in bits
    assert cmp(Num("0"), "8000000000000000").kind == "value"
    assert cmp(Num("-0.0"), "8000000000000000") is None


def test_non_finite_values():
    assert cmp("NA", R_NA_HEX) is None
    assert cmp("NaN", "7ff8000000000000") is None
    assert cmp("NaN", "7ff8000000000001") is None  # a payload cannot cross JSON
    assert cmp("NA", "7ff8000000000000").kind == "value"
    assert cmp("NaN", R_NA_HEX).kind == "value"
    assert cmp("Inf", "7ff0000000000000") is None
    assert cmp("-Inf", "fff0000000000000") is None
    assert cmp(None, R_NA_HEX).kind == "type"
    assert compare_double(None, R_NA_HEX, "x", Numbers(null="NA"), None, Tally()) is None
    assert cmp("1.5", float_to_hex(1.5)).kind == "type"


def test_bits_lost_in_json_are_their_own_class():
    x = 2 / 3
    f = cmp(Num(f"{x:.15g}"), float_to_hex(x))
    assert f.kind == "json_precision"
    f = cmp(Num("0.6666666666667"), float_to_hex(x))
    assert f.kind == "json_precision"
    # truncated, not rounded: a different value
    f = cmp(Num("0.6666666666666"), float_to_hex(x))
    assert f.kind == "value"
    f = cmp(Num("0.6666666666667"), float_to_hex(x + 1e-6))
    assert f.kind == "value"


def test_tolerance_needs_a_reason_and_is_counted():
    with pytest.raises(ValueError):
        Tolerance.parse({"abs": 1e-12}, "here")
    t = Tolerance.parse({"rel": 1e-15, "reason": "summation order"}, "here")
    x = 1.0
    tally = Tally()
    assert (
        compare_double(Num(repr(math.nextafter(x, 2.0))), float_to_hex(x), "x", N, t, tally) is None
    )
    assert tally.within_tolerance == 1
    assert compare_double(Num("1.001"), float_to_hex(x), "x", N, t, Tally()).kind == "value"
    # a tolerance never lets a NaN through
    assert compare_double("NaN", float_to_hex(x), "x", N, t, Tally()).kind == "value"


def test_exact_types():
    t = Tally()
    assert compare_scalar(Num("22"), {"integer": 22}, "i", N, None, t) is None
    assert compare_scalar(Num("22.0"), {"integer": 22}, "i", N, None, t).kind == "value"
    assert compare_scalar(True, {"logical": True}, "b", N, None, t) is None
    assert compare_scalar(1, {"logical": True}, "b", N, None, t).kind == "value"
    assert compare_scalar("fixed", {"string": "fixed"}, "s", N, None, t) is None
    assert compare_scalar(None, None, "s", N, None, t) is None
    assert t.scalars == 6


def ref_table():
    return encode_table({"country": ["c1", "c2", "c3"], "value": [1.05, 0.98, 1.0]})


def rows(text):
    return loads(text)


def test_tables_match_by_key_in_any_order():
    out = rows(
        '[{"country":"c3","value":1.0,"value_display":"1.000"},'
        '{"country":"c1","value":1.05},{"country":"c2","value":0.98}]'
    )
    t = Tally()
    f = compare_table(out, ref_table(), "t", N, None, None, ["*_display"], {}, t)
    assert f == [] and t.table_cells == 6


def test_table_failures():
    t = Tally()
    out = rows('[{"country":"c1","value":1.05},{"country":"c4","value":0.98,"extra":1}]')
    f = compare_table(out, ref_table(), "t", N, None, None, [], {}, t)
    kinds = sorted(x.kind for x in f)
    # c2 and c3 missing, c4 extra, column `extra` not in the reference
    assert kinds == ["extra", "extra", "missing", "missing"]
    dup = rows('[{"country":"c1","value":1.05},{"country":"c1","value":1.05}]')
    f = compare_table(dup, ref_table(), "t", N, None, None, [], {}, Tally())
    assert "duplicate" in {x.kind for x in f}


def test_empty_keys_match_rows_by_position():
    # rows without a name column, as a server writes when it drops a table's index
    out = rows(
        '[{"country":"c1","value":1.05},{"country":"c2","value":0.98},{"country":"c3","value":1.0}]'
    )
    t = Tally()
    assert compare_table(out, ref_table(), "t", N, [], None, [], {}, t) == []
    assert t.table_cells == 6
    swapped = rows(
        '[{"country":"c2","value":0.98},{"country":"c1","value":1.05},{"country":"c3","value":1.0}]'
    )
    f = compare_table(swapped, ref_table(), "t", N, [], None, [], {}, Tally())
    assert [x.where for x in f] == [
        "t[row 0].country",
        "t[row 0].value",
        "t[row 1].country",
        "t[row 1].value",
    ]


def test_table_column_mapping_and_subset():
    ref = encode_table({"country": ["c1"], "value": [1.05], "weight": [0.3]})
    out = rows('[{"country":"c1","welfare":1.05}]')
    f = compare_table(
        out, ref, "t", N, ["country"], {"welfare": "value"}, [], {}, Tally(), ["country", "value"]
    )
    assert f == []
