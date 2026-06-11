"""File readers backed by DuckDB.

Each reader returns a `duckdb.DuckDBPyRelation` so the rest of the
pipeline can stay format-agnostic. `load()` dispatches on file extension.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import csv as csv_reader
from . import jsonl as jsonl_reader

__all__ = ["load", "UnsupportedFormatError"]


class UnsupportedFormatError(ValueError):
    """Raised when a file extension has no registered reader."""


_DISPATCH = {
    ".csv": csv_reader.read,
    ".tsv": csv_reader.read,
    ".jsonl": jsonl_reader.read,
    ".ndjson": jsonl_reader.read,
}


def load(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> duckdb.DuckDBPyRelation:
    """Load *path* into a DuckDB relation, dispatching on its suffix."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    reader = _DISPATCH.get(suffix)
    if reader is None:
        raise UnsupportedFormatError(f"No reader registered for extension {suffix!r} (path={p}).")
    con = connection or duckdb.connect()
    return reader(p, connection=con)
