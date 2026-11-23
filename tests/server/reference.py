"""Reference values for the growth server: the same function, called directly.

python reference.py <out.json>
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from growth_server import growth

from modelcheck.encoding import encode_scalar, encode_table, write_reference

RUNS = {
    "five_percent": (100.0, 0.05, 10),
    "awkward": (0.1, 0.07, 30),
}


def main() -> None:
    values = {}
    for name, (principal, rate, years) in RUNS.items():
        g = growth(principal, rate, years)
        values[name] = {
            "rows": encode_table(
                {"year": [r["year"] for r in g["rows"]], "value": [r["value"] for r in g["rows"]]}
            ),
            "doubling_time": encode_scalar(g["doubling_time"]),
        }
    model = {"name": "growth", "version": "1", "python": platform.python_version()}
    write_reference(sys.argv[1], values, model)


if __name__ == "__main__":
    main()
