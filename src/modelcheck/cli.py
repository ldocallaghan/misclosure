"""Command line: `modelcheck conform`, `modelcheck score`, `modelcheck transcript`."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

import anyio

from modelcheck import __version__


def _conform(args: argparse.Namespace) -> int:
    from modelcheck.client import ServerSpec
    from modelcheck.conform import report
    from modelcheck.conform.runner import CaseFile, Runner

    cf = CaseFile.load(args.cases)
    server = None
    if args.server or args.url:
        server = ServerSpec(
            command=shlex.split(args.server) if args.server else None,
            url=args.url,
            mode=args.mode or "auto",
        )
    elif args.mode:
        spec = dict(cf.data.get("server") or {})
        spec["mode"] = args.mode
        cf.data["server"] = spec
    errlog = open(args.server_log, "w", encoding="utf-8") if args.server_log else None  # noqa: SIM115
    try:
        runner = Runner(
            cf,
            server=server,
            workdir=Path(args.workdir) if args.workdir else None,
            reference_override=Path(args.reference) if args.reference else None,
            only=args.case or None,
            errlog=errlog,
        )
        result = anyio.run(runner.run)
    finally:
        if errlog:
            errlog.close()
    paths = report.write(result, Path(args.out), Path(args.junit) if args.junit else None)
    t = result.totals()
    print(
        f"{'PASS' if result.passed else 'FAIL'}: {len(result.cases)} cases, "
        f"{t.values} values compared ({t.table_cells} table cells, {t.scalars} scalars, "
        f"{t.other} other checks), "
        f"{sum(len(c.failures) for c in result.cases)} failures"
    )
    version = result.negotiation.get("sdk_protocol_version")
    print(f"protocol {version}; reports in {paths['json'].parent}")
    return 0 if result.passed else 1


def _score(args: argparse.Namespace) -> int:
    from modelcheck.score.scorer import run_score

    return run_score(
        Path(args.eval),
        out=Path(args.out) if args.out else None,
        transcripts=Path(args.transcripts) if args.transcripts else None,
    )


def _transcript(args: argparse.Namespace) -> int:
    from modelcheck.score.transcript import convert

    convert(Path(args.input), Path(args.output), args.source_format, strip_prefix=args.strip_prefix)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="modelcheck", description=__doc__)
    p.add_argument("--version", action="version", version=f"modelcheck {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("conform", help="compare a server's output with reference values")
    c.add_argument("cases", help="case file (YAML or JSON)")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--server", help="command that starts the server on stdio")
    g.add_argument("--url", help="URL of a streamable HTTP server")
    c.add_argument("--mode", help='protocol negotiation: "auto" (default), "legacy" or a version')
    c.add_argument(
        "--reference", help="use this recorded reference file in place of the declared one"
    )
    c.add_argument("--case", action="append", help="run only this case; repeatable")
    c.add_argument("--out", default="modelcheck-out", help="report directory")
    c.add_argument("--junit", help="path of the JUnit XML file")
    c.add_argument("--workdir", help="keep working files here")
    c.add_argument("--server-log", help="write the server's stderr to this file")
    c.set_defaults(func=_conform)

    s = sub.add_parser("score", help="score answers against reference values")
    s.add_argument("eval", help="evaluation file (YAML or JSON)")
    s.add_argument("--transcripts", help="transcript directory, in place of the declared one")
    s.add_argument("--out", help="report directory (default: beside the evaluation file)")
    s.set_defaults(func=_score)

    t = sub.add_parser("transcript", help="convert a transcript to the modelcheck format")
    t.add_argument("input")
    t.add_argument("output")
    t.add_argument("--from", dest="source_format", default="claude-stream-json")
    t.add_argument("--strip-prefix", default="mcp__", help="tool-name prefix to remove")
    t.set_defaults(func=_transcript)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
