import json
import math
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from modelcheck.encoding import (
    R_NA,
    R_NA_HEX,
    decode_double,
    encode_column,
    encode_scalar,
    encode_table,
    float_to_hex,
    hex_to_float,
    is_r_na,
    label,
    write_reference,
)

ROOT = Path(__file__).resolve().parent.parent

AWKWARD = [
    "0000000000000001",  # smallest subnormal
    "000fffffffffffff",  # largest subnormal
    "0010000000000000",  # smallest normal
    "8000000000000000",  # -0.0
    "0000000000000000",  # 0.0
    "7fefffffffffffff",  # largest finite double
    "ffefffffffffffff",  # its negative
    "7ff0000000000000",  # Inf
    "fff0000000000000",  # -Inf
    "7ff00000000007a2",  # R's NA
    "7ff8000000000000",  # R's NaN
    "7ff8000000000001",  # a NaN with a payload
    "fff8000000000000",  # a NaN with the sign bit set
    "3fb999999999999a",  # 0.1
]


def test_awkward_values_round_trip():
    for h in AWKWARD:
        assert float_to_hex(hex_to_float(h)) == h
    assert float_to_hex(-0.0) == "8000000000000000"
    assert float_to_hex(5e-324) == "0000000000000001"
    assert float_to_hex(1.7976931348623157e308) == "7fefffffffffffff"
    assert float_to_hex(R_NA) == R_NA_HEX


def test_na_is_told_apart_from_other_nans():
    assert is_r_na(hex_to_float(R_NA_HEX))
    assert not is_r_na(math.nan)
    assert not is_r_na(hex_to_float("7ff8000000000001"))
    assert label(R_NA) == "NA"
    assert label(hex_to_float("7ff8000000000001")) == "NaN"
    assert label(hex_to_float("fff8000000000000")) == "NaN"
    assert label(-0.0) != label(0.0)


def test_tokens_decode():
    assert is_r_na(decode_double("NA"))
    assert math.isnan(decode_double("NaN")) and not is_r_na(decode_double("NaN"))
    assert decode_double("Inf") == math.inf
    assert decode_double("-Inf") == -math.inf
    with pytest.raises(ValueError):
        hex_to_float("3FF0000000000000")
    with pytest.raises(ValueError):
        hex_to_float("3ff")


def test_python_encoder():
    t = encode_table(
        {
            "country": ["c1", "c2", None],
            "n": [1, None, 3],
            "ok": [True, None, False],
            "value": [0.1, None, -0.0],
        }
    )
    types = [c["type"] for c in t["columns"]]
    assert types == ["string", "integer", "logical", "double"]
    assert t["columns"][3]["values"] == ["3fb999999999999a", R_NA_HEX, "8000000000000000"]
    assert encode_column("x", [1, 2], type="double")["values"] == [
        "3ff0000000000000",
        "4000000000000000",
    ]
    assert encode_scalar(22) == {"integer": 22}
    assert encode_scalar(True) == {"logical": True}
    assert encode_scalar(0.1) == {"double": "3fb999999999999a"}
    assert encode_scalar(None) is None
    with pytest.raises(ValueError):
        encode_table({"a": [1.0], "b": [1.0, 2.0]})


def python_reference(path: Path) -> dict:
    awkward = encode_table({"bits": AWKWARD, "value": [hex_to_float(h) for h in AWKWARD]})
    mixed = encode_table(
        {
            "country": ["c1", "c2", None],
            "n": [1, None, 3],
            "ok": [True, None, False],
            "value": [0.1, None, -0.0],
        }
    )
    write_reference(
        path, {"awkward": awkward, "mixed": mixed}, {"name": "encoder test", "version": "1"}
    )
    return json.loads(path.read_text())


def test_r_encoder_matches_python(tmp_path):
    if shutil.which("Rscript") is None:
        pytest.skip("Rscript not found")
    if subprocess.run(["Rscript", "-e", "library(jsonlite)"], capture_output=True).returncode:
        pytest.skip("R package jsonlite not installed")
    out = tmp_path / "r.json"
    subprocess.run(
        ["Rscript", "--vanilla", str(ROOT / "encoders/r/test_encode.R"), str(out)], check=True
    )
    assert json.loads(out.read_text()) == python_reference(tmp_path / "py.json")


def test_julia_encoder_matches_python(tmp_path):
    # MODELCHECK_JULIA may name another command, such as a container run
    command = os.environ.get("MODELCHECK_JULIA") or shutil.which("julia")
    if not command:
        pytest.skip("julia not found; set MODELCHECK_JULIA to run this test")
    out = tmp_path / "julia.json"
    script = ROOT / "encoders/julia/test_encode.jl"
    subprocess.run([*shlex.split(command), str(script), str(out)], check=True)
    assert json.loads(out.read_text()) == python_reference(tmp_path / "py.json")
