# misclosure

misclosure tests an MCP server that runs a scientific or engineering model. It makes two checks, both against reference values from direct runs of the model:

- **Conformance.** Every number the server returns must equal the reference bit for bit, unless a case declares a tolerance and gives a reason.
- **Answer evaluation.** A language model answers questions through the server. Every number in each answer must be a reference value, a value from a declared derivation, a number given in the question or a declared constant, within the rounding the answer shows.

It works with any MCP server, over stdio or streamable HTTP, and with any model that can write its results to JSON. It was extracted from the tests of [kite-mcp](https://github.com/ldocallaghan/kite-mcp), and it reproduces kite-mcp's results: see [Worked example: kite-mcp](#worked-example-kite-mcp).

## Why

A server between a language model and a numerical model has several places to change a number: the code that calls the model, the encoding of the results, the JSON it writes, and the client that reads it. A test that compares to six decimal places hides a change in the seventh, and a server that writes 15 significant digits loses the last bits of most doubles. misclosure compares bit patterns, so any change shows, and reports a server that loses bits in JSON as its own class of failure.

The answer evaluation checks a different thing: whether the numbers a language model quotes came from the model's results. It does not check whether the prose around them is right.

## Install

misclosure needs Python 3.11 or later. In a clone of this repository:

```
uv sync
uv run misclosure --help
```

or `pip install .` in a virtual environment. Its dependencies are the official MCP Python SDK (`mcp`) and PyYAML.

## Quick start

`tests/server/growth_server.py` is a small MCP server in Python. It wraps compound growth: the value of a principal after each year at a fixed rate, and the doubling time. `tests/server/reference.py` computes the same function directly and writes the reference file.

```
uv run misclosure conform examples/testserver/cases.yaml
```

```
PASS: 2 cases, 86 values compared (84 table cells, 2 scalars, 0 other checks), 0 failures
protocol 2026-07-28; reports in misclosure-out
```

The server has two more tools with faults: `grow_faulty` returns one value 1 ulp too high, and `grow_short_json` writes 15 significant digits. `tests/fixtures/faults.yaml` runs them:

```
uv run misclosure conform tests/fixtures/faults.yaml
```

The 1-ulp error fails, with both bit patterns in the report (`405cf0cccccccccf` against `405cf0ccccccccce`). The same output passes in the case that declares a relative tolerance of 1e-15, and the report lists that tolerance with its reason. The 15-digit tool fails with 30 values reported as bits lost in JSON.

## Conformance

```
misclosure conform CASES.yaml [--server "COMMAND"] [--url URL] [--mode auto|legacy|VERSION]
                              [--out DIR] [--junit FILE] [--case ID] [--reference FILE]
```

`--server` and `--url` override the server the case file names. `--mode` sets the SDK's protocol negotiation: `auto` (the default) asks the server with `server/discover` and falls back to the `initialize` handshake; `legacy` uses the handshake. `--reference` uses a recorded reference file in place of the declared one. The exit status is 0 when every case passes, 1 on any failure and 2 when the reference or the server cannot be started.

The runner writes three files to `--out` (default `misclosure-out`):

- `conformance.json`: every case, every failure with both bit patterns, the tolerances used, the reference's model and SHA-256, and the protocol version each side gave;
- `conformance.md`: a summary table and the first 50 failures;
- `conformance-junit.xml`: one test case per case, for CI.

### Case files

A case file is YAML or JSON:

```yaml
misclosure: 1

reference:
  command: ["${python}", make_reference.py, "${out}"]   # or file: recorded.json

server:
  command: ["${python}", my_server.py]                 # or url: http://127.0.0.1:8000/mcp

cases:
  - id: five_percent
    steps:
      - call: grow
        arguments: {principal: 100.0, rate: 0.05, years: 10}
        save: run
        checks:
          - table: {output: rows, reference: five_percent.rows, keys: [year]}
          - value: {output: doubling_time, reference: five_percent.doubling_time}
```

Each case is a list of steps. A step calls a tool (`call` with `arguments`) or reads a resource (`read` with a URI, whose text is the output's `text`). `save: name` keeps the output for later steps, which refer to it as `${name.field}`. `error: true` says the call is expected to fail. `each: {keys_of: SELECTOR, as: name}` repeats a step for every key of a reference object, and `each: {values: [...], as: name}` for every value in a list. `setup` holds steps that run once before the cases; a case's `vars` are names its steps can use.

Selectors are dotted paths into the output or the reference: `info.criterion`, `rows[0].value`, `rows[*].flag`, `tables["a.b"]`. Strings may hold templates: `${name.path}` for saved outputs, loop names and `vars`; `${env.NAME}` for environment variables; `${workdir}`, `${case_dir}`, `${python}` and, in a reference command, `${out}`. Commands in a case file run in the case file's directory.

The checks:

| Check | Passes when |
|---|---|
| `value: {output, reference, tolerance}` | one value equals a reference scalar |
| `table: {output, reference, keys, columns, reference_columns, ignore_columns, tolerance}` | a list of row objects equals a reference table |
| `equals: {output, value}` | the output equals a literal; with `[*]`, every element does |
| `absent: SELECTOR`, `present: SELECTOR` | the output lacks or has a field |
| `same_keys: {output, reference}` | a list of names equals the keys of a reference object, in any order |
| `json: {output, reference, fields}` | plain JSON equals the reference, limited to `fields` in each object |
| `contains: {output, text}` | a string holds the text |

A table check matches rows by key columns, in any order. By default the keys are the reference columns that are not doubles. `keys: []` matches rows by position, for output whose rows carry no names. `columns` maps output column names to reference column names; `reference_columns` limits the check to some reference columns. Missing or extra rows, missing columns and output columns that are not in the reference fail, unless `ignore_columns` lists them; patterns such as `*_display` are allowed.

### Comparison

- **Doubles** compare by bit pattern. NA and other NaNs compare by class, as JSON cannot carry a NaN payload. -0.0 and 0.0 differ.
- **Strings, integers and logicals** compare exactly, and an integer must be written as a JSON integer.
- **A tolerance** is declared on a `value` check or per column of a `table` check, as `{abs: ..., rel: ..., reason: "..."}`. It applies to finite doubles only. A tolerance without a reason is an error. The report lists every tolerance, with the number of values that differed in bits and passed within it.
- **Non-finite doubles.** JSON has no NaN or infinity, so a server writes them as strings. By default misclosure reads `"NA"`, `"NaN"`, `"Inf"` and `"-Inf"`. `numbers: {tokens: {...}, null: NA}` in the case file changes the strings, and says what JSON null means.

Each failure has a kind: `value` (a different double or value), `json_precision` (bits lost in JSON: the number written is the reference rounded to the digits the server wrote), `type`, `missing`, `extra`, `duplicate`, `error` (the call failed) or `client` (see below).

### What the client reads

misclosure connects with the official MCP Python SDK. Its transports pass every message through and keep a copy of the text the server wrote. The runner reads each number from that text with Python's `json` module, which rounds a decimal literal to the nearest double, and compares it with the number the SDK parsed. A difference is a `client` failure. `tests/test_jsonlit_selectors.py` checks that the SDK's parser gives the correctly rounded double for about 80,000 literals.

## Reference files and encoders

A reference file is JSON:

```json
{"misclosure_reference": 1,
 "model": {"name": "KITE", "version": "26.10", "commit": "5995dd1..."},
 "values": {"run1": {"info": {"iterations": {"integer": 22}},
                     "tables": {"welfare_change": {"columns": [
                       {"name": "country", "type": "string", "values": ["c1", "c2"]},
                       {"name": "value", "type": "double",
                        "values": ["3feb40946d4761fc", "3fed778cd68f9a55"]}]}}}}}
```

A double is the 16 hexadecimal digits of its big-endian IEEE 754 bit pattern. A reference may also write `"NA"`, `"NaN"`, `"Inf"` or `"-Inf"`. A scalar is `{"double": ...}`, `{"integer": ...}`, `{"logical": ...}` or `{"string": ...}`; a table is a list of typed columns. Missing values in string, integer and logical columns are null. Other JSON may sit between these nodes.

A case file declares its reference as `{file: path, sha256: ..., model: {...}}` or `{command: [...], model: {...}}`. The SHA-256, when given, is checked. A file without the `misclosure_reference` key is read as the values alone, with the model from the declaration.

The encoders write this format:

| Language | File | Functions |
|---|---|---|
| Python | `misclosure.encoding` | `encode_double`, `encode_column`, `encode_table`, `encode_scalar`, `write_reference` |
| R | `encoders/r/misclosure_encode.R` (needs jsonlite) | `mcl_hex`, `mcl_unhex`, `mcl_column`, `mcl_table`, `mcl_scalar`, `mcl_write_reference` |
| Julia | `encoders/julia/MisclosureEncode.jl` (Base only) | `mcl_hex`, `mcl_unhex`, `mcl_column`, `mcl_table`, `mcl_scalar`, `mcl_write_reference` |

Each has a test that round-trips subnormals, -0.0, the largest finite double, infinities, R's NA, NaN payloads and 0.1, and the Python tests check that the R and Julia encoders write the same file as the Python one. Each encoder writes a missing double as R's NA bit pattern.

## Answer evaluation

```
misclosure score EVAL.yaml [--transcripts DIR] [--out DIR]
```

### Evaluation files

```yaml
misclosure: 1
questions: questions.yaml
reference: {file: answers.json, sha256: ...}
transcripts: {dir: transcripts, format: claude-stream-json}

numbers:
  skip_code_blocks: all       # or a list of languages, such as [r]
  ignore_sign: true           # "2.3 % lower" may quote -2.3 without its sign

derivations:
  each_value: [x, (x - 1) * 100]
  comparisons:
    - tables: ["*_change"]
      derive: {difference: b - a, ratio: b / a}

constants: [0, 1, 100]
traps: {plugins: [my_traps.py]}
pass_bar: {max_unsupported: 0, max_failing_questions: 0}
```

A question gives its id, the question, the numbers given in it, the reference cases that support it, and optional comparisons and trap:

```yaml
- id: q05
  label: compare_runs
  question: ...
  given: [20, 1.2, 0.5]
  cases: [war, war_club]
  comparisons:
    - {a: war, b: war_club, table: tables.welfare_change, column: value}
```

### Support

A number in an answer is supported when, within the rounding it shows, it matches a value in its question's support set:

1. every finite double and integer in the question's reference cases;
2. for each comparison, the derivations of the first `comparisons` rule whose `tables` patterns match the table, computed row by row between cases `a` and `b`, with rows paired by their key columns;
3. each value of 1 and 2 passed through every `each_value` derivation;
4. the numbers given in the question;
5. the declared constants.

Derivations are arithmetic in `x`, or in `a` and `b`, with `+ - * / **` and parentheses; a division by zero gives no value. The rounding a number shows is half a unit in its last place: 0.8516 matches values within 0.00005 of it.

The number reader reads signs, including the Unicode minus, thousands separators, decimals, scientific notation and percentages. It skips list numbering at the start of a line, numbers that are part of an identifier (c1, model_2015, v1.2) and fenced code blocks. In a range written with a hyphen between two decimals, such as 0.5-0.8, it reads the second number without a sign.

### Traps

A trap is a question whose right answer is a refusal or a warning. A question names its rule as `trap: {rule: name, ...}`. misclosure has one general rule, `regex`, with `must` and `must_not` lists of patterns. Other rules are Python functions in a plugin file:

```python
from misclosure.score.traps import TrapContext, trap_rule


@trap_rule("says_synthetic")
def says_synthetic(ctx: TrapContext) -> tuple[bool, str]:
    ok = "synthetic" in ctx.answer.lower()
    return ok, f"says the data are synthetic: {'yes' if ok else 'no'}"
```

`examples/kite/kite_traps.py` holds kite-mcp's three trap rules.

### Transcripts

The misclosure transcript format is JSON Lines, one record per line:

```
{"type": "meta", "format": "misclosure-transcript", "version": 1, "model": "...", "harness": "..."}
{"type": "tool_call", "id": "c1", "name": "run_scenario", "arguments": {...}}
{"type": "tool_result", "id": "c1", "is_error": false, "text": "...", "structured": {...}}
{"type": "answer", "text": "..."}
```

Records come in the order they happened; the final answer is the last `answer` record; tool names have no MCP server prefix. `format: claude-stream-json` reads the output of `claude -p --output-format stream-json --verbose` directly, and `misclosure transcript IN OUT` writes it in the misclosure format.

### Output

`results.md` and `results.json`, with one row per question (tools called, numbers checked, unsupported numbers, trap outcome), every unsupported number with its line and context, and the number of questions failing the bar. The exit status is 1 when more questions fail than `max_failing_questions` allows.

## Worked example: kite-mcp

[kite-mcp](https://github.com/ldocallaghan/kite-mcp) serves [KITE](https://github.com/julianhinz/KITE), the trade model suite of the Kiel Institute, over MCP. `examples/kite/` holds its conformance cases and its evaluation in misclosure's formats. They need a kite-mcp checkout and build, and R with KITE at commit `5995dd1`, data.table and jsonlite:

```
export KITE_MCP_DIR=/path/to/kite-mcp
export KITE_MCP_BIN=$KITE_MCP_DIR/target/release/kite-mcp
uv run misclosure conform examples/kite/cases.yaml
uv run misclosure score examples/kite/eval.yaml
uv run misclosure score examples/kite/eval-heldout.yaml
MISCLOSURE_KITE=1 uv run pytest tests/test_kite.py
```

Results on 23 November 2026, kite-mcp at commit `49a3f00`:

- **Conformance.** 12 cases, 501 tables, 0 failures. `get_table` returned 17,394 cells, every one equal to KITE's, which is the count kite-mcp's own conformance test compares. The run also compared 72 headline cells, 110 info-block scalars and 360 other checks (status, table names, warnings, flags). The reference script, which uses misclosure's R encoder, writes the same bits as kite-mcp's own.
- **Protocol.** The SDK (version 2.3.0) negotiated protocol version 2026-07-28 through `server/discover`; kite-mcp (rmcp 3.10) lists 2024-11-05 to 2026-07-28 as supported. With `--mode legacy` both sides use 2025-11-25.
- **Evaluation.** `misclosure score` on kite-mcp's committed transcripts reproduces both of its `results.md` tables in every column: 10 questions and 5 held-out questions, with the same tools, the same count of numbers checked, no unsupported numbers and the same trap outcomes. The rules are those of kite-mcp's scorer, declared in `eval.yaml`.

## Limits

- **What conformance shows.** It shows that the server returns what the reference returns. If the reference script calls the model wrongly, both are wrong in the same way.
- **Prose.** The scorer checks where numbers came from. A sentence with no number, or a right number in a wrong sentence, is not checked.
- **Rounding.** A number that matches any value in the support set within its rounding is supported, so a short number such as 2 or 0.5 is often supported by chance. The support set is per question for this reason.
- **NaN payloads.** JSON cannot carry them, so NaNs compare by class: NA or other.
- **Transcripts.** One adapter is included, for Claude Code. Other harnesses write the misclosure format.

## Tests

```
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests run the growth server over stdio and streamable HTTP. The R and Julia encoder tests run when `Rscript` (with jsonlite) and `julia` are found; `MISCLOSURE_JULIA` may name another Julia command. The kite-mcp proof runs when `MISCLOSURE_KITE=1`.

## Licence

Apache License 2.0. See `LICENSE`.
