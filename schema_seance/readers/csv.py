"""CSV reader backed by DuckDB's `read_csv_auto`."""

from __future__ import annotations

from pathlib import Path

import duckdb


def read(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyRelation:
    """Return a DuckDB relation over the CSV/TSV at *path*."""
    raw = path if isinstance(path, str) else str(Path(path))
    p = raw.replace("'", "''")
    # DuckDB sniffs delimiter, header, types automatically.
    return connection.sql(f"SELECT * FROM read_csv_auto('{p}', sample_size=-1)")
