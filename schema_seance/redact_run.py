"""Run a :class:`RedactionPlan` against a file, writing a redacted copy.

Pure I/O glue, kept separate from :mod:`schema_seance.redact` so the
planning logic stays trivially testable.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb

from .readers import load
from .redact import RedactionError, RedactionPlan, redact_row

__all__ = [
    "SUPPORTED_OUTPUT_FORMATS",
    "infer_format",
    "run_redaction",
]


SUPPORTED_OUTPUT_FORMATS: frozenset[str] = frozenset({"csv", "jsonl", "parquet"})


_EXT_TO_FORMAT: dict[str, str] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".sqlite": "parquet",
    ".sqlite3": "parquet",
    ".db": "parquet",
}


def infer_format(path: Path) -> str:
    """Map a file path to one of :data:`SUPPORTED_OUTPUT_FORMATS`.

    Falls back to ``csv`` if the suffix is unknown.
    """
    return _EXT_TO_FORMAT.get(path.suffix.lower(), "csv")


def _write_csv(out_path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def _write_jsonl(out_path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            obj = {col: row[i] for i, col in enumerate(columns)}
            fh.write(json.dumps(obj, default=str))
            fh.write("\n")


_DUCKDB_SAFE_TYPES = {
    "BOOLEAN",
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DATE",
    "TIMESTAMP",
    "TIME",
    "VARCHAR",
}


def _coerce_parquet_type(dtype: str, redacted: bool) -> str:
    """Pick a write-safe DuckDB type.

    Once a column is redacted, force VARCHAR — masked/hashed values won't
    fit the original numeric/date type. Otherwise preserve a sensible
    DuckDB scalar type and fall back to VARCHAR for exotic ones.
    """
    if redacted:
        return "VARCHAR"
    upper = dtype.upper().split("(")[0]
    if any(upper.startswith(t) for t in _DUCKDB_SAFE_TYPES):
        return dtype
    return "VARCHAR"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _write_parquet(
    out_path: Path,
    columns: list[str],
    dtypes: list[str],
    redacted_cols: set[str],
    rows: list[list[Any]],
) -> None:
    con = duckdb.connect()
    type_specs = [
        f"{_quote_ident(c)} {_coerce_parquet_type(dt, c in redacted_cols)}"
        for c, dt in zip(columns, dtypes, strict=False)
    ]
    table = "_seance_redact_out"
    con.execute(f"CREATE TABLE {table} ({', '.join(type_specs)})")
    placeholders = ", ".join(["?"] * len(columns))
    if rows:
        # Stringify redacted cells whose target column is VARCHAR but whose
        # value may not yet be a string (e.g. hash() output is already str
        # but apply_value can return an int year as str either way).
        norm_rows = [
            [str(v) if v is not None and not isinstance(v, (int, float, bool)) else v for v in r]
            for r in rows
        ]
        con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", norm_rows)
    safe_out = str(out_path).replace("'", "''")
    con.execute(f"COPY {table} TO '{safe_out}' (FORMAT 'parquet')")
    con.close()


def run_redaction(
    src_path: Path,
    out_path: Path,
    plan: RedactionPlan,
    *,
    table: str | None = None,
    out_format: str | None = None,
    sheet: str | int | None = None,
) -> dict[str, int]:
    """Stream rows from *src_path*, apply *plan*, write to *out_path*.

    Returns a per-column count of cells actually changed (for the
    persona-flavoured summary). Mutates ``plan.counts`` in place too.
    """
    fmt = (out_format or infer_format(out_path)).lower()
    if fmt not in SUPPORTED_OUTPUT_FORMATS:
        raise RedactionError(
            f"Unsupported output format {fmt!r}. Choose one of: {sorted(SUPPORTED_OUTPUT_FORMATS)}."
        )

    relation = load(src_path, table=table, sheet=sheet)
    columns = list(relation.columns)
    dtypes = [str(t) for t in relation.types]
    fetched = relation.fetchall()
    rows: list[list[Any]] = [list(r) for r in fetched]

    counts: dict[str, int] = {}
    redacted_rows = [redact_row(r, columns, plan, counts=counts) for r in rows]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        _write_csv(out_path, columns, redacted_rows)
    elif fmt == "jsonl":
        _write_jsonl(out_path, columns, redacted_rows)
    else:
        redacted_cols = {a.column for a in plan.actions}
        _write_parquet(out_path, columns, dtypes, redacted_cols, redacted_rows)

    # Update counts on the plan (it's frozen but the dict field is mutable).
    plan.counts.clear()
    plan.counts.update(counts)
    return counts
