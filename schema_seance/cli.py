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
from .readers import SQLiteTableError, UnsupportedFormatError, load
from .render.json import dumps as dumps_json
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
@click.option(
    "--table",
    "table",
    default=None,
    help="Table name to read (SQLite only). Defaults to the first table.",
)
@click.option(
    "--sample",
    "sample",
    type=click.IntRange(min=1),
    default=None,
    help="Profile only the first N rows. Useful on huge files.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a stable JSON document instead of the pretty terminal view.",
)
def summon(path: Path, table: str | None, sample: int | None, as_json: bool) -> None:
    """Summon the schema of a data file (CSV/JSONL/Parquet/SQLite)."""
    console = Console()
    try:
        relation = load(path, table=table)
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}\n"
                "Supported: [bold].csv[/bold], [bold].tsv[/bold], "
                "[bold].jsonl[/bold], [bold].ndjson[/bold], "
                "[bold].parquet[/bold], [bold].sqlite[/bold] / "
                "[bold].db[/bold].",
                title="🔮 Madame Schema",
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title="🔮 Madame Schema",
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    report = profile_relation(relation, path=path, sample=sample)

    if as_json:
        click.echo(dumps_json(report))
        return

    render_terminal(report, console=console)


if __name__ == "__main__":  # pragma: no cover
    main()
