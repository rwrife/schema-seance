"""Tests for the schema-diff feature (`seance compare`)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.diff import compare
from schema_seance.profile import profile as profile_relation
from schema_seance.readers import load
from schema_seance.render.diff import diff_to_dict


def _profile(path):
    rel = load(path)
    return profile_relation(rel, path=path)


def test_compare_detects_added_and_removed_columns(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,name\n1,Alice\n2,Bob\n")
    after.write_text("id,email\n1,a@example.com\n2,b@example.com\n")

    diff = compare(_profile(before), _profile(after))
    names = {c.name: c for c in diff.columns}
    assert names["email"].status == "added"
    assert names["name"].status == "removed"
    assert names["id"].status == "unchanged"
    # Added PII (email) should show up as a PII change.
    pii_kinds = [p.kind for p in names["email"].pii_changes]
    assert "email" in pii_kinds


def test_compare_detects_dtype_drift(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,age\n1,10\n2,20\n3,30\n")
    # age becomes string-y -> dtype family drift
    after.write_text('id,age\n1,"ten"\n2,"twenty"\n3,"thirty"\n')

    diff = compare(_profile(before), _profile(after))
    age = next(c for c in diff.columns if c.name == "age")
    assert age.status == "changed"
    assert any(c.field == "dtype" and c.severity == "high" for c in age.changes)
    assert diff.severity in ("high", "medium")


def test_compare_unchanged_when_identical(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    body = "id,name\n1,Alice\n2,Bob\n3,Carol\n"
    before.write_text(body)
    after.write_text(body)

    diff = compare(_profile(before), _profile(after))
    assert diff.severity == "none"
    assert all(c.status == "unchanged" for c in diff.columns)


def test_cli_compare_renders_terminal(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,name\n1,Alice\n2,Bob\n")
    after.write_text("id,email\n1,a@example.com\n2,b@example.com\n")

    result = CliRunner().invoke(main, ["compare", str(before), str(after)])
    assert result.exit_code == 0, result.output
    assert "Two Spirits Compared" in result.output
    assert "email" in result.output


def test_cli_compare_json_output_is_stable(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,name\n1,Alice\n2,Bob\n")
    after.write_text("id,name,email\n1,Alice,a@example.com\n2,Bob,b@example.com\n")

    result = CliRunner().invoke(main, ["compare", str(before), str(after), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert "email" in payload["summary"]["added"]
    cols = {c["name"]: c for c in payload["columns"]}
    assert cols["email"]["status"] == "added"


def test_cli_compare_fail_on_drift(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,age\n1,10\n2,20\n3,30\n")
    after.write_text('id,age\n1,"ten"\n2,"twenty"\n3,"thirty"\n')

    result = CliRunner().invoke(main, ["compare", str(before), str(after), "--fail-on", "high"])
    assert result.exit_code == 5, result.output


def test_diff_to_dict_round_trip_shape(tmp_path) -> None:
    before = tmp_path / "a.csv"
    after = tmp_path / "b.csv"
    before.write_text("id,name\n1,Alice\n")
    after.write_text("id,name\n1,Alice\n2,Bob\n")
    diff = compare(_profile(before), _profile(after))
    d = diff_to_dict(diff)
    assert {"schema_version", "before", "after", "severity", "summary", "columns"} <= d.keys()
