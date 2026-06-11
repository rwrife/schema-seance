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


def test_summon_placeholder() -> None:
    result = CliRunner().invoke(main, ["summon", "ghosts.csv"])
    assert result.exit_code == 0
    assert "ghosts.csv" in result.output
