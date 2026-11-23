"""Numbers in an answer, with the rounding each one shows.

Reads signed numbers (with "-", "+" or the Unicode minus), thousands
separators, decimals, scientific notation and percentages. It skips:

- fenced code blocks: of every language, or of the languages listed;
- list numbering at the start of a line, such as "1. " or "### 2. ";
- numbers that are part of an identifier or a path: c1, s2, model_2015,
  r1a2b3, v1.2, 10x is read (x is a multiplier), 3rd is not.

In a range written with a hyphen between two decimals, such as 0.5-0.8, the
second number is read without a sign. Ranges with an en dash or "to" need no
special case.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MINUS_SIGN = chr(0x2212)
_NUMBER = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s?%)?")
_LIST_MARKER = re.compile(r"^\s*(?:#+\s*)?\d+\.\s")
_FENCE = re.compile(r"^\s*(```+|~~~+)(.*)$")


@dataclass(frozen=True)
class Shown:
    text: str
    value: float
    tolerance: float
    """Half a unit in the last place shown."""
    significant: int
    line: int
    context: str

    @property
    def is_percent(self) -> bool:
        return self.text.endswith("%")


def remove_code(text: str, languages: Sequence[str] | None) -> str:
    """Drop fenced code blocks: all of them when `languages` is None."""
    keep: list[str] = []
    fence: str | None = None
    skipping = False
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * 3
            lang = m.group(2).strip().split(" ")[0].lower() if m.group(2).strip() else ""
            skipping = languages is None or lang in {x.lower() for x in languages}
            keep.append("" if skipping else line)
            continue
        if fence is not None and m and m.group(1).startswith(fence) and not m.group(2).strip():
            keep.append("" if skipping else line)
            fence, skipping = None, False
            continue
        keep.append("" if skipping else line)
    return "\n".join(keep)


def numbers(
    text: str, skip_code: Sequence[str] | None = None, hyphen_ranges: bool = True
) -> list[Shown]:
    """Every number shown in `text`.

    `skip_code` lists the fence languages whose blocks are not read; None
    skips every fenced block.
    """
    text = remove_code(text, skip_code).replace(MINUS_SIGN, "-")
    out: list[Shown] = []
    for ln, line in enumerate(text.split("\n"), 1):
        marker = _LIST_MARKER.match(line)
        skip_until = marker.end() if marker else 0
        for m in _NUMBER.finditer(line):
            if m.start() < skip_until:
                continue
            raw = m.group(0).strip()
            start = m.start()
            before = line[start - 1] if start > 0 else ""
            after = line[m.end()] if m.end() < len(line) else ""
            if (
                hyphen_ranges
                and raw.startswith("-")
                and before.isdigit()
                and "." in raw
                and _ends_with_decimal(line[:start])
            ):
                raw, start = raw[1:], start + 1
                before = "-"
            elif before and (before.isalnum() or before in "_./@"):
                continue
            if after and ((after.isalpha() and after != "x") or after == "_"):
                continue
            num = raw.rstrip("%").strip().replace(",", "")
            try:
                value = float(num)
            except ValueError:
                continue
            mantissa, _, exp_s = num.lower().partition("e")
            exp = int(exp_s) if exp_s else 0
            decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
            digits = "".join(c for c in mantissa if c.isdigit())
            significant = max(len(digits.lstrip("0")), 1)
            lo, hi = max(0, start - 40), min(len(line), m.end() + 40)
            out.append(
                Shown(
                    text=raw,
                    value=value,
                    tolerance=0.5 * 10.0 ** (exp - decimals) * (1 + 1e-9),
                    significant=significant,
                    line=ln,
                    context=line[lo:hi].strip(),
                )
            )
    return out


def _ends_with_decimal(prefix: str) -> bool:
    return re.search(r"\d\.\d+$", prefix) is not None
