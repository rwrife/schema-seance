"""Tests for the `seance parlor` TUI mode.

The Textual app itself is not driven in CI (no PTY), but the
session/data layer it sits on top of is fully tested here, plus a CLI
smoke test that the command exists and rejects bad input cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.parlor import load_session

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_session_csv_exposes_columns_and_count() -> None:
    sess = load_session(FIXTURES / "tiny.csv")
    names = [n for n, _ in sess.columns]
    assert "id" in names
    assert "name" in names
    assert sess.row_count >= 1
    assert sess.view_name == "data"


def test_session_sample_returns_rows() -> None:
    sess = load_session(FIXTURES / "tiny.csv")
    names, rows = sess.sample(limit=10)
    assert names
    assert len(rows) <= 10
    assert len(rows) >= 1


def test_session_run_sql_filters() -> None:
    sess = load_session(FIXTURES / "tiny.csv")
    names, rows = sess.run_sql("SELECT name FROM data WHERE id = 1")
    assert names == ["name"]
    assert len(rows) == 1


def test_session_run_sql_respects_explicit_limit() -> None:
    sess = load_session(FIXTURES / "tiny.csv")
    _, rows = sess.run_sql("SELECT * FROM data LIMIT 1")
    assert len(rows) == 1


def test_session_run_sql_empty_returns_empty() -> None:
    sess = load_session(FIXTURES / "tiny.csv")
    names, rows = sess.run_sql("   ")
    assert names == []
    assert rows == []


def test_session_run_sql_bad_query_raises() -> None:
    import duckdb

    sess = load_session(FIXTURES / "tiny.csv")
    with pytest.raises(duckdb.Error):
        sess.run_sql("SELECT * FROM no_such_view")


def test_parlor_command_registered() -> None:
    result = CliRunner().invoke(main, ["parlor", "--help"])
    assert result.exit_code == 0, result.output
    assert "parlor" in result.output.lower()


def test_parlor_unsupported_extension_errors(tmp_path: Path) -> None:
    weird = tmp_path / "ouija.xyz"
    weird.write_text("nope")
    result = CliRunner().invoke(main, ["parlor", str(weird)])
    assert result.exit_code == 2


def test_parlor_missing_file_errors() -> None:
    result = CliRunner().invoke(main, ["parlor", "definitely-not-here.csv"])
    assert result.exit_code != 0
