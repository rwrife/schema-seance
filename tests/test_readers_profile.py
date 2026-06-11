"""Tests for readers + profile (M2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from schema_seance.profile import ProfileReport, profile
from schema_seance.readers import UnsupportedFormatError, load

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_csv_returns_relation_with_expected_columns() -> None:
    rel = load(FIXTURES / "tiny.csv")
    assert list(rel.columns) == ["id", "name", "email", "score"]
    assert rel.count("*").fetchone()[0] == 3


def test_load_jsonl_returns_relation_with_expected_columns() -> None:
    rel = load(FIXTURES / "tiny.jsonl")
    assert set(rel.columns) == {"id", "name", "score"}
    assert rel.count("*").fetchone()[0] == 3


def test_load_unknown_extension_raises() -> None:
    bogus = FIXTURES / "tiny.weird"
    bogus.write_text("nope")
    try:
        with pytest.raises(UnsupportedFormatError):
            load(bogus)
    finally:
        bogus.unlink(missing_ok=True)


def test_load_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load(FIXTURES / "does-not-exist.csv")


def test_profile_csv_shape_and_null_pct() -> None:
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv")
    assert isinstance(report, ProfileReport)
    assert report.rows == 3
    assert report.cols == 4
    assert report.size_bytes is not None and report.size_bytes > 0
    assert report.encoding == "utf-8"

    by_name = {c.name: c for c in report.columns}
    assert by_name["id"].null_pct == 0.0
    # one of three emails is null -> ~33.33%
    assert by_name["email"].null_pct == pytest.approx(33.33, abs=0.01)
    assert by_name["score"].null_pct == pytest.approx(33.33, abs=0.01)
    # dtype strings include something type-y
    assert by_name["id"].dtype  # non-empty
    # samples are populated (non-null pick)
    assert by_name["name"].sample in {"Alice", "Bob", "Carol"}


def test_profile_jsonl_shape() -> None:
    rel = load(FIXTURES / "tiny.jsonl")
    report = profile(rel, path=FIXTURES / "tiny.jsonl")
    assert report.rows == 3
    assert report.cols == 3
    names = {c.name for c in report.columns}
    assert names == {"id", "name", "score"}
