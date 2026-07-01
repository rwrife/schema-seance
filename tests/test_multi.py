"""Tests for multi-file summon (issue #35)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.multi import (
    DEFAULT_MAX_FILES,
    MultiInputError,
    expand_inputs,
    profile_many,
)


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_expand_inputs_directory(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id,name", ["1,A", "2,B"])
    _write_csv(tmp_path / "b.csv", "id,name", ["3,C"])
    (tmp_path / "README.md").write_text("ignored")
    (tmp_path / "sub").mkdir()
    _write_csv(tmp_path / "sub" / "c.csv", "id,name", ["4,D"])

    result = expand_inputs(tmp_path)
    names = sorted(Path(p).name for p in result)
    assert names == ["a.csv", "b.csv", "c.csv"]


def test_expand_inputs_glob(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id", ["1"])
    _write_csv(tmp_path / "b.csv", "id", ["2"])
    (tmp_path / "c.txt").write_text("nope")
    result = expand_inputs(str(tmp_path / "*.csv"))
    assert len(result) == 2


def test_expand_inputs_include_exclude(tmp_path: Path) -> None:
    _write_csv(tmp_path / "keep-a.csv", "id", ["1"])
    _write_csv(tmp_path / "keep-b.csv", "id", ["2"])
    _write_csv(tmp_path / "skip.csv", "id", ["3"])
    result = expand_inputs(tmp_path, include=("keep-*.csv",), exclude=("*-b.csv",))
    assert [Path(p).name for p in result] == ["keep-a.csv"]


def test_expand_inputs_max_files(tmp_path: Path) -> None:
    for i in range(5):
        _write_csv(tmp_path / f"f{i}.csv", "id", ["1"])
    with pytest.raises(MultiInputError):
        expand_inputs(tmp_path, max_files=2)


def test_expand_inputs_no_match(tmp_path: Path) -> None:
    with pytest.raises(MultiInputError):
        expand_inputs(str(tmp_path / "*.csv"))


def test_profile_many_rolls_up_rows_and_clusters(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id,name", ["1,A", "2,B"])
    _write_csv(tmp_path / "b.csv", "id,name", ["3,C", "4,D", "5,E"])
    # Different schema — should form its own cluster and drift.
    _write_csv(tmp_path / "c.csv", "id,name,extra", ["6,F,x"])

    inputs = expand_inputs(tmp_path)
    report = profile_many(inputs, timeseries=False)
    assert report.loaded_count == 3
    assert report.failed_count == 0
    assert report.total_rows == 6
    assert len(report.clusters) == 2
    # The two matching-schema files should cluster together.
    biggest = report.clusters[0]
    assert biggest.size == 2
    assert "extra" in report.drifted_columns


def test_profile_many_isolates_failures(tmp_path: Path) -> None:
    _write_csv(tmp_path / "ok.csv", "id", ["1"])
    bad = tmp_path / "broken.csv"
    bad.write_text('"unterminated,quote\n1,2\n')

    inputs = [str(tmp_path / "ok.csv"), str(bad)]
    report = profile_many(inputs, timeseries=False)
    # ok.csv should load; broken.csv may or may not (DuckDB is lenient),
    # but the run must not abort either way.
    assert report.loaded_count >= 1
    assert report.loaded_count + report.failed_count == 2


def test_cli_summon_directory_renders_congregation(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id,name", ["1,A"])
    _write_csv(tmp_path / "b.csv", "id,name", ["2,B"])
    result = CliRunner().invoke(main, ["summon", str(tmp_path), "--no-timeseries"])
    assert result.exit_code == 0, result.output
    assert "The Congregation" in result.output
    assert "The Roll Call" in result.output
    assert "Total rows" in result.output


def test_cli_summon_directory_json(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id,name", ["1,A"])
    _write_csv(tmp_path / "b.csv", "id,name,extra", ["2,B,x"])
    result = CliRunner().invoke(main, ["summon", str(tmp_path), "--json", "--no-timeseries"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "multi"
    assert payload["summary"]["files_loaded"] == 2
    assert payload["summary"]["total_rows"] == 2
    assert payload["summary"]["schema_clusters"] == 2
    assert "extra" in payload["summary"]["drifted_columns"]
    assert len(payload["files"]) == 2
    assert all(f["profile"] is not None for f in payload["files"])


def test_cli_summon_glob(tmp_path: Path) -> None:
    _write_csv(tmp_path / "keep.csv", "id", ["1"])
    _write_csv(tmp_path / "skip.tsv", "id", ["2"])
    result = CliRunner().invoke(
        main,
        ["summon", str(tmp_path / "*.csv"), "--json", "--no-timeseries"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["files_loaded"] == 1


def test_cli_summon_max_files_cap(tmp_path: Path) -> None:
    for i in range(3):
        _write_csv(tmp_path / f"f{i}.csv", "id", ["1"])
    result = CliRunner().invoke(
        main,
        ["summon", str(tmp_path), "--max-files", "1", "--no-timeseries"],
    )
    assert result.exit_code == 2
    assert "exceeds --max-files" in result.output or "max-files" in result.output


def test_cli_summon_mixed_formats(tmp_path: Path) -> None:
    _write_csv(tmp_path / "a.csv", "id,name", ["1,A"])
    (tmp_path / "b.jsonl").write_text('{"id": 2, "name": "B"}\n')
    result = CliRunner().invoke(
        main,
        ["summon", str(tmp_path), "--json", "--no-timeseries"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["files_loaded"] == 2


def test_default_max_files_is_reasonable() -> None:
    # Guard against accidental change to the safety cap.
    assert DEFAULT_MAX_FILES >= 10
