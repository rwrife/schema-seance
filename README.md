# schema-seance 🔮

> _Summon the spirits of your data._

A spooky little CLI that channels schemas, PII, and anomalies out of CSV,
JSONL, Parquet, and SQLite files. Madame Schema is your medium; DuckDB is
her crystal ball.

**Status:** early WIP — M1 scaffold, M2 readers, M3 full profiling, and M4
PII + anomalies landed. See [PLAN.md](./PLAN.md) for the full design and
milestones.

## Install

With [uv](https://docs.astral.sh/uv/) (recommended for hacking):

```bash
git clone https://github.com/rwrife/schema-seance
cd schema-seance
uv sync
uv run seance
```

Or with `pipx` once published:

```bash
pipx install schema-seance   # not yet on PyPI — see M6
```

## Hello, Madame Schema

Run `seance` with no arguments to summon the parlor:

```bash
$ seance
╭─ 🔮 Madame Schema  ·  Medium of Messy Data ─────────────────────────╮
│                                                                     │
│  The parlor is dim. The candle gutters. Place your dataset on the   │
│  velvet.                                                            │
│  I sense… columns. Yes. Many columns. Some of them are lying to     │
│  you.                                                               │
│  Summon a file with seance summon <path> and we shall begin.        │
│                                                                     │
╰─────────────────────────────────────────────────────────────────────╯
```

## Summoning a file

`seance summon` reads CSV/TSV, JSONL/NDJSON, Parquet, and SQLite files via
DuckDB and prints four sections:

- **The Veil Parts** — rows, columns, size, encoding.
- **The Spirits Speak** — per column: dtype, null %, distinct count, min,
  max, mean, stddev, and top values.
- **Whispers of the Personal** — PII heuristics (email, phone, credit-card
  with Luhn check, SSN-ish, IPv4/IPv6, likely-name columns, DOB) each with
  a confidence score in `[0, 1]` and a `low`/`medium`/`high` band.
- **Restless Anomalies** — mixed value shapes in VARCHAR columns,
  primary-key candidates with duplicates, high-null columns, and IQR-based
  numeric outliers.

```bash
uv run seance summon tests/fixtures/tiny.csv
uv run seance summon path/to/data.parquet
uv run seance summon path/to/data.sqlite --table events
uv run seance summon big.csv --sample 100000
uv run seance summon big.csv --json | jq '.columns[] | select(.null_pct > 50)'
uv run seance summon people.csv --fail-on-pii high   # CI smoke check
```

### Flags

- `--table NAME` — pick a SQLite table (defaults to the first one).
- `--sample N` — profile only the first `N` rows. Reported `rows` and
  `sampled`/`sample_size` reflect this.
- `--json` — emit a stable, versioned JSON document instead of the Rich
  view. See [`docs/json-schema.md`](./docs/json-schema.md).
- `--fail-on-pii {low|medium|high}` — exit with code **3** when any column
  has a PII finding at or above the chosen confidence band. See the
  exit-code contract below.

### Exit codes

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| `0`  | Profile completed successfully.                                        |
| `2`  | Input is unsupported, unreadable, or `--table` doesn't exist.          |
| `3`  | `--fail-on-pii` was set and a finding met or exceeded that confidence. |

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same on Python 3.11 and 3.12 via GitHub Actions.

## License

MIT.
