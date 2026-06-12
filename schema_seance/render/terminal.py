"""Rich-based terminal rendering for profile reports."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..profile import ProfileReport


def _humanize_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{n} B"


def _format_sample(value: object) -> str:
    if value is None:
        return "[dim]∅[/dim]"
    s = str(value)
    if len(s) > 40:
        s = s[:37] + "…"
    return s


def render(report: ProfileReport, console: Console | None = None) -> None:
    """Render *report* to *console* (a fresh Console if not provided)."""
    console = console or Console()

    veil = Table.grid(padding=(0, 2))
    veil.add_column(style="bold magenta")
    veil.add_column()
    veil.add_row("File", str(report.path) if report.path else "—")
    veil.add_row("Rows", f"{report.rows:,}")
    veil.add_row("Columns", f"{report.cols:,}")
    veil.add_row("Size", _humanize_bytes(report.size_bytes))
    veil.add_row("Encoding", report.encoding or "—")
    console.print(
        Panel(
            veil,
            title=Text("🕯  The Veil Parts", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )

    spirits = Table(
        title=None,
        header_style="bold magenta",
        show_lines=False,
        expand=True,
    )
    spirits.add_column("Column", style="bold")
    spirits.add_column("Type", style="cyan")
    spirits.add_column("Null %", justify="right")
    spirits.add_column("Sample", overflow="fold")

    for col in report.columns:
        spirits.add_row(
            col.name,
            col.dtype,
            f"{col.null_pct:.2f}",
            _format_sample(col.sample),
        )

    console.print(
        Panel(
            spirits,
            title=Text("👻 The Spirits Speak", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )
