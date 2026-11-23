# Design notes

## Two checks against one reference

Both checks start from reference values: the results of the model called directly, with no server code in the path, written in a format that keeps every bit. Conformance asks whether the server returns those values. The answer evaluation asks whether a language model, given the server, quotes only those values and numbers derived from them in declared ways. The first check is about the server; the second is about the server, its tool descriptions and the model together.

## Why the default is exact bits

A server that wraps a numerical model should return the model's numbers. When it does, the comparison with a direct run is equality of bit patterns, and any other result is a fault somewhere: in the server's call of the model, in its encoding, in the JSON it writes or in the client. A tolerance would hide faults of the size it allows, and the size of a fault says little about its cause. So a tolerance is an exception a case must declare, with a reason, and the report lists every one with the number of values it let through.

There are honest reasons for a tolerance: a server that computes in a different order from the reference, or on other hardware. modelcheck accepts them when declared. It does not accept a tolerance without a reason.

## Comparison rules

- A double compares by its 64-bit pattern. -0.0 and 0.0 differ, and so do two NaNs of different class.
- JSON has no NaN or infinity. A server writes them as strings, and modelcheck reads `"NA"`, `"NaN"`, `"Inf"` and `"-Inf"` by default. NaNs compare by class: R's NA (a NaN whose low 32 bits are 1954) or any other NaN. JSON cannot carry a payload beyond that.
- Integers, logicals and strings compare exactly. An integer must be a JSON integer: `22.0` does not equal the integer 22.
- A table is a list of row objects. Rows match by key columns, in any order; by default the keys are the reference columns that are not doubles. A missing row, an extra row, a duplicate key, a missing column and an unexpected column each fail. Columns that hold the server's own additions, such as display strings, are listed in `ignore_columns`.
- A tolerance applies only to finite doubles. It never lets a NaN or an infinity through.

## Bits lost in JSON

A server can compute the right double and write too few digits of it. To tell this apart from a wrong value, modelcheck keeps the text of every number the server wrote. When a double differs from the reference, the runner rounds the reference to the number of significant digits in the server's text. If that gives the server's number, the failure is `json_precision`: the value was right and the JSON lost bits. Seventeen significant digits are always enough, so a literal with 17 or more digits is never put in this class.

## Reading numbers as the server wrote them

The MCP Python SDK parses each message with pydantic. modelcheck uses the SDK's `Client` for the protocol, and its own transports for stdio and streamable HTTP, which pass every message through unchanged and keep a copy of the text. The runner parses that copy with Python's `json` module, which rounds a decimal literal to the nearest double, and compares each number with the SDK's parse. A difference is a `client` failure, a fault on modelcheck's side of the wire. A unit test checks the SDK's parser against Python's on about 80,000 literals: random doubles in shortest, 17-digit and 26-digit forms, and decimals with exponents from -330 to 310.

The copy also records what each side said about the protocol version, from the `server/discover` result or the `initialize` exchange.

## Reference values from any language

A reference is a JSON file or a command that writes one. Doubles are hexadecimal bit patterns, which every language can write without a decimal conversion, so a reference written in R, Julia or Python carries the same bits. The R encoder is about 60 lines and uses `writeBin`, the Julia encoder uses `reinterpret`, and the Python encoder uses `struct`. A file may carry the model name, version and commit, and a declaration may give its SHA-256, so a recorded reference can be tied to the run that made it.

## The derivations model

Language models rarely quote a result as returned. They give percentages, differences between scenarios, ratios and converted units. The scorer accepts these only when the evaluation file declares them:

- `each_value` derivations apply to every reference value: `x`, `(x - 1) * 100` for a percentage change from a ratio, `x * 1000` for a unit conversion;
- `comparisons` derivations apply between two named cases of a question, row by row, for the tables their patterns match: `b - a`, `b / a`;
- each comparison value also goes through the `each_value` derivations, so the percentage change of a ratio between two scenarios is covered by `b / a` and `(x - 1) * 100`.

Derivations are arithmetic expressions, parsed with Python's `ast` and evaluated without `eval`. What is declared is what is accepted; there is no built-in list. kite-mcp's rules are one such declaration, in `examples/kite/eval.yaml`.

A number matches a value when the value rounds to it: within half a unit in the last place the answer shows. By default the sign is ignored, because prose often states a negative change as "2.3 % lower".

## The transcript schema

The scorer needs three things from a run: the tool calls, their results and the final answer. The modelcheck transcript format holds these and nothing else, one JSON record per line, in the order they happened, so any harness can write it. The adapter for Claude Code's `stream-json` output reads tool calls from assistant messages, tool results from user messages and the answer from the `result` record, and removes the `mcp__<server>__` prefix from tool names.

## Choices in the number reader

kite-mcp's reader was the starting point. modelcheck changes it in two ways, and neither changes a count on kite-mcp's 15 transcripts:

- It skips fenced code blocks of every language by default; kite-mcp skips R blocks only, and `skip_code_blocks: [r]` restores that.
- It reads a hyphen between two decimals as a range (0.5-0.8 gives 0.5 and 0.8). Without a decimal point on both sides the hyphen is left alone, so a date such as 2026-11-15 gives 2026 only, as before. `hyphen_ranges: false` turns this off.

The code-block rule, the hyphen ranges and `ignore_sign` are settings in the evaluation file.

## How modelcheck was built

modelcheck was written on 23 November 2026 by Claude Code 2.1.322, Anthropic's coding agent, running the model Claude Opus 5.5 (`claude-opus-5-5`). It worked from a build specification by the repository's author, who set the scope and the acceptance tests. The work ran on Windows 11 with WSL 2 (Ubuntu 22.04), Python 3.11.15 with uv 0.11.8, the MCP Python SDK 2.3.0 (protocol 2026-07-28), pydantic 2.14.4, PyYAML 6.0.4, pytest 9.2.0, ruff 0.17.6, R 4.6.2 with jsonlite 2.0.0 and KITE 26.10 at commit `5995dd1`, and Julia 1.11.7.

The order was: a check of the SDK (its protocol version, its connection to kite-mcp, and the rounding of its JSON parser); the encoders and their round-trip tests; the conformance runner; the proof against kite-mcp's conformance cases; the scorer and the proof against kite-mcp's evaluation; the Python test server and its fault tests; the documentation.

The proofs found two faults in modelcheck during the build. The first conformance run against kite-mcp failed ten checks: the server's headline holds two of the three columns of the welfare table, and the table check compared all three. The check gained `reference_columns`. The first scoring run of the test fixture failed because comparisons read tables from a fixed `tables` key, the layout of kite-mcp's reference; a comparison now names its table with a selector.

What the results do not show: the kite-mcp proof covers one server, one model family and one harness. The growth server is the only other server modelcheck has run against.
