"""Interactive Textual TUI — `seance parlor`.

A keyboard-first parlor for browsing a file's columns, paging through
sample rows, and running ad-hoc SQL via DuckDB. The data file is loaded
as a DuckDB relation registered under the view name ``data`` so users
can write queries like ``SELECT * FROM data WHERE ...``.

The TUI itself lives behind a soft import: Textual is an optional
dependency declared under the ``[tui]`` extra. Importing this module
without Textual installed raises :class:`ParlorUnavailableError` with
an actionable install hint.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

__all__ = [
    "ParlorUnavailableError",
    "build_app",
    "load_session",
    "ParlorSession",
]


class ParlorUnavailableError(RuntimeError):
    """Raised when Textual is not installed but the parlor was requested."""


@dataclass(frozen=True)
class ParlorSession:
    """Pre-loaded state handed to the Textual app.

    Exposed separately so headless tests can assert behavior without
    spinning up a terminal.
    """

    path: Path
    connection: duckdb.DuckDBPyConnection
    columns: tuple[tuple[str, str], ...]  # (name, dtype)
    row_count: int
    view_name: str = "data"

    def run_sql(self, sql: str, *, limit: int = 200) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Execute *sql* and return (column-names, rows) truncated to *limit*.

        A bare ``LIMIT`` is appended only when the query has no explicit
        limit clause, so power users can override. Errors propagate
        as :class:`duckdb.Error` for the caller to display.
        """
        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            return [], []
        upper = stripped.upper()
        needs_limit = " LIMIT " not in f" {upper} " and not upper.endswith(" LIMIT")
        final = f"{stripped} LIMIT {limit}" if needs_limit else stripped
        rel = self.connection.sql(final)
        names = list(rel.columns)
        rows = rel.fetchall()
        return names, rows

    def sample(self, *, limit: int = 50) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Return up to *limit* rows of the source relation."""
        return self.run_sql(f"SELECT * FROM {self.view_name}", limit=limit)


def load_session(path: str | Path, *, table: str | None = None) -> ParlorSession:
    """Load *path* into a parlor session.

    Uses the same reader dispatch as the rest of the tool so every
    supported format works in the parlor.
    """
    from .readers import load as load_relation

    p = Path(path)
    connection = duckdb.connect()
    relation = load_relation(p, connection=connection, table=table)
    relation.create_view("data", replace=True)
    cols = tuple(zip(relation.columns, relation.dtypes, strict=False))
    cols = tuple((str(n), str(d)) for n, d in cols)
    row_count = int(connection.sql("SELECT COUNT(*) FROM data").fetchone()[0])
    return ParlorSession(
        path=p,
        connection=connection,
        columns=cols,
        row_count=row_count,
    )


if TYPE_CHECKING:  # pragma: no cover - typing only
    from textual.app import App


def build_app(session: ParlorSession) -> App:
    """Construct (but do not run) the Textual parlor app for *session*."""
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical
        from textual.widgets import DataTable, Footer, Header, Input, Static
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in CLI
        raise ParlorUnavailableError(
            "Textual is not installed. Install the TUI extra with "
            "`pip install schema-seance[tui]` (or `uv pip install textual`)."
        ) from exc

    class ParlorApp(App):  # type: ignore[misc]
        CSS = """
        Screen { layout: vertical; }
        #cols { width: 32; border: round magenta; }
        #preview { border: round magenta; }
        #sql_row { height: 3; }
        #sql_input { border: round magenta; }
        #status { color: $text-muted; padding: 0 1; }
        #results { border: round magenta; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("ctrl+c", "quit", "Quit", show=False),
            Binding("ctrl+r", "reset", "Reset query"),
            Binding("f5", "run", "Run SQL"),
        ]

        TITLE = "🔮 schema-seance · parlor"

        def __init__(self, sess: ParlorSession) -> None:
            super().__init__()
            self.session = sess

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                cols = DataTable(id="cols", cursor_type="row", zebra_stripes=True)
                cols.add_columns("column", "dtype")
                for name, dtype in self.session.columns:
                    cols.add_row(name, dtype)
                yield cols
                preview = DataTable(id="preview", zebra_stripes=True)
                yield preview
            with Vertical(id="sql_row"):
                yield Input(
                    placeholder="SELECT * FROM data WHERE ...   (Enter or F5 to run)",
                    id="sql_input",
                )
            yield Static(
                f"{self.session.path.name} · {self.session.row_count} rows · "
                f"{len(self.session.columns)} cols",
                id="status",
            )
            yield DataTable(id="results", zebra_stripes=True)
            yield Footer()

        def on_mount(self) -> None:
            self._populate_preview()

        def _populate_preview(self) -> None:
            table = self.query_one("#preview", DataTable)
            table.clear(columns=True)
            names, rows = self.session.sample(limit=50)
            if names:
                table.add_columns(*names)
                for row in rows:
                    table.add_row(*[("" if v is None else str(v)) for v in row])

        @on(Input.Submitted, "#sql_input")
        def _on_submit(self, event: Input.Submitted) -> None:  # noqa: F821
            self._run_query(event.value)

        def action_run(self) -> None:
            self._run_query(self.query_one("#sql_input", Input).value)

        def action_reset(self) -> None:
            self.query_one("#sql_input", Input).value = ""
            self._populate_preview()
            self._set_status(
                f"{self.session.path.name} · {self.session.row_count} rows · "
                f"{len(self.session.columns)} cols"
            )

        def _set_status(self, text: str) -> None:
            self.query_one("#status", Static).update(text)

        def _run_query(self, sql: str) -> None:
            results = self.query_one("#results", DataTable)
            results.clear(columns=True)
            if not sql.strip():
                self._set_status("The spirits await a query…")
                return
            try:
                names, rows = self.session.run_sql(sql)
            except duckdb.Error as exc:
                self._set_status(f"⚠ {exc}")
                return
            if names:
                results.add_columns(*names)
            for row in rows:
                results.add_row(*[("" if v is None else str(v)) for v in row])
            self._set_status(f"{len(rows)} row(s) returned (limit 200).")

    return ParlorApp(session)
