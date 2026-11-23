"""Selectors: paths into JSON trees, and `${...}` templates.

A selector is a dotted path. Each part is a key, and may be followed by
`[n]` for a list index or `[*]` for every element of a list. A key that
holds a dot or a bracket is written in quotes inside brackets: `tables["a.b"]`.

    info.criterion
    rows[*].unconverged
    tables.welfare_change
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from modelcheck.jsonlit import Num, plain


class SelectorError(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


WILDCARD = object()
_TOKEN = re.compile(r'\[\s*(\*|-?\d+|"(?:[^"\\]|\\.)*")\s*\]|([^.\[\]]+)|(\.)')


def parse(path: str) -> list[Any]:
    parts: list[Any] = []
    pos = 0
    expect_key = True
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"cannot read selector {path!r} at character {pos + 1}")
        bracket, key, dot = m.groups()
        if dot:
            if expect_key:
                raise ValueError(f"empty part in selector {path!r}")
            expect_key = True
        elif key is not None:
            if not expect_key:
                raise ValueError(f"missing dot before {key!r} in selector {path!r}")
            parts.append(key.strip())
            expect_key = False
        else:
            if bracket == "*":
                parts.append(WILDCARD)
            elif bracket.startswith('"'):
                parts.append(bracket[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
            else:
                parts.append(int(bracket))
            expect_key = False
        pos = m.end()
    if expect_key and parts:
        raise ValueError(f"selector {path!r} ends with a dot")
    return parts


def render(parts: list[Any]) -> str:
    out = ""
    for p in parts:
        if p is WILDCARD:
            out += "[*]"
        elif isinstance(p, int):
            out += f"[{p}]"
        elif re.fullmatch(r"[^.\[\]\"]+", p):
            out += ("." if out else "") + p
        else:
            out += '["' + p.replace("\\", "\\\\").replace('"', '\\"') + '"]'
    return out


def select_all(tree: Any, path: str | list[Any]) -> list[tuple[str, Any]]:
    """Every (concrete path, value) the selector names. Missing keys raise."""
    parts = parse(path) if isinstance(path, str) else list(path)
    found: list[tuple[list[Any], Any]] = [([], tree)]
    for part in parts:
        nxt: list[tuple[list[Any], Any]] = []
        for where, node in found:
            if part is WILDCARD:
                if not isinstance(node, list):
                    raise SelectorError(f"{render(where) or 'the root'} is not a list")
                nxt.extend(([*where, i], v) for i, v in enumerate(node))
            elif isinstance(part, int):
                if not isinstance(node, list) or not -len(node) <= part < len(node):
                    raise SelectorError(f"{render([*where, part])} does not exist")
                nxt.append(([*where, part], node[part]))
            else:
                if not isinstance(node, dict) or part not in node:
                    raise SelectorError(f"{render([*where, part])} does not exist")
                nxt.append(([*where, part], node[part]))
        found = nxt
    return [(render(w), v) for w, v in found]


def select(tree: Any, path: str | list[Any]) -> Any:
    """The one value a selector without wildcards names."""
    hits = select_all(tree, path)
    if len(hits) != 1:
        raise SelectorError(f"selector {path!r} names {len(hits)} values; expected one")
    return hits[0][1]


def exists(tree: Any, path: str) -> bool:
    try:
        select_all(tree, path)
    except SelectorError:
        return False
    return True


# ---- templates ------------------------------------------------------------------

_TEMPLATE = re.compile(r"\$\{([^}]+)\}")


def _lookup(name: str, context: Mapping[str, Any]) -> Any:
    name = name.strip()
    if name.startswith("env."):
        var = name[4:]
        if var not in os.environ:
            raise SelectorError(f"environment variable {var} is not set")
        return os.environ[var]
    parts = parse(name)
    head = parts[0]
    if head not in context:
        raise SelectorError(f"${{{name}}}: no value named {head!r}")
    return select(context[head], parts[1:]) if len(parts) > 1 else context[head]


def substitute(obj: Any, context: Mapping[str, Any]) -> Any:
    """Replace `${name.path}` in every string of `obj`.

    A string that is one template and nothing else becomes the value itself,
    so `"${run.iterations}"` gives a number. Inside a longer string the value
    is written as text. `${env.NAME}` reads an environment variable.
    """
    if isinstance(obj, str):
        whole = _TEMPLATE.fullmatch(obj)
        if whole:
            return plain(_lookup(whole.group(1), context))

        def text(m: re.Match[str]) -> str:
            v = plain(_lookup(m.group(1), context))
            return v if isinstance(v, str) else str(v)

        return _TEMPLATE.sub(text, obj)
    if isinstance(obj, dict):
        return {k: substitute(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, context) for v in obj]
    return obj


__all__ = ["WILDCARD", "Num", "SelectorError", "parse", "select", "select_all", "substitute"]
