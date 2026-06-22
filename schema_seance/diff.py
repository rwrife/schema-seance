"""Schema diff between two profile reports.

Powers ``seance compare a.csv b.csv`` — flags column adds/removes,
dtype drift, null/distinct/distribution shifts, and PII appearance or
disappearance. Designed to be machine-friendly (stable JSON) and
CI-gate-friendly (exit code via ``--fail-on``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pii import PIIFinding
from .profile import ColumnProfile, ProfileReport

__all__ = [
    "ColumnDiff",
    "FieldChange",
    "PIIChange",
    "SchemaDiff",
    "compare",
    "SEVERITY_ORDER",
    "SEVERITY_BANDS",
]

# Higher = worse. The renderer / --fail-on flag compare against this order.
SEVERITY_ORDER = ("none", "low", "medium", "high")
SEVERITY_BANDS: dict[str, int] = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Thresholds for "did it really change?"  Tuned to be quiet on tiny noise.
_NULL_PCT_DELTA_LOW = 1.0  # absolute percentage points
_NULL_PCT_DELTA_MED = 10.0
_NULL_PCT_DELTA_HIGH = 25.0

_DISTINCT_REL_LOW = 0.10  # relative change
_DISTINCT_REL_MED = 0.50
_DISTINCT_REL_HIGH = 2.00

_NUMERIC_REL_LOW = 0.10  # mean/stddev relative change
_NUMERIC_REL_MED = 0.50
_NUMERIC_REL_HIGH = 2.00


@dataclass(frozen=True)
class FieldChange:
    """A single field that differs between the two column profiles."""

    field: str
    before: Any
    after: Any
    severity: str = "low"
    detail: str | None = None


@dataclass(frozen=True)
class PIIChange:
    kind: str
    direction: str  # "appeared" | "disappeared" | "shifted"
    before: float | None
    after: float | None
    severity: str = "medium"


@dataclass(frozen=True)
class ColumnDiff:
    name: str
    status: str  # "added" | "removed" | "changed" | "unchanged"
    before_dtype: str | None = None
    after_dtype: str | None = None
    changes: tuple[FieldChange, ...] = ()
    pii_changes: tuple[PIIChange, ...] = ()

    @property
    def severity(self) -> str:
        worst = 0
        if self.status in ("added", "removed"):
            worst = max(worst, SEVERITY_BANDS["medium"])
        for c in self.changes:
            worst = max(worst, SEVERITY_BANDS.get(c.severity, 0))
        for p in self.pii_changes:
            worst = max(worst, SEVERITY_BANDS.get(p.severity, 0))
        return SEVERITY_ORDER[worst]


@dataclass(frozen=True)
class SchemaDiff:
    before_path: str | None
    after_path: str | None
    before_rows: int
    after_rows: int
    before_cols: int
    after_cols: int
    columns: tuple[ColumnDiff, ...] = field(default_factory=tuple)

    @property
    def added(self) -> tuple[ColumnDiff, ...]:
        return tuple(c for c in self.columns if c.status == "added")

    @property
    def removed(self) -> tuple[ColumnDiff, ...]:
        return tuple(c for c in self.columns if c.status == "removed")

    @property
    def changed(self) -> tuple[ColumnDiff, ...]:
        return tuple(c for c in self.columns if c.status == "changed")

    @property
    def unchanged(self) -> tuple[ColumnDiff, ...]:
        return tuple(c for c in self.columns if c.status == "unchanged")

    @property
    def severity(self) -> str:
        worst = 0
        for c in self.columns:
            worst = max(worst, SEVERITY_BANDS[c.severity])
        return SEVERITY_ORDER[worst]


def _band_for_abs(delta: float, low: float, med: float, high: float) -> str:
    d = abs(delta)
    if d >= high:
        return "high"
    if d >= med:
        return "medium"
    if d >= low:
        return "low"
    return "none"


def _band_for_rel(
    before: float | None, after: float | None, low: float, med: float, high: float
) -> str:
    if before is None and after is None:
        return "none"
    if before is None or after is None:
        return "medium"
    base = max(abs(before), 1e-9)
    rel = abs(after - before) / base
    if rel >= high:
        return "high"
    if rel >= med:
        return "medium"
    if rel >= low:
        return "low"
    return "none"


def _dtype_severity(a: str, b: str) -> str:
    if a == b:
        return "none"

    # Crude family check: number-ish vs string-ish vs date-ish.
    def fam(t: str) -> str:
        u = t.upper()
        if any(
            u.startswith(p)
            for p in (
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
        ):
            return "numeric"
        if u.startswith(("VARCHAR", "CHAR", "TEXT", "STRING", "BLOB")):
            return "text"
        if "DATE" in u or "TIME" in u:
            return "temporal"
        if u.startswith("BOOL"):
            return "bool"
        return "other"

    return "high" if fam(a) != fam(b) else "medium"


def _pii_map(findings: tuple[PIIFinding, ...]) -> dict[str, PIIFinding]:
    return {f.kind: f for f in findings}


def _pii_changes(
    before: tuple[PIIFinding, ...],
    after: tuple[PIIFinding, ...],
) -> tuple[PIIChange, ...]:
    out: list[PIIChange] = []
    bm = _pii_map(before)
    am = _pii_map(after)
    for kind in sorted(set(bm) | set(am)):
        b = bm.get(kind)
        a = am.get(kind)
        if b is None and a is not None:
            sev = "high" if a.confidence >= 0.60 else "medium"
            out.append(
                PIIChange(
                    kind=kind, direction="appeared", before=None, after=a.confidence, severity=sev
                )
            )
        elif a is None and b is not None:
            sev = "high" if b.confidence >= 0.60 else "medium"
            out.append(
                PIIChange(
                    kind=kind,
                    direction="disappeared",
                    before=b.confidence,
                    after=None,
                    severity=sev,
                )
            )
        elif a is not None and b is not None:
            delta = abs(a.confidence - b.confidence)
            if delta >= 0.20:
                sev = "high" if delta >= 0.40 else "medium"
                out.append(
                    PIIChange(
                        kind=kind,
                        direction="shifted",
                        before=b.confidence,
                        after=a.confidence,
                        severity=sev,
                    )
                )
    return tuple(out)


def _diff_column(before: ColumnProfile, after: ColumnProfile) -> ColumnDiff:
    changes: list[FieldChange] = []

    if before.dtype != after.dtype:
        changes.append(
            FieldChange(
                field="dtype",
                before=before.dtype,
                after=after.dtype,
                severity=_dtype_severity(before.dtype, after.dtype),
            )
        )

    null_delta = (after.null_pct or 0.0) - (before.null_pct or 0.0)
    null_sev = _band_for_abs(
        null_delta, _NULL_PCT_DELTA_LOW, _NULL_PCT_DELTA_MED, _NULL_PCT_DELTA_HIGH
    )
    if null_sev != "none":
        changes.append(
            FieldChange(
                field="null_pct",
                before=before.null_pct,
                after=after.null_pct,
                severity=null_sev,
                detail=f"Δ {null_delta:+.2f} pp",
            )
        )

    distinct_sev = _band_for_rel(
        float(before.distinct),
        float(after.distinct),
        _DISTINCT_REL_LOW,
        _DISTINCT_REL_MED,
        _DISTINCT_REL_HIGH,
    )
    if distinct_sev != "none":
        changes.append(
            FieldChange(
                field="distinct",
                before=before.distinct,
                after=after.distinct,
                severity=distinct_sev,
            )
        )

    for fld in ("mean", "stddev"):
        b = getattr(before, fld)
        a = getattr(after, fld)
        sev = _band_for_rel(b, a, _NUMERIC_REL_LOW, _NUMERIC_REL_MED, _NUMERIC_REL_HIGH)
        if sev != "none":
            changes.append(FieldChange(field=fld, before=b, after=a, severity=sev))

    pii_changes = _pii_changes(before.pii, after.pii)

    status = "changed" if (changes or pii_changes) else "unchanged"
    return ColumnDiff(
        name=after.name,
        status=status,
        before_dtype=before.dtype,
        after_dtype=after.dtype,
        changes=tuple(changes),
        pii_changes=pii_changes,
    )


def compare(before: ProfileReport, after: ProfileReport) -> SchemaDiff:
    """Compare two ProfileReports and return a structured diff."""
    before_by_name = {c.name: c for c in before.columns}

    # Preserve "after" column order for stability, then append removed ones.
    diffs: list[ColumnDiff] = []
    seen: set[str] = set()
    for col in after.columns:
        seen.add(col.name)
        if col.name not in before_by_name:
            diffs.append(
                ColumnDiff(
                    name=col.name,
                    status="added",
                    before_dtype=None,
                    after_dtype=col.dtype,
                    pii_changes=_pii_changes((), col.pii),
                )
            )
        else:
            diffs.append(_diff_column(before_by_name[col.name], col))

    for col in before.columns:
        if col.name in seen:
            continue
        diffs.append(
            ColumnDiff(
                name=col.name,
                status="removed",
                before_dtype=col.dtype,
                after_dtype=None,
                pii_changes=_pii_changes(col.pii, ()),
            )
        )

    return SchemaDiff(
        before_path=str(before.path) if before.path else None,
        after_path=str(after.path) if after.path else None,
        before_rows=before.rows,
        after_rows=after.rows,
        before_cols=before.cols,
        after_cols=after.cols,
        columns=tuple(diffs),
    )
