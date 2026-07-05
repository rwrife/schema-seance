"""Tests for the Data Quality Score + Grade + Badge feature (#40)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.anomalies import Anomaly
from schema_seance.cli import main as seance_cli
from schema_seance.pii import PIIFinding
from schema_seance.profile import ColumnProfile, ProfileReport, profile
from schema_seance.readers import load
from schema_seance.render.json import dumps as dumps_json
from schema_seance.score import (
    GRADE_BANDS,
    PenaltyDetail,
    ScoreResult,
    compute_score,
    letter_for,
    render_badge_svg,
    score_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_col(
    name: str,
    *,
    dtype: str = "VARCHAR",
    null_pct: float = 0.0,
    distinct: int = 10,
    anomalies: tuple[Anomaly, ...] = (),
    pii: tuple[PIIFinding, ...] = (),
) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        dtype=dtype,
        null_pct=null_pct,
        distinct=distinct,
        sample=None,
        anomalies=anomalies,
        pii=pii,
    )


def _mk_report(*columns: ColumnProfile, path: Path | None = None) -> ProfileReport:
    return ProfileReport(
        path=path,
        rows=100,
        cols=len(columns),
        size_bytes=None,
        encoding="utf-8",
        columns=list(columns),
    )


# ---------------------------------------------------------------------------
# letter_for / GRADE_BANDS sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (100, "A"),
        (95, "A"),
        (90, "A"),
        (89, "B"),
        (80, "B"),
        (75, "C"),
        (70, "C"),
        (69, "D"),
        (60, "D"),
        (59, "F"),
        (0, "F"),
    ],
)
def test_letter_for_uses_grade_bands(score: int, expected: str) -> None:
    assert letter_for(score) == expected


def test_grade_bands_sorted_high_to_low() -> None:
    thresholds = [t for t, _ in GRADE_BANDS]
    assert thresholds == sorted(thresholds, reverse=True)


# ---------------------------------------------------------------------------
# compute_score — pure model
# ---------------------------------------------------------------------------


def test_score_perfect_report_is_100_a() -> None:
    report = _mk_report(
        _mk_col("id", dtype="INTEGER"),
        _mk_col("name", dtype="VARCHAR"),
    )
    result = compute_score(report)
    assert result.score == 100
    assert result.grade == "A"
    assert result.penalties == ()


def test_score_null_density_only_counts_columns_over_floor() -> None:
    # 15% nulls is under the 20% floor — should not contribute.
    report = _mk_report(_mk_col("a", null_pct=15.0), _mk_col("b", null_pct=10.0))
    assert compute_score(report).score == 100


def test_score_null_density_penalty_and_cap() -> None:
    # Two columns > floor: 40% -> 20, 60% -> 30 (capped per-col at 20).
    # Sum would be 40, but the *total* null-density cap is 30.
    report = _mk_report(_mk_col("a", null_pct=40.0), _mk_col("b", null_pct=60.0))
    result = compute_score(report)
    null_penalties = [p for p in result.penalties if p.kind == "null_density"]
    assert len(null_penalties) == 1
    assert null_penalties[0].points == 30
    assert "'b'" in null_penalties[0].detail  # highest-null column first


def test_score_mixed_types_charges_per_column_and_caps() -> None:
    mixed = Anomaly(kind="mixed_types", severity="warn", detail="")
    cols = [_mk_col(f"c{i}", anomalies=(mixed,)) for i in range(5)]
    result = compute_score(_mk_report(*cols))
    mt = [p for p in result.penalties if p.kind == "mixed_types"]
    assert len(mt) == 1
    # 5 * 6 = 30, capped at 18
    assert mt[0].points == 18


def test_score_pk_duplicates_charges_and_caps() -> None:
    dup = Anomaly(kind="pk_duplicates", severity="high", detail="")
    cols = [_mk_col(f"c{i}", anomalies=(dup,)) for i in range(3)]
    result = compute_score(_mk_report(*cols))
    pk = [p for p in result.penalties if p.kind == "pk_duplicates"]
    assert len(pk) == 1
    # 3 * 10 = 30, capped at 20
    assert pk[0].points == 20


def test_score_pii_exposure_uses_worst_finding_per_column() -> None:
    hi = PIIFinding(kind="email", confidence=0.95, match_ratio=1.0, matched=10, sampled=10)
    lo = PIIFinding(kind="phone", confidence=0.35, match_ratio=0.4, matched=4, sampled=10)
    col_hi = _mk_col("contact", pii=(hi, lo))
    col_med = _mk_col(
        "note",
        pii=(PIIFinding(kind="name", confidence=0.7, match_ratio=0.7, matched=7, sampled=10),),
    )
    result = compute_score(_mk_report(col_hi, col_med))
    pii = [p for p in result.penalties if p.kind == "pii_exposure"]
    assert len(pii) == 1
    # Worst per column: contact=high(4), note=medium(2) => 6
    assert pii[0].points == 6


def test_score_numeric_outliers_severity_weights() -> None:
    warn = Anomaly(kind="numeric_outliers", severity="warn", detail="")
    info = Anomaly(kind="numeric_outliers", severity="info", detail="")
    cols = [
        _mk_col("a", dtype="INTEGER", anomalies=(warn,)),
        _mk_col("b", dtype="INTEGER", anomalies=(info,)),
    ]
    result = compute_score(_mk_report(*cols))
    o = [p for p in result.penalties if p.kind == "numeric_outliers"]
    assert len(o) == 1
    assert o[0].points == 3  # 2 + 1


def test_score_encoding_penalty_only_for_text_files_without_encoding(tmp_path: Path) -> None:
    # Text ext + no encoding -> penalty fires.
    p = tmp_path / "mystery.csv"
    p.write_text("a,b\n1,2\n")
    report = ProfileReport(
        path=p,
        rows=1,
        cols=2,
        size_bytes=8,
        encoding=None,
        columns=[_mk_col("a", dtype="INTEGER"), _mk_col("b", dtype="INTEGER")],
    )
    result = compute_score(report)
    assert any(p.kind == "encoding" for p in result.penalties)

    # With an encoding declared: no penalty.
    report2 = ProfileReport(
        path=p,
        rows=1,
        cols=2,
        size_bytes=8,
        encoding="utf-8",
        columns=[_mk_col("a", dtype="INTEGER")],
    )
    assert not any(p.kind == "encoding" for p in compute_score(report2).penalties)


def test_score_clamped_at_zero_for_catastrophic_report() -> None:
    # Force enough penalties to go below zero and confirm we clamp.
    mixed = Anomaly(kind="mixed_types", severity="warn", detail="")
    dup = Anomaly(kind="pk_duplicates", severity="high", detail="")
    hi_pii = PIIFinding(kind="email", confidence=0.95, match_ratio=1.0, matched=10, sampled=10)
    cols = []
    for i in range(6):
        cols.append(
            _mk_col(
                f"c{i}",
                null_pct=95.0,
                anomalies=(mixed, dup),
                pii=(hi_pii,),
            )
        )
    result = compute_score(_mk_report(*cols))
    assert result.score >= 0
    assert result.score <= 100
    assert result.grade == "F"


def test_score_penalties_are_sorted_by_points_desc() -> None:
    mixed = Anomaly(kind="mixed_types", severity="warn", detail="")
    report = _mk_report(
        _mk_col("a", null_pct=80.0),
        _mk_col("b", anomalies=(mixed,)),
    )
    result = compute_score(report)
    points = [p.points for p in result.penalties]
    assert points == sorted(points, reverse=True)


# ---------------------------------------------------------------------------
# ScoreResult / dict / colors
# ---------------------------------------------------------------------------


def test_score_result_color_matches_grade() -> None:
    a = ScoreResult(score=95, grade="A")
    f = ScoreResult(score=10, grade="F")
    assert a.color != f.color
    assert a.color.startswith("#")
    assert f.color.startswith("#")


def test_score_to_dict_shape_is_stable() -> None:
    result = ScoreResult(
        score=72,
        grade="C",
        penalties=(PenaltyDetail(kind="null_density", points=18, detail="x"),),
    )
    d = score_to_dict(result)
    assert d == {
        "score": 72,
        "grade": "C",
        "color": result.color,
        "penalties": [{"kind": "null_density", "points": 18, "detail": "x"}],
    }


# ---------------------------------------------------------------------------
# SVG badge
# ---------------------------------------------------------------------------


def test_render_badge_svg_is_valid_svg() -> None:
    result = ScoreResult(score=72, grade="C")
    svg = render_badge_svg(result)
    assert svg.startswith("<svg ")
    assert svg.endswith("</svg>")
    assert "72 · C" in svg
    assert result.color in svg
    assert 'role="img"' in svg
    assert 'aria-label="Data Quality: 72 · C"' in svg


def test_render_badge_svg_custom_label_and_value() -> None:
    result = ScoreResult(score=100, grade="A")
    svg = render_badge_svg(result, label="Quality", value="100")
    assert "Quality" in svg
    assert ">100<" in svg
    # Grade A color must be present somewhere for the two-tone right side.
    assert result.color in svg


def test_render_badge_svg_escapes_label() -> None:
    result = ScoreResult(score=50, grade="F")
    svg = render_badge_svg(result, label="<hax>")
    assert "&lt;hax&gt;" in svg
    assert "<hax>" not in svg


# ---------------------------------------------------------------------------
# End-to-end profile → score (integration)
# ---------------------------------------------------------------------------


def _write_messy_csv(tmp_path: Path) -> Path:
    p = tmp_path / "messy.csv"
    p.write_text(
        "user_id,email,age,note\n"
        "1,alice@example.com,32,ok\n"
        "2,bob@example.com,,seven\n"
        "3,alice@example.com,,42\n"
        "4,carol@example.com,999,ok\n"
        "5,dan@example.com,,\n"
        "6,eve@example.com,,\n"
    )
    return p


def test_profile_then_score_end_to_end(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    rel = load(p)
    report = profile(rel, path=p)
    result = compute_score(report)
    # Deterministic penalties we expect from this fixture:
    kinds = {pen.kind for pen in result.penalties}
    assert "null_density" in kinds
    assert "pii_exposure" in kinds
    # It's messy, so the grade should not be an A.
    assert result.grade in {"B", "C", "D", "F"}
    assert 0 <= result.score < 100


def test_json_includes_score_block_only_when_requested(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    rel = load(p)
    report = profile(rel, path=p)

    plain = json.loads(dumps_json(report))
    assert "score" not in plain

    scored = json.loads(dumps_json(report, score=compute_score(report)))
    assert "score" in scored
    assert set(scored["score"].keys()) == {"score", "grade", "color", "penalties"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_score_prints_quality_panel(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--score"])
    assert result.exit_code == 0, result.output
    assert "Quality Score" in result.output
    assert "Grade" in result.output


def test_cli_score_only_prints_bare_integer(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--score-only"])
    assert result.exit_code == 0, result.output
    stdout = result.output.strip()
    assert re.fullmatch(r"\d{1,3}", stdout), f"expected bare int, got {stdout!r}"
    assert 0 <= int(stdout) <= 100
    # No pretty panel / persona commentary leaked in.
    assert "Grade" not in result.output


def test_cli_min_score_exit_code(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    fail = runner.invoke(seance_cli, ["summon", str(p), "--min-score", "95", "--quiet"])
    assert fail.exit_code == 6, fail.output
    passing = runner.invoke(seance_cli, ["summon", str(p), "--min-score", "20", "--quiet"])
    assert passing.exit_code == 0, passing.output


def test_cli_min_score_gate_via_score_only(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--score-only", "--min-score", "95"])
    # Bare int still printed even when threshold fails.
    assert result.exit_code == 6
    assert re.fullmatch(r"\d{1,3}", result.output.strip())


def test_cli_badge_writes_svg_file(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    badge = tmp_path / "quality.svg"
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--badge", str(badge), "--quiet"])
    assert result.exit_code == 0, result.output
    assert badge.exists()
    body = badge.read_text(encoding="utf-8")
    assert body.startswith("<svg ")
    assert "Data Quality" in body


def test_cli_json_includes_score_when_flag_set(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()

    result = runner.invoke(seance_cli, ["summon", str(p), "--json", "--score"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "score" in payload
    assert isinstance(payload["score"]["score"], int)
    assert payload["score"]["grade"] in {"A", "B", "C", "D", "F"}

    # Without --score, the JSON payload stays backwards-compatible.
    result2 = runner.invoke(seance_cli, ["summon", str(p), "--json"])
    payload2 = json.loads(result2.output)
    assert "score" not in payload2


def test_cli_score_only_rejects_json(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--score-only", "--json"])
    assert result.exit_code != 0
    assert "score-only" in result.output.lower()


def test_cli_score_rejects_expectations(tmp_path: Path) -> None:
    p = _write_messy_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--score", "--expectations", "gx"])
    assert result.exit_code != 0
    assert "expectations" in result.output.lower()
