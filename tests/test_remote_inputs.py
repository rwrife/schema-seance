"""Tests for remote input support (issue #29).

These tests don't hit the network — they exercise the URI dispatch path
(extension inference, scheme detection, error wrapping) and stub out
the actual DuckDB read so we can verify the pipeline plumbing.
"""

from __future__ import annotations

from unittest.mock import patch

import duckdb
import pytest

from schema_seance.readers import RemoteAccessError, UnsupportedFormatError, load
from schema_seance.remote import (
    REMOTE_SCHEMES,
    configure_s3,
    ensure_httpfs,
    is_remote,
    remote_suffix,
    wrap_duckdb_error,
)


def test_is_remote_detects_s3_and_https() -> None:
    assert is_remote("s3://bucket/key.parquet") is True
    assert is_remote("https://example.com/data.csv") is True
    assert is_remote("http://example.com/data.csv") is True
    assert is_remote("/tmp/file.csv") is False
    assert is_remote("file.csv") is False


def test_remote_schemes_set() -> None:
    assert REMOTE_SCHEMES == frozenset({"s3", "http", "https"})


def test_remote_suffix_strips_query_and_fragment() -> None:
    assert remote_suffix("https://x.com/a.csv?token=abc#frag") == ".csv"
    assert remote_suffix("s3://b/k/file.parquet") == ".parquet"
    assert remote_suffix("https://x.com/noext") == ""


def test_load_remote_unknown_extension_raises_helpful_error() -> None:
    with pytest.raises(UnsupportedFormatError) as info:
        load("https://example.com/mystery")
    assert "--format" in str(info.value)


def test_load_remote_sqlite_rejected_with_friendly_message() -> None:
    with pytest.raises(UnsupportedFormatError) as info:
        load("https://example.com/file.sqlite")
    assert "SQLite" in str(info.value)


def test_load_remote_csv_calls_httpfs_and_csv_reader() -> None:
    con = duckdb.connect()
    with (
        patch("schema_seance.readers.ensure_httpfs") as mock_httpfs,
        patch("schema_seance.readers.csv_reader") as mock_csv,
    ):
        sentinel = object()
        mock_csv.read.return_value = sentinel
        result = load("https://example.com/data.csv", connection=con)
        assert result is sentinel
        mock_httpfs.assert_called_once_with(con)
        mock_csv.read.assert_called_once()
        # Reader must receive the URL verbatim, not a Path()-mangled version.
        args, kwargs = mock_csv.read.call_args
        assert args[0] == "https://example.com/data.csv"
        assert kwargs.get("connection") is con


def test_load_remote_s3_configures_s3_credentials() -> None:
    con = duckdb.connect()
    with (
        patch("schema_seance.readers.ensure_httpfs"),
        patch("schema_seance.readers.configure_s3") as mock_s3,
        patch("schema_seance.readers.parquet_reader") as mock_pq,
    ):
        mock_pq.read.return_value = "ok"
        load("s3://bucket/key.parquet", connection=con, region="us-west-2")
        mock_s3.assert_called_once_with(con, region="us-west-2")


def test_load_remote_format_override_wins_over_extension() -> None:
    con = duckdb.connect()
    with (
        patch("schema_seance.readers.ensure_httpfs"),
        patch("schema_seance.readers.jsonl_reader") as mock_jsonl,
    ):
        mock_jsonl.read.return_value = "ok"
        load("https://example.com/opaque?x=1", connection=con, format="jsonl")
        mock_jsonl.read.assert_called_once()


def test_load_remote_wraps_duckdb_error_with_friendly_message() -> None:
    con = duckdb.connect()
    with (
        patch("schema_seance.readers.ensure_httpfs"),
        patch("schema_seance.readers.csv_reader") as mock_csv,
    ):
        mock_csv.read.side_effect = duckdb.Error("HTTP 404")
        with pytest.raises(RemoteAccessError) as info:
            load("https://example.com/missing.csv", connection=con)
        assert "https://example.com/missing.csv" in str(info.value)
        assert "404" in str(info.value)


def test_ensure_httpfs_is_idempotent_and_doesnt_explode() -> None:
    con = duckdb.connect()
    # Should not raise even if called twice; INSTALL failure is swallowed.
    ensure_httpfs(con)
    ensure_httpfs(con)


def test_configure_s3_uses_aws_region_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    con = duckdb.connect()
    ensure_httpfs(con)
    # Should not raise; either CREATE SECRET path or fallback SET path runs.
    configure_s3(con)


def test_wrap_duckdb_error_includes_uri() -> None:
    err = wrap_duckdb_error("s3://bucket/key.parquet", RuntimeError("boom"))
    assert isinstance(err, RemoteAccessError)
    assert "s3://bucket/key.parquet" in str(err)


def test_load_local_path_still_works_unchanged(tmp_path) -> None:
    p = tmp_path / "tiny.csv"
    p.write_text("a,b\n1,2\n3,4\n")
    rel = load(p)
    assert rel.count("*").fetchone()[0] == 2


def test_load_local_format_override(tmp_path) -> None:
    # An ambiguous extension forced to CSV via --format.
    p = tmp_path / "weirdext.dat"
    p.write_text("a,b\n1,2\n")
    rel = load(p, format="csv")
    assert list(rel.columns) == ["a", "b"]
