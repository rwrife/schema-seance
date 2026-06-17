"""Full profile of a DuckDB relation — M3 slice.

Computes file-level metadata ("The Veil Parts") and per-column statistics
("The Spirits Speak"):
  - dtype, null %, distinct count, sample
  - numeric mean / stddev / min / max
  - non-numeric min / max (lexicographic for strings, natural for dates)
  - top-k most frequent values (with counts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from .anomalies import Anomaly
from .anomalies import detect_column as detect_anomalies
from .pii import PIIFinding
from .pii import detect_column as detect_pii

__all__ = [
    "ColumnProfile",
    "ProfileReport",
    "TopValue",
    "profile",
]

# Bumped when the JSON output schema changes in a breaking way.
PROFILE_SCHEMA_VERSION = 2

# How many values to pull for PII/anomaly heuristics per column.
_DETECT_SAMPLE = 500


_NUMERIC_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
    "NUMERIC",
)


def _is_numeric(dtype: str) -> bool:
    upper = dtype.upper()
    return any(upper.startswith(p) for p in _NUMERIC_PREFIXES)


@dataclass(frozen=True)
class TopValue:
    value: Any
    count: int


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    null_pct: float
    distinct: int
    sample: Any
    min: Any = None
    max: Any = None
    mean: float | None = None
    stddev: float | None = None
    top: tuple[TopValue, ...] = ()
    pii: tuple[PIIFinding, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()


@dataclass(frozen=True)
class ProfileReport:
    path: Path | None
    rows: int
    cols: int
    size_bytes: int | None
    encoding: str | None
    sampled: bool = False
    sample_size: int | None = None
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
    text_exts = {".csv", ".tsv", ".jsonl", ".ndjson"}
    encoding = "utf-8" if path.suffix.lower() in text_exts else None
    return size, encoding


def _profile_column(
    relation: duckdb.DuckDBPyRelation,
    name: str,
    dtype: str,
    rows: int,
    *,
    top_k: int,
) -> ColumnProfile:
    qname = _quote_ident(name)
    numeric = _is_numeric(dtype)
    parts = [
        f"SUM(CASE WHEN {qname} IS NULL THEN 1 ELSE 0 END) AS nulls",
        f"COUNT(DISTINCT {qname}) AS distinct_ct",
        f"any_value({qname}) AS sample_val",
    ]
    if numeric:
        parts += [
            f"MIN({qname}) AS min_v",
            f"MAX({qname}) AS max_v",
            f"AVG(CAST({qname} AS DOUBLE)) AS mean_v",
            f"STDDEV_SAMP(CAST({qname} AS DOUBLE)) AS std_v",
        ]
    else:
        # MIN/MAX on non-numerics: works for strings/dates/timestamps/bools.
        # Nested types (structs, lists, maps) don't support MIN/MAX so we skip.
        upper = dtype.upper()
        comparable = not (
            upper.startswith("STRUCT")
            or upper.startswith("MAP")
            or upper.endswith("[]")
            or "LIST" in upper
        )
        if comparable:
            parts += [
                f"MIN({qname}) AS min_v",
                f"MAX({qname}) AS max_v",
            ]
        else:
            parts += [
                "CAST(NULL AS VARCHAR) AS min_v",
                "CAST(NULL AS VARCHAR) AS max_v",
            ]
    agg_sql = "SELECT " + ", ".join(parts) + " FROM rel"
    try:
        row = relation.query("rel", agg_sql).fetchone()
    except duckdb.Error:
        # Defensive fallback: aggregate-incompatible column types.
        fallback = (
            f"SELECT "
            f"SUM(CASE WHEN {qname} IS NULL THEN 1 ELSE 0 END) AS nulls, "
            f"COUNT(DISTINCT {qname}) AS distinct_ct, "
            f"any_value({qname}) AS sample_val, "
            f"CAST(NULL AS VARCHAR) AS min_v, "
            f"CAST(NULL AS VARCHAR) AS max_v "
            f"FROM rel"
        )
        row = relation.query("rel", fallback).fetchone()
        numeric = False
    if row is None:
        row = (0, 0, None, None, None) + ((None, None) if numeric else ())

    nulls = int(row[0] or 0)
    distinct = int(row[1] or 0)
    sample_val = row[2]
    min_v = row[3]
    max_v = row[4]
    mean_v = float(row[5]) if numeric and row[5] is not None else None
    std_v = float(row[6]) if numeric and len(row) > 6 and row[6] is not None else None

    denom = rows if rows > 0 else 1
    null_pct = round((nulls / denom) * 100.0, 2) if rows > 0 else 0.0

    # Top-k frequent non-null values. Wrap in TRY by guarding the query;
    # group-by on unhashable nested types would raise.
    top: tuple[TopValue, ...] = ()
    try:
        top_rows = relation.query(
            "rel",
            (
                f"SELECT {qname} AS v, COUNT(*) AS c FROM rel "
                f"WHERE {qname} IS NOT NULL "
                f"GROUP BY 1 ORDER BY c DESC, 1 LIMIT {int(top_k)}"
            ),
        ).fetchall()
        top = tuple(TopValue(value=v, count=int(c)) for v, c in top_rows)
    except duckdb.Error:
        top = ()

    # Pull a value sample for PII/anomaly heuristics.
    sample_values: list = []
    numeric_values: list[float] = []
    try:
        sample_rows = relation.query(
            "rel",
            (f"SELECT {qname} AS v FROM rel WHERE {qname} IS NOT NULL LIMIT {int(_DETECT_SAMPLE)}"),
        ).fetchall()
        sample_values = [r[0] for r in sample_rows]
        if numeric:
            for v in sample_values:
                try:
                    numeric_values.append(float(v))
                except (TypeError, ValueError):
                    continue
    except duckdb.Error:
        sample_values = []

    pii_findings = tuple(detect_pii(name, dtype, sample_values))
    anomaly_findings = tuple(
        detect_anomalies(
            name=name,
            dtype=dtype,
            rows=rows,
            distinct=distinct,
            null_pct=null_pct,
            values=sample_values,
            numeric_values=numeric_values if numeric else None,
        )
    )

    return ColumnProfile(
        name=name,
        dtype=dtype,
        null_pct=null_pct,
        distinct=distinct,
        sample=sample_val,
        min=min_v,
        max=max_v,
        mean=mean_v,
        stddev=std_v,
        top=top,
        pii=pii_findings,
        anomalies=anomaly_findings,
    )


def profile(
    relation: duckdb.DuckDBPyRelation,
    *,
    path: str | Path | None = None,
    sample: int | None = None,
    top_k: int = 5,
) -> ProfileReport:
    """Compute a full profile of *relation*.

    Parameters
    ----------
    relation:
        DuckDB relation returned by a reader.
    path:
        Optional source path for file-size/encoding metadata.
    sample:
        If given, limit profiling to the first ``sample`` rows. Reported
        ``rows`` reflects the sampled size; ``sampled=True`` flags it.
    top_k:
        Number of top frequent values to capture per column.
    """
    p = Path(path) if path is not None else None

    rel = relation
    sampled = False
    sample_size: int | None = None
    if sample is not None and sample > 0:
        rel = relation.limit(sample)
        sampled = True
        sample_size = sample

    columns = list(rel.columns)
    dtypes = [str(t) for t in rel.dtypes]
    rows = int(rel.count("*").fetchone()[0] or 0)

    column_profiles = [
        _profile_column(rel, name, dtype, rows, top_k=top_k)
        for name, dtype in zip(columns, dtypes, strict=True)
    ]

    size_bytes, encoding = _file_meta(p)
    return ProfileReport(
        path=p,
        rows=rows,
        cols=len(columns),
        size_bytes=size_bytes,
        encoding=encoding,
        sampled=sampled,
        sample_size=sample_size,
        columns=column_profiles,
    )
