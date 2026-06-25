"""Tests for HTML report output (`--html`)."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.profile import profile as profile_relation
from schema_seance.readers import load
from schema_seance.render.html import dumps as dumps_html


class _StrictParser(HTMLParser):
    """Tiny HTML parser that errors on malformed input."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []

    def error(self, message: str) -> None:  # pragma: no cover - rarely invoked
        self.errors.append(message)


def _assert_parses(html: str) -> None:
    p = _StrictParser()
    p.feed(html)
    p.close()
    assert not p.errors, p.errors


def _make_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "ghosts.csv"
    csv.write_text(
        "id,email,name\n"
        "1,alice@example.com,Alice\n"
        "2,bob@example.com,Bob\n"
        "3,carol@example.com,Carol\n"
    )
    return csv


def test_dumps_html_produces_self_contained_doc(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    report = profile_relation(load(csv), path=csv)
    html = dumps_html(report)
    _assert_parses(html)
    assert html.startswith("<!DOCTYPE html>")
    # All sections present
    assert "The Veil Parts" in html
    assert "The Spirits Speak" in html
    # No external assets/CDN
    lower = html.lower()
    assert "<link" not in lower
    assert "<script" not in lower
    assert "http://" not in lower
    assert "https://" not in lower


def test_dumps_html_includes_pii_band(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    report = profile_relation(load(csv), path=csv)
    html = dumps_html(report)
    # Email column should trigger PII whispers section
    assert "Whispers of the Personal" in html
    assert "band-" in html  # confidence band CSS class applied


def test_dumps_html_escapes_user_data(tmp_path: Path) -> None:
    csv = tmp_path / "naughty.csv"
    csv.write_text('id,name\n1,"<script>alert(1)</script>"\n')
    report = profile_relation(load(csv), path=csv)
    html = dumps_html(report)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_summon_html_flag_writes_file(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    out = tmp_path / "report.html"
    result = CliRunner().invoke(main, ["summon", str(csv), "--html", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "The Veil Parts" in content
    assert "The Spirits Speak" in content
    # Terminal output still rendered by default
    assert "The Veil Parts" in result.output


def test_summon_html_with_quiet_silences_stdout(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    out = tmp_path / "q.html"
    result = CliRunner().invoke(main, ["summon", str(csv), "--html", str(out), "--quiet"])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "The Veil Parts" not in result.output
    assert result.output.strip() == ""


def test_summon_html_and_json_coexist(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    out = tmp_path / "both.html"
    result = CliRunner().invoke(main, ["summon", str(csv), "--html", str(out), "--json"])
    assert result.exit_code == 0, result.output
    assert out.exists()
    # JSON went to stdout
    assert result.output.lstrip().startswith("{")
    assert '"schema_version"' in result.output


def test_summon_html_respects_persona(tmp_path: Path) -> None:
    csv = _make_csv(tmp_path)
    out = tmp_path / "pirate.html"
    result = CliRunner().invoke(
        main, ["--persona", "pirate", "summon", str(csv), "--html", str(out)]
    )
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    # Pirate persona display name shows in banner
    from schema_seance.personas import PERSONAS

    assert PERSONAS["pirate"].display_name in content
