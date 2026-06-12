"""Tests for full profiling, sample, JSON output (M3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from schema_seance.profile import PROFILE_SCHEMA_VERSION, ProfileReport, profile
from schema_seance.readers import SQLiteTableError, UnsupportedFormatError, load
from schema_seance.render.json import dumps as dumps_json
from schema_seance.render.json import report_to_dict

FIXTURES = Path(__file__).parent / "fixtures"


def _make_parquet(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.parquet"
    con = duckdb.connect()
    src = str(FIXTURES / "tiny.csv").replace("'", "''")
    out = str(p).replace("'", "''")
    con.execute(f"COPY (SELECT * FROM read_csv_auto('{src}')) TO '{out}' (FORMAT 'parquet')")
    con.close()
    return p


def _make_sqlite(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.sqlite"
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE ghosts (id INTEGER, name TEXT, score REAL);
        INSERT INTO ghosts VALUES (1, 'Alice', 9.5), (2, 'Bob', NULL), (3, 'Carol', 8.0);
        CREATE TABLE seances (id INTEGER, kind TEXT);
        INSERT INTO seances VALUES (1, 'parlor'), (2, 'graveyard');
        """
    )
    con.commit()
    con.close()
    return p


def test_parquet_reader_roundtrip(tmp_path: Path) -> None:
    p = _make_parquet(tmp_path)
    rel = load(p)
    assert set(rel.columns) == {"id", "name", "email", "score"}
    assert rel.count("*").fetchone()[0] == 3


def test_sqlite_reader_defaults_to_first_table(tmp_path: Path) -> None:
    p = _make_sqlite(tmp_path)
    rel = load(p)
    assert set(rel.columns) == {"id", "name", "score"}
    assert rel.count("*").fetchone()[0] == 3


def test_sqlite_reader_with_explicit_table(tmp_path: Path) -> None:
    p = _make_sqlite(tmp_path)
    rel = load(p, table="seances")
    assert set(rel.columns) == {"id", "kind"}
    assert rel.count("*").fetchone()[0] == 2


def test_sqlite_reader_unknown_table_raises(tmp_path: Path) -> None:
    p = _make_sqlite(tmp_path)
    with pytest.raises(SQLiteTableError):
        load(p, table="not_there")


def test_profile_full_column_stats() -> None:
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv")
    by_name = {c.name: c for c in report.columns}

    score = by_name["score"]
    assert score.distinct == 2  # 9.5, 7.25, NULL
    assert score.min == 7.25
    assert score.max == 9.5
    assert score.mean == pytest.approx((9.5 + 7.25) / 2)
    assert score.stddev is not None

    name_col = by_name["name"]
    assert name_col.distinct == 3
    assert name_col.min == "Alice"
    assert name_col.max == "Carol"
    assert name_col.mean is None
    assert name_col.stddev is None

    # top values present and ordered by count desc
    assert name_col.top
    counts = [t.count for t in name_col.top]
    assert counts == sorted(counts, reverse=True)


def test_profile_sample_limits_rows() -> None:
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv", sample=2)
    assert report.sampled is True
    assert report.sample_size == 2
    assert report.rows == 2


def test_json_render_has_stable_shape() -> None:
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv")
    payload = json.loads(dumps_json(report))
    assert payload["schema_version"] == PROFILE_SCHEMA_VERSION
    assert payload["rows"] == 3
    assert payload["cols"] == 4
    assert payload["sampled"] is False
    assert payload["sample_size"] is None
    assert isinstance(payload["columns"], list)
    expected_col_keys = {
        "name",
        "dtype",
        "null_pct",
        "distinct",
        "sample",
        "min",
        "max",
        "mean",
        "stddev",
        "top",
    }
    for col in payload["columns"]:
        assert set(col.keys()) == expected_col_keys
        for t in col["top"]:
            assert set(t.keys()) == {"value", "count"}
    # file block stable
    assert set(payload["file"].keys()) == {"path", "size_bytes", "encoding"}


def test_json_snapshot_csv() -> None:
    """Stable snapshot of the JSON output for tiny.csv (modulo absolute path)."""
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv")
    payload = report_to_dict(report)
    payload["file"]["path"] = "tiny.csv"
    payload["file"]["size_bytes"] = 999  # vary across OSes / line endings
    # Sort each column's top list by (value-str, count) for stability.
    for col in payload["columns"]:
        col["top"] = sorted(col["top"], key=lambda t: (str(t["value"]), -t["count"]))
    expected = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "file": {"path": "tiny.csv", "size_bytes": 999, "encoding": "utf-8"},
        "rows": 3,
        "cols": 4,
        "sampled": False,
        "sample_size": None,
        "columns": [
            {
                "name": "id",
                "dtype": "BIGINT",
                "null_pct": 0.0,
                "distinct": 3,
                "sample": payload["columns"][0]["sample"],  # any_value isn't deterministic
                "min": 1,
                "max": 3,
                "mean": 2.0,
                "stddev": payload["columns"][0]["stddev"],
                "top": sorted(
                    [{"value": 1, "count": 1}, {"value": 2, "count": 1}, {"value": 3, "count": 1}],
                    key=lambda t: (str(t["value"]), -t["count"]),
                ),
            },
            {
                "name": "name",
                "dtype": "VARCHAR",
                "null_pct": 0.0,
                "distinct": 3,
                "sample": payload["columns"][1]["sample"],
                "min": "Alice",
                "max": "Carol",
                "mean": None,
                "stddev": None,
                "top": sorted(
                    [
                        {"value": "Alice", "count": 1},
                        {"value": "Bob", "count": 1},
                        {"value": "Carol", "count": 1},
                    ],
                    key=lambda t: (str(t["value"]), -t["count"]),
                ),
            },
            {
                "name": "email",
                "dtype": "VARCHAR",
                "null_pct": 33.33,
                "distinct": 2,
                "sample": payload["columns"][2]["sample"],
                "min": "alice@example.com",
                "max": "carol@example.com",
                "mean": None,
                "stddev": None,
                "top": sorted(
                    [
                        {"value": "alice@example.com", "count": 1},
                        {"value": "carol@example.com", "count": 1},
                    ],
                    key=lambda t: (str(t["value"]), -t["count"]),
                ),
            },
            {
                "name": "score",
                "dtype": "DOUBLE",
                "null_pct": 33.33,
                "distinct": 2,
                "sample": payload["columns"][3]["sample"],
                "min": 7.25,
                "max": 9.5,
                "mean": 8.375,
                "stddev": payload["columns"][3]["stddev"],
                "top": sorted(
                    [{"value": 7.25, "count": 1}, {"value": 9.5, "count": 1}],
                    key=lambda t: (str(t["value"]), -t["count"]),
                ),
            },
        ],
    }
    assert payload == expected


def test_profile_report_is_dataclass() -> None:
    rel = load(FIXTURES / "tiny.csv")
    report = profile(rel, path=FIXTURES / "tiny.csv")
    assert isinstance(report, ProfileReport)


def test_unsupported_extension_still_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "tiny.xyz"
    bogus.write_text("nope")
    with pytest.raises(UnsupportedFormatError):
        load(bogus)
