"""PII redaction export — the ``seance redact`` subcommand backbone.

Given a profiled :class:`~schema_seance.profile.ProfileReport`, build a
:class:`RedactionPlan` that maps each column whose PII confidence meets a
minimum band to a :class:`RedactionAction` (kind + strategy). Then
:func:`apply_value` deterministically transforms each cell.

The plan is pure data, so the CLI layer (or tests) can dry-run it without
touching the filesystem.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .pii import CONFIDENCE_BANDS, PIIFinding
from .profile import ProfileReport

__all__ = [
    "STRATEGIES",
    "DEFAULT_STRATEGY",
    "RedactionAction",
    "RedactionPlan",
    "build_plan",
    "apply_value",
    "redact_row",
    "RedactionError",
]


class RedactionError(ValueError):
    """Raised for invalid strategy specs or unsupported output formats."""


# Allowed strategy names. ``year`` is only meaningful for DOB-like columns
# but we keep it generic — invalid combinations fall back to ``mask``.
STRATEGIES: frozenset[str] = frozenset({"mask", "hash", "null", "keep", "year"})


# Default strategy per detector kind. Aligned with the issue's acceptance
# criteria:
#   - emails / phones / CC / SSN -> mask (with smart preservation)
#   - names                       -> hash (sha-256, truncated)
#   - DOB                         -> year only
#   - IPs                         -> network truncation (handled by mask)
DEFAULT_STRATEGY: dict[str, str] = {
    "email": "mask",
    "phone": "mask",
    "credit_card": "mask",
    "ssn": "mask",
    "ipv4": "mask",
    "ipv6": "mask",
    "name": "hash",
    "dob": "year",
}


# Length of the hex slice returned by the ``hash`` strategy. 16 hex chars
# (~64 bits) is collision-resistant enough for analytics use without
# blowing up output size.
_HASH_HEX_LEN = 16


@dataclass(frozen=True)
class RedactionAction:
    """One column's redaction decision."""

    column: str
    kind: str
    strategy: str
    confidence: float


@dataclass(frozen=True)
class RedactionPlan:
    """Per-column redaction plan and the columns the input exposes."""

    actions: tuple[RedactionAction, ...]
    all_columns: tuple[str, ...] = ()
    min_confidence: str = "high"
    # Counts populated by :func:`apply_to_rows` after execution. Pure
    # planning leaves it empty.
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(a.column for a in self.actions)

    def action_for(self, column: str) -> RedactionAction | None:
        for a in self.actions:
            if a.column == column:
                return a
        return None


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def _best_finding(findings: tuple[PIIFinding, ...], threshold: float) -> PIIFinding | None:
    eligible = [f for f in findings if f.confidence >= threshold]
    if not eligible:
        return None
    return max(eligible, key=lambda f: f.confidence)


def parse_strategy_overrides(specs: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Parse ``detector=strategy`` strings into a mapping.

    Raises :class:`RedactionError` on malformed input.
    """
    out: dict[str, str] = {}
    for raw in specs:
        if "=" not in raw:
            raise RedactionError(
                f"Bad --strategy {raw!r}; expected <detector>=<mask|hash|null|keep>."
            )
        kind, _, strat = raw.partition("=")
        kind = kind.strip().lower()
        strat = strat.strip().lower()
        if not kind or strat not in STRATEGIES:
            raise RedactionError(
                f"Bad --strategy {raw!r}; strategy must be one of {sorted(STRATEGIES)}."
            )
        out[kind] = strat
    return out


def build_plan(
    report: ProfileReport,
    *,
    min_confidence: str = "high",
    strategy_overrides: dict[str, str] | None = None,
) -> RedactionPlan:
    """Decide which columns to redact and how.

    ``min_confidence`` is one of ``low|medium|high``. ``strategy_overrides``
    is keyed by detector kind (e.g. ``{"email": "hash"}``).
    """
    band = (min_confidence or "high").lower()
    if band not in CONFIDENCE_BANDS:
        raise RedactionError(f"Bad --min-confidence {min_confidence!r}; choose low|medium|high.")
    threshold = CONFIDENCE_BANDS[band]
    overrides = {k.lower(): v.lower() for k, v in (strategy_overrides or {}).items()}

    actions: list[RedactionAction] = []
    for col in report.columns:
        best = _best_finding(col.pii, threshold)
        if best is None:
            continue
        strat = overrides.get(best.kind, DEFAULT_STRATEGY.get(best.kind, "mask"))
        if strat == "keep":
            continue
        actions.append(
            RedactionAction(
                column=col.name,
                kind=best.kind,
                strategy=strat,
                confidence=best.confidence,
            )
        )
    return RedactionPlan(
        actions=tuple(actions),
        all_columns=tuple(c.name for c in report.columns),
        min_confidence=band,
    )


# ---------------------------------------------------------------------------
# Value-level transforms
# ---------------------------------------------------------------------------


_DIGITS = re.compile(r"\d")


def _mask_email(value: str) -> str:
    if "@" not in value:
        return "***"
    _, _, domain = value.partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return f"***@***.{tld}" if tld else "***@***"


def _mask_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def _mask_credit_card(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def _mask_ssn(value: str) -> str:
    # Always fully redact; SSN has no useful suffix.
    return "***-**-****"


def _mask_ipv4(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 4:
        return "***"
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0"


def _mask_ipv6(value: str) -> str:
    # Truncate to /48 — first three hex groups, rest collapsed.
    head = value.split("::", 1)[0]
    groups = head.split(":")
    keep = [g for g in groups if g][:3]
    if not keep:
        return "::"
    return ":".join(keep) + "::"


def _mask_name(value: str) -> str:
    return "***"


def _mask_generic(value: str) -> str:
    return "***"


_MASKERS = {
    "email": _mask_email,
    "phone": _mask_phone,
    "credit_card": _mask_credit_card,
    "ssn": _mask_ssn,
    "ipv4": _mask_ipv4,
    "ipv6": _mask_ipv6,
    "name": _mask_name,
}


def _hash_value(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:_HASH_HEX_LEN]


_YEAR_RE = re.compile(r"(\d{4})")


def _year_only(value: str) -> str | None:
    """Best-effort: extract a 4-digit year, else None."""
    m = _YEAR_RE.search(value)
    return m.group(1) if m else None


def apply_value(value: Any, kind: str, strategy: str) -> Any:
    """Return the redacted form of *value* per (kind, strategy).

    ``None`` passes through untouched (a null cell remains null).
    """
    if value is None:
        return None
    if strategy == "keep":
        return value
    if strategy == "null":
        return None

    s = str(value)
    if strategy == "hash":
        return _hash_value(s)
    if strategy == "year":
        yr = _year_only(s)
        # year strategy on a non-date column degrades gracefully to mask.
        return yr if yr is not None else _mask_generic(s)
    # mask
    masker = _MASKERS.get(kind, _mask_generic)
    return masker(s)


def redact_row(
    row: dict[str, Any] | list | tuple,
    columns: list[str] | tuple[str, ...],
    plan: RedactionPlan,
    counts: dict[str, int] | None = None,
) -> dict[str, Any] | list:
    """Apply *plan* to a single row.

    Accepts either a mapping or a positional row paired with *columns*.
    If *counts* is given, increments per-column tallies of cells that
    actually changed value.
    """
    actions_by_col = {a.column: a for a in plan.actions}

    if isinstance(row, dict):
        out_map: dict[str, Any] = dict(row)
        for col, action in actions_by_col.items():
            if col not in out_map:
                continue
            original = out_map[col]
            new_val = apply_value(original, action.kind, action.strategy)
            out_map[col] = new_val
            if counts is not None and new_val != original and original is not None:
                counts[col] = counts.get(col, 0) + 1
        return out_map

    # positional
    out_list = list(row)
    for idx, col in enumerate(columns):
        action = actions_by_col.get(col)
        if action is None or idx >= len(out_list):
            continue
        original = out_list[idx]
        new_val = apply_value(original, action.kind, action.strategy)
        out_list[idx] = new_val
        if counts is not None and new_val != original and original is not None:
            counts[col] = counts.get(col, 0) + 1
    return out_list
