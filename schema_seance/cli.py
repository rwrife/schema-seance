"""Click entrypoint for the `seance` CLI."""

from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .persona import default_persona


def _greet(console: Console) -> None:
    persona = default_persona()
    body = Text()
    body.append(f"{persona.greeting}\n\n", style="italic magenta")
    body.append(persona.tagline, style="dim")
    panel = Panel(
        body,
        title=f"🔮 {persona.name}",
        subtitle=f"schema-seance v{__version__}",
        border_style="magenta",
        padding=(1, 2),
    )
    console.print(panel)
    console.print(
        "[dim]Try [bold]seance summon <file>[/bold] once the spirits are ready "
        "(coming in M2).[/dim]"
    )


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="seance")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Summon schemas, PII, and anomalies from your data files."""
    if ctx.invoked_subcommand is None:
        _greet(Console())


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def summon(file: str) -> None:
    """Channel a data file (CSV/JSONL/Parquet/SQLite). Not yet implemented."""
    console = Console()
    console.print(f"[magenta italic]Madame Schema reaches toward[/] [bold]{file}[/bold]…")
    console.print("[yellow]…but the spirits are still gathering. Profiling lands in M2.[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    main()
