# decimal-landing

Convert decimal columns in CSV to an explicit `DECIMAL(precision, scale)`
contract, with exact rounding-loss totals and enforceable budgets. Stage the
converted file and publish it only if every row and every budget passes.

`1.005 → 1.00` and `1.015 → 1.02` cancel in a signed total, but together lose
`0.01` in absolute error. This tool reports both. It also checks overflow **after**
rounding: `9999.995` rounded half up cannot fit `DECIMAL(6,2)`.

Python 3.11+, MIT, no runtime dependencies or network calls. Intended for offline
ETL handoffs, not accounting policy or an emulation of every SQL engine.
[中文说明](README.zh-CN.md)

## Install and run

```sh
git clone https://github.com/KanadeK/decimal-landing.git
cd decimal-landing
python -m venv .venv
```

Activate with `source .venv/bin/activate` (Linux/macOS) or
`.venv\Scripts\Activate.ps1` (PowerShell), then:

```sh
python -m pip install .
decimal-landing examples/amounts.csv --contract examples/contract.json
decimal-landing examples/amounts.csv --contract examples/contract.json --output landed.csv
decimal-landing examples/rejected.csv --contract examples/contract.json
```

The first two commands exit 0: three records, two changed numeric values, signed
error `0`, absolute error `0.01`. The output values are `12.34`, `12.36`, `100.00`;
IDs remain `001`, `002`, `003`. The third exits 1, reporting `overflow` and
`invalid_number`. A dry run (no `--output`) creates no files.

`python -m decimal_landing` is equivalent. Release wheels are installable using
`python -m pip install <wheel-file>`; this package is not published to PyPI.
Uninstall with `python -m pip uninstall decimal-landing`. Delete your generated
`landed.csv` and virtual environment to clean up; no cache or settings are stored.

## Contract

```json
{
  "version": 1,
  "columns": [
    {
      "name": "amount",
      "precision": 6,
      "scale": 2,
      "rounding": "half_even",
      "max_cell_abs_error": "0.005",
      "max_total_abs_error": "0.010",
      "max_changed": 2,
      "null_values": [""]
    }
  ]
}
```

| Property | Meaning |
|---|---|
| `precision` | Total decimal digits, 1–128. Sign and decimal point do not count. |
| `scale` | Fractional digits, 0 through precision. Output always has this scale. |
| `rounding` | `reject` (default), `half_even`, `half_up` (ties away from zero), or `toward_zero`. |
| `max_cell_abs_error` | Optional maximum absolute difference for each accepted numeric cell. |
| `max_total_abs_error` | Optional sum of **absolute** differences for the column, not absolute net difference. |
| `max_changed` | Optional maximum count of numerically changed cells. |
| `null_values` | Optional exact strings to pass through unchanged and exclude from numeric totals; default `[]`. |

Budget limits are inclusive, nonnegative decimal **strings**. Omitting a budget
means that particular limit is disabled; the explicit rounding policy and
precision checks still apply. `reject` allows redundant zeroes such as `1.2300`
but rejects any actual nonzero precision loss. Formatting-only changes do not
increase `changed`. Negative zero is written as unsigned zero.

Numbers use ASCII decimal notation, optional sign, dot and exponent. No whitespace
trimming, currency symbols, grouping separators, NaN, Infinity or locale guessing.
Limits: 1024 characters per literal, 512 coefficient digits, explicit exponent
within ±512; these apply even to zero. Contract size 64 KiB. CSV fields are limited
to 1 MiB and headers to 1024 unique nonempty names. Unknown/duplicate JSON keys
are rejected.

## Reports and exit codes

- **0:** all conversions and budgets pass; a requested output was published.
- **1:** numeric values or loss budgets fail; no requested output is published.
- **2:** malformed CSV/contract, usage, encoding or I/O error; no output is published.

The JSON report includes accepted/null/rejected/changed counts, exact source and
target sums, signed error, total absolute error, maximum absolute cell error and
issue counts. Only the first 20 issue examples are retained; counts cover the
whole input. Positions are one-based data records. Column budget issues have
`record: null`. Totals include **accepted numeric cells only**; rejected values
and configured null tokens are excluded. A failed report must not be mistaken
for a complete converted table. Reports contain aggregates and column names,
which may themselves be sensitive; issue examples do not echo source values.

## Output and safety

CSV input is comma-delimited, UTF-8 (optional BOM), with double-quote escaping.
Headers are required. Unselected **cell values** and column order are preserved,
including embedded line breaks. CSV quoting and record terminators are normalized
to LF; all values in selected numeric columns are rendered at the target scale.
NULL tokens are compared as text after ordinary CSV parsing: this tool does not
preserve a distinction based solely on whether a cell was quoted.

Rows stream into a temporary file beside the destination. A successful batch
uses an atomic hard link to publish without overwriting (requires a filesystem
supporting hard links), or atomic replacement with `--force`. Existing output is
preserved on failure. Input/contract aliases and symlink outputs are rejected.
Use static inputs and a trusted output directory. A killed process may leave a
`.transit-*.tmp` file containing data; remove only your own interrupted job files.
No guarantee against hostile directory races or concurrent input changes.

## Boundaries and alternatives

[PostgreSQL numeric](https://www.postgresql.org/docs/current/datatype-numeric.html)
documents rounding during coercion. This tool makes that decision explicit
before import. It does not connect to a database, generate SQL, support negative
scales or `scale > precision`, guarantee an engine's error ordering, distribute
rounding remainders or make financial judgments. Select a policy for your workflow.

[qsv](https://github.com/dathere/qsv) is a broad CSV toolkit;
[OpenRecon](https://github.com/Naresh-Paturi-Community/openrecon) matches payment
records. Use those when you need general validation or reconciliation. This
project is a focused conversion with exact loss budgets; it is not a replacement
for either. It does not sanitize spreadsheet formulas in unselected text.

## Verification

```sh
python -m pip install -e . build==1.6.1 ruff==0.17.2
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

The integer/rational implementation is checked against Python Decimal in 3,000
seeded rounding cases, plus sign/tie/carry boundaries, exact large values,
aggregate cancellation, budgets, rollback, multiline CSV and CLI exit codes.
CI runs Python 3.11/3.13 on Linux, Windows and macOS and never commits code.
