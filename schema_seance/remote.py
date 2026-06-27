"""Remote input support: ``s3://`` and ``https://`` URIs for DuckDB readers.

Centralizes URL detection, format inference, DuckDB ``httpfs`` / ``aws``
extension loading, and friendly error wrapping so the rest of the codebase
can treat remote URIs as just another reader input.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from urllib.parse import urlparse

import duckdb

__all__ = [
    "REMOTE_SCHEMES",
    "RemoteAccessError",
    "is_remote",
    "remote_suffix",
    "ensure_httpfs",
    "configure_s3",
    "wrap_duckdb_error",
]

REMOTE_SCHEMES = frozenset({"s3", "http", "https"})


class RemoteAccessError(RuntimeError):
    """Raised when a remote URI cannot be reached or read."""


def is_remote(uri: str | os.PathLike[str]) -> bool:
    """Return True when *uri* is a remote URL (``s3://`` / ``http(s)://``)."""
    text = os.fspath(uri)
    scheme = urlparse(text).scheme.lower()
    return scheme in REMOTE_SCHEMES


def remote_suffix(uri: str) -> str:
    """Return the lowercase suffix (``.csv`` etc.) of a remote URL's path.

    Query strings and fragments are ignored. Returns ``""`` when the URL
    has no extension.
    """
    parsed = urlparse(uri)
    return PurePosixPath(parsed.path).suffix.lower()


def ensure_httpfs(connection: duckdb.DuckDBPyConnection) -> None:
    """Best-effort INSTALL+LOAD of the ``httpfs`` extension.

    ``INSTALL`` is allowed to fail (offline / already installed); a failing
    ``LOAD`` is surfaced as :class:`RemoteAccessError`.
    """
    try:
        connection.execute("INSTALL httpfs")
    except duckdb.Error:
        # Already installed or no network — try LOAD anyway.
        pass
    try:
        connection.execute("LOAD httpfs")
    except duckdb.Error as exc:  # pragma: no cover - depends on env
        raise RemoteAccessError(
            "Could not load DuckDB's `httpfs` extension. Install it with "
            "`duckdb` or check network connectivity."
        ) from exc


def configure_s3(
    connection: duckdb.DuckDBPyConnection,
    *,
    region: str | None = None,
) -> None:
    """Wire DuckDB's S3 client to the standard AWS credential chain.

    DuckDB ≥ 0.10 ships a ``CREATE SECRET`` provider (``credential_chain``)
    that walks env vars, ``~/.aws/credentials``, and IMDS. We try that first
    and fall back to setting individual S3 settings from env vars so older
    DuckDB versions still work.
    """
    chosen_region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    secret_sql = "CREATE OR REPLACE SECRET seance_s3 (TYPE s3, PROVIDER credential_chain"
    if chosen_region:
        secret_sql += f", REGION '{chosen_region}'"
    secret_sql += ")"
    try:
        connection.execute(secret_sql)
        return
    except duckdb.Error:
        # Older DuckDB or missing aws extension — fall back to SET statements.
        pass

    if chosen_region:
        connection.execute(f"SET s3_region='{_sql_escape(chosen_region)}'")
    key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    token = os.environ.get("AWS_SESSION_TOKEN")
    if key:
        connection.execute(f"SET s3_access_key_id='{_sql_escape(key)}'")
    if secret:
        connection.execute(f"SET s3_secret_access_key='{_sql_escape(secret)}'")
    if token:
        connection.execute(f"SET s3_session_token='{_sql_escape(token)}'")


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def wrap_duckdb_error(uri: str, exc: BaseException) -> RemoteAccessError:
    """Wrap a DuckDB error from a remote read with a friendly message."""
    return RemoteAccessError(f"The spirits couldn't reach {uri!r}: {exc}")
