"""Running a case file against a server."""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import yaml

from modelcheck import jsonlit
from modelcheck.client import Connection, ServerSpec
from modelcheck.conform.compare import (
    DEFAULT_TOKENS,
    Failure,
    Numbers,
    Tally,
    Tolerance,
    compare_scalar,
    compare_table,
)
from modelcheck.encoding import float_to_hex, is_scalar_node
from modelcheck.reference import Reference
from modelcheck.reference import load as load_reference
from modelcheck.selectors import SelectorError, select, select_all, substitute

CASE_FORMAT = 1
CHECKS = ("equals", "absent", "present", "value", "table", "same_keys", "json", "contains")


class CaseFileError(Exception):
    pass


def read_structured_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


@dataclass
class CaseFile:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> CaseFile:
        p = Path(path).resolve()
        data = read_structured_file(p)
        if not isinstance(data, dict):
            raise CaseFileError(f"{p}: expected a mapping at the top level")
        if data.get("modelcheck") != CASE_FORMAT:
            raise CaseFileError(f"{p}: set `modelcheck: {CASE_FORMAT}` at the top")
        if "reference" not in data:
            raise CaseFileError(f"{p}: no `reference`")
        if not isinstance(data.get("cases"), list) or not data["cases"]:
            raise CaseFileError(f"{p}: no `cases`")
        ids = [c.get("id") for c in data["cases"]]
        if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise CaseFileError(f"{p}: every case needs a unique string `id`")
        for c in data["cases"]:
            for s in c.get("steps", []):
                _validate_step(s, f"{p.name}: case {c['id']}")
        for s in data.get("setup", []):
            _validate_step(s, f"{p.name}: setup")
        return cls(p, data)

    @property
    def base(self) -> Path:
        return self.path.parent

    def numbers(self) -> Numbers:
        spec = self.data.get("numbers") or {}
        tokens = dict(DEFAULT_TOKENS)
        tokens.update(spec.get("tokens") or {})
        return Numbers(tokens=tokens, null=spec.get("null"))


def flatten_checks(checks: Any) -> list[Any]:
    """Checks as a flat list: nested lists, as YAML aliases make, are spliced in."""
    out: list[Any] = []
    for c in checks or []:
        if isinstance(c, list):
            out.extend(flatten_checks(c))
        else:
            out.append(c)
    return out


def _validate_step(step: Any, where: str) -> None:
    if not isinstance(step, dict) or ("call" in step) == ("read" in step):
        raise CaseFileError(f"{where}: each step needs one of `call` or `read`")
    for check in flatten_checks(step.get("checks")):
        if not isinstance(check, dict) or len(check) != 1 or next(iter(check)) not in CHECKS:
            raise CaseFileError(f"{where}: a check is one of {', '.join(CHECKS)}: {check!r}")


@dataclass
class ToleranceUse:
    case: str
    where: str
    field: str
    tolerance: Tolerance
    values_within: int = 0


@dataclass
class CaseResult:
    id: str
    failures: list[Failure] = field(default_factory=list)
    tally: Tally = field(default_factory=Tally)
    calls: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.failures and self.error is None


@dataclass
class RunResult:
    case_file: str
    reference: Reference
    negotiation: dict[str, Any]
    cases: list[CaseResult]
    tolerances: list[ToleranceUse]
    client_checked: int
    server: dict[str, Any]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def totals(self) -> Tally:
        t = Tally()
        for c in self.cases:
            t.add(c.tally)
        return t

    def failures_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.cases:
            for f in c.failures:
                out[f.kind] = out.get(f.kind, 0) + 1
            if c.error:
                out["error"] = out.get("error", 0) + 1
        return out


# ---- the run ----------------------------------------------------------------------


def _compare_parses(literal: Any, sdk: Any, where: str, out: list[Failure]) -> int:
    """Check the SDK's parse of each number against Python's correctly rounded one."""
    if isinstance(literal, jsonlit.Num):
        mine = literal.as_float()
        if isinstance(sdk, bool) or not isinstance(sdk, (int, float)):
            out.append(Failure("client", where, f"the SDK read {literal.text} as {sdk!r}"))
            return 1
        theirs = float(sdk)
        if literal.is_integer_literal and int(literal.text) != sdk:
            out.append(Failure("client", where, f"the SDK read {literal.text} as {sdk!r}"))
        elif not literal.is_integer_literal and float_to_hex(mine) != float_to_hex(theirs):
            out.append(
                Failure(
                    "client",
                    where,
                    f"the SDK read {literal.text} to other bits",
                    output=float_to_hex(theirs),
                    reference=float_to_hex(mine),
                )
            )
        return 1
    n = 0
    if isinstance(literal, dict) and isinstance(sdk, dict):
        for k, v in literal.items():
            n += _compare_parses(v, sdk.get(k), f"{where}.{k}", out)
    elif isinstance(literal, list) and isinstance(sdk, list):
        for i, (a, b) in enumerate(zip(literal, sdk, strict=False)):
            n += _compare_parses(a, b, f"{where}[{i}]", out)
    return n


class Runner:
    def __init__(
        self,
        case_file: CaseFile,
        server: ServerSpec | None = None,
        workdir: Path | None = None,
        reference_override: Path | None = None,
        only: list[str] | None = None,
        errlog: TextIO | None = None,
    ) -> None:
        self.cf = case_file
        self.server_override = server
        self.workdir = workdir
        self.reference_override = reference_override
        self.only = only
        self.errlog = errlog
        self.numbers = case_file.numbers()
        self.tolerances: list[ToleranceUse] = []
        self.client_checked = 0

    def _context(self, workdir: Path) -> dict[str, Any]:
        return {"workdir": str(workdir), "case_dir": str(self.cf.base), "python": sys.executable}

    def _server_spec(self, ctx: Mapping[str, Any]) -> ServerSpec:
        if self.server_override is not None:
            s = self.server_override
            command = [str(c) for c in substitute(s.command, ctx)] if s.command else None
            return ServerSpec(command=command, url=s.url, env=s.env, cwd=s.cwd, mode=s.mode)
        spec = self.cf.data.get("server")
        if not spec:
            raise CaseFileError("no server: give --server or --url, or `server` in the case file")
        command = spec.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        return ServerSpec(
            command=[str(c) for c in substitute(command, ctx)] if command else None,
            url=substitute(spec.get("url"), ctx),
            env={k: str(v) for k, v in substitute(spec.get("env", {}), ctx).items()},
            # a command in the case file runs in the case file's directory
            cwd=str(self.cf.base / substitute(spec.get("cwd", "."), ctx)),
            mode=str(spec.get("mode", "auto")),
        )

    async def run(self) -> RunResult:
        if self.workdir is not None:
            self.workdir.mkdir(parents=True, exist_ok=True)
            return await self._run(self.workdir)
        with tempfile.TemporaryDirectory(prefix="modelcheck-") as tmp:
            return await self._run(Path(tmp))

    async def _run(self, workdir: Path) -> RunResult:
        ctx = self._context(workdir)
        if self.reference_override is not None:
            declared = {
                "file": str(self.reference_override.resolve()),
                "model": self.cf.data["reference"].get("model", {}),
            }
            reference = load_reference(declared, ctx, Path("/"))
        else:
            reference = load_reference(self.cf.data["reference"], ctx, self.cf.base)
        spec = self._server_spec(ctx)
        results: list[CaseResult] = []
        async with Connection(spec, errlog=self.errlog) as conn:
            negotiation = conn.negotiation()
            saved: dict[str, Any] = {}
            setup = CaseResult("setup")
            for i, step in enumerate(self.cf.data.get("setup", [])):
                await self._step(conn, step, f"setup step {i + 1}", setup, saved, reference, ctx)
            if setup.failures or setup.error:
                results.append(setup)
            for case in self.cf.data["cases"]:
                if self.only and case["id"] not in self.only:
                    continue
                cr = CaseResult(case["id"])
                local = {**saved, "case": case["id"], **(case.get("vars") or {})}
                try:
                    for i, step in enumerate(case.get("steps", [])):
                        await self._step(conn, step, f"step {i + 1}", cr, local, reference, ctx)
                except (SelectorError, ValueError) as exc:
                    cr.error = str(exc)
                results.append(cr)
        return RunResult(
            case_file=str(self.cf.path),
            reference=reference,
            negotiation=negotiation,
            cases=results,
            tolerances=self.tolerance_summary(results),
            client_checked=self.client_checked,
            server={"command": spec.command, "url": spec.url, "mode": spec.mode},
        )

    async def _step(
        self,
        conn: Connection,
        step: Mapping[str, Any],
        label: str,
        cr: CaseResult,
        saved: dict[str, Any],
        reference: Reference,
        ctx: Mapping[str, Any],
    ) -> None:
        each = step.get("each")
        if not each:
            await self._one(conn, step, label, cr, saved, reference, {**ctx, **saved})
            return
        scope = {**ctx, **saved, "reference": reference.values}
        name = each.get("as", "item")
        if "keys_of" in each:
            items = list(select(reference.values, substitute(each["keys_of"], scope)).keys())
        else:
            items = list(substitute(each.get("values", []), scope))
        for item in items:
            await self._one(
                conn,
                step,
                f"{label} {name}={item}",
                cr,
                saved,
                reference,
                {**ctx, **saved, name: item},
            )

    async def _one(
        self,
        conn: Connection,
        step: Mapping[str, Any],
        label: str,
        cr: CaseResult,
        saved: dict[str, Any],
        reference: Reference,
        scope: Mapping[str, Any],
    ) -> None:
        scope = {**scope, "reference": reference.values}
        cr.calls += 1
        if "call" in step:
            tool = substitute(step["call"], scope)
            args = substitute(step.get("arguments", {}), scope)
            outcome = await conn.call_tool(tool, args)
            where = f"{label} {tool}"
            if outcome.structured is not None and outcome.sdk_structured is not None:
                self.client_checked += _compare_parses(
                    outcome.structured, outcome.sdk_structured, where, cr.failures
                )
            expect_error = bool(step.get("error", False))
            if outcome.is_error != expect_error:
                msg = outcome.text[:500] if outcome.is_error else "the call succeeded"
                cr.failures.append(Failure("error", where, f"expected error={expect_error}: {msg}"))
                return
            output: Any = outcome.structured
            if output is None and outcome.text:
                try:
                    output = jsonlit.loads(outcome.text)
                except ValueError:
                    output = {"text": outcome.text}
        else:
            uri = substitute(step["read"], scope)
            where = f"{label} {uri}"
            output = {"text": await conn.read_resource(uri)}
        if "save" in step:
            saved[step["save"]] = output
        local = {**scope, "output": output, **({step["save"]: output} if "save" in step else {})}
        source = str(tool) if "call" in step else "resource"
        for check in flatten_checks(step.get("checks")):
            ((kind, spec),) = check.items()
            spec = substitute(spec, local)
            before = cr.tally.table_cells
            try:
                self._check(kind, spec, output, reference, where, cr)
            except SelectorError as exc:
                cr.failures.append(Failure("missing", where, str(exc)))
            if cr.tally.table_cells != before:
                by = cr.tally.cells_by_call
                by[source] = by.get(source, 0) + cr.tally.table_cells - before

    def _check(
        self,
        kind: str,
        spec: Any,
        output: Any,
        reference: Reference,
        where: str,
        cr: CaseResult,
    ) -> None:
        ref = reference.values
        if kind == "equals":
            for path, v in select_all(output, spec["output"]):
                cr.tally.other += 1
                if jsonlit.plain(v) != spec["value"] or isinstance(v, bool) != isinstance(
                    spec["value"], bool
                ):
                    cr.failures.append(
                        Failure(
                            "value",
                            f"{where}: {path}",
                            f"expected {spec['value']!r}, got {jsonlit.plain(v)!r}",
                        )
                    )
        elif kind in ("absent", "present"):
            path = spec if isinstance(spec, str) else spec["output"]
            cr.tally.other += 1
            try:
                select_all(output, path)
                there = True
            except SelectorError:
                there = False
            if there != (kind == "present"):
                cr.failures.append(
                    Failure(
                        "value", f"{where}: {path}", "present" if there else "absent", None, None
                    )
                )
        elif kind == "value":
            tol = self._tolerance(spec.get("tolerance"), cr.id, where, spec["output"])
            ref_node = select(ref, spec["reference"])
            try:
                out = select(output, spec["output"])
            except SelectorError:
                if ref_node is None:
                    cr.tally.scalars += 1
                    return
                raise
            if not (ref_node is None or is_scalar_node(ref_node)):
                raise ValueError(f"{where}: {spec['reference']} is not a scalar in the reference")
            f = compare_scalar(
                out, ref_node, f"{where}: {spec['output']}", self.numbers, tol, cr.tally
            )
            if f:
                cr.failures.append(f)
        elif kind == "table":
            tols = {
                col: self._tolerance(t, cr.id, where, f"{spec['output']}.{col}")
                for col, t in (spec.get("tolerance") or {}).items()
            }
            rows = select(output, spec["output"])
            keys = spec.get("keys")
            cr.failures.extend(
                compare_table(
                    rows,
                    select(ref, spec["reference"]),
                    f"{where}: {spec['output']}",
                    self.numbers,
                    None if keys in (None, "auto") else list(keys),
                    spec.get("columns"),
                    list(spec.get("ignore_columns", [])),
                    {k: v for k, v in tols.items() if v is not None},
                    cr.tally,
                    spec.get("reference_columns"),
                )
            )
        elif kind == "same_keys":
            got = jsonlit.plain(select(output, spec["output"]))
            want = select(ref, spec["reference"])
            got_set = set(got.keys() if isinstance(got, dict) else got)
            want_set = set(want.keys())
            cr.tally.other += 1
            if got_set != want_set:
                missing, extra = sorted(want_set - got_set), sorted(got_set - want_set)
                cr.failures.append(
                    Failure(
                        "value", f"{where}: {spec['output']}", f"missing {missing}, extra {extra}"
                    )
                )
        elif kind == "json":
            got = jsonlit.plain(select(output, spec["output"]))
            want = select(ref, spec["reference"])
            fields = spec.get("fields")
            if fields:

                def project(x: Any) -> Any:
                    if isinstance(x, list):
                        return [project(v) for v in x]
                    if isinstance(x, dict):
                        return {k: x.get(k) for k in fields}
                    return x

                got, want = project(got), project(want)
            cr.tally.other += 1
            if got != want:
                cr.failures.append(
                    Failure(
                        "value",
                        f"{where}: {spec['output']}",
                        f"expected {json.dumps(want)[:300]}, got {json.dumps(got)[:300]}",
                    )
                )
        elif kind == "contains":
            text = select(output, spec.get("output", "text"))
            cr.tally.other += 1
            if not isinstance(text, str) or spec["text"] not in text:
                cr.failures.append(
                    Failure(
                        "value",
                        f"{where}: {spec.get('output', 'text')}",
                        f"does not contain {spec['text']!r}",
                    )
                )

    def _tolerance(self, spec: Any, case: str, where: str, field_: str) -> Tolerance | None:
        if not spec:
            return None
        t = Tolerance.parse(spec, f"{case}: {where}: {field_}")
        self.tolerances.append(ToleranceUse(case, where, field_, t))
        return t

    def tolerance_summary(self, cases: list[CaseResult]) -> list[ToleranceUse]:
        """Each declared tolerance once per case and field, with the values it let through."""
        within: dict[int, int] = {}
        for c in cases:
            for k, v in c.tally.by_tolerance.items():
                within[k] = within.get(k, 0) + v
        merged: dict[tuple[Any, ...], ToleranceUse] = {}
        for u in self.tolerances:
            t = u.tolerance
            key = (u.case, u.field, t.abs, t.rel, t.reason)
            m = merged.setdefault(key, ToleranceUse(u.case, u.where, u.field, t))
            m.values_within += within.get(id(t), 0)
        return list(merged.values())
