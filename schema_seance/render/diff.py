"""Renderers for SchemaDiff — terminal (Rich) and JSON."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from schema_seance.diff import ColumnDiff, FieldChange, PIIChange, SchemaDiff
from schema_seance.personas import DEFAULT_PERSONA_ID, PERSONAS, Persona

__all__ = ["render_terminal", "diff_to_dict", "dumps"]


_SEVERITY_STYLES = {
    "none": "dim",
    "low": "yellow",
    "medium": "bold yellow",
    "high": "bold red",
}

_STATUS_STYLES = {
    "added": "bold green",
    "removed": "bold red",
    "changed": "bold yellow",
    "unchanged": "dim",
}

_STATUS_GLYPHS = {
    "added": "＋",
    "removed": "－",
    "changed": "≠",
    "unchanged": "·",
}


def _fmt_value(v: Any) -> str:
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _change_line(c: FieldChange) -> str:
    base = f"{c.field}: {_fmt_value(c.before)} → {_fmt_value(c.after)}"
    if c.detail:
        base += f"  ({c.detail})"
    return base


def _pii_line(c: PIIChange) -> str:
    if c.direction == "appeared":
        return f"PII {c.kind} appeared (confidence {c.after:.2f})"
    if c.direction == "disappeared":
        return f"PII {c.kind} disappeared (was {c.before:.2f})"
    return f"PII {c.kind} shifted ({c.before:.2f} → {c.after:.2f})"


def render_terminal(
    diff: SchemaDiff,
    console: Console | None = None,
    *,
    persona: Persona | None = None,
) -> None:
    """Render *diff* as a layered Rich view: header, table, per-column details."""
    console = console or Console()
    persona = persona or PERSONAS[DEFAULT_PERSONA_ID]

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold magenta")
    header.add_column()
    header.add_row("Before", diff.before_path or "—")
    header.add_row("After", diff.after_path or "—")
    header.add_row(
        "Rows",
        f"{diff.before_rows:,} → {diff.after_rows:,}",
    )
    header.add_row(
        "Columns",
        f"{diff.before_cols:,} → {diff.after_cols:,}",
    )

    title = Text(f"{persona.emoji} Two Spirits Compared", style="bold magenta")
    console.print(Panel(header, title=title, border_style="magenta", padding=(1, 2)))

    summary = Table(title="Schema Drift", show_lines=False, expand=False)
    summary.add_column("", style="bold")
    summary.add_column("Column")
    summary.add_column("Before dtype")
    summary.add_column("After dtype")
    summary.add_column("Severity")
    summary.add_column("Notes")

    for col in diff.columns:
        if col.status == "unchanged":
            continue
        glyph = Text(_STATUS_GLYPHS[col.status], style=_STATUS_STYLES[col.status])
        sev_style = _SEVERITY_STYLES.get(col.severity, "white")
        notes_bits: list[str] = []
        for c in col.changes:
            notes_bits.append(_change_line(c))
        for p in col.pii_changes:
            notes_bits.append(_pii_line(p))
        if not notes_bits and col.status in ("added", "removed"):
            notes_bits.append(col.status)
        summary.add_row(
            glyph,
            col.name,
            col.before_dtype or "—",
            col.after_dtype or "—",
            Text(col.severity, style=sev_style),
            "\n".join(notes_bits) or "—",
        )

    if summary.row_count == 0:
        console.print(
            Panel(
                "The veil shows no drift. Both schemas hum in the same key.",
                title=persona.panel_title,
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print(summary)

    overall = Text(
        f"Overall severity: {diff.severity}", style=_SEVERITY_STYLES.get(diff.severity, "white")
    )
    console.print(overall)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

DIFF_SCHEMA_VERSION = 1


def _field_change_to_dict(c: FieldChange) -> dict[str, Any]:
    return {
        "field": c.field,
        "before": c.before,
        "after": c.after,
        "severity": c.severity,
        "detail": c.detail,
    }


def _pii_change_to_dict(c: PIIChange) -> dict[str, Any]:
    return {
        "kind": c.kind,
        "direction": c.direction,
        "before": c.before,
        "after": c.after,
        "severity": c.severity,
    }


def _column_to_dict(c: ColumnDiff) -> dict[str, Any]:
    return {
        "name": c.name,
        "status": c.status,
        "before_dtype": c.before_dtype,
        "after_dtype": c.after_dtype,
        "severity": c.severity,
        "changes": [_field_change_to_dict(x) for x in c.changes],
        "pii_changes": [_pii_change_to_dict(x) for x in c.pii_changes],
    }


def diff_to_dict(diff: SchemaDiff) -> dict[str, Any]:
    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "before": {"path": diff.before_path, "rows": diff.before_rows, "cols": diff.before_cols},
        "after": {"path": diff.after_path, "rows": diff.after_rows, "cols": diff.after_cols},
        "severity": diff.severity,
        "summary": {
            "added": [c.name for c in diff.added],
            "removed": [c.name for c in diff.removed],
            "changed": [c.name for c in diff.changed],
            "unchanged": [c.name for c in diff.unchanged],
        },
        "columns": [_column_to_dict(c) for c in diff.columns],
    }


def dumps(diff: SchemaDiff, *, indent: int | None = 2) -> str:
    return json.dumps(diff_to_dict(diff), indent=indent, default=str, ensure_ascii=False)
