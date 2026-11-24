"""The conformance runner against the growth server, over stdio and HTTP."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest

from misclosure.client import ServerSpec
from misclosure.conform import report
from misclosure.conform.runner import CaseFile, Runner

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "examples/testserver/cases.yaml"
FAULTS = ROOT / "tests/fixtures/faults.yaml"
SERVER = ROOT / "tests/server/growth_server.py"


def run(path, **kw):
    return anyio.run(Runner(CaseFile.load(path), **kw).run)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_correct_tool_passes_over_stdio(mode):
    server = ServerSpec(command=[sys.executable, str(SERVER)], mode=mode)
    r = run(CASES, server=server)
    assert r.passed, report.to_markdown(r)
    t = r.totals()
    assert t.table_cells == 84 and t.scalars == 2
    assert r.client_checked > 0
    want = "2026-07-28" if mode == "auto" else "2025-11-25"
    assert r.negotiation["sdk_protocol_version"] == want


def test_one_ulp_is_caught():
    r = run(FAULTS, only=["one_ulp"])
    assert not r.passed
    (case,) = r.cases
    (f,) = case.failures
    assert f.kind == "value"
    assert "year=3" in f.where
    # the two bit patterns differ in the last bit only
    assert int(f.output, 16) - int(f.reference, 16) == 1


def test_a_declared_tolerance_passes_it_and_is_reported():
    r = run(FAULTS, only=["one_ulp_tolerated"])
    assert r.passed
    (u,) = r.tolerances
    assert u.tolerance.rel == 1e-15 and u.values_within == 1
    md = report.to_markdown(r)
    assert "test of a declared tolerance" in md


def test_bits_lost_in_json_are_reported_as_such():
    r = run(FAULTS, only=["short_json"])
    kinds = {f.kind for c in r.cases for f in c.failures}
    assert kinds == {"json_precision"}
    junit = report.to_junit(r)
    assert 'failures="1"' in junit and "json_precision" in junit


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_streamable_http():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        r = run(CASES, server=ServerSpec(url=f"http://127.0.0.1:{port}/mcp"))
        assert r.passed, report.to_markdown(r)
        assert r.totals().table_cells == 84
        assert r.client_checked > 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)
