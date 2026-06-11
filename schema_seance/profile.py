"""Raw profile of a DuckDB relation — M2 slice.

Computes file-level metadata ("The Veil Parts") and per-column basics
("The Spirits Speak"): dtype, null %, a sample value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

__all__ = ["ColumnProfile", "ProfileReport", "profile"]


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    null_pct: float
    sample: Any


@dataclass(frozen=True)
class ProfileReport:
    path: Path | None
    rows: int
    cols: int
    size_bytes: int | None
    encoding: str | None
    columns: list[ColumnProfile] = field(default_factory=list)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _file_meta(path: Path | None) -> tuple[int | None, str | None]:
    if path is None:
        return None, None
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    # Encoding detection is best-effort; DuckDB normalizes to UTF-8 internally.
    encoding = "utf-8" if path.suffix.lower() in {".csv", ".tsv", ".jsonl", ".ndjson"} else None
    return size, encoding


def profile(
    relation: duckdb.DuckDBPyRelation,
    *,
    path: str | Path | None = None,
) -> ProfileReport:
    """Compute a raw profile of *relation*.

    Parameters
    ----------
    relation:
        DuckDB relation returned by a reader.
    path:
        Optional source path for file-size/encoding metadata.
    """
    p = Path(path) if path is not None else None
    columns = list(relation.columns)
    dtypes = [str(t) for t in relation.dtypes]

    rows = relation.count("*").fetchone()[0]
    rows = int(rows or 0)

    column_profiles: list[ColumnProfile] = []
    if columns:
        # One scan for null counts + first non-null sample per column.
        null_select = ", ".join(
            f"SUM(CASE WHEN {_quote_ident(c)} IS NULL THEN 1 ELSE 0 END) AS n_{i}"
            for i, c in enumerate(columns)
        )
        sample_select = ", ".join(
            f"any_value({_quote_ident(c)}) AS s_{i}" for i, c in enumerate(columns)
        )
        agg_sql = f"SELECT {null_select}, {sample_select} FROM rel"
        row = relation.query("rel", agg_sql).fetchone()
        null_counts = list(row[: len(columns)])
        samples = list(row[len(columns) :])
        denom = rows if rows > 0 else 1
        for i, (name, dtype) in enumerate(zip(columns, dtypes, strict=True)):
            nulls = int(null_counts[i] or 0)
            null_pct = (nulls / denom) * 100.0 if rows > 0 else 0.0
            column_profiles.append(
                ColumnProfile(
                    name=name,
                    dtype=dtype,
                    null_pct=round(null_pct, 2),
                    sample=samples[i],
                )
            )

    size_bytes, encoding = _file_meta(p)
    return ProfileReport(
        path=p,
        rows=rows,
        cols=len(columns),
        size_bytes=size_bytes,
        encoding=encoding,
        columns=column_profiles,
    )
