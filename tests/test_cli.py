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


def _build_xlsx(path):
    pytest = __import__("pytest")
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "alpha"
    ws.append(["id", "value"])
    ws.append([1, "a"])
    ws.append([2, "b"])
    wb.create_sheet("beta").append(["only"])
    wb.save(path)


def test_summon_xlsx_active_sheet(tmp_path) -> None:
    book = tmp_path / "demo.xlsx"
    _build_xlsx(book)
    result = CliRunner().invoke(main, ["summon", str(book)])
    assert result.exit_code == 0, result.output
    assert "value" in result.output


def test_summon_xlsx_select_sheet_by_index(tmp_path) -> None:
    book = tmp_path / "demo.xlsx"
    _build_xlsx(book)
    result = CliRunner().invoke(main, ["summon", str(book), "--sheet", "0"])
    assert result.exit_code == 0, result.output


def test_summon_xlsx_missing_sheet_errors(tmp_path) -> None:
    book = tmp_path / "demo.xlsx"
    _build_xlsx(book)
    result = CliRunner().invoke(main, ["summon", str(book), "--sheet", "ghost"])
    assert result.exit_code == 2


def test_list_sheets_command(tmp_path) -> None:
    book = tmp_path / "demo.xlsx"
    _build_xlsx(book)
    result = CliRunner().invoke(main, ["list-sheets", str(book)])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output


def test_list_sheets_json(tmp_path) -> None:
    import json

    book = tmp_path / "demo.xlsx"
    _build_xlsx(book)
    result = CliRunner().invoke(main, ["list-sheets", str(book), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [s["name"] for s in payload["sheets"]] == ["alpha", "beta"]


def test_list_sheets_rejects_csv(tmp_path) -> None:
    csv = tmp_path / "not-a-book.csv"
    csv.write_text("a,b\n1,2\n")
    result = CliRunner().invoke(main, ["list-sheets", str(csv)])
    assert result.exit_code == 2
