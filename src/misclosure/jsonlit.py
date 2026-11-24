"""JSON that keeps the text of every number.

The conformance runner parses each message a server writes with Python's
`json` module, which rounds decimal literals to the nearest double, and keeps
the literal text beside the value. The text tells the runner how many digits
the server wrote, which it needs to report a server that loses bits in JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Num:
    """A JSON number and the text the server wrote for it."""

    text: str

    @property
    def is_integer_literal(self) -> bool:
        return not any(c in self.text for c in ".eEaInN")

    def as_float(self) -> float:
        """The double the literal denotes, correctly rounded; "-0" is -0.0."""
        if self.is_integer_literal:
            n = int(self.text)
            if n == 0 and self.text.startswith("-"):
                return -0.0
            return float(n)
        return float(self.text)

    def plain(self) -> int | float:
        return int(self.text) if self.is_integer_literal else float(self.text)

    def significant_digits(self) -> int:
        """Digits in the mantissa, without leading zeros."""
        mantissa = self.text.lstrip("+-").lower().split("e")[0]
        digits = mantissa.replace(".", "").lstrip("0")
        if "." in mantissa and digits:
            # trailing zeros after the point are digits the server wrote
            return len(digits)
        return len(digits.rstrip("0")) or 1


def loads(text: str) -> Any:
    """Parse JSON, with every number as a `Num`."""
    return json.loads(text, parse_float=Num, parse_int=Num, parse_constant=Num)


def plain(tree: Any) -> Any:
    """The same tree with each `Num` replaced by an int or a float."""
    if isinstance(tree, Num):
        return tree.plain()
    if isinstance(tree, dict):
        return {k: plain(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [plain(v) for v in tree]
    return tree
