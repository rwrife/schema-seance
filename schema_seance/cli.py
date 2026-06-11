"""Click entrypoint for the `seance` console script."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .persona import GREETING_TAGLINE, GREETING_TITLE, greeting_panel_body


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
@click.argument("path", type=click.Path(exists=False))
def summon(path: str) -> None:
    """Summon the schema of a data file. (Coming in M2.)"""
    console = Console()
    console.print(
        Panel(
            f"The spirits stir, but their voices are not yet clear for [bold]{path}[/bold].\n"
            "Readers arrive in [italic]M2[/italic] — see PLAN.md.",
            title="🔮 Madame Schema",
            border_style="magenta",
        )
    )


if __name__ == "__main__":  # pragma: no cover
    main()
