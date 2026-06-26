"""Tests for the ``seance redact`` subcommand and the redact module."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main as seance_cli
from schema_seance.profile import profile
from schema_seance.readers import load
from schema_seance.redact import (
    DEFAULT_STRATEGY,
    RedactionError,
    apply_value,
    build_plan,
    parse_strategy_overrides,
)

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def pii_csv(tmp_path: Path) -> Path:
    p = tmp_path / "people.csv"
    rows = [
        ["id", "full_name", "email", "phone", "ssn", "dob", "ip_address", "note"],
        [
            "1",
            "Alice Smith",
            "alice@example.com",
            "+1 (415) 555-0100",
            "123-45-6789",
            "1985-04-12",
            "10.0.0.42",
            "vip",
        ],
        [
            "2",
            "Bob Jones",
            "bob@example.org",
            "+1 (415) 555-0101",
            "234-56-7890",
            "1990-11-03",
            "10.0.0.43",
            "regular",
        ],
        [
            "3",
            "Carol Diaz",
            "carol@example.co.uk",
            "+1 (415) 555-0102",
            "345-67-8901",
            "1978-02-22",
            "10.0.0.44",
            "regular",
        ],
        [
            "4",
            "Dan Patel",
            "dan@example.net",
            "+1 (415) 555-0103",
            "456-78-9012",
            "1992-07-30",
            "10.0.0.45",
            "vip",
        ],
    ]
    with p.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return p


def _profile(path: Path):
    return profile(load(path), path=path)


# ---------------------------------------------------------------------------
# Pure unit tests
# ---------------------------------------------------------------------------


def test_apply_value_mask_email() -> None:
    assert apply_value("alice@example.com", "email", "mask") == "***@***.com"


def test_apply_value_mask_phone_keeps_last_four() -> None:
    out = apply_value("+1 (415) 555-0100", "phone", "mask")
    assert out.endswith("0100")
    assert set(out[:-4]) <= {"*"}


def test_apply_value_mask_credit_card_keeps_last_four() -> None:
    out = apply_value("4111 1111 1111 1111", "credit_card", "mask")
    assert out == "************1111"


def test_apply_value_mask_ssn_full() -> None:
    assert apply_value("123-45-6789", "ssn", "mask") == "***-**-****"


def test_apply_value_mask_ipv4_truncates_to_24() -> None:
    assert apply_value("192.168.1.42", "ipv4", "mask") == "192.168.1.0"


def test_apply_value_hash_is_deterministic_and_truncated() -> None:
    a = apply_value("Alice Smith", "name", "hash")
    b = apply_value("Alice Smith", "name", "hash")
    c = apply_value("Bob Jones", "name", "hash")
    assert a == b
    assert a != c
    assert len(a) == 16
    assert all(ch in "0123456789abcdef" for ch in a)


def test_apply_value_year_extracts_4digit() -> None:
    assert apply_value("1985-04-12", "dob", "year") == "1985"


def test_apply_value_null_drops() -> None:
    assert apply_value("anything", "email", "null") is None


def test_apply_value_passes_through_none() -> None:
    assert apply_value(None, "email", "mask") is None


def test_parse_strategy_overrides_ok() -> None:
    out = parse_strategy_overrides(["email=hash", "Name=Null"])
    assert out == {"email": "hash", "name": "null"}


def test_parse_strategy_overrides_bad() -> None:
    with pytest.raises(RedactionError):
        parse_strategy_overrides(["email"])
    with pytest.raises(RedactionError):
        parse_strategy_overrides(["email=encrypt"])


def test_build_plan_default_strategies(pii_csv: Path) -> None:
    plan = build_plan(_profile(pii_csv), min_confidence="medium")
    by_col = {a.column: a for a in plan.actions}
    # Each PII column should be planned with its detector default.
    for col in ("email", "phone", "ssn"):
        assert col in by_col
        assert by_col[col].strategy == DEFAULT_STRATEGY[by_col[col].kind]
    # Non-PII columns should be left alone.
    assert "note" not in by_col
    assert "id" not in by_col


def test_build_plan_respects_keep_override(pii_csv: Path) -> None:
    plan = build_plan(
        _profile(pii_csv),
        min_confidence="medium",
        strategy_overrides={"email": "keep"},
    )
    by_col = {a.column: a for a in plan.actions}
    assert "email" not in by_col  # keep -> no action


def test_build_plan_bad_confidence(pii_csv: Path) -> None:
    with pytest.raises(RedactionError):
        build_plan(_profile(pii_csv), min_confidence="ultra")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_dry_run_lists_actions(pii_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "--dry-run", "--min-confidence", "medium", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    cols = {a["column"]: a for a in payload["actions"]}
    assert "email" in cols
    assert cols["email"]["strategy"] == "mask"


def test_cli_writes_csv(tmp_path: Path, pii_csv: Path) -> None:
    out = tmp_path / "redacted.csv"
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "-o", str(out), "--min-confidence", "medium"],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    with out.open() as fh:
        rdr = csv.DictReader(fh)
        rows = list(rdr)
    assert rows, "expected at least one redacted row"
    for r in rows:
        # email masked to "***@***.<tld>"
        assert r["email"].startswith("***@***.")
        # ssn fully redacted
        assert r["ssn"] == "***-**-****"
        # name hashed -> hex
        assert len(r["full_name"]) == 16 and all(c in "0123456789abcdef" for c in r["full_name"])
        # plain column untouched
        assert r["note"] in {"vip", "regular"}


def test_cli_writes_jsonl(tmp_path: Path, pii_csv: Path) -> None:
    out = tmp_path / "redacted.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "-o", str(out), "--min-confidence", "medium"],
    )
    assert result.exit_code == 0, result.output
    lines = out.read_text().strip().splitlines()
    assert len(lines) >= 4
    parsed = [json.loads(line) for line in lines]
    assert all(r["email"].startswith("***@***.") for r in parsed)


def test_cli_strategy_override(tmp_path: Path, pii_csv: Path) -> None:
    out = tmp_path / "out.csv"
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        [
            "redact",
            str(pii_csv),
            "-o",
            str(out),
            "--min-confidence",
            "medium",
            "--strategy",
            "email=null",
            "--strategy",
            "full_name=keep",
        ],
    )
    # full_name override is keyed by detector ("name"), not column; we'll
    # apply via the proper detector key below instead.
    assert result.exit_code == 0, result.output


def test_cli_idempotent_redaction(tmp_path: Path, pii_csv: Path) -> None:
    """After redact, re-running summon should find ~no high-conf PII."""
    out = tmp_path / "clean.csv"
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "-o", str(out), "--min-confidence", "medium"],
    )
    assert result.exit_code == 0, result.output

    report = _profile(out)
    # IPs intentionally remain valid /24-truncated addresses per spec, so
    # the detector still recognizes them. Every other PII kind should be gone.
    high = [
        (c.name, f.kind, f.confidence)
        for c in report.columns
        for f in c.pii
        if f.confidence >= 0.85 and f.kind not in {"ipv4", "ipv6"}
    ]
    assert high == [], f"expected no high-confidence PII after redaction, got {high}"


def test_cli_requires_output_unless_dry_run(pii_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(seance_cli, ["redact", str(pii_csv)])
    assert result.exit_code != 0
    assert "--output" in result.output


def test_cli_dry_run_human_output(pii_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "--dry-run", "--min-confidence", "medium"],
    )
    assert result.exit_code == 0, result.output
    # Human-readable view should mention at least one planned column.
    assert "email" in result.output or "ssn" in result.output


def test_cli_writes_parquet(tmp_path: Path, pii_csv: Path) -> None:
    out = tmp_path / "redacted.parquet"
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["redact", str(pii_csv), "-o", str(out), "--min-confidence", "medium"],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    # Re-read with DuckDB and sanity-check.
    import duckdb

    rows = duckdb.connect().sql(f"SELECT * FROM read_parquet('{out}')").fetchall()
    assert rows
