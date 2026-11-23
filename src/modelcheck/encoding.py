"""Reference number encoding.

A double is written as the 16 lower-case hexadecimal digits of its big-endian
IEEE 754 bit pattern, so a reference file carries every bit of the value.
A reference may also write a double as one of the tokens "NA", "NaN", "Inf"
or "-Inf". "NA" is R's missing value: a NaN whose low 32 bits are 1954.

This module also holds the Python encoder: functions that write values,
columns, tables and reference files in this encoding.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

R_NA_LOW_WORD = 1954
R_NA_HEX = "7ff00000000007a2"
TOKENS = ("NA", "NaN", "Inf", "-Inf")
SCALAR_TYPES = ("double", "integer", "logical", "string")
REFERENCE_FORMAT = 1

_HEX = re.compile(r"^[0-9a-f]{16}$")


def float_to_hex(x: float) -> str:
    """The 16 hexadecimal digits of the bit pattern of `x`."""
    return struct.pack(">d", x).hex()


def hex_to_float(s: str) -> float:
    """The double whose bit pattern is the 16 hexadecimal digits `s`."""
    if not isinstance(s, str) or not _HEX.match(s):
        raise ValueError(f"{s!r} is not a double: expected 16 lower-case hexadecimal digits")
    return struct.unpack(">d", bytes.fromhex(s))[0]


def float_bits(x: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def is_r_na(x: float) -> bool:
    """True for R's NA_real_: a NaN whose low 32 bits are 1954."""
    return math.isnan(x) and (float_bits(x) & 0xFFFFFFFF) == R_NA_LOW_WORD


R_NA = hex_to_float(R_NA_HEX)


def decode_double(v: str) -> float:
    """A reference double: 16 hexadecimal digits or a token."""
    if v == "NA":
        return R_NA
    if v == "NaN":
        return math.nan
    if v == "Inf":
        return math.inf
    if v == "-Inf":
        return -math.inf
    return hex_to_float(v)


def label(x: float) -> str:
    """How two doubles are compared: NA and other NaNs by class, the rest by bits.

    JSON has no NaN, so a server writes NaNs as tokens and cannot carry a
    payload other than the distinction between NA and NaN.
    """
    if is_r_na(x):
        return "NA"
    if math.isnan(x):
        return "NaN"
    return float_to_hex(x)


# ---- Python encoder -------------------------------------------------------------


def encode_double(x: float) -> str:
    return float_to_hex(float(x))


def _column_type(values: Sequence[Any]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "double"
    if all(isinstance(v, bool) for v in present):
        return "logical"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "integer"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "double"
    if all(isinstance(v, str) for v in present):
        return "string"
    raise TypeError("a column must hold one type: doubles, integers, logicals or strings")


def encode_column(name: str, values: Sequence[Any], type: str | None = None) -> dict[str, Any]:
    """One column. Doubles become hexadecimal; None is a missing value.

    A missing double is written as R's NA. Give `type` to force a column of
    integers to be read as doubles.
    """
    t = type or _column_type(values)
    if t not in SCALAR_TYPES:
        raise ValueError(f"unknown column type {t!r}")
    if t == "double":
        enc: list[Any] = [R_NA_HEX if v is None else encode_double(v) for v in values]
    else:
        enc = list(values)
    return {"name": name, "type": t, "values": enc}


def encode_table(
    columns: Mapping[str, Sequence[Any]], types: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """A table from a mapping of column name to values, in the mapping's order."""
    lengths = {len(v) for v in columns.values()}
    if len(lengths) > 1:
        raise ValueError("every column of a table must have the same length")
    types = types or {}
    return {"columns": [encode_column(n, v, types.get(n)) for n, v in columns.items()]}


def encode_scalar(x: Any) -> dict[str, Any] | None:
    """One value: {"double": hex}, {"integer": n}, {"logical": b} or {"string": s}."""
    if x is None:
        return None
    if isinstance(x, bool):
        return {"logical": x}
    if isinstance(x, int):
        return {"integer": x}
    if isinstance(x, float):
        return {"double": encode_double(x)}
    if isinstance(x, str):
        return {"string": x}
    raise TypeError(f"cannot encode {type(x).__name__} as a scalar")


def reference_document(values: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    """A reference file: the format number, the model it came from, and the values."""
    return {"modelcheck_reference": REFERENCE_FORMAT, "model": dict(model), "values": dict(values)}


def write_reference(path: str | Path, values: Mapping[str, Any], model: Mapping[str, Any]) -> str:
    """Write a reference file and return the SHA-256 of its bytes."""
    text = json.dumps(reference_document(values, model), indent=1, ensure_ascii=False) + "\n"
    data = text.encode("utf-8")
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# ---- reading typed nodes ---------------------------------------------------------


def is_scalar_node(node: Any) -> bool:
    return isinstance(node, dict) and len(node) == 1 and next(iter(node)) in SCALAR_TYPES


def is_table_node(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and set(node) == {"columns"}
        and isinstance(node["columns"], list)
        and all(
            isinstance(c, dict) and {"name", "type", "values"} <= set(c) for c in node["columns"]
        )
    )
