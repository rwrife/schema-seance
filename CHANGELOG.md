# Changelog

All notable changes to **schema-seance** are recorded here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Excel reader: `seance summon` / `read` / `redact` / `parlor` now accept
  `.xlsx` / `.xlsm` workbooks via an optional `[excel]` extra
  (`pip install 'schema-seance[excel]'`). New `--sheet NAME|INDEX` flag
  selects a sheet (defaults to the workbook's active sheet) and a new
  `seance list-sheets <file>` command prints the sheet inventory with
  approximate row counts (also `--json`). Closes #33.
- Remote inputs for `seance summon` / `seance read`: accept `s3://bucket/key`
  and `https://…` URLs in addition to local paths. DuckDB's `httpfs`
  extension is auto-loaded on first remote use; AWS credentials come from
  the standard chain (env vars, `~/.aws/credentials`, IMDS). New `--format`
  flag overrides extension inference for opaque URLs; `--region` overrides
  the S3 region. Network errors surface as a friendly Madame-Schema-flavored
  message with exit code 2. Closes #29.
- `seance redact <file> -o <out>`: emit a redacted copy of the input. Reuses
  the PII detectors, picks per-detector default strategies (mask for emails /
  phones / cards / SSN / IPs, hash for names, year-only for DOB), and supports
  `--min-confidence`, `--strategy <detector>=<mask|hash|null|keep|year>`
  (repeatable), `--format {csv,jsonl,parquet}`, and `--dry-run` (with `--json`
  for scripting). Re-running `summon` on the output yields ~0 high-confidence
  PII for non-IP detectors. Closes #28.
- `--html PATH` flag on `seance summon` and `seance read`: writes a
  self-contained HTML report (inlined CSS, no external assets, no JS)
  covering The Veil Parts, The Spirits Speak, Whispers of the Personal
  (with color-coded confidence bands), and Restless Anomalies. `read`
  also includes the LLM reading as a top section. Persona-aware banner.
  Works alongside `--json`; pair with `--quiet` to write silently in CI.
  Closes #27.
- `seance mcp` subcommand: stdio MCP (Model Context Protocol) server
  exposing `summon` and `read` as tools so agents (Claude Desktop, Cursor,
  Continue, etc.) can profile data on demand. Speaks JSON-RPC 2.0 with no
  extra dependencies; accepts line-delimited or LSP-style `Content-Length`
  framing on input. Closes #10.
- Persona packs: swappable narrator voices via `--persona <id>` or
  `SEANCE_PERSONA` env. Ships with `madame` (default), `skeptic`,
  `pirate`, `noir`, `corporate`, and `shakespeare`. New `seance personas`
  subcommand lists them. Personas affect greetings, panel titles, refusal
  phrases, and the LLM system prompt only; JSON output, exit codes, and
  PII rules are unchanged. Closes #9.
- `seance compare a b` command: profiles two files and emits a structured
  schema diff (column adds/removes, dtype drift, null/distinct/distribution
  shifts, PII appearance/disappearance) with per-column and overall
  severity bands. Supports `--sample`, `--before-table`/`--after-table`
  (SQLite), stable `--json` output (`schema_version: 1`), and `--fail-on
  {low,medium,high}` for CI data-contract gates (exit code **5**). Closes #8.
- Homebrew formula template at `packaging/homebrew/schema-seance.rb`
  (plus a `packaging/homebrew/README.md`) for serving `schema-seance` from
  a third-party tap. Includes a `test do` block that runs `seance
  --version` and `seance summon ... --json` so a broken bottle fails at
  `brew test` instead of in the user's terminal. README install section
  now documents the `brew tap rwrife/schema-seance` flow alongside
  `pipx` and `pip`.
- Release workflow now runs a wheel-install smoke test before publishing
  to PyPI: installs the built wheel into a clean venv, invokes
  `seance --help`, and verifies `seance summon tests/fixtures/tiny.csv
  --json` produces a parseable report (with `schema_version`). Catches
  packaging regressions (missing data files, broken entry points, etc.)
  before they ever hit users via `pipx install schema-seance`.
- `docs/demo/seance.tape` — [VHS](https://github.com/charmbracelet/vhs)
  script that renders an animated `seance.gif` (greeting → `summon` →
  `--json`) for the README. Run `vhs docs/demo/seance.tape` to regenerate.
- `seance parlor <file>` interactive TUI (Textual) — column browser, sample
  preview, and ad-hoc DuckDB SQL against the `data` view. Ships behind the
  optional `[tui]` extra.
- `CHANGELOG.md` and a tag-driven release workflow that publishes wheels +
  sdists to PyPI via OIDC trusted publishing.
- Rich-rendered SVG demo screenshots embedded in the README (greeting +
  `seance summon tests/fixtures/tiny.csv`), regenerable via
  `uv run python docs/demo/generate.py`.
- README install section now documents `pipx install schema-seance` and
  plain `pip install schema-seance` alongside the `uv` dev flow.

## [0.1.0] — 2026-06-20

First public preview. Madame Schema can read a file end-to-end.

### Added
- `seance` CLI with a Madame Schema parlor greeting (M1).
- `seance summon <file>` reads CSV, JSONL/NDJSON, Parquet, and SQLite via
  DuckDB and renders four sections: **The Veil Parts**, **The Spirits Speak**,
  **Whispers of the Personal**, and **Restless Anomalies** (M2 + M3).
- Full per-column profiling: dtype, null %, distinct, min/max, mean/stddev,
  top values; `--sample N` to cap profiling cost; stable `--json` output
  (see `docs/json-schema.md`); `--table` for SQLite (M3).
- PII heuristics (email, phone, credit-card with Luhn, SSN-ish, IPv4/IPv6,
  likely-name columns, DOB) with low/medium/high confidence bands; numeric
  IQR outliers; mixed-shape and PK-candidate duplicate checks;
  `--fail-on-pii {low|medium|high}` exits **3** for CI smoke checks (M4).
- `seance read <file>` calls any OpenAI-compatible endpoint
  (`SEANCE_LLM_BASE_URL`, `SEANCE_LLM_API_KEY`, `SEANCE_LLM_MODEL`) for a
  three-paragraph reading; token + USD cost estimate printed; hermetic by
  default — network is only touched inside `read`. Failures exit **4** without
  printing a half-baked response (M5).
- GitHub Actions CI: `ruff check`, `ruff format --check`, `pytest` on
  Python 3.11 and 3.12.

[Unreleased]: https://github.com/rwrife/schema-seance/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rwrife/schema-seance/releases/tag/v0.1.0
