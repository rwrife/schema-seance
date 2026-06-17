"""Anomaly heuristics — the "Restless Anomalies" section.

Currently:

- ``high_nulls``      — column null-percentage above a threshold.
- ``mixed_types``     — VARCHAR column whose values split between
                        numeric / date / string buckets.
- ``pk_duplicates``   — primary-key-candidate columns (by name or by
                        being a non-null column with distinct == rows)
                        that contain duplicates.
- ``numeric_outliers``— numeric columns with IQR outliers.

Each anomaly carries a severity (``info``/``warn``/``high``) so the
renderer can colour them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Anomaly",
    "detect_column",
    "HIGH_NULL_PCT",
]

HIGH_NULL_PCT = 50.0


@dataclass(frozen=True)
class Anomaly:
    kind: str
    severity: str  # info | warn | high
    detail: str


_PK_NAME_RE = re.compile(r"^(id|.*_id|pk|primary_key|uuid|guid)$", re.IGNORECASE)


def _looks_pk_candidate(name: str, dtype: str, rows: int, distinct: int) -> bool:
    if _PK_NAME_RE.match(name):
        return True
    # Non-trivial column that is fully distinct is also a candidate.
    return rows >= 5 and distinct == rows


_NUM_VAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_DATE_VAL_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$"
)
_BOOL_VAL_RE = re.compile(r"^(true|false|yes|no|y|n|0|1)$", re.IGNORECASE)


def _classify(value: str) -> str:
    v = value.strip()
    if not v:
        return "empty"
    if _NUM_VAL_RE.match(v):
        return "numeric"
    if _DATE_VAL_RE.match(v):
        return "date"
    if _BOOL_VAL_RE.match(v):
        return "bool"
    return "string"


def _detect_mixed_types(name: str, dtype: str, values: list) -> Anomaly | None:
    if not values:
        return None
    if "VARCHAR" not in dtype.upper() and "TEXT" not in dtype.upper():
        return None
    buckets: dict[str, int] = {}
    for v in values:
        if v is None:
            continue
        c = _classify(str(v))
        if c == "empty":
            continue
        buckets[c] = buckets.get(c, 0) + 1
    if not buckets:
        return None
    total = sum(buckets.values())
    significant = {k: c for k, c in buckets.items() if c / total >= 0.05}
    # Treat string as the "default" bucket — mix means non-string + string,
    # or two non-string types.
    non_string = [k for k in significant if k != "string"]
    if len(significant) >= 2 and non_string:
        ordered = ", ".join(
            f"{k}={c}" for k, c in sorted(significant.items(), key=lambda kv: -kv[1])
        )
        return Anomaly(
            kind="mixed_types",
            severity="warn",
            detail=f"VARCHAR column carries mixed value shapes ({ordered}).",
        )
    return None


def _detect_high_nulls(null_pct: float, threshold: float) -> Anomaly | None:
    if null_pct >= threshold:
        sev = "high" if null_pct >= 90 else "warn"
        return Anomaly(
            kind="high_nulls",
            severity=sev,
            detail=f"{null_pct:.2f}% of values are NULL (>= {threshold:.0f}%).",
        )
    return None


def _detect_pk_duplicates(name: str, dtype: str, rows: int, distinct: int) -> Anomaly | None:
    if rows <= 1:
        return None
    if not _PK_NAME_RE.match(name):
        return None
    if distinct < rows:
        dup = rows - distinct
        return Anomaly(
            kind="pk_duplicates",
            severity="high",
            detail=(
                f"PK-candidate column has {dup:,} duplicate value(s) "
                f"({distinct:,}/{rows:,} distinct)."
            ),
        )
    return None


def _detect_numeric_outliers(numeric_values: list[float]) -> Anomaly | None:
    n = len(numeric_values)
    if n < 8:
        return None
    s = sorted(numeric_values)

    def _quantile(q: float) -> float:
        if not s:
            return 0.0
        idx = (n - 1) * q
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return s[lo] * (1 - frac) + s[hi] * frac

    q1 = _quantile(0.25)
    q3 = _quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return None
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    out = [v for v in s if v < lower or v > upper]
    if not out:
        return None
    pct = 100.0 * len(out) / n
    sev = "warn" if pct >= 1.0 else "info"
    return Anomaly(
        kind="numeric_outliers",
        severity=sev,
        detail=(
            f"{len(out)} of {n} values lie outside IQR fence "
            f"[{lower:.4g}, {upper:.4g}] ({pct:.1f}%)."
        ),
    )


def detect_column(
    *,
    name: str,
    dtype: str,
    rows: int,
    distinct: int,
    null_pct: float,
    values: list[Any],
    numeric_values: list[float] | None = None,
    high_null_threshold: float = HIGH_NULL_PCT,
) -> list[Anomaly]:
    findings: list[Anomaly] = []

    nulls = _detect_high_nulls(null_pct, high_null_threshold)
    if nulls is not None:
        findings.append(nulls)

    mixed = _detect_mixed_types(name, dtype, values)
    if mixed is not None:
        findings.append(mixed)

    pk = _detect_pk_duplicates(name, dtype, rows, distinct)
    if pk is not None:
        findings.append(pk)

    if numeric_values:
        out = _detect_numeric_outliers(numeric_values)
        if out is not None:
            findings.append(out)

    return findings
