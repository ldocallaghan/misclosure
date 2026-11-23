"""Transcripts: the modelcheck format, and an adapter for Claude Code.

A modelcheck transcript is a JSON Lines file. Each line is one record:

    {"type": "meta", "format": "modelcheck-transcript", "version": 1,
     "model": "...", "harness": "..."}
    {"type": "tool_call", "id": "...", "name": "run_scenario", "arguments": {...}}
    {"type": "tool_result", "id": "...", "is_error": false, "text": "...",
     "structured": {...}}
    {"type": "answer", "text": "..."}

Records come in the order they happened. `meta` is first and optional; its
fields other than `type`, `format` and `version` are free. The final answer
is the last `answer` record. Tool names are given without any MCP server
prefix. Any harness can write this format; `claude-stream-json` below
converts the stream-json output of `claude -p`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BYTE_ORDER_MARK = chr(0xFEFF)
FORMAT = "modelcheck-transcript"
VERSION = 1


@dataclass
class Transcript:
    meta: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def answer(self) -> str | None:
        answers = [r["text"] for r in self.records if r.get("type") == "answer"]
        return answers[-1] if answers else None

    def tools_called(self) -> list[str]:
        return [r["name"] for r in self.records if r.get("type") == "tool_call"]

    @property
    def model(self) -> str | None:
        m = self.meta.get("model")
        return str(m) if m else None


def strip_server_prefix(name: str, prefix: str = "mcp__") -> str:
    """`mcp__kite__run_scenario` -> `run_scenario`."""
    if prefix and name.startswith(prefix):
        rest = name[len(prefix) :]
        if "__" in rest:
            return rest.split("__", 1)[1]
    return name


def from_claude_stream_json(lines: Iterable[str], strip_prefix: str = "mcp__") -> Transcript:
    """Read the stream-json output of `claude -p --output-format stream-json`.

    Also reads the sanitised form kite-mcp keeps, whose first line is a
    `meta` record holding the model name.
    """
    t = Transcript()
    for line in lines:
        line = line.strip().lstrip(BYTE_ORDER_MARK)
        if not line:
            continue
        try:
            v = json.loads(line)
        except ValueError:
            continue
        kind = v.get("type")
        if kind in ("system", "meta"):
            if kind == "meta" or v.get("subtype") == "init":
                for k in ("model", "claude_code_version"):
                    if v.get(k) and k not in t.meta:
                        t.meta[k] = v[k]
                t.meta.setdefault("harness", "claude-code")
        elif kind == "assistant":
            for c in (v.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    t.records.append(
                        {
                            "type": "tool_call",
                            "id": c.get("id"),
                            "name": strip_server_prefix(c.get("name", ""), strip_prefix),
                            "arguments": c.get("input") or {},
                        }
                    )
        elif kind == "user":
            content = (v.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        text = "\n".join(b.get("text", "") for b in body if b.get("type") == "text")
                    else:
                        text = body if isinstance(body, str) else ""
                    t.records.append(
                        {
                            "type": "tool_result",
                            "id": c.get("tool_use_id"),
                            "is_error": bool(c.get("is_error", False)),
                            "text": text,
                            "structured": None,
                        }
                    )
        elif kind == "result" and isinstance(v.get("result"), str):
            t.records.append({"type": "answer", "text": v["result"]})
    return t


def read_modelcheck(lines: Iterable[str]) -> Transcript:
    t = Transcript()
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        v = json.loads(line)
        if v.get("type") == "meta":
            if v.get("format", FORMAT) != FORMAT or v.get("version", VERSION) != VERSION:
                raise ValueError(f"line {n}: not a {FORMAT} version {VERSION} record")
            t.meta.update({k: x for k, x in v.items() if k not in ("type", "format", "version")})
        elif v.get("type") in ("tool_call", "tool_result", "answer"):
            t.records.append(v)
        else:
            raise ValueError(f"line {n}: unknown record type {v.get('type')!r}")
    return t


def lines_of(t: Transcript) -> Iterator[str]:
    yield json.dumps({"type": "meta", "format": FORMAT, "version": VERSION, **t.meta})
    for r in t.records:
        yield json.dumps(r, ensure_ascii=False)


def read(path: Path, format: str = "modelcheck", strip_prefix: str = "mcp__") -> Transcript:
    with path.open(encoding="utf-8") as f:
        if format == "modelcheck":
            return read_modelcheck(f)
        if format == "claude-stream-json":
            return from_claude_stream_json(f, strip_prefix)
    raise ValueError(f"unknown transcript format {format!r}")


def convert(src: Path, dst: Path, source_format: str, strip_prefix: str = "mcp__") -> None:
    t = read(src, source_format, strip_prefix)
    dst.write_text("\n".join(lines_of(t)) + "\n", encoding="utf-8")
