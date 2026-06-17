"""Rich-based terminal rendering for profile reports."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..pii import confidence_band
from ..profile import ColumnProfile, ProfileReport


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


def _format_value(value: object, *, width: int = 40) -> str:
    if value is None:
        return "[dim]∅[/dim]"
    s = str(value)
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e6 or (value != 0 and abs(value) < 1e-3):
        return f"{value:.3e}"
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _format_top(col: ColumnProfile) -> str:
    if not col.top:
        return "[dim]—[/dim]"
    chunks = [f"{_format_value(t.value, width=18)} ×{t.count}" for t in col.top[:3]]
    return ", ".join(chunks)


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
    if report.sampled:
        veil.add_row("Sampled", f"first {report.sample_size:,} rows")
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
    spirits.add_column("Column", style="bold", no_wrap=True)
    spirits.add_column("Type", style="cyan", no_wrap=True)
    spirits.add_column("Null %", justify="right")
    spirits.add_column("Distinct", justify="right")
    spirits.add_column("Min", overflow="fold")
    spirits.add_column("Max", overflow="fold")
    spirits.add_column("Mean", justify="right")
    spirits.add_column("Stddev", justify="right")
    spirits.add_column("Top", overflow="fold")

    for col in report.columns:
        spirits.add_row(
            col.name,
            col.dtype,
            f"{col.null_pct:.2f}",
            f"{col.distinct:,}",
            _format_value(col.min, width=20),
            _format_value(col.max, width=20),
            _format_number(col.mean),
            _format_number(col.stddev),
            _format_top(col),
        )

    console.print(
        Panel(
            spirits,
            title=Text("👻 The Spirits Speak", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )

    _render_pii(report, console)
    _render_anomalies(report, console)


_BAND_STYLE = {
    "high": "bold red",
    "medium": "yellow",
    "low": "dim white",
    "none": "dim",
}

_SEVERITY_STYLE = {
    "high": "bold red",
    "warn": "yellow",
    "info": "cyan",
}


def _render_pii(report: ProfileReport, console: Console) -> None:
    rows = [(col, f) for col in report.columns for f in col.pii if f.confidence > 0]
    if not rows:
        return
    table = Table(header_style="bold magenta", expand=True, show_lines=False)
    table.add_column("Column", style="bold", no_wrap=True)
    table.add_column("Kind", style="cyan", no_wrap=True)
    table.add_column("Band", no_wrap=True)
    table.add_column("Confidence", justify="right")
    table.add_column("Match", justify="right")
    for col, finding in sorted(rows, key=lambda r: -r[1].confidence):
        band = confidence_band(finding.confidence)
        style = _BAND_STYLE.get(band, "white")
        table.add_row(
            col.name,
            finding.kind,
            f"[{style}]{band}[/{style}]",
            f"{finding.confidence:.2f}",
            f"{finding.matched}/{finding.sampled}",
        )
    console.print(
        Panel(
            table,
            title=Text("🔮 Whispers of the Personal", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )


def _render_anomalies(report: ProfileReport, console: Console) -> None:
    rows = [(col, a) for col in report.columns for a in col.anomalies]
    if not rows:
        return
    table = Table(header_style="bold magenta", expand=True, show_lines=False)
    table.add_column("Column", style="bold", no_wrap=True)
    table.add_column("Kind", style="cyan", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    severity_rank = {"high": 0, "warn": 1, "info": 2}
    for col, anomaly in sorted(rows, key=lambda r: severity_rank.get(r[1].severity, 9)):
        style = _SEVERITY_STYLE.get(anomaly.severity, "white")
        table.add_row(
            col.name,
            anomaly.kind,
            f"[{style}]{anomaly.severity}[/{style}]",
            anomaly.detail,
        )
    console.print(
        Panel(
            table,
            title=Text("⚡ Restless Anomalies", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )
