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
    p = str(Path(path)).replace("'", "''")
    return connection.sql(f"SELECT * FROM read_json_auto('{p}', format='newline_delimited')")
