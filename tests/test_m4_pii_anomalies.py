"""Tests for M4: PII detectors + anomalies + --fail-on-pii."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.anomalies import detect_column as detect_anomalies
from schema_seance.cli import main as seance_cli
from schema_seance.pii import (
    CONFIDENCE_BANDS,
    confidence_band,
)
from schema_seance.pii import (
    detect_column as detect_pii,
)
from schema_seance.profile import profile
from schema_seance.readers import load
from schema_seance.render.json import dumps as dumps_json

# ---------------------------------------------------------------------------
# Pure detector tests
# ---------------------------------------------------------------------------


def test_email_detected_high() -> None:
    values = [
        "alice@example.com",
        "bob.smith+x@test.co.uk",
        "carol_99@sub.example.org",
        "dan@example.io",
    ]
    findings = detect_pii("contact_email", "VARCHAR", values)
    by_kind = {f.kind: f for f in findings}
    assert "email" in by_kind
    assert confidence_band(by_kind["email"].confidence) == "high"


def test_email_negative() -> None:
    values = ["not an email", "definitely not", "hello world", "12345"]
    findings = detect_pii("note", "VARCHAR", values)
    assert "email" not in {f.kind for f in findings}


def test_phone_detected() -> None:
    values = [
        "+1 415-555-2671",
        "(415) 555-9981",
        "+44 20 7946 0991",
        "415.555.0143",
    ]
    findings = detect_pii("mobile", "VARCHAR", values)
    kinds = {f.kind for f in findings}
    assert "phone" in kinds


def test_phone_negative_ignores_random_digits() -> None:
    values = ["abc", "x", "name", "label"]
    findings = detect_pii("label", "VARCHAR", values)
    assert "phone" not in {f.kind for f in findings}


def test_credit_card_luhn_positive_and_negative() -> None:
    # Mix of valid Luhn (Visa/MC test numbers) + an invalid one.
    valid = ["4111111111111111", "5500-0000-0000-0004", "340000000000009"]
    invalid_only = ["4111111111111112", "1234567890123456"]
    pos = detect_pii("cc", "VARCHAR", valid)
    neg = detect_pii("cc", "VARCHAR", invalid_only)
    assert "credit_card" in {f.kind for f in pos}
    assert "credit_card" not in {f.kind for f in neg}


def test_ssn_strict_and_loose() -> None:
    strict = ["123-45-6789", "987-65-4320", "111-22-3333"]
    loose = ["123456789", "987654320", "111223333"]
    fs = detect_pii("ssn", "VARCHAR", strict)
    fl = detect_pii("ssn", "VARCHAR", loose)
    assert "ssn" in {f.kind for f in fs}
    assert "ssn" in {f.kind for f in fl}  # name hint promotes loose match
    # Same digits in a column not named ssn-like should NOT loose-match.
    fl_unhinted = detect_pii("random_id", "VARCHAR", loose)
    assert "ssn" not in {f.kind for f in fl_unhinted}


def test_ipv4_and_ipv6() -> None:
    v4 = ["10.0.0.1", "192.168.1.1", "8.8.8.8", "127.0.0.1"]
    v6 = ["::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334", "fe80::1"]
    f4 = detect_pii("client_ip", "VARCHAR", v4)
    f6 = detect_pii("client_ipv6", "VARCHAR", v6)
    assert "ipv4" in {f.kind for f in f4}
    assert "ipv6" in {f.kind for f in f6}


def test_name_column_by_header_hint() -> None:
    values = ["Alice", "Bob", "Carol", "Diane"]
    findings = detect_pii("first_name", "VARCHAR", values)
    assert "name" in {f.kind for f in findings}


def test_dob_column_by_header_and_dtype() -> None:
    values = ["1990-01-02", "1985-11-30", "2001-06-15"]
    findings = detect_pii("dob", "DATE", values)
    assert "dob" in {f.kind for f in findings}


def test_confidence_in_unit_interval() -> None:
    values = ["alice@example.com"] * 10
    findings = detect_pii("email", "VARCHAR", values)
    for f in findings:
        assert 0.0 <= f.confidence <= 1.0


# ---------------------------------------------------------------------------
# Anomaly tests
# ---------------------------------------------------------------------------


def test_high_nulls_detected() -> None:
    anomalies = detect_anomalies(
        name="x", dtype="VARCHAR", rows=100, distinct=10, null_pct=75.0, values=["a"]
    )
    kinds = {a.kind for a in anomalies}
    assert "high_nulls" in kinds


def test_mixed_types_in_varchar() -> None:
    values = ["1", "2", "3", "4", "hello", "world", "5"]
    anomalies = detect_anomalies(
        name="amount", dtype="VARCHAR", rows=7, distinct=7, null_pct=0.0, values=values
    )
    assert "mixed_types" in {a.kind for a in anomalies}


def test_pk_duplicates_detected() -> None:
    anomalies = detect_anomalies(
        name="user_id",
        dtype="BIGINT",
        rows=10,
        distinct=8,
        null_pct=0.0,
        values=[1, 2, 3, 4, 5, 6, 7, 8, 1, 2],
    )
    assert "pk_duplicates" in {a.kind for a in anomalies}


def test_numeric_outliers_via_iqr() -> None:
    nums = [10.0, 11.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 10000.0]
    anomalies = detect_anomalies(
        name="latency_ms",
        dtype="DOUBLE",
        rows=len(nums),
        distinct=len(nums),
        null_pct=0.0,
        values=nums,
        numeric_values=nums,
    )
    assert "numeric_outliers" in {a.kind for a in anomalies}


# ---------------------------------------------------------------------------
# End-to-end: profile + CLI exit-code contract
# ---------------------------------------------------------------------------


def _make_pii_csv(tmp_path: Path) -> Path:
    p = tmp_path / "people.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["user_id", "first_name", "email", "ip", "card"])
        rows = [
            (1, "Alice", "alice@example.com", "10.0.0.1", "4111111111111111"),
            (2, "Bob", "bob@example.com", "10.0.0.2", "5500000000000004"),
            (3, "Carol", "carol@example.com", "10.0.0.3", "340000000000009"),
            (4, "Diane", "diane@example.com", "10.0.0.4", "4111111111111111"),
        ]
        for r in rows:
            w.writerow(r)
    return p


def test_profile_surfaces_pii_findings(tmp_path: Path) -> None:
    p = _make_pii_csv(tmp_path)
    rel = load(p)
    report = profile(rel, path=p)
    by_name = {c.name: c for c in report.columns}
    email_kinds = {f.kind for f in by_name["email"].pii}
    assert "email" in email_kinds
    card_kinds = {f.kind for f in by_name["card"].pii}
    assert "credit_card" in card_kinds
    ip_kinds = {f.kind for f in by_name["ip"].pii}
    assert "ipv4" in ip_kinds
    name_kinds = {f.kind for f in by_name["first_name"].pii}
    assert "name" in name_kinds


def test_json_includes_pii_and_anomalies(tmp_path: Path) -> None:
    p = _make_pii_csv(tmp_path)
    rel = load(p)
    report = profile(rel, path=p)
    payload = json.loads(dumps_json(report))
    for col in payload["columns"]:
        assert "pii" in col
        assert "anomalies" in col
        for finding in col["pii"]:
            assert set(finding.keys()) == {
                "kind",
                "confidence",
                "match_ratio",
                "matched",
                "sampled",
            }


def test_cli_fail_on_pii_high_exits_nonzero(tmp_path: Path) -> None:
    p = _make_pii_csv(tmp_path)
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--fail-on-pii", "high"])
    assert result.exit_code == 3, result.output


def test_cli_fail_on_pii_high_passes_without_pii(tmp_path: Path) -> None:
    # Wholesome CSV with no PII signal.
    p = tmp_path / "boring.csv"
    p.write_text("id,score\n1,9.5\n2,7.25\n3,8.0\n")
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["summon", str(p), "--fail-on-pii", "high"])
    assert result.exit_code == 0, result.output


def test_cli_fail_on_pii_threshold_band_matches_constants() -> None:
    # Sanity: bands are sorted low < medium < high.
    assert CONFIDENCE_BANDS["low"] < CONFIDENCE_BANDS["medium"] < CONFIDENCE_BANDS["high"]


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.95, "high"),
        (0.70, "medium"),
        (0.40, "low"),
        (0.10, "none"),
    ],
)
def test_confidence_band_thresholds(value: float, expected: str) -> None:
    assert confidence_band(value) == expected
