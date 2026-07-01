"""Renderers for multi-file (Congregation) reports."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..multi import MultiReport
from ..profile import PROFILE_SCHEMA_VERSION
from .json import report_to_dict as _single_to_dict

__all__ = ["render_multi", "multi_to_dict", "dumps_multi"]


def _fmt_path(p: str, width: int = 60) -> str:
    if len(p) <= width:
        return p
    return "…" + p[-(width - 1) :]


def render_multi(report: MultiReport, console: Console | None = None) -> None:
    console = console or Console()

    # Per-file summary lines
    per_file = Table(header_style="bold magenta", expand=True, show_lines=False)
    per_file.add_column("File", style="bold", overflow="fold")
    per_file.add_column("Rows", justify="right")
    per_file.add_column("Cols", justify="right")
    per_file.add_column("Status", no_wrap=True)
    for f in report.files:
        if f.ok and f.report is not None:
            per_file.add_row(
                _fmt_path(f.path),
                f"{f.report.rows:,}",
                f"{f.report.cols:,}",
                "[green]ok[/green]",
            )
        else:
            per_file.add_row(
                _fmt_path(f.path),
                "—",
                "—",
                f"[red]{f.error_kind or 'error'}[/red]",
            )
    console.print(
        Panel(
            per_file,
            title=Text("📜 The Roll Call", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )

    # Roll-up
    congregation = Table.grid(padding=(0, 2))
    congregation.add_column(style="bold magenta")
    congregation.add_column()
    congregation.add_row("Inputs", ", ".join(report.inputs) or "—")
    congregation.add_row("Files loaded", f"{report.loaded_count:,}")
    congregation.add_row("Files failed", f"{report.failed_count:,}")
    congregation.add_row("Total rows", f"{report.total_rows:,}")
    congregation.add_row("Schema clusters", f"{len(report.clusters):,}")
    congregation.add_row(
        "Drifted columns",
        ", ".join(report.drifted_columns) if report.drifted_columns else "—",
    )
    console.print(
        Panel(
            congregation,
            title=Text("⛧ The Congregation", style="bold magenta"),
            border_style="magenta",
            padding=(1, 2),
        )
    )

    if report.clusters:
        clusters = Table(header_style="bold magenta", expand=True, show_lines=False)
        clusters.add_column("#", justify="right", no_wrap=True)
        clusters.add_column("Files", justify="right")
        clusters.add_column("Columns", overflow="fold")
        clusters.add_column("Members", overflow="fold")
        for i, c in enumerate(report.clusters, start=1):
            cols = ", ".join(f"{n}:{t}" for n, t in c.signature[:8])
            if len(c.signature) > 8:
                cols += f", …(+{len(c.signature) - 8})"
            members = "\n".join(_fmt_path(m, 50) for m in c.files[:5])
            if len(c.files) > 5:
                members += f"\n…(+{len(c.files) - 5} more)"
            clusters.add_row(str(i), str(c.size), cols, members)
        console.print(
            Panel(
                clusters,
                title=Text("🪑 Schema Circles", style="bold magenta"),
                border_style="magenta",
                padding=(1, 2),
            )
        )

    if report.failures:
        fails = Table(header_style="bold red", expand=True, show_lines=False)
        fails.add_column("File", style="bold", overflow="fold")
        fails.add_column("Kind", no_wrap=True)
        fails.add_column("Error", overflow="fold")
        for f in report.failures:
            fails.add_row(_fmt_path(f.path), f.error_kind or "error", f.error or "")
        console.print(
            Panel(
                fails,
                title=Text("💀 The Silent Ones", style="bold red"),
                border_style="red",
                padding=(1, 2),
            )
        )


def multi_to_dict(report: MultiReport) -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "kind": "multi",
        "inputs": list(report.inputs),
        "summary": {
            "files_loaded": report.loaded_count,
            "files_failed": report.failed_count,
            "total_rows": report.total_rows,
            "schema_clusters": len(report.clusters),
            "drifted_columns": list(report.drifted_columns),
        },
        "files": [
            {
                "path": f.path,
                "ok": f.ok,
                "error": f.error,
                "error_kind": f.error_kind,
                "profile": _single_to_dict(f.report) if f.ok and f.report else None,
            }
            for f in report.files
        ],
        "clusters": [
            {
                "size": c.size,
                "columns": [{"name": n, "dtype": t} for n, t in c.signature],
                "files": list(c.files),
            }
            for c in report.clusters
        ],
    }


def dumps_multi(report: MultiReport, *, indent: int | None = 2) -> str:
    import json

    return json.dumps(
        multi_to_dict(report),
        indent=indent,
        sort_keys=False,
        ensure_ascii=False,
        default=str,
    )
