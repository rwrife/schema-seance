"""File readers backed by DuckDB.

Each reader returns a `duckdb.DuckDBPyRelation` so the rest of the
pipeline can stay format-agnostic. `load()` dispatches on URI scheme
(remote vs local) and then on file extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from ..remote import (
    RemoteAccessError,
    configure_s3,
    ensure_httpfs,
    is_remote,
    remote_suffix,
    wrap_duckdb_error,
)
from . import csv as csv_reader
from . import excel as excel_reader
from . import jsonl as jsonl_reader
from . import parquet as parquet_reader
from . import sqlite as sqlite_reader
from .excel import ExcelReaderError
from .sqlite import SQLiteTableError

__all__ = [
    "load",
    "UnsupportedFormatError",
    "SQLiteTableError",
    "ExcelReaderError",
    "RemoteAccessError",
]


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
    ".xlsx": excel_reader.read,
    ".xlsm": excel_reader.read,
}

_REMOTE_DISPATCH: dict[str, str] = {
    ".csv": "csv_reader",
    ".tsv": "csv_reader",
    ".jsonl": "jsonl_reader",
    ".ndjson": "jsonl_reader",
    ".parquet": "parquet_reader",
    ".pq": "parquet_reader",
}

_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}

_FORMAT_TO_SUFFIX = {
    "csv": ".csv",
    "tsv": ".tsv",
    "jsonl": ".jsonl",
    "ndjson": ".ndjson",
    "parquet": ".parquet",
}


def load(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    table: str | None = None,
    format: str | None = None,
    region: str | None = None,
    sheet: str | int | None = None,
) -> duckdb.DuckDBPyRelation:
    """Load *path* into a DuckDB relation.

    Accepts a local filesystem path or a remote URI (``s3://…``,
    ``http(s)://…``). Dispatch is scheme-first, extension-second; pass
    ``format`` (one of ``csv``/``tsv``/``jsonl``/``ndjson``/``parquet``)
    to override extension inference (useful for opaque URLs).

    Pass ``table`` to select a specific SQLite table; ignored for other
    formats. ``region`` overrides the S3 region for remote S3 inputs.
    """
    uri = str(path)
    if is_remote(uri):
        return _load_remote(
            uri,
            connection=connection,
            format=format,
            region=region,
        )

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if format is not None:
        suffix = _resolve_format(format)
    else:
        suffix = p.suffix.lower()
    reader = _DISPATCH.get(suffix)
    if reader is None:
        raise UnsupportedFormatError(f"No reader registered for extension {suffix!r} (path={p}).")
    con = connection or duckdb.connect()
    if suffix in _SQLITE_SUFFIXES:
        return reader(p, connection=con, table=table)
    if suffix in _EXCEL_SUFFIXES:
        return reader(p, connection=con, sheet=sheet)
    return reader(p, connection=con)


def _resolve_format(format: str) -> str:
    key = format.lower().lstrip(".")
    if key not in _FORMAT_TO_SUFFIX:
        raise UnsupportedFormatError(
            f"Unknown --format {format!r}. Choose one of: {', '.join(sorted(_FORMAT_TO_SUFFIX))}."
        )
    return _FORMAT_TO_SUFFIX[key]


def _load_remote(
    uri: str,
    *,
    connection: duckdb.DuckDBPyConnection | None,
    format: str | None,
    region: str | None,
) -> duckdb.DuckDBPyRelation:
    if format is not None:
        suffix = _resolve_format(format)
    else:
        suffix = remote_suffix(uri)
    reader_name = _REMOTE_DISPATCH.get(suffix)
    if reader_name is None:
        if suffix in _SQLITE_SUFFIXES:
            raise UnsupportedFormatError(
                "SQLite files cannot be read directly over the network — "
                "download the .sqlite file locally first."
            )
        raise UnsupportedFormatError(
            f"No remote reader for extension {suffix!r} (uri={uri}). "
            "Pass --format to override extension inference."
        )
    # Resolve through globals() so tests can monkey-patch readers.csv_reader etc.
    reader_module = globals()[reader_name]
    con = connection or duckdb.connect()
    ensure_httpfs(con)
    if uri.lower().startswith("s3://"):
        configure_s3(con, region=region)
    try:
        return reader_module.read(uri, connection=con)
    except duckdb.Error as exc:
        raise wrap_duckdb_error(uri, exc) from exc
