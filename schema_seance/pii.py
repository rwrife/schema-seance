"""PII heuristics — the "Whispers of the Personal" section.

Detectors return a list of :class:`PIIFinding` per column. Each finding
carries a ``kind`` (e.g. ``email``), a ``confidence`` in [0, 1], and a
``match_ratio`` (fraction of sampled non-null values that matched).

Confidence is computed from the match ratio with a small column-name
bonus when the header obviously implies the kind (e.g. ``email``,
``ssn``). The bonus is bounded so a column full of garbage with a
"helpful" name can still only crawl into the low-confidence band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PIIFinding",
    "detect_column",
    "confidence_band",
    "CONFIDENCE_BANDS",
]

# Band thresholds used by ``--fail-on-pii`` and the renderer.
CONFIDENCE_BANDS: dict[str, float] = {
    "low": 0.30,
    "medium": 0.60,
    "high": 0.85,
}


@dataclass(frozen=True)
class PIIFinding:
    kind: str
    confidence: float
    match_ratio: float
    matched: int
    sampled: int


# ---------------------------------------------------------------------------
# Regexes / matchers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Permissive intl phone: optional +, 7-15 digits with spaces/dashes/parens.
_PHONE_RE = re.compile(r"^\+?[\d][\d \-().]{6,20}$")

_DIGITS_RE = re.compile(r"\D+")

_SSN_RE = re.compile(r"^(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}$")
_SSN_LOOSE_RE = re.compile(r"^\d{9}$")

_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)
# Pragmatic IPv6: hex groups separated by ``:`` with optional ``::``.
_IPV6_RE = re.compile(
    r"^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
    r"|([0-9a-fA-F]{1,4}:){1,7}:"
    r"|:(:[0-9a-fA-F]{1,4}){1,7}"
    r"|::)$"
)

# Column-name hints. Used both as a tiny confidence bump and to weight
# weaker patterns (e.g. SSN-loose 9-digit).
_NAME_HINTS: dict[str, tuple[str, ...]] = {
    "email": ("email", "e_mail", "mail"),
    "phone": ("phone", "mobile", "cell", "tel"),
    "credit_card": ("card", "cc", "credit", "pan"),
    "ssn": ("ssn", "social"),
    "ipv4": ("ip", "ipv4", "address"),
    "ipv6": ("ipv6",),
    "name": ("name", "first_name", "last_name", "full_name", "fname", "lname"),
    "dob": ("dob", "birth", "birthday", "birthdate", "date_of_birth"),
}

# Tokens that, when present in a value, look name-ish.
_NAME_TITLES = ("mr", "mrs", "ms", "dr", "prof", "sir")


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _has_hint(col_norm: str, kind: str) -> bool:
    hints = _NAME_HINTS.get(kind, ())
    return any(h == col_norm or h in col_norm.split("_") for h in hints)


# ---------------------------------------------------------------------------
# Per-value matchers
# ---------------------------------------------------------------------------


def _luhn_ok(digits: str) -> bool:
    if not 12 <= len(digits) <= 19 or not digits.isdigit():
        return False
    total = 0
    parity = (len(digits) - 2) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_credit_card(value: str) -> bool:
    digits = _DIGITS_RE.sub("", value)
    return _luhn_ok(digits)


def _is_phone(value: str) -> bool:
    if not _PHONE_RE.match(value):
        return False
    digits = _DIGITS_RE.sub("", value)
    return 7 <= len(digits) <= 15


def _is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _is_ipv4(value: str) -> bool:
    return bool(_IPV4_RE.match(value))


def _is_ipv6(value: str) -> bool:
    return ":" in value and bool(_IPV6_RE.match(value))


def _is_ssn(value: str) -> bool:
    return bool(_SSN_RE.match(value))


def _is_ssn_loose(value: str) -> bool:
    return bool(_SSN_LOOSE_RE.match(value))


_NAME_TOKEN_RE = re.compile(r"^[A-Z][a-z'’\-]{1,}(?:[ \-][A-Z][a-z'’\-]{1,}){0,3}$")


def _looks_name(value: str) -> bool:
    v = value.strip()
    if not v or len(v) > 60:
        return False
    if v.lower().split()[0].rstrip(".") in _NAME_TITLES:
        return True
    return bool(_NAME_TOKEN_RE.match(v))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_MATCHERS: tuple[tuple[str, callable, float], ...] = (
    # (kind, matcher, base-required-ratio-for-medium-confidence)
    ("email", _is_email, 0.60),
    ("credit_card", _is_credit_card, 0.50),
    ("ipv4", _is_ipv4, 0.60),
    ("ipv6", _is_ipv6, 0.60),
    ("phone", _is_phone, 0.60),
    ("ssn", _is_ssn, 0.40),
)


def _score(match_ratio: float, hinted: bool) -> float:
    """Map match ratio + name-hint into a 0..1 confidence."""
    # 100% matches with a confirming column name => 1.0; without => 0.9.
    base = match_ratio
    bonus = 0.10 if hinted else 0.0
    return max(0.0, min(1.0, base * 0.9 + bonus + 0.0))


def _make_finding(kind: str, matched: int, sampled: int, hinted: bool) -> PIIFinding | None:
    if sampled == 0 or matched == 0:
        return None
    ratio = matched / sampled
    conf = _score(ratio, hinted)
    return PIIFinding(
        kind=kind,
        confidence=round(conf, 3),
        match_ratio=round(ratio, 3),
        matched=matched,
        sampled=sampled,
    )


def detect_column(
    name: str,
    dtype: str,
    values: list,
) -> list[PIIFinding]:
    """Return all PII findings for one column from a sample of values.

    ``values`` should be non-null distinct or representative samples.
    Non-string values are stringified for matcher purposes.
    """
    findings: list[PIIFinding] = []
    col_norm = _norm_col(name)
    str_values: list[str] = [str(v).strip() for v in values if v is not None]
    sampled = len(str_values)
    if sampled == 0:
        return findings

    for kind, matcher, _ in _MATCHERS:
        # Skip SSN-strict if no dashes present; loose handler below.
        matched = sum(1 for v in str_values if matcher(v))
        hinted = _has_hint(col_norm, kind)
        finding = _make_finding(kind, matched, sampled, hinted)
        if finding is not None:
            findings.append(finding)

    # SSN loose: only counts when the column name strongly hints SSN,
    # otherwise raw 9-digit ints get flagged everywhere.
    if _has_hint(col_norm, "ssn"):
        matched = sum(1 for v in str_values if _is_ssn_loose(v))
        # Only report if strict-SSN didn't already cover the column.
        if matched > 0 and not any(f.kind == "ssn" for f in findings):
            ratio = matched / sampled
            findings.append(
                PIIFinding(
                    kind="ssn",
                    confidence=round(min(1.0, ratio * 0.7 + 0.2), 3),
                    match_ratio=round(ratio, 3),
                    matched=matched,
                    sampled=sampled,
                )
            )

    # Likely-name column: header hint OR strong value pattern.
    name_hinted = _has_hint(col_norm, "name")
    name_matched = sum(1 for v in str_values if _looks_name(v))
    if name_hinted or name_matched / max(sampled, 1) >= 0.7:
        ratio = name_matched / sampled
        # Header alone -> medium ceiling; pattern + header -> high.
        if name_hinted and ratio >= 0.3:
            conf = round(min(1.0, ratio * 0.7 + 0.25), 3)
        elif name_hinted:
            conf = 0.35
        else:
            conf = round(min(0.9, ratio * 0.85), 3)
        if conf >= CONFIDENCE_BANDS["low"]:
            findings.append(
                PIIFinding(
                    kind="name",
                    confidence=conf,
                    match_ratio=round(ratio, 3),
                    matched=name_matched,
                    sampled=sampled,
                )
            )

    # DOB column: header hint AND a date-ish dtype or parseable values.
    dob_hinted = _has_hint(col_norm, "dob")
    if dob_hinted:
        upper = dtype.upper()
        date_dtype = "DATE" in upper or "TIMESTAMP" in upper
        # crude string-date check
        date_like = sum(
            1
            for v in str_values
            if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", v)
            or re.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$", v)
        )
        ratio = (sampled if date_dtype else date_like) / sampled
        if ratio > 0:
            conf = round(min(1.0, ratio * 0.8 + 0.2), 3)
            findings.append(
                PIIFinding(
                    kind="dob",
                    confidence=conf,
                    match_ratio=round(ratio, 3),
                    matched=int(ratio * sampled),
                    sampled=sampled,
                )
            )

    # Deduplicate (kind) keeping highest confidence.
    best: dict[str, PIIFinding] = {}
    for f in findings:
        if f.kind not in best or f.confidence > best[f.kind].confidence:
            best[f.kind] = f
    return sorted(best.values(), key=lambda f: f.confidence, reverse=True)


def confidence_band(confidence: float) -> str:
    """Return ``high``/``medium``/``low``/``none`` for *confidence*."""
    if confidence >= CONFIDENCE_BANDS["high"]:
        return "high"
    if confidence >= CONFIDENCE_BANDS["medium"]:
        return "medium"
    if confidence >= CONFIDENCE_BANDS["low"]:
        return "low"
    return "none"
