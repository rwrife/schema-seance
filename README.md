# schema-seance 🔮

> _Summon the spirits of your data._

A spooky little CLI that channels schemas, PII, and anomalies out of CSV,
JSONL, Parquet, SQLite, and Excel (.xlsx/.xlsm) files. Madame Schema is
your medium; DuckDB is her crystal ball.

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

Or with [`pipx`](https://pipx.pypa.io/) for an isolated install of the CLI
(works once v0.1.0 is published — tracking in [#6](https://github.com/rwrife/schema-seance/issues/6)):

```bash
pipx install schema-seance
seance --version  # 0.1.0
```

Or plain `pip` into any virtualenv:

```bash
pip install schema-seance
```

Or via [Homebrew](https://brew.sh/) (third-party tap, see
[`packaging/homebrew/`](./packaging/homebrew/)):

```bash
brew tap rwrife/schema-seance https://github.com/rwrife/homebrew-schema-seance
brew install schema-seance
```

## Hello, Madame Schema

Run `seance` with no arguments to summon the parlor:

![seance greeting](./docs/demo/greeting.svg)

## Summoning a file

![seance summon tiny.csv](./docs/demo/summon-tiny.svg)

Want motion? An animated walkthrough (greeting → `summon` → `--json`) is
generated from [`docs/demo/seance.tape`](./docs/demo/seance.tape) with
[VHS](https://github.com/charmbracelet/vhs):

```bash
vhs docs/demo/seance.tape   # writes docs/demo/seance.gif
```

The rendered `seance.gif` is committed alongside each release and embedded
here once available.

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
- **The Hours That Pass** — for date / timestamp columns (real dtypes or
  ISO / RFC3339 / common-format strings): time range, inferred cadence,
  % of rows that conform, expected vs. actual buckets with the top gaps,
  and day-of-week / hour-of-day skew hints. Skip with `--no-timeseries`.

```bash
uv run seance summon tests/fixtures/tiny.csv
uv run seance summon path/to/data.parquet
uv run seance summon path/to/data.sqlite --table events
uv run seance summon workbook.xlsx --sheet sales      # pip install 'schema-seance[excel]'
uv run seance list-sheets workbook.xlsx               # inventory + row counts
uv run seance summon big.csv --sample 100000
uv run seance summon big.csv --json | jq '.columns[] | select(.null_pct > 50)'
uv run seance summon people.csv --fail-on-pii high   # CI smoke check
uv run seance summon data.csv --html report.html     # self-contained HTML report
```

### Flags

- `--table NAME` — pick a SQLite table (defaults to the first one).
- `--sheet NAME|INDEX` — pick an Excel sheet by name or 0-based index
  (defaults to the active sheet). Requires the optional `[excel]` extra.
- `--sample N` — profile only the first `N` rows. Reported `rows` and
  `sampled`/`sample_size` reflect this.
- `--json` — emit a stable, versioned JSON document instead of the Rich
  view. See [`docs/json-schema.md`](./docs/json-schema.md).
- `--fail-on-pii {low|medium|high}` — exit with code **3** when any column
  has a PII finding at or above the chosen confidence band. See the
  exit-code contract below.
- `--no-timeseries` — skip the **Hours That Pass** temporal section.
- `--html PATH` — also write a self-contained HTML report (CSS inlined,
  no external assets) covering all four sections. Works alongside `--json`.
- `--quiet` — suppress stdout output (useful with `--html` to write the
  report silently in CI).
- `--format {csv|tsv|jsonl|ndjson|parquet}` — override format inference.
  Handy for opaque remote URLs without an extension.
- `--region REGION` — AWS region for `s3://` inputs (overrides
  `AWS_REGION` / `AWS_DEFAULT_REGION`).

### Remote inputs (S3 + HTTPS)

`summon` and `read` also accept `s3://bucket/key` and `https://…` URLs —
no download dance required. DuckDB's `httpfs` extension is auto-installed
on first use, and AWS credentials come from the standard chain
(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars,
`~/.aws/credentials`, or IMDS on EC2).

```bash
uv run seance summon https://example.com/data.csv
uv run seance summon s3://my-bucket/events/2026/01/events.parquet --region us-west-2
uv run seance read https://example.com/data.jsonl
```

If the URL has no extension (e.g. a signed pre-auth link), pass
`--format` to tell Madame Schema what's behind the veil:

```bash
uv run seance summon "https://example.com/download?id=42" --format parquet
```

### Multi-file summon (The Congregation)

Point `summon` at a directory, a glob, or a remote glob and you get one
cross-file report instead of many:

```bash
uv run seance summon ./data/
uv run seance summon 'data/*.parquet'
uv run seance summon 's3://bucket/events/2026/*/*.parquet' --region us-west-2
```

The report includes a per-file roll call, **The Congregation** roll-up
(total rows, schema-cluster count, drifted columns), and **Schema
Circles** — files sharing the same column-name + dtype signature. Files
that fail to load show up under **The Silent Ones** and never abort the
run.

Filter the set with `--include` / `--exclude` (basename globs,
repeatable) and raise or lower the safety cap with `--max-files`
(default `50`). `--json` returns a stable payload:

```json
{
  "kind": "multi",
  "inputs": ["./data/"],
  "summary": {
    "files_loaded": 3,
    "files_failed": 0,
    "total_rows": 12345,
    "schema_clusters": 2,
    "drifted_columns": ["extra"]
  },
  "files": [ { "path": "…", "ok": true, "profile": { … } } ],
  "clusters": [ { "size": 2, "columns": [ … ], "files": [ … ] } ]
}
```

### Exit codes

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| `0`  | Profile completed successfully.                                        |
| `2`  | Input is unsupported, unreadable, or `--table` doesn't exist.          |
| `3`  | `--fail-on-pii` was set and a finding met or exceeded that confidence. |
| `4`  | `seance read` could not reach (or got nothing usable from) the LLM.    |
| `5`  | `seance compare --fail-on` was set and the diff met that severity.     |

## Banishing PII — `seance redact`

`summon` whispers which columns reek of PII. `redact` *banishes* it.
Point it at a file, pick a destination, and Madame Schema emits a
redacted copy with each flagged column transformed by a per-detector
strategy.

```bash
# Default: mask emails/phones/cards/SSN/IPs, hash names, year-only DOBs.
uv run seance redact people.csv -o people.redacted.csv

# Preview the plan without writing anything.
uv run seance redact people.csv --dry-run --json

# Lower the bar to medium confidence and override individual strategies.
uv run seance redact data.jsonl -o clean.jsonl \
    --min-confidence medium \
    --strategy email=hash --strategy name=null

# Force an output format (otherwise inferred from --output's extension).
uv run seance redact data.csv -o clean.parquet --format parquet
```

Strategies: `mask` (smart per-kind redaction, keeps last 4 of cards /
phones, /24 of IPv4, /48 of IPv6), `hash` (truncated SHA-256), `null`
(drop the cell), `keep` (skip the column), `year` (DOB → 4-digit year).
`--dry-run` prints the plan; pair with `--json` for scripting.

## Compare two files

`seance compare a.csv b.csv` profiles both files, then flags column
adds/removes, dtype drift, null/distinct/distribution shifts, and PII
appearing or disappearing. Great as a CI gate on data contracts.

```bash
uv run seance compare yesterday.csv today.csv
uv run seance compare a.csv b.csv --json | jq '.summary'
uv run seance compare a.parquet b.parquet --fail-on medium   # CI gate
uv run seance compare old.sqlite new.sqlite \\
    --before-table events --after-table events
```

Each column lands in one of four buckets — `added`, `removed`,
`changed`, `unchanged` — with a per-column severity (`none`/`low`/
`medium`/`high`) and a list of specific field changes. The diff's
overall severity is the worst column severity; `--fail-on` exits with
code **5** when it meets or exceeds the chosen level.


## Personas

The narrator's voice is swappable. Pass `--persona <id>` (any subcommand),
or set `SEANCE_PERSONA=<id>` in the environment. List them with
`seance personas`:

| id            | Voice                                                  |
| ------------- | ------------------------------------------------------ |
| `madame`      | Madame Schema — deadpan Victorian medium (default).    |
| `skeptic`     | Clinical analyst. No theatre, hedged claims.           |
| `pirate`      | Cap'n Schema — salty data buccaneer.                   |
| `noir`        | Detective Schema — first-person hard-boiled.           |
| `corporate`   | PM Schema — buzzword-light stakeholder brief.          |
| `shakespeare` | The Bard of Schema — faux early-modern English.        |

Examples:

```bash
seance --persona pirate summon data.csv
SEANCE_PERSONA=noir seance read data.csv
seance --persona skeptic compare before.csv after.csv
```

Personas only affect text (greetings, panel titles, refusal phrases,
and the LLM system prompt). They do not change PII rules, profile
contents, JSON output, or exit codes — those stay stable for scripting.

## Optional: ask the LLM for a reading

`seance read <file>` runs the same profile, then asks an OpenAI-compatible
chat endpoint for a three-paragraph “reading” in your chosen persona's voice
(Madame Schema by default).
Nothing in this command touches the network until you invoke it, and the
standard `summon` path never reaches for an LLM.

Configure the provider via environment variables:

| Variable               | Required | Example                              |
| ---------------------- | -------- | ------------------------------------ |
| `SEANCE_LLM_BASE_URL`  | yes      | `https://api.openai.com/v1` / `http://localhost:11434/v1` |
| `SEANCE_LLM_MODEL`     | yes      | `gpt-4o-mini` / `llama3:8b`          |
| `SEANCE_LLM_API_KEY`   | no       | `sk-…` (skip for most local providers) |

```bash
# OpenAI
export SEANCE_LLM_BASE_URL=https://api.openai.com/v1
export SEANCE_LLM_API_KEY=sk-…
export SEANCE_LLM_MODEL=gpt-4o-mini
uv run seance read path/to/data.csv

# Local Ollama
export SEANCE_LLM_BASE_URL=http://localhost:11434/v1
export SEANCE_LLM_MODEL=llama3:8b
uv run seance read path/to/data.csv --no-show-profile
```

Flags: `--timeout SECONDS` (default 30) hard-caps the request;
`--no-show-profile` skips the standard rendered profile and shows only the
reading. Token counts (and a USD cost estimate for known OpenAI models) are
printed under the reading panel. If the call fails or times out, the
command exits **4** without ever printing a half-baked response.

## The parlor (interactive TUI)

`seance parlor <file>` opens a keyboard-first Textual TUI: a column
browser on the left, a paged sample of rows on the right, an SQL input
below, and a results pane. The file is registered as the DuckDB view
`data`, so any query against it works:

```sql
SELECT name, COUNT(*) FROM data GROUP BY name ORDER BY 2 DESC
```

Key bindings: `Enter` or `F5` run the query, `Ctrl+R` resets, `q` quits.
Results are capped at 200 rows unless you supply an explicit `LIMIT`.

The TUI is an **optional** extra so the base install stays small:

```bash
pipx install "schema-seance[tui]"
# or
pip install "schema-seance[tui]"
```

## MCP server mode

`seance mcp` runs a tiny [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio, exposing two tools:

- **`summon`** — profile a file and return schema, per-column stats, PII
  findings, and anomalies as JSON. Offline.
- **`read`** — profile, then ask the configured LLM for a narrative
  reading. Requires the usual `SEANCE_LLM_*` env vars.

Wire it into any MCP client as a stdio server. Example for Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "schema-seance": {
      "command": "seance",
      "args": ["mcp"]
    }
  }
}
```

The server speaks JSON-RPC 2.0 with no extra dependencies; clients that
speak line-delimited JSON or LSP-style `Content-Length` framing both work.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same on Python 3.11 and 3.12 via GitHub Actions.

## Releasing

Releases are tag-driven. Pushing a `vX.Y.Z` tag triggers
[`.github/workflows/release.yml`](./.github/workflows/release.yml), which builds
the sdist + wheel and publishes to PyPI via OIDC trusted publishing (no API
token stored in the repo). Before tagging:

1. Bump `version` in `pyproject.toml` and `schema_seance/__init__.py`.
2. Move the `[Unreleased]` entries in [CHANGELOG.md](./CHANGELOG.md) under a new
   `vX.Y.Z` section, dated.
3. `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The workflow uploads the build, runs a wheel-install smoke test
   (clean venv → `seance --help` → `seance summon ... --json`) so a
   broken wheel fails *before* it reaches PyPI, then publishes via
   OIDC. Cut the GitHub release from the tag with the changelog
   excerpt as the notes.

The PyPI project (`schema-seance`) must have this repo configured as a
trusted publisher for the `pypi` environment.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).

## License

MIT.
