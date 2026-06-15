"""Regenerate the SVG demo assets embedded in README.

Usage:
    uv run python docs/demo/generate.py

Re-run whenever the greeting or rendered profile output changes so the
README screenshots don't drift.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from schema_seance.cli import _print_greeting
from schema_seance.profile import profile as profile_relation
from schema_seance.readers import load
from schema_seance.render.terminal import render as render_terminal


def _write_greeting(out_dir: Path) -> Path:
    console = Console(record=True, width=78)
    _print_greeting(console)
    target = out_dir / "greeting.svg"
    console.save_svg(str(target), title="seance")
    return target


def _write_summon(out_dir: Path, fixture: Path) -> Path:
    console = Console(record=True, width=110)
    relation = load(fixture)
    report = profile_relation(relation, path=fixture)
    render_terminal(report, console)
    target = out_dir / "summon-tiny.svg"
    console.save_svg(str(target), title=f"seance summon {fixture.name}")
    return target


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    fixture = Path("tests/fixtures/tiny.csv")
    paths = [_write_greeting(out_dir), _write_summon(out_dir, fixture)]
    for p in paths:
        print(f"wrote {p.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
