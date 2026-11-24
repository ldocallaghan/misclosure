"""Reference values: from a recorded file or from a command that writes one.

A reference file is JSON. In its full form it carries the model it came from:

    {"misclosure_reference": 1,
     "model": {"name": "KITE", "version": "26.10", "commit": "5995dd1..."},
     "values": {...}}

A file without the `misclosure_reference` key is read as the values alone,
and the model is then taken from the declaration in the case or evaluation
file. Either way the declaration may give the file's SHA-256, which is
checked before the values are used.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from misclosure.encoding import REFERENCE_FORMAT
from misclosure.selectors import substitute


class ReferenceError(Exception):
    pass


@dataclass
class Reference:
    values: dict[str, Any]
    model: dict[str, Any]
    sha256: str
    source: str
    command: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "command": self.command,
            "sha256": self.sha256,
            "model": self.model,
        }


def _read(path: Path, declared: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = declared.get("sha256")
    if expected and expected.lower() != digest:
        raise ReferenceError(f"{path}: SHA-256 is {digest}, the declaration gives {expected}")
    doc = json.loads(data)
    if isinstance(doc, dict) and "misclosure_reference" in doc:
        if doc["misclosure_reference"] != REFERENCE_FORMAT:
            raise ReferenceError(
                f"{path}: reference format {doc['misclosure_reference']}; "
                f"this version reads {REFERENCE_FORMAT}"
            )
        values, model = doc.get("values", {}), dict(doc.get("model") or {})
    else:
        values, model = doc, {}
    for k, v in (declared.get("model") or {}).items():
        if k in model and str(model[k]) != str(v):
            raise ReferenceError(f"{path}: model {k} is {model[k]!r}; the declaration gives {v!r}")
        model.setdefault(k, v)
    if not isinstance(values, dict):
        raise ReferenceError(f"{path}: the values must be a JSON object")
    return values, model, digest


def load(declared: Mapping[str, Any], context: Mapping[str, Any], base: Path) -> Reference:
    """Load the reference a case or evaluation file declares.

    `{"file": path, "sha256": ..., "model": {...}}` reads a recorded file.
    `{"command": [...], "output": path, "cwd": dir, "env": {...}}` runs the
    command first; `output` defaults to `${workdir}/reference.json`.
    """
    if "file" in declared:
        path = base / substitute(declared["file"], context)
        values, model, digest = _read(path, declared)
        return Reference(values, model, digest, str(declared["file"]))
    if "command" in declared:
        ctx = dict(context)
        ctx.setdefault("out", str(Path(context["workdir"]) / "reference.json"))
        out = Path(substitute(declared.get("output", "${out}"), ctx))
        ctx["out"] = str(out)
        command = [str(c) for c in substitute(list(declared["command"]), ctx)]
        cwd = base / substitute(declared["cwd"], ctx) if "cwd" in declared else base
        env = {
            **os.environ,
            **{k: str(v) for k, v in substitute(declared.get("env", {}), ctx).items()},
        }
        run = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
        if run.returncode != 0:
            raise ReferenceError(
                f"reference command failed with status {run.returncode}: {command}\n"
                f"{run.stdout[-4000:]}\n{run.stderr[-4000:]}"
            )
        values, model, digest = _read(out, declared)
        return Reference(values, model, digest, str(out), command=command)
    raise ReferenceError("a reference needs `file` or `command`")
