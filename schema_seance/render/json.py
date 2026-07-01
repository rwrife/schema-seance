"""Stable JSON serialization for ProfileReport.

Schema is versioned via ``schema_seance.profile.PROFILE_SCHEMA_VERSION``.
Field names and shape are part of the public contract; bump the version
before changing them.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from decimal import Decimal
from typing import Any

from ..profile import PROFILE_SCHEMA_VERSION, ColumnProfile, ProfileReport

__all__ = ["report_to_dict", "dumps"]


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, _dt.datetime | _dt.date | _dt.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _column_to_dict(col: ColumnProfile) -> dict[str, Any]:
    return {
        "name": col.name,
        "dtype": col.dtype,
        "null_pct": col.null_pct,
        "distinct": col.distinct,
        "sample": _jsonable(col.sample),
        "min": _jsonable(col.min),
        "max": _jsonable(col.max),
        "mean": _jsonable(col.mean),
        "stddev": _jsonable(col.stddev),
        "top": [{"value": _jsonable(t.value), "count": t.count} for t in col.top],
        "pii": [
            {
                "kind": f.kind,
                "confidence": f.confidence,
                "match_ratio": f.match_ratio,
                "matched": f.matched,
                "sampled": f.sampled,
            }
            for f in col.pii
        ],
        "anomalies": [
            {"kind": a.kind, "severity": a.severity, "detail": a.detail} for a in col.anomalies
        ],
    }


def report_to_dict(report: ProfileReport) -> dict[str, Any]:
    """Convert a ProfileReport into the documented JSON-friendly dict."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "file": {
            "path": str(report.path) if report.path else None,
            "size_bytes": report.size_bytes,
            "encoding": report.encoding,
        },
        "rows": report.rows,
        "cols": report.cols,
        "sampled": report.sampled,
        "sample_size": report.sample_size,
        "columns": [_column_to_dict(c) for c in report.columns],
        "time_series": [_timeseries_to_dict(t) for t in report.time_series],
    }


def _timeseries_to_dict(ts: Any) -> dict[str, Any]:
    return {
        "column": ts.column,
        "detected_from": ts.detected_from,
        "points": ts.points,
        "range": {"min": ts.range_min, "max": ts.range_max},
        "cadence_seconds": ts.cadence_seconds,
        "cadence_label": ts.cadence_label,
        "conformance_pct": ts.conformance_pct,
        "expected_buckets": ts.expected_buckets,
        "missing_buckets": ts.missing_buckets,
        "gaps": [
            {
                "start": g.start,
                "end": g.end,
                "duration_seconds": g.duration_seconds,
            }
            for g in ts.gaps
        ],
        "seasonality": [
            {
                "bucket": s.bucket,
                "label": s.label,
                "share": s.share,
                "expected_share": s.expected_share,
                "ratio": s.ratio,
            }
            for s in ts.seasonality
        ],
    }


def dumps(report: ProfileReport, *, indent: int | None = 2) -> str:
    """Serialize *report* to a stable JSON string."""
    return json.dumps(
        report_to_dict(report),
        indent=indent,
        sort_keys=False,
        ensure_ascii=False,
        default=str,
    )
