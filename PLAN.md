# schema-seance — PLAN

> _"Step into the parlor. Place your hands on the dataset. The spirits will speak."_

## 1. Pitch

`schema-seance` is a CLI medium for your messy data. Point it at a CSV, JSONL,
Parquet, or SQLite file and it channels the schema, divines likely PII columns,
surfaces anomalies and outliers, and (optionally) lets an LLM whisper a short
narrative about what the data is *really* hiding. Half data-profiler, half
parlor trick — but actually useful before you load that mystery file into
production.

## 2. Trend inspiration

Three currents in mid-2026 made this feel inevitable:

- **Data-tool TUIs are having a moment.** `sqlit-tui` (https://pypi.org/project/sqlit-tui/),
  `harlequin`, and a wave of ratatui-based data viewers are showing up on
  r/dataengineering and Terminal Trove
  (https://terminaltrove.com/categories/tui/). People want fast, keyboard-driven
  data inspection without spinning up a Jupyter notebook.
- **DuckDB-as-engine pattern.** Embedding DuckDB into CLIs to query
  CSV/Parquet/JSONL in-place has become the default move for new data tools
  (see harlequin, dlt, the awesome-duckdb list).
- **AI tools with personality ship better.** From `commit-roast` to the
  general Product Hunt zeitgeist
  (https://www.producthunt.com/categories/productivity), tools with a
  consistent voice and persona consistently outperform clinical ones in
  retention. A "seance" framing is memorable, brandable, and gives us a UI
  vocabulary (Spirits, Whispers, The Veil, etc.).

## 3. Why it's different

- Most profilers (pandas-profiling/ydata-profiling, great_expectations,
  Soda) target the notebook or pipeline. `schema-seance` is a single-shot,
  terminal-first experience designed for the **first 60 seconds with an
  unknown file**.
- Unlike `csvkit` / `xsv` / `qsv`, it doesn't just describe — it *interprets*:
  PII heuristics, anomaly hints, and an optional LLM "reading" of the data.
- Unlike heavy notebook profilers, output is text-first and pipeable; you can
  `schema-seance summon data.csv --json | jq ...` in scripts.
- The persona (a deadpan Victorian medium named **Madame Schema**) is a
  consistent UX gimmick, not a one-off Easter egg — every command speaks in
  it, and it's swappable later (skeptic mode, pirate mode, etc.).

## 4. MVP scope (v0.1)

- `seance summon <file>` — primary command.
- Supported inputs: `.csv`, `.jsonl`, `.parquet`, `.sqlite`/`.db` (first
  table or `--table`).
- Output sections in the terminal (via Rich):
  1. **The Veil Parts** — file metadata: rows, columns, size, encoding.
  2. **The Spirits Speak** — per-column type, null %, distinct %, sample
     values, min/max.
  3. **Whispers of the Personal** — PII heuristics (emails, phones, SSN-ish,
     IPs, names, dates of birth) with a confidence score.
  4. **Restless Anomalies** — outliers, mixed types, suspicious nulls,
     duplicate primary-key candidates.
- Flags: `--json` (machine output), `--table NAME`, `--sample N`, `--quiet`.
- Exit code non-zero if "high confidence" PII is found (toggleable) — useful
  for CI smoke checks.

LLM integration is **deliberately deferred** to M5; MVP must be useful with
zero network access.

## 5. Tech stack

- **Python 3.11** + **uv** for project + lock management. Fast install,
  great for CLI distribution.
- **Click** for command parsing. Boring, battle-tested.
- **Rich** for pretty terminal output (panels, tables, colors).
- **DuckDB** (python bindings) as the universal reader/profiler engine —
  one library reads CSV/JSONL/Parquet/SQLite and gives us SQL aggregates
  for free.
- **pytest** for tests; **ruff** for lint/format.
- Optional (M5+): **httpx** + small OpenAI-compatible client for the LLM
  "reading"; provider URL/key via env vars so it works with OpenAI, local
  Ollama, etc.

Why not Rust/Go? Python + uv ships fast enough for a CLI, the data-profiling
ecosystem is richer, and contributors are more plentiful. We can rewrite the
hot path in Rust later if anyone actually uses this.

## 6. Architecture

```
schema_seance/
  cli.py            # Click entrypoint, flag parsing, output orchestration
  readers/
    __init__.py     # dispatch on extension
    csv.py
    jsonl.py
    parquet.py
    sqlite.py
  profile.py        # DuckDB-backed column profiler -> ColumnProfile dataclass
  pii.py            # regex + heuristic PII detectors, returns confidence
  anomalies.py      # outlier, mixed-type, dup-PK candidate checks
  persona.py        # Madame Schema's voice -> small template/string library
  render/
    terminal.py     # Rich renderers per section
    json.py         # JSON serializer for --json
  llm.py            # (M5) optional narrator
tests/
```

Key contracts:

- `readers.load(path, table=None) -> duckdb.DuckDBPyRelation`
- `profile.profile(relation) -> ProfileReport`
- `render.terminal.render(report, persona)`

## 7. Milestones

### M1 — scaffold + hello-world
- `uv init` project, package layout under `schema_seance/`.
- `seance` console-script entrypoint prints a Madame Schema greeting.
- Ruff, pytest, GitHub Actions CI (lint + test on Py 3.11/3.12).
- README updated with install + hello-world demo.

### M2 — readers + raw profile
- CSV and JSONL readers via DuckDB.
- `seance summon <file>` prints "Veil Parts" (rows, cols, size) and a
  basic "Spirits Speak" table (column name, dtype, null %, sample).
- Unit tests with tiny fixture files.

### M3 — full column profiling
- Add Parquet + SQLite readers (`--table` for SQLite).
- Per-column distinct count, min/max, top-k values, numeric mean/stddev.
- `--sample N` flag to limit profiling cost on huge files.
- `--json` output stable enough to script against.

### M4 — PII whispers + anomalies
- Regex/heuristic detectors: email, phone (US/intl), credit-card (Luhn),
  SSN-ish, IPv4/6, likely-name columns, DOB columns.
- Anomaly checks: mixed types in a column, > N% nulls, duplicate values in
  PK-candidate columns, numeric outliers via IQR.
- `--fail-on-pii` flag and exit-code contract documented.

### M5 — optional LLM "reading"
- `seance read <file>` — same profile, then an OpenAI-compatible LLM is
  asked to produce a 3-paragraph "reading" of the data.
- Provider via `SEANCE_LLM_BASE_URL`, `SEANCE_LLM_API_KEY`,
  `SEANCE_LLM_MODEL`. Works with Ollama, OpenAI, vLLM, etc.
- Token + cost shown after the call. Hard timeout, graceful fallback.

### M6 — distribution + polish
- Publish to PyPI as `schema-seance` (and `pipx install` instructions).
- Homebrew tap (optional).
- Animated terminal demo (vhs/asciinema) in README.
- v0.1.0 release with CHANGELOG.

## 8. Backlog / future features (v0.2+)

1. **TUI mode** (`seance parlor`) — interactive Textual TUI to browse
   columns, sample rows, run ad-hoc SQL.
2. **Schema diff** — `seance compare a.csv b.csv` highlights drift.
3. **Persona packs** — Skeptic, Pirate, Noir Detective, Corporate-PM.
4. **MCP server mode** — expose `summon`/`read` over MCP so agents can
   profile data on demand.
5. **Excel / .xlsx reader.**
6. **S3 / HTTP URL inputs** (`seance summon s3://bucket/file.parquet`).
7. **PII redaction export** — emit a redacted copy of the file.
8. **Great Expectations / Soda export** — generate a starter expectation
   suite from the profile.
9. **Time-series detection** — auto-detect date columns and surface
   gaps/seasonality hints.
10. **Multi-file summon** — point at a directory, get a cross-file report.
11. **HTML report** — `--html report.html` for sharing.
12. **Polars backend** as an alternative to DuckDB for very small files.

## 9. Out of scope

- Not a data-cleaning tool. We *report*, we don't mutate the source file.
- Not a notebook replacement. If you want plots and rich dataframes, use
  pandas/Polars/ydata-profiling.
- Not a real-time monitor. One-shot analysis only — streaming/live tailing
  is explicitly not a goal.
- Not a data catalog. We don't persist findings to a central registry
  (though M8 above leaves the door open).
- No GUI desktop app. Terminal-first, forever.
