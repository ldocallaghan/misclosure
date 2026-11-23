"""A small MCP server for modelcheck's own tests and quick start.

It wraps one numerical function, compound growth: the value of a principal
after each year at a fixed rate, and the doubling time. Three tools return it:

- `grow`: the values as computed;
- `grow_faulty`: the same, with the year-3 value moved up by one unit in the
  last place (1 ulp), the smallest error a double can have;
- `grow_short_json`: the values rounded to 15 significant digits, as a
  server does that writes doubles with %.15g.

    python growth_server.py              # MCP over stdio
    python growth_server.py --http 8765  # streamable HTTP on 127.0.0.1:8765/mcp
"""

from __future__ import annotations

import argparse
import math
from typing import Any

from mcp.server.mcpserver import MCPServer


def growth(principal: float, rate: float, years: int) -> dict[str, Any]:
    rows = [{"year": t, "value": principal * (1.0 + rate) ** t} for t in range(years + 1)]
    return {"rows": rows, "doubling_time": math.log(2.0) / math.log1p(rate)}


server = MCPServer("growth")


@server.tool()
def grow(principal: float, rate: float, years: int) -> dict[str, Any]:
    """Value of `principal` after each year at `rate`, and the doubling time in years."""
    return growth(principal, rate, years)


@server.tool()
def grow_faulty(principal: float, rate: float, years: int) -> dict[str, Any]:
    """As grow, with the year-3 value 1 ulp too high."""
    out = growth(principal, rate, years)
    for row in out["rows"]:
        if row["year"] == 3:
            row["value"] = math.nextafter(row["value"], math.inf)
    return out


@server.tool()
def grow_short_json(principal: float, rate: float, years: int) -> dict[str, Any]:
    """As grow, with every double rounded to 15 significant digits."""
    out = growth(principal, rate, years)
    for row in out["rows"]:
        row["value"] = float(f"{row['value']:.15g}")
    out["doubling_time"] = float(f"{out['doubling_time']:.15g}")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--http", type=int, help="serve streamable HTTP on this port")
    args = p.parse_args()
    if args.http:
        server.run("streamable-http", host="127.0.0.1", port=args.http)
    else:
        server.run()


if __name__ == "__main__":
    main()
