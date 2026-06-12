"""File readers backed by DuckDB.

Each reader returns a `duckdb.DuckDBPyRelation` so the rest of the
pipeline can stay format-agnostic. `load()` dispatches on file extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from . import csv as csv_reader
from . import jsonl as jsonl_reader
from . import parquet as parquet_reader
from . import sqlite as sqlite_reader
from .sqlite import SQLiteTableError

__all__ = ["load", "UnsupportedFormatError", "SQLiteTableError"]


class UnsupportedFormatError(ValueError):
    """Raised when a file extension has no registered reader."""


_DISPATCH: dict[str, Any] = {
    ".csv": csv_reader.read,
    ".tsv": csv_reader.read,
    ".jsonl": jsonl_reader.read,
    ".ndjson": jsonl_reader.read,
    ".parquet": parquet_reader.read,
    ".pq": parquet_reader.read,
    ".sqlite": sqlite_reader.read,
    ".sqlite3": sqlite_reader.read,
    ".db": sqlite_reader.read,
}

_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


def load(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    table: str | None = None,
) -> duckdb.DuckDBPyRelation:
    """Load *path* into a DuckDB relation, dispatching on its suffix.

    Pass ``table`` to select a specific SQLite table; ignored for other
    formats.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    reader = _DISPATCH.get(suffix)
    if reader is None:
        raise UnsupportedFormatError(f"No reader registered for extension {suffix!r} (path={p}).")
    con = connection or duckdb.connect()
    if suffix in _SQLITE_SUFFIXES:
        return reader(p, connection=con, table=table)
    return reader(p, connection=con)
