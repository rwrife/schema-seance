"""Quality Score + letter Grade for a ProfileReport.

Rolls the profile up into a single 0–100 score (higher = healthier) and an
A–F grade so the CLI can emit README badges, gate CI, and give humans a
one-glance verdict without parsing the full report.

The scoring model is intentionally simple and *pure* — no I/O, no side
effects — so it stays trivial to reason about and to test:

* Start at 100.
* Subtract capped penalties for each health signal we already collect
  (null density, mixed types, PK duplicates, numeric outliers, PII
  exposure, encoding weirdness).
* Clamp to ``[0, 100]``, round to an integer, look up the grade.

Weights are hard-coded here so the "public contract" of a score is stable;
they can be lifted into config later without changing the return shape.

The badge output mirrors shields.io's visual grammar (rounded rect, two
tones, small SVG) so people can drop it in a README without pulling in
shields.io as a dependency.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from .profile import ProfileReport

__all__ = [
    "GRADE_BANDS",
    "PenaltyDetail",
    "ScoreResult",
    "compute_score",
    "render_badge_svg",
]


# ---------------------------------------------------------------------------
# Grade bands
# ---------------------------------------------------------------------------
# Ordered high → low so ``letter_for`` can short-circuit on the first hit.
GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (0, "F"),
)


def letter_for(score: int) -> str:
    """Return the letter grade for ``score`` using :data:`GRADE_BANDS`."""
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PenaltyDetail:
    """One line item on the score breakdown."""

    kind: str
    points: int  # positive integer; how many points were deducted
    detail: str


@dataclass(frozen=True)
class ScoreResult:
    """The final score, grade, and (optional) breakdown."""

    score: int
    grade: str
    penalties: tuple[PenaltyDetail, ...] = field(default_factory=tuple)

    @property
    def color(self) -> str:
        """shields.io-style colour for the score / badge."""
        return _grade_color(self.grade)


# ---------------------------------------------------------------------------
# Penalty weights (kept explicit so tests can pin behaviour)
# ---------------------------------------------------------------------------

# Null density: per-column contribution min(null_pct / 2, MAX_PER_COL).
# Only columns above the "meaningful nulls" floor count.
_NULL_FLOOR_PCT = 20.0
_NULL_PER_COL_CAP = 20  # any single column can't cost more than this
_NULL_TOTAL_CAP = 30

# Mixed types: flat per-column charge.
_MIXED_PER_COL = 6
_MIXED_TOTAL_CAP = 18

# PK-candidate columns with duplicate values.
_PK_DUP_PER_COL = 10
_PK_DUP_TOTAL_CAP = 20

# Numeric outliers — mild by default; anomaly severity drives the cost.
_OUTLIER_BY_SEVERITY = {"info": 1, "warn": 2, "high": 3}
_OUTLIER_TOTAL_CAP = 8

# Undeclared PII exposure (per column, worst finding on that column wins).
_PII_BY_BAND = {"high": 4, "medium": 2, "low": 1, "none": 0}
_PII_TOTAL_CAP = 20

# Encoding / mojibake hints — placeholder that fires when a text-ish
# reader couldn't nail the encoding.
_ENCODING_TOTAL_CAP = 3

_TEXT_EXTS = {".csv", ".tsv", ".jsonl", ".ndjson"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_score(report: ProfileReport) -> ScoreResult:
    """Compute a :class:`ScoreResult` from a :class:`ProfileReport`.

    Pure function — no I/O, no persona logic. Callers (CLI, JSON serializer,
    HTML report) are free to render it however they like.
    """
    penalties: list[PenaltyDetail] = []

    _apply_null_density(report, penalties)
    _apply_mixed_types(report, penalties)
    _apply_pk_duplicates(report, penalties)
    _apply_numeric_outliers(report, penalties)
    _apply_pii_exposure(report, penalties)
    _apply_encoding(report, penalties)

    deducted = sum(p.points for p in penalties)
    raw = 100 - deducted
    score = max(0, min(100, raw))
    grade = letter_for(score)
    # Sort penalties by size desc for nice, stable presentation.
    penalties.sort(key=lambda p: (-p.points, p.kind))
    return ScoreResult(score=score, grade=grade, penalties=tuple(penalties))


# ---------------------------------------------------------------------------
# JSON helper (kept here so render/json.py stays a passive serializer)
# ---------------------------------------------------------------------------


def score_to_dict(result: ScoreResult) -> dict[str, Any]:
    """Convert a :class:`ScoreResult` into a stable JSON-friendly dict."""
    return {
        "score": result.score,
        "grade": result.grade,
        "color": result.color,
        "penalties": [
            {"kind": p.kind, "points": p.points, "detail": p.detail} for p in result.penalties
        ],
    }


# ---------------------------------------------------------------------------
# Individual penalty rules
# ---------------------------------------------------------------------------


def _apply_null_density(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    hot: list[tuple[str, float, int]] = []
    for col in report.columns:
        if col.null_pct <= _NULL_FLOOR_PCT:
            continue
        cost = min(int(round(col.null_pct / 2.0)), _NULL_PER_COL_CAP)
        if cost <= 0:
            continue
        hot.append((col.name, col.null_pct, cost))
    if not hot:
        return
    total = min(sum(c[2] for c in hot), _NULL_TOTAL_CAP)
    if total <= 0:
        return
    worst = sorted(hot, key=lambda h: -h[1])[:3]
    detail_bits = ", ".join(f"'{n}' {p:.0f}%" for n, p, _ in worst)
    out.append(
        PenaltyDetail(
            kind="null_density",
            points=total,
            detail=(
                f"high null density ({len(hot)} column"
                f"{'s' if len(hot) != 1 else ''} > {_NULL_FLOOR_PCT:.0f}% null: "
                f"{detail_bits})"
            ),
        )
    )


def _apply_mixed_types(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    hits = [
        col.name for col in report.columns if any(a.kind == "mixed_types" for a in col.anomalies)
    ]
    if not hits:
        return
    total = min(len(hits) * _MIXED_PER_COL, _MIXED_TOTAL_CAP)
    quoted = ", ".join(f"'{n}'" for n in hits[:3])
    tail = "" if len(hits) <= 3 else f" (+{len(hits) - 3} more)"
    out.append(
        PenaltyDetail(
            kind="mixed_types",
            points=total,
            detail=f"mixed types in {quoted}{tail}",
        )
    )


def _apply_pk_duplicates(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    hits = [
        col.name for col in report.columns if any(a.kind == "pk_duplicates" for a in col.anomalies)
    ]
    if not hits:
        return
    total = min(len(hits) * _PK_DUP_PER_COL, _PK_DUP_TOTAL_CAP)
    quoted = ", ".join(f"'{n}'" for n in hits[:3])
    tail = "" if len(hits) <= 3 else f" (+{len(hits) - 3} more)"
    out.append(
        PenaltyDetail(
            kind="pk_duplicates",
            points=total,
            detail=f"duplicate values in PK-candidate {quoted}{tail}",
        )
    )


def _apply_numeric_outliers(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    contributions: list[tuple[str, int]] = []
    for col in report.columns:
        for a in col.anomalies:
            if a.kind != "numeric_outliers":
                continue
            cost = _OUTLIER_BY_SEVERITY.get(a.severity, 1)
            if cost > 0:
                contributions.append((col.name, cost))
            break  # one row per column
    if not contributions:
        return
    total = min(sum(c for _, c in contributions), _OUTLIER_TOTAL_CAP)
    if total <= 0:
        return
    names = ", ".join(f"'{n}'" for n, _ in contributions[:3])
    tail = "" if len(contributions) <= 3 else f" (+{len(contributions) - 3} more)"
    out.append(
        PenaltyDetail(
            kind="numeric_outliers",
            points=total,
            detail=f"numeric outliers in {names}{tail}",
        )
    )


def _apply_pii_exposure(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    # Deferred to avoid circular imports (pii imports nothing from profile).
    from .pii import confidence_band

    per_col_cost: list[tuple[str, str, int]] = []
    for col in report.columns:
        worst_cost = 0
        worst_kind = None
        worst_band = "none"
        for finding in col.pii:
            band = confidence_band(finding.confidence)
            cost = _PII_BY_BAND.get(band, 0)
            if cost > worst_cost:
                worst_cost = cost
                worst_kind = finding.kind
                worst_band = band
        if worst_cost > 0 and worst_kind is not None:
            per_col_cost.append((col.name, f"{worst_kind}:{worst_band}", worst_cost))
    if not per_col_cost:
        return
    total = min(sum(c for _, _, c in per_col_cost), _PII_TOTAL_CAP)
    if total <= 0:
        return
    detail_bits = ", ".join(f"{n} ({k})" for n, k, _ in per_col_cost[:3])
    tail = "" if len(per_col_cost) <= 3 else f" (+{len(per_col_cost) - 3} more)"
    out.append(
        PenaltyDetail(
            kind="pii_exposure",
            points=total,
            detail=f"PII detected without masking: {detail_bits}{tail}",
        )
    )


def _apply_encoding(report: ProfileReport, out: list[PenaltyDetail]) -> None:
    # Only fire when we're looking at a text-ish local file whose encoding
    # the reader refused to declare. Remote URLs / binary formats don't
    # contribute — we simply don't know enough.
    path = report.path
    if path is None:
        return
    try:
        suffix = getattr(path, "suffix", "").lower()  # type: ignore[union-attr]
    except AttributeError:
        return
    if suffix not in _TEXT_EXTS:
        return
    if report.encoding:
        return
    out.append(
        PenaltyDetail(
            kind="encoding",
            points=_ENCODING_TOTAL_CAP,
            detail=f"could not confirm text encoding for {suffix} input",
        )
    )


# ---------------------------------------------------------------------------
# Badge rendering
# ---------------------------------------------------------------------------


def _grade_color(grade: str) -> str:
    return {
        "A": "#4c1",  # shields.io "brightgreen"
        "B": "#97CA00",  # "green"
        "C": "#dfb317",  # "yellow"
        "D": "#fe7d37",  # "orange"
        "F": "#e05d44",  # "red"
    }.get(grade, "#9f9f9f")


def _text_width(text: str) -> int:
    """Very rough px width estimate for shields.io-style badges.

    Not a real font metric, but good enough for a compact SVG that renders
    consistently in GitHub's markdown pipeline.
    """
    # Wider chars get a bit more room; skinny chars a bit less.
    wide = set("MW@%_")
    thin = set("ijl1.,:'")
    px = 0.0
    for ch in text:
        if ch in wide:
            px += 8.5
        elif ch in thin:
            px += 3.5
        else:
            px += 6.5
    return max(20, int(round(px)))


def render_badge_svg(
    result: ScoreResult,
    *,
    label: str = "Data Quality",
    value: str | None = None,
) -> str:
    """Render *result* as a shields.io-style rounded SVG badge.

    Uses only inline SVG (no external font, no CSS import) so it renders
    identically in GitHub, GitLab, and static-site generators.
    """
    label_text = html.escape(label)
    value_text = html.escape(value if value is not None else f"{result.score} · {result.grade}")

    pad = 10
    label_w = _text_width(label_text) + pad
    value_w = _text_width(value_text) + pad
    total_w = label_w + value_w
    color = result.color

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{total_w}" height="20" '
        f'role="img" aria-label="{label_text}: {value_text}">'
        f"<title>{label_text}: {value_text}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/>'
        f"</linearGradient>"
        f'<clipPath id="r"><rect width="{total_w}" height="20" rx="3" fill="#fff"/>'
        f"</clipPath>"
        f'<g clip-path="url(#r)">'
        f'<rect width="{label_w}" height="20" fill="#555"/>'
        f'<rect x="{label_w}" width="{value_w}" height="20" fill="{color}"/>'
        f'<rect width="{total_w}" height="20" fill="url(#s)"/>'
        f"</g>"
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'text-rendering="geometricPrecision" font-size="110">'
        f'<text aria-hidden="true" x="{label_w * 5}" y="150" fill="#010101" '
        f'fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(label_w - pad) * 10}">{label_text}</text>'
        f'<text x="{label_w * 5}" y="140" transform="scale(.1)" fill="#fff" '
        f'textLength="{(label_w - pad) * 10}">{label_text}</text>'
        f'<text aria-hidden="true" x="{(label_w + value_w / 2) * 10}" y="150" '
        f'fill="#010101" fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(value_w - pad) * 10}">{value_text}</text>'
        f'<text x="{(label_w + value_w / 2) * 10}" y="140" transform="scale(.1)" '
        f'fill="#fff" textLength="{(value_w - pad) * 10}">{value_text}</text>'
        f"</g>"
        f"</svg>"
    )
