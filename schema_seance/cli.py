"""Click entrypoint for the `seance` console script."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .llm import LLMUnavailableError
from .llm import load_config as load_llm_config
from .llm import read as llm_read
from .persona import GREETING_TAGLINE, GREETING_TITLE, greeting_panel_body
from .pii import CONFIDENCE_BANDS
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
@click.option(
    "--fail-on-pii",
    "fail_on_pii",
    type=click.Choice(["low", "medium", "high"], case_sensitive=False),
    default=None,
    help=(
        "Exit non-zero (code 3) when any PII finding meets or exceeds the given confidence band."
    ),
)
def summon(
    path: Path,
    table: str | None,
    sample: int | None,
    as_json: bool,
    fail_on_pii: str | None,
) -> None:
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
    else:
        render_terminal(report, console=console)

    if fail_on_pii is not None:
        threshold = CONFIDENCE_BANDS[fail_on_pii.lower()]
        worst = 0.0
        worst_kind = None
        worst_col = None
        for col in report.columns:
            for finding in col.pii:
                if finding.confidence > worst:
                    worst = finding.confidence
                    worst_kind = finding.kind
                    worst_col = col.name
        if worst >= threshold:
            if not as_json:
                console.print(
                    f"[bold red]The veil refuses you.[/bold red] "
                    f"{worst_kind} in column '{worst_col}' "
                    f"(confidence {worst:.2f} ≥ {fail_on_pii} threshold "
                    f"{threshold:.2f})."
                )
            raise SystemExit(3)


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
    "--timeout",
    "timeout",
    type=click.FloatRange(min=1.0),
    default=30.0,
    show_default=True,
    help="Hard timeout (seconds) for the LLM call.",
)
@click.option(
    "--show-profile/--no-show-profile",
    "show_profile",
    default=True,
    show_default=True,
    help="Print the standard profile before the reading.",
)
def read(
    path: Path,
    table: str | None,
    sample: int | None,
    timeout: float,
    show_profile: bool,
) -> None:
    """Profile a file, then ask an LLM for a 3-paragraph reading."""
    console = Console()
    try:
        relation = load(path, table=table)
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title="\U0001f52e Madame Schema",
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title="\U0001f52e Madame Schema",
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    report = profile_relation(relation, path=path, sample=sample)
    if show_profile:
        render_terminal(report, console=console)

    try:
        config = load_llm_config(timeout=timeout)
    except LLMUnavailableError as exc:
        console.print(
            Panel(
                f"The veil stays drawn. {exc}",
                title="\U0001f52e Madame Schema",
                border_style="yellow",
            )
        )
        raise SystemExit(4) from exc

    try:
        result = llm_read(report, config)
    except LLMUnavailableError as exc:
        console.print(
            Panel(
                f"The spirits would not speak through {config.model}. {exc}",
                title="\U0001f52e Madame Schema",
                border_style="yellow",
            )
        )
        raise SystemExit(4) from exc

    console.print(
        Panel(
            result.text,
            title=Text("\U0001f52e A Reading", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )

    bits: list[str] = [f"model=[bold]{result.model}[/bold]"]
    if result.total_tokens is not None:
        bits.append(
            f"tokens={result.total_tokens} "
            f"(prompt {result.prompt_tokens}, completion {result.completion_tokens})"
        )
    if result.cost_usd is not None:
        bits.append(f"cost \u2248 ${result.cost_usd:.4f}")
    bits.append(f"in {result.elapsed_seconds:.2f}s")
    console.print("  " + " \u00b7 ".join(bits), style="dim")


if __name__ == "__main__":  # pragma: no cover
    main()
