"""Time-series detection — "The Hours That Pass" section.

For each column that looks like a date or timestamp, compute:

- ``range``          — min/max timestamp
- ``cadence``        — most common inter-row delta, plus % of rows
                       that conform to it
- ``gaps``           — expected vs. actual buckets in the range, with
                       the top-N largest gaps surfaced
- ``seasonality``    — day-of-week and hour-of-day skew flags when a
                       bucket sees > 2× the expected frequency

Detection works for either real DuckDB DATE/TIMESTAMP columns or
string columns parseable by a small set of common formats. The PII /
anomaly heuristics already get a sample of column values; we reuse the
same sample budget here to stay fast.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Gap",
    "SeasonalitySignal",
    "TimeSeriesProfile",
    "detect_column",
    "CADENCE_TOLERANCE",
]


# Inter-bucket conformance tolerance: a delta that matches the median
# within this factor counts as "on the cadence".
CADENCE_TOLERANCE = 0.10

# How many of the largest gaps to surface.
TOP_GAPS = 5

# Seasonality flag threshold: a bucket whose share is at least this many
# times its expected share gets flagged.
SEASONALITY_RATIO = 2.0

# Minimum non-null timestamps needed before we attempt to profile a column.
MIN_POINTS = 4


@dataclass(frozen=True)
class Gap:
    start: str
    end: str
    duration_seconds: float


@dataclass(frozen=True)
class SeasonalitySignal:
    bucket: str  # "day_of_week" | "hour_of_day"
    label: str  # e.g. "Mon", "14"
    share: float  # observed share of points (0..1)
    expected_share: float  # 1/n_buckets
    ratio: float  # share / expected_share


@dataclass(frozen=True)
class TimeSeriesProfile:
    column: str
    detected_from: str  # "dtype" | "parsed"
    points: int
    range_min: str
    range_max: str
    cadence_seconds: float | None
    cadence_label: str | None  # human label ("1d", "1h15m", "30s")
    conformance_pct: float  # % of inter-bucket deltas matching cadence
    expected_buckets: int | None
    missing_buckets: int | None
    gaps: tuple[Gap, ...] = ()
    seasonality: tuple[SeasonalitySignal, ...] = field(default_factory=tuple)


# --- Date detection ---------------------------------------------------------


_DATE_DTYPES = ("DATE", "TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "DATETIME")


def _looks_like_date_dtype(dtype: str) -> bool:
    upper = dtype.upper()
    return any(upper.startswith(prefix) for prefix in _DATE_DTYPES)


def _looks_like_date_name(name: str) -> bool:
    n = name.lower()
    return any(
        tok in n
        for tok in (
            "date",
            "time",
            "timestamp",
            "_at",
            "_on",
            "created",
            "updated",
            "occurred",
            "logged",
        )
    )


# Common parse formats, tried in order. ISO-8601 / RFC3339 first.
_PARSE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y%m%d",
)


def _parse_one(value: str) -> _dt.datetime | None:
    s = value.strip()
    if not s:
        return None
    # Handle the RFC3339 "Z" suffix without timezone-aware comparisons later.
    if s.endswith("Z"):
        s = s[:-1]
    # Trim trailing +HH:MM / -HH:MM offsets — we collapse to naive.
    if len(s) >= 6 and s[-6] in "+-" and s[-3] == ":":
        s = s[:-6]
    for fmt in _PARSE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat handles a fair number of fractional/no-T variants.
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _coerce(values: list[Any], dtype: str) -> tuple[list[_dt.datetime], str] | None:
    """Return (parsed_datetimes, source) or None if the column doesn't qualify."""
    if not values:
        return None

    parsed: list[_dt.datetime] = []
    source = "dtype"

    if _looks_like_date_dtype(dtype):
        for v in values:
            if v is None:
                continue
            if isinstance(v, _dt.datetime):
                parsed.append(v.replace(tzinfo=None) if v.tzinfo else v)
            elif isinstance(v, _dt.date):
                parsed.append(_dt.datetime(v.year, v.month, v.day))
            else:
                # Foreign type masquerading as a date — try the string path.
                p = _parse_one(str(v))
                if p is not None:
                    parsed.append(p)
        if len(parsed) >= MIN_POINTS:
            return parsed, source
        return None

    # String column — only try when sample looks plausible to avoid wasted work.
    string_vals = [v for v in values if isinstance(v, str) and v.strip()]
    if not string_vals:
        return None
    # Quick prefilter: at least 70% of sampled strings contain a digit-run that
    # could plausibly be a year/date. Cheap, keeps us off ID columns.
    plausible = sum(1 for s in string_vals if _has_date_shape(s))
    if plausible / max(1, len(string_vals)) < 0.7:
        return None

    matches = 0
    for s in string_vals:
        p = _parse_one(s)
        if p is not None:
            parsed.append(p)
            matches += 1
    # Need both a healthy parse rate AND enough points to bother.
    if matches / max(1, len(string_vals)) < 0.8 or len(parsed) < MIN_POINTS:
        return None
    return parsed, "parsed"


def _has_date_shape(s: str) -> bool:
    s = s.strip()
    if len(s) < 6 or len(s) > 40:
        return False
    digits = sum(c.isdigit() for c in s)
    if digits < 4:
        return False
    seps = sum(c in "-/:T " for c in s)
    return seps >= 2 or "T" in s


# --- Cadence + gaps ---------------------------------------------------------


def _format_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s <= 0:
        return f"{seconds:.3f}s"
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    if s < 60:
        return f"{s}s"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if sec:
        parts.append(f"{sec}s")
    return "".join(parts) or "0s"


def _infer_cadence(deltas: list[float]) -> tuple[float | None, float]:
    """Return (cadence_seconds, conformance_pct)."""
    if not deltas:
        return None, 0.0
    # Use the median as a robust cadence estimate.
    sorted_d = sorted(deltas)
    mid = len(sorted_d) // 2
    if len(sorted_d) % 2:
        cadence = sorted_d[mid]
    else:
        cadence = (sorted_d[mid - 1] + sorted_d[mid]) / 2
    if cadence <= 0:
        return None, 0.0
    tol = cadence * CADENCE_TOLERANCE
    on_cadence = sum(1 for d in deltas if abs(d - cadence) <= tol)
    return cadence, round(100.0 * on_cadence / len(deltas), 2)


def _gap_report(
    points: list[_dt.datetime], cadence: float | None
) -> tuple[int | None, int | None, tuple[Gap, ...]]:
    if cadence is None or cadence <= 0 or len(points) < 2:
        return None, None, ()
    total_seconds = (points[-1] - points[0]).total_seconds()
    expected = int(round(total_seconds / cadence)) + 1
    actual = len(points)
    missing = max(0, expected - actual)

    # Largest gaps by duration relative to cadence.
    candidates: list[Gap] = []
    threshold = cadence * (1.0 + CADENCE_TOLERANCE)
    for prev, cur in zip(points, points[1:], strict=False):
        delta = (cur - prev).total_seconds()
        if delta > threshold:
            candidates.append(
                Gap(
                    start=prev.isoformat(),
                    end=cur.isoformat(),
                    duration_seconds=delta,
                )
            )
    candidates.sort(key=lambda g: g.duration_seconds, reverse=True)
    return expected, missing, tuple(candidates[:TOP_GAPS])


# --- Seasonality ------------------------------------------------------------


_DOW_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _seasonality(points: list[_dt.datetime]) -> tuple[SeasonalitySignal, ...]:
    if len(points) < 14:
        return ()
    signals: list[SeasonalitySignal] = []

    # Day-of-week skew. Only flag if the timestamps actually span >= 7 days,
    # otherwise the buckets are trivially uneven.
    span_days = (points[-1] - points[0]).total_seconds() / 86400.0
    if span_days >= 7:
        dow_counts = [0] * 7
        for p in points:
            dow_counts[p.weekday()] += 1
        total = sum(dow_counts)
        expected = 1 / 7
        for i, c in enumerate(dow_counts):
            share = c / total
            ratio = share / expected if expected else 0.0
            if ratio >= SEASONALITY_RATIO:
                signals.append(
                    SeasonalitySignal(
                        bucket="day_of_week",
                        label=_DOW_LABELS[i],
                        share=round(share, 4),
                        expected_share=round(expected, 4),
                        ratio=round(ratio, 2),
                    )
                )

    # Hour-of-day only meaningful when the column carries sub-day resolution
    # (i.e. not all timestamps are midnight) AND we span >= 24h.
    has_hours = any(p.hour or p.minute or p.second for p in points)
    span_hours = (points[-1] - points[0]).total_seconds() / 3600.0
    if has_hours and span_hours >= 24:
        hod_counts = [0] * 24
        for p in points:
            hod_counts[p.hour] += 1
        total = sum(hod_counts)
        expected = 1 / 24
        for i, c in enumerate(hod_counts):
            share = c / total
            ratio = share / expected if expected else 0.0
            if ratio >= SEASONALITY_RATIO:
                signals.append(
                    SeasonalitySignal(
                        bucket="hour_of_day",
                        label=f"{i:02d}",
                        share=round(share, 4),
                        expected_share=round(expected, 4),
                        ratio=round(ratio, 2),
                    )
                )

    return tuple(signals)


# --- Public entrypoint ------------------------------------------------------


def detect_column(*, name: str, dtype: str, values: list[Any]) -> TimeSeriesProfile | None:
    """Return a :class:`TimeSeriesProfile` if *values* look temporal, else None."""
    # Cheap reject: if the name doesn't hint date AND dtype isn't date-like,
    # fall through anyway — string columns named "logged" or "created" are
    # the common case, but a column literally typed as TIMESTAMP wins outright.
    coerced = _coerce(values, dtype)
    if coerced is None:
        return None
    parsed, source = coerced

    # If the source is parsed strings, require a date-shaped column name —
    # avoids profiling free-text columns that happen to contain ISO dates.
    if source == "parsed" and not _looks_like_date_name(name):
        return None

    parsed.sort()
    deltas = [(b - a).total_seconds() for a, b in zip(parsed, parsed[1:], strict=False)]
    cadence, conformance = _infer_cadence(deltas)
    expected, missing, gaps = _gap_report(parsed, cadence)
    seasonality = _seasonality(parsed)

    return TimeSeriesProfile(
        column=name,
        detected_from=source,
        points=len(parsed),
        range_min=parsed[0].isoformat(),
        range_max=parsed[-1].isoformat(),
        cadence_seconds=cadence,
        cadence_label=_format_duration(cadence) if cadence else None,
        conformance_pct=conformance,
        expected_buckets=expected,
        missing_buckets=missing,
        gaps=gaps,
        seasonality=seasonality,
    )
