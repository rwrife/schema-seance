# schema-seance 🔮

> _Summon the spirits of your data._

A spooky little CLI that channels schemas, PII, and anomalies out of CSV,
JSONL, Parquet, and SQLite files. Madame Schema is your medium; DuckDB is
her crystal ball.

**Status:** early WIP — M1 (scaffold) landed. See [PLAN.md](./PLAN.md) for
the full design and milestones.

## Install

The recommended install is via [`uv`](https://docs.astral.sh/uv/) or
[`pipx`](https://pipx.pypa.io/) once published. Until then, run from a clone:

```bash
git clone https://github.com/rwrife/schema-seance.git
cd schema-seance
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

Or with plain pip:

```bash
pip install -e ".[dev]"
```

## Hello, spirits

```bash
$ seance
╭─ 🔮 Madame Schema ─────────────────────────────────────────╮
│                                                            │
│  The veil is thin tonight…                                 │
│                                                            │
│  Place your hands on the dataset. The spirits will speak.  │
│                                                            │
╰──────────────────────────── schema-seance v0.0.1 ──────────╯
```

`seance summon <file>` is reserved for the M2 profiler. Today it just
acknowledges the file and waits for the spirits to gather.

## Develop

```bash
ruff check .
ruff format --check .
pytest
```

CI runs the same on Python 3.11 and 3.12.

## License

MIT.
