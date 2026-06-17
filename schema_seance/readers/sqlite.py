"""SQLite reader backed by DuckDB's `sqlite_scanner`."""

from __future__ import annotations

from pathlib import Path

import duckdb


class SQLiteTableError(ValueError):
    """Raised when a requested table cannot be located in a SQLite file."""


def _attach(connection: duckdb.DuckDBPyConnection, path: Path) -> str:
    """Install + load sqlite_scanner and ATTACH the DB. Returns the alias."""
    try:
        connection.execute("INSTALL sqlite")
    except duckdb.Error:
        # Already installed / offline: best-effort, LOAD may still succeed.
        pass
    connection.execute("LOAD sqlite")
    alias = "seance_sqlite"
    quoted = str(path).replace("'", "''")
    connection.execute(f"ATTACH '{quoted}' AS {alias} (TYPE sqlite, READ_ONLY)")
    return alias


def list_tables(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> list[str]:
    """Return the user tables in *path*, in their natural order."""
    con = connection or duckdb.connect()
    alias = _attach(con, Path(path))
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog = ? AND table_schema = 'main' "
        "ORDER BY table_name",
        [alias],
    ).fetchall()
    return [r[0] for r in rows]


def read(
    path: str | Path,
    *,
    connection: duckdb.DuckDBPyConnection,
    table: str | None = None,
) -> duckdb.DuckDBPyRelation:
    """Return a DuckDB relation over a table in the SQLite DB at *path*.

    Defaults to the alphabetically-first user table when *table* is omitted.
    """
    p = Path(path)
    alias = _attach(connection, p)
    tables = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog = ? AND table_schema = 'main' "
        "ORDER BY table_name",
        [alias],
    ).fetchall()
    names = [r[0] for r in tables]
    if not names:
        raise SQLiteTableError(f"No tables found in SQLite file {p}.")
    if table is None:
        chosen = names[0]
    elif table in names:
        chosen = table
    else:
        joined = ", ".join(names)
        raise SQLiteTableError(f"Table {table!r} not found in {p}. Available: {joined}.")
    quoted_alias = alias.replace('"', '""')
    quoted_table = chosen.replace('"', '""')
    return connection.sql(f'SELECT * FROM "{quoted_alias}"."{quoted_table}"')
