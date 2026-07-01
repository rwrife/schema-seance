"""Tests for time-series detection (issue #34)."""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

import duckdb
import pytest

from schema_seance.profile import profile
from schema_seance.render.json import report_to_dict
from schema_seance.timeseries import detect_column


def _csv(tmp_path: Path, rows: list[dict[str, str]], name: str = "data.csv") -> Path:
    p = tmp_path / name
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


def _load(path: Path) -> duckdb.DuckDBPyRelation:
    src = str(path).replace("'", "''")
    return duckdb.sql(f"SELECT * FROM read_csv_auto('{src}')")


def test_detect_iso_date_strings_basic(tmp_path: Path) -> None:
    rows = [
        {"created_at": (_dt.date(2024, 1, 1) + _dt.timedelta(days=i)).isoformat(), "x": str(i)}
        for i in range(30)
    ]
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p)
    assert len(report.time_series) == 1
    ts = report.time_series[0]
    assert ts.column == "created_at"
    assert ts.cadence_label == "1d"
    assert ts.conformance_pct == 100.0
    assert ts.missing_buckets == 0


def test_detect_native_timestamp_dtype() -> None:
    rel = duckdb.sql(
        """
        SELECT TIMESTAMP '2024-01-01 00:00:00' + INTERVAL (i) HOUR AS ts
        FROM range(0, 48) t(i)
        """
    )
    report = profile(rel)
    assert len(report.time_series) == 1
    ts = report.time_series[0]
    assert ts.detected_from == "dtype"
    assert ts.cadence_label == "1h"
    assert ts.missing_buckets == 0


def test_ragged_cadence_has_gaps(tmp_path: Path) -> None:
    base = _dt.date(2024, 1, 1)
    days = [0, 1, 2, 3, 4, 10, 11, 12, 20, 21, 22, 23]  # two large gaps
    rows = [{"logged_on": (base + _dt.timedelta(days=d)).isoformat()} for d in days]
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p)
    assert report.time_series
    ts = report.time_series[0]
    assert ts.missing_buckets and ts.missing_buckets > 0
    assert ts.gaps  # at least one gap
    # Largest gap should be > 4 days
    assert ts.gaps[0].duration_seconds > 4 * 86400


def test_no_date_column_yields_no_timeseries(tmp_path: Path) -> None:
    rows = [{"name": f"x{i}", "n": str(i)} for i in range(20)]
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p)
    assert report.time_series == []


def test_seasonality_dow_skew_detected(tmp_path: Path) -> None:
    # 30 days, but include 4 EXTRA entries on every Monday — pushes Mon
    # share well above 2× expected (1/7 ≈ 14%).
    base = _dt.date(2024, 1, 1)  # Mon
    rows: list[dict[str, str]] = []
    for d in range(30):
        day = base + _dt.timedelta(days=d)
        rows.append({"event_at": day.isoformat()})
        if day.weekday() == 0:  # Monday
            for _ in range(6):
                rows.append({"event_at": day.isoformat()})
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p)
    assert report.time_series
    ts = report.time_series[0]
    labels = {s.label for s in ts.seasonality if s.bucket == "day_of_week"}
    assert "Mon" in labels


def test_json_includes_timeseries_section(tmp_path: Path) -> None:
    rows = [{"timestamp": f"2024-01-{i + 1:02d}", "v": str(i)} for i in range(10)]
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p)
    d = report_to_dict(report)
    assert "time_series" in d
    assert d["time_series"]
    entry = d["time_series"][0]
    assert entry["column"] == "timestamp"
    assert "cadence_seconds" in entry
    assert "gaps" in entry
    assert "seasonality" in entry


def test_disable_timeseries_via_kwarg(tmp_path: Path) -> None:
    rows = [{"created_at": f"2024-01-{i + 1:02d}"} for i in range(10)]
    p = _csv(tmp_path, rows)
    report = profile(_load(p), path=p, timeseries=False)
    assert report.time_series == []


def test_string_column_without_date_name_ignored() -> None:
    # Free-text column containing ISO dates but no date-y name — skip.
    values = [f"note 2024-01-{i + 1:02d} happened" for i in range(20)]
    result = detect_column(name="notes", dtype="VARCHAR", values=values)
    # Won't pass the date-shape prefilter anyway, but the gate also requires
    # a date-shaped column name for parsed strings.
    assert result is None


def test_cli_no_timeseries_flag_skips_section(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from schema_seance.cli import main

    rows = [{"created_at": f"2024-01-{i + 1:02d}", "v": str(i)} for i in range(10)]
    p = _csv(tmp_path, rows)
    runner = CliRunner()
    res_with = runner.invoke(main, ["summon", str(p), "--json"])
    assert res_with.exit_code == 0, res_with.output
    assert '"time_series"' in res_with.output
    assert 'time_series": []' not in res_with.output.replace(" ", "")

    res_without = runner.invoke(main, ["summon", str(p), "--json", "--no-timeseries"])
    assert res_without.exit_code == 0, res_without.output
    # Section still present (stable schema) but empty.
    assert '"time_series": []' in res_without.output


@pytest.mark.parametrize(
    "fmt,value",
    [
        ("iso", "2024-06-15T13:45:00"),
        ("iso_z", "2024-06-15T13:45:00Z"),
        ("rfc3339_offset", "2024-06-15T13:45:00+02:00"),
        ("space", "2024-06-15 13:45:00"),
        ("date_only", "2024-06-15"),
    ],
)
def test_common_formats_parsed(fmt: str, value: str) -> None:
    from schema_seance.timeseries import _parse_one

    assert _parse_one(value) is not None, fmt
