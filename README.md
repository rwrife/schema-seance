# schema-seance 🔮

> _Summon the spirits of your data._

A spooky little CLI that channels schemas, PII, and anomalies out of CSV,
JSONL, Parquet, and SQLite files. Madame Schema is your medium; DuckDB is
her crystal ball.

**Status:** early WIP — M1 scaffold + M2 readers landed. See [PLAN.md](./PLAN.md) for the
full design and milestones.

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

The `summon` command is wired up but only echoes a placeholder until M2
brings the readers online.

## Summoning a file (M2)

`seance summon` now reads CSV/TSV and JSONL/NDJSON files via DuckDB and
prints two sections:

- **The Veil Parts** — rows, columns, size, encoding.
- **The Spirits Speak** — per column: dtype, null %, a sample value.

```bash
uv run seance summon tests/fixtures/tiny.csv
```

Parquet, SQLite, full per-column profiling, PII whispers, and anomaly
hints arrive in later milestones.

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
