"""Smoke tests for the `seance` CLI."""

from __future__ import annotations

from click.testing import CliRunner

from schema_seance import __version__
from schema_seance.cli import main
from schema_seance.persona import GREETING_TITLE


def test_no_args_prints_greeting() -> None:
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0
    assert GREETING_TITLE in result.output


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_summon_missing_file_errors() -> None:
    result = CliRunner().invoke(main, ["summon", "definitely-not-here.csv"])
    assert result.exit_code != 0


def test_summon_csv_renders_sections(tmp_path) -> None:
    csv = tmp_path / "ghosts.csv"
    csv.write_text("id,name\n1,Alice\n2,Bob\n")
    result = CliRunner().invoke(main, ["summon", str(csv)])
    assert result.exit_code == 0, result.output
    assert "The Veil Parts" in result.output
    assert "The Spirits Speak" in result.output
    assert "name" in result.output


def test_summon_unsupported_extension_errors(tmp_path) -> None:
    weird = tmp_path / "ouija.xyz"
    weird.write_text("nope")
    result = CliRunner().invoke(main, ["summon", str(weird)])
    assert result.exit_code == 2
