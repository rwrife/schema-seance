"""JSONL/NDJSON reader backed by DuckDB's `read_json_auto`."""

from __future__ import annotations

from pathlib import Path

import duckdb


def read(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyRelation:
    """Return a DuckDB relation over the JSONL/NDJSON file at *path*."""
    raw = path if isinstance(path, str) else str(Path(path))
    p = raw.replace("'", "''")
    return connection.sql(f"SELECT * FROM read_json_auto('{p}', format='newline_delimited')")
