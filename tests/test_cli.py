"""Smoke tests for the `seance` CLI."""

from __future__ import annotations

from click.testing import CliRunner

from schema_seance import __version__
from schema_seance.cli import main
from schema_seance.persona import default_persona


def test_no_args_greets_with_persona() -> None:
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    persona = default_persona()
    # Persona name appears in the panel title
    assert persona.name in result.output


def test_version_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_summon_placeholder(tmp_path) -> None:
    f = tmp_path / "ghost.csv"
    f.write_text("a,b\n1,2\n")
    runner = CliRunner()
    result = runner.invoke(main, ["summon", str(f)])
    assert result.exit_code == 0
    assert "M2" in result.output
