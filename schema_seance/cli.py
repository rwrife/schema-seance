"""Click entrypoint for the `seance` console script."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .diff import SEVERITY_BANDS
from .diff import compare as compare_reports
from .llm import LLMUnavailableError
from .llm import load_config as load_llm_config
from .llm import read as llm_read
from .parlor import ParlorUnavailableError, build_app, load_session
from .personas import Persona, UnknownPersonaError, available_ids
from .personas import resolve as resolve_persona
from .pii import CONFIDENCE_BANDS
from .profile import profile as profile_relation
from .readers import RemoteAccessError, SQLiteTableError, UnsupportedFormatError, load
from .redact import (
    DEFAULT_STRATEGY,
    RedactionError,
    parse_strategy_overrides,
)
from .redact import (
    build_plan as build_redaction_plan,
)
from .redact_run import (
    SUPPORTED_OUTPUT_FORMATS,
    infer_format,
    run_redaction,
)
from .remote import is_remote
from .render.diff import dumps as dumps_diff_json
from .render.diff import render_terminal as render_diff_terminal
from .render.html import dumps as dumps_html
from .render.json import dumps as dumps_json
from .render.terminal import render as render_terminal


class _DataInput(click.ParamType):
    """Click type that accepts a local file path OR a remote URL.

    Local paths must exist and be readable; remote URLs (``s3://``,
    ``http(s)://``) are passed through as strings.
    """

    name = "path_or_url"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if value is None:
            return value
        text = str(value)
        if is_remote(text):
            return text
        local = click.Path(exists=True, dir_okay=False, readable=True, path_type=Path)
        return local.convert(value, param, ctx)


DATA_INPUT = _DataInput()


def _print_greeting(console: Console, persona: Persona) -> None:
    title = Text(persona.panel_title, style="bold magenta")
    title.append(f"  ·  {persona.tagline}", style="italic dim")
    console.print(
        Panel(
            persona.greeting_body(),
            title=title,
            border_style="magenta",
            padding=(1, 2),
        )
    )


def _persona_from_ctx(ctx: click.Context) -> Persona:
    """Get the resolved persona from the click context (always populated)."""
    persona = (ctx.obj or {}).get("persona")
    if persona is None:
        persona = resolve_persona()
        ctx.ensure_object(dict)["persona"] = persona
    return persona


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="seance")
@click.option(
    "--persona",
    "persona_name",
    type=click.Choice(available_ids(), case_sensitive=False),
    default=None,
    help=(
        "Narrator voice. Defaults to SEANCE_PERSONA env var, then 'madame'. "
        f"Available: {', '.join(available_ids())}."
    ),
)
@click.pass_context
def main(ctx: click.Context, persona_name: str | None) -> None:
    """schema-seance — channel the spirits of your data."""
    try:
        persona = resolve_persona(persona_name)
    except UnknownPersonaError as exc:  # pragma: no cover - Click guards via Choice
        raise click.BadParameter(str(exc), param_hint="--persona") from exc
    ctx.ensure_object(dict)["persona"] = persona
    if ctx.invoked_subcommand is None:
        _print_greeting(Console(), persona)


@main.command()
@click.argument(
    "path",
    type=DATA_INPUT,
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
@click.option(
    "--html",
    "html_path",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Also write a self-contained HTML report to the given path.",
)
@click.option(
    "--quiet",
    "quiet",
    is_flag=True,
    default=False,
    help="Suppress stdout output (still writes --html / --json files).",
)
@click.option(
    "--format",
    "format_override",
    type=click.Choice(["csv", "tsv", "jsonl", "ndjson", "parquet"], case_sensitive=False),
    default=None,
    help="Override format inference (useful for opaque remote URLs).",
)
@click.option(
    "--region",
    "region",
    default=None,
    help="AWS region for s3:// inputs (overrides AWS_REGION env var).",
)
@click.pass_context
def summon(
    ctx: click.Context,
    path: Path | str,
    table: str | None,
    sample: int | None,
    as_json: bool,
    fail_on_pii: str | None,
    html_path: Path | None,
    quiet: bool,
    format_override: str | None,
    region: str | None,
) -> None:
    """Summon the schema of a data file (CSV/JSONL/Parquet/SQLite)."""
    console = Console()
    persona = _persona_from_ctx(ctx)
    try:
        relation = load(path, table=table, format=format_override, region=region)
    except RemoteAccessError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}\n"
                "Supported: [bold].csv[/bold], [bold].tsv[/bold], "
                "[bold].jsonl[/bold], [bold].ndjson[/bold], "
                "[bold].parquet[/bold], [bold].sqlite[/bold] / "
                "[bold].db[/bold].",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    report = profile_relation(relation, path=path, sample=sample)

    if html_path is not None:
        html_path.write_text(
            dumps_html(report, persona=persona, title=f"Seance — {path.name}"),
            encoding="utf-8",
        )

    if quiet:
        pass
    elif as_json:
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
            if not as_json and not quiet:
                console.print(
                    f"[bold red]{persona.refusal_phrase}[/bold red] "
                    f"{worst_kind} in column '{worst_col}' "
                    f"(confidence {worst:.2f} ≥ {fail_on_pii} threshold "
                    f"{threshold:.2f})."
                )
            raise SystemExit(3)


@main.command()
@click.argument(
    "path",
    type=DATA_INPUT,
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
@click.option(
    "--html",
    "html_path",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Also write a self-contained HTML report (including the reading) to the given path.",
)
@click.option(
    "--quiet",
    "quiet",
    is_flag=True,
    default=False,
    help="Suppress stdout output (still writes --html files).",
)
@click.option(
    "--format",
    "format_override",
    type=click.Choice(["csv", "tsv", "jsonl", "ndjson", "parquet"], case_sensitive=False),
    default=None,
    help="Override format inference (useful for opaque remote URLs).",
)
@click.option(
    "--region",
    "region",
    default=None,
    help="AWS region for s3:// inputs (overrides AWS_REGION env var).",
)
@click.pass_context
def read(
    ctx: click.Context,
    path: Path | str,
    table: str | None,
    sample: int | None,
    timeout: float,
    show_profile: bool,
    html_path: Path | None,
    quiet: bool,
    format_override: str | None,
    region: str | None,
) -> None:
    """Profile a file, then ask an LLM for a 3-paragraph reading."""
    console = Console()
    persona = _persona_from_ctx(ctx)
    try:
        relation = load(path, table=table, format=format_override, region=region)
    except RemoteAccessError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    report = profile_relation(relation, path=path, sample=sample)
    if show_profile and not quiet:
        render_terminal(report, console=console)

    try:
        config = load_llm_config(timeout=timeout)
    except LLMUnavailableError as exc:
        console.print(
            Panel(
                f"The veil stays drawn. {exc}",
                title=persona.panel_title,
                border_style="yellow",
            )
        )
        raise SystemExit(4) from exc

    try:
        result = llm_read(report, config, persona=persona)
    except LLMUnavailableError as exc:
        console.print(
            Panel(
                f"The spirits would not speak through {config.model}. {exc}",
                title=persona.panel_title,
                border_style="yellow",
            )
        )
        raise SystemExit(4) from exc

    if html_path is not None:
        html_path.write_text(
            dumps_html(
                report,
                persona=persona,
                reading=result,
                title=f"Seance Reading \u2014 {path.name}",
            ),
            encoding="utf-8",
        )

    if quiet:
        return

    console.print(
        Panel(
            result.text,
            title=Text(persona.reading_panel_title, style="bold magenta"),
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


def _load_and_profile(
    console: Console,
    persona: Persona,
    path: Path,
    *,
    table: str | None,
    sample: int | None,
):
    try:
        relation = load(path, table=table)
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    return profile_relation(relation, path=path, sample=sample)


@main.command()
@click.argument(
    "before",
    type=DATA_INPUT,
)
@click.argument(
    "after",
    type=DATA_INPUT,
)
@click.option(
    "--before-table",
    "before_table",
    default=None,
    help="Table name to read from BEFORE file (SQLite only).",
)
@click.option(
    "--after-table",
    "after_table",
    default=None,
    help="Table name to read from AFTER file (SQLite only).",
)
@click.option(
    "--sample",
    "sample",
    type=click.IntRange(min=1),
    default=None,
    help="Profile only the first N rows of each file.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a stable JSON diff document instead of the terminal view.",
)
@click.option(
    "--fail-on",
    "fail_on",
    type=click.Choice(["low", "medium", "high"], case_sensitive=False),
    default=None,
    help=(
        "Exit non-zero (code 5) when the overall diff severity meets or "
        "exceeds the given level. Useful for CI data-contract gates."
    ),
)
@click.pass_context
def compare(
    ctx: click.Context,
    before: Path,
    after: Path,
    before_table: str | None,
    after_table: str | None,
    sample: int | None,
    as_json: bool,
    fail_on: str | None,
) -> None:
    """Compare two data files and surface schema/PII/distribution drift."""
    console = Console()
    persona = _persona_from_ctx(ctx)
    before_report = _load_and_profile(console, persona, before, table=before_table, sample=sample)
    after_report = _load_and_profile(console, persona, after, table=after_table, sample=sample)
    diff = compare_reports(before_report, after_report)

    if as_json:
        click.echo(dumps_diff_json(diff))
    else:
        render_diff_terminal(diff, console=console, persona=persona)

    if fail_on is not None:
        threshold = SEVERITY_BANDS[fail_on.lower()]
        if SEVERITY_BANDS[diff.severity] >= threshold:
            if not as_json:
                console.print(
                    f"[bold red]{persona.refusal_phrase}[/bold red] "
                    f"diff severity {diff.severity!r} \u2265 {fail_on!r} threshold."
                )
            raise SystemExit(5)


@main.command()
@click.argument(
    "path",
    type=DATA_INPUT,
)
@click.option(
    "--table",
    "table",
    default=None,
    help="Table name to read (SQLite only). Defaults to the first table.",
)
@click.pass_context
def parlor(ctx: click.Context, path: Path, table: str | None) -> None:
    """Open the interactive TUI parlor for a data file.

    Browse columns, page through sample rows, and run ad-hoc SQL
    against the file via DuckDB (registered as the view ``data``).
    Requires the optional ``[tui]`` extra (``pip install schema-seance[tui]``).
    """
    console = Console()
    persona = _persona_from_ctx(ctx)
    try:
        session = load_session(path, table=table)
    except UnsupportedFormatError as exc:
        console.print(
            Panel(
                f"The spirits recoil. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc
    except SQLiteTableError as exc:
        console.print(
            Panel(
                f"The spirits cannot find that table. {exc}",
                title=persona.panel_title,
                border_style="red",
            )
        )
        raise SystemExit(2) from exc

    try:
        app = build_app(session)
    except ParlorUnavailableError as exc:
        console.print(
            Panel(
                f"The parlor is shuttered. {exc}",
                title=persona.panel_title,
                border_style="yellow",
            )
        )
        raise SystemExit(4) from exc

    app.run()


@main.command(name="mcp")
def mcp_serve() -> None:
    """Run an MCP stdio server exposing ``summon`` and ``read`` as tools.

    Wire this into an MCP client (Claude Desktop, Cursor, Continue, etc.) as
    a stdio server: ``command: seance``, ``args: ["mcp"]``. The server
    speaks JSON-RPC 2.0 over stdin/stdout.
    """
    from .mcp import serve as _mcp_serve

    _mcp_serve()


@main.command()
@click.argument(
    "path",
    type=DATA_INPUT,
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    required=False,
    default=None,
    help="Destination path. Required unless --dry-run is set.",
)
@click.option(
    "--table",
    "table",
    default=None,
    help="Table name to read (SQLite only). Defaults to the first table.",
)
@click.option(
    "--min-confidence",
    "min_confidence",
    type=click.Choice(["low", "medium", "high"], case_sensitive=False),
    default="high",
    show_default=True,
    help="Redact columns whose PII confidence is at or above this band.",
)
@click.option(
    "--strategy",
    "strategy_specs",
    multiple=True,
    metavar="DETECTOR=STRATEGY",
    help=(
        "Override the default strategy for a detector. Repeatable. "
        "Strategies: mask, hash, null, keep, year. "
        "Example: --strategy email=hash --strategy name=null."
    ),
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(sorted(SUPPORTED_OUTPUT_FORMATS), case_sensitive=False),
    default=None,
    help="Output format. Defaults to inferring from the output extension.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Print the planned redactions without writing output.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a stable JSON document instead of pretty output (for scripting).",
)
@click.pass_context
def redact(
    ctx: click.Context,
    path: Path,
    output: Path | None,
    table: str | None,
    min_confidence: str,
    strategy_specs: tuple[str, ...],
    out_format: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Banish PII: emit a redacted copy of <path>."""
    console = Console()
    persona = _persona_from_ctx(ctx)

    if output is None and not dry_run:
        raise click.UsageError("--output is required unless --dry-run is set.")

    try:
        overrides = parse_strategy_overrides(strategy_specs)
    except RedactionError as exc:
        raise click.BadParameter(str(exc), param_hint="--strategy") from exc

    report = _load_and_profile(console, persona, path, table=table, sample=None)
    try:
        plan = build_redaction_plan(
            report,
            min_confidence=min_confidence,
            strategy_overrides=overrides,
        )
    except RedactionError as exc:
        raise click.BadParameter(str(exc)) from exc

    plan_payload = {
        "path": str(path),
        "min_confidence": plan.min_confidence,
        "output": str(output) if output else None,
        "format": (out_format or (infer_format(output) if output else None)),
        "dry_run": dry_run,
        "actions": [
            {
                "column": a.column,
                "kind": a.kind,
                "strategy": a.strategy,
                "confidence": a.confidence,
                "default": a.strategy == DEFAULT_STRATEGY.get(a.kind),
            }
            for a in plan.actions
        ],
    }

    if dry_run:
        if as_json:
            click.echo(json.dumps(plan_payload, indent=2, sort_keys=True))
            return
        if not plan.actions:
            console.print(
                Panel(
                    "No columns met the confidence threshold. The spirits are already at rest.",
                    title=persona.panel_title,
                    border_style="magenta",
                )
            )
            return
        console.print(
            Panel(
                f"Planned redactions · min-confidence={plan.min_confidence}",
                title=persona.panel_title,
                border_style="magenta",
            )
        )
        for a in plan.actions:
            console.print(
                f"  [bold]{a.column}[/bold] "
                f"→ [magenta]{a.strategy}[/magenta] "
                f"([dim]{a.kind}, conf {a.confidence:.2f}[/dim])"
            )
        return

    try:
        counts = run_redaction(
            path,
            output,
            plan,
            table=table,
            out_format=out_format,
        )
    except RedactionError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        plan_payload["counts"] = counts
        plan_payload["total_cells_redacted"] = sum(counts.values())
        click.echo(json.dumps(plan_payload, indent=2, sort_keys=True))
        return

    total = sum(counts.values())
    console.print(
        Panel(
            f"Wrote redacted copy to [bold]{output}[/bold]\n"
            f"Cells redacted: [bold]{total}[/bold] across "
            f"{len(plan.actions)} column(s).\n"
            f"[italic dim]The spirits are at rest.[/italic dim]",
            title=persona.panel_title,
            border_style="magenta",
        )
    )
    if counts:
        for col, n in sorted(counts.items()):
            console.print(f"  [bold]{col}[/bold]: {n} cell(s)")


@main.command(name="personas")
def list_personas() -> None:
    """List available narrator personas."""
    from .personas import PERSONAS, available_ids

    console = Console()
    for pid in available_ids():
        p = PERSONAS[pid]
        console.print(f"[bold magenta]{p.emoji} {pid}[/bold magenta] — {p.display_name}")
        console.print(f"    [dim]{p.tagline}[/dim]")


if __name__ == "__main__":  # pragma: no cover
    main()
