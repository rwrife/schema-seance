"""Click entrypoint for the `seance` console script."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .persona import GREETING_TAGLINE, GREETING_TITLE, greeting_panel_body
from .profile import profile as profile_relation
from .readers import UnsupportedFormatError, load
from .render.terminal import render as render_terminal


def _print_greeting(console: Console) -> None:
    title = Text(f"🔮 {GREETING_TITLE}", style="bold magenta")
    title.append(f"  ·  {GREETING_TAGLINE}", style="italic dim")
    console.print(
        Panel(
            greeting_panel_body(),
            title=title,
            border_style="magenta",
            padding=(1, 2),
        )
    )


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="seance")
@click.pass_context
def main(ctx: click.Context) -> None:
    """schema-seance — channel the spirits of your data."""
    if ctx.invoked_subcommand is None:
        _print_greeting(Console())


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
def summon(path: Path) -> None:
    """Summon the schema of a data file (CSV or JSONL for now)."""
    console = Console()
    try:
        relation = load(path)
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}\n"
                "Supported in M2: [bold].csv[/bold], [bold].tsv[/bold], "
                "[bold].jsonl[/bold], [bold].ndjson[/bold]. "
                "Parquet + SQLite arrive in [italic]M3[/italic].",
                title="🔮 Madame Schema",
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    report = profile_relation(relation, path=path)
    render_terminal(report, console=console)


if __name__ == "__main__":  # pragma: no cover
    main()
