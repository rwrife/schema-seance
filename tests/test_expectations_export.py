"""Tests for :mod:`schema_seance.export.expectations`.

Covers both pure builders (``to_great_expectations`` / ``to_soda``) and
the CLI wiring on ``seance summon --expectations {gx,soda}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main as seance_cli
from schema_seance.export.expectations import (
    EXPECTATIONS_SCHEMA_VERSION,
    dumps,
    suite_name_from_path,
    to_great_expectations,
    to_soda,
)
from schema_seance.profile import profile
from schema_seance.readers import load

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def orders_csv(tmp_path: Path) -> Path:
    """A slightly richer CSV than the shared tiny.csv fixture.

    * ``status`` is low-cardinality enum-ish → value-set expectation.
    * ``qty`` is a full integer column with no nulls → min/max range +
      not-null.
    * ``email`` is high-confidence PII → skipped by default.
    """
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(
        "id,status,qty,email\n"
        "1,paid,3,alice@example.com\n"
        "2,pending,1,bob@example.com\n"
        "3,paid,7,carol@example.com\n"
        "4,shipped,2,dan@example.com\n"
        "5,paid,5,eve@example.com\n",
        encoding="utf-8",
    )
    return csv_path


@pytest.fixture()
def orders_report(orders_csv: Path):
    return profile(load(orders_csv), path=orders_csv)


# ---------------------------------------------------------------------------
# suite_name_from_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        (Path("/data/orders.csv"), "orders"),
        (Path("weird name!.csv"), "weird_name"),
        (Path("with.multi.dots.parquet"), "with.multi.dots"),
        (None, "seance_suite"),
        ("", "seance_suite"),
    ],
)
def test_suite_name_from_path(path, expected: str) -> None:
    assert suite_name_from_path(path) == expected


# ---------------------------------------------------------------------------
# Great Expectations exporter
# ---------------------------------------------------------------------------


def test_gx_shape_and_meta(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    assert suite["expectation_suite_name"] == "orders"
    assert suite["meta"]["generated_by"] == "schema-seance"
    assert suite["meta"]["schema_version"] == EXPECTATIONS_SCHEMA_VERSION
    assert suite["meta"]["rows_profiled"] == 5
    assert suite["meta"]["include_pii"] is False
    assert suite["data_asset_type"] is None
    assert isinstance(suite["expectations"], list)


def test_gx_table_columns_expectation_covers_full_schema(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    tbl = [
        e
        for e in suite["expectations"]
        if e["expectation_type"] == "expect_table_columns_to_match_set"
    ]
    assert len(tbl) == 1
    # The table-level expectation always lists every column, even PII ones.
    assert tbl[0]["kwargs"]["column_set"] == ["id", "status", "qty", "email"]


def test_gx_skips_pii_by_default(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    per_column_cols = {
        e["kwargs"].get("column")
        for e in suite["expectations"]
        if e["expectation_type"] != "expect_table_columns_to_match_set"
    }
    assert "email" not in per_column_cols


def test_gx_include_pii_re_enables_email(orders_report) -> None:
    suite = to_great_expectations(orders_report, include_pii=True)
    per_column_cols = {
        e["kwargs"].get("column")
        for e in suite["expectations"]
        if e["expectation_type"] != "expect_table_columns_to_match_set"
    }
    assert "email" in per_column_cols
    assert suite["meta"]["include_pii"] is True


def test_gx_not_null_for_full_columns(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    not_null_cols = {
        e["kwargs"]["column"]
        for e in suite["expectations"]
        if e["expectation_type"] == "expect_column_values_to_not_be_null"
    }
    assert not_null_cols == {"id", "status", "qty"}


def test_gx_numeric_range_for_qty(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    ranges = [
        e
        for e in suite["expectations"]
        if e["expectation_type"] == "expect_column_values_to_be_between"
        and e["kwargs"]["column"] == "qty"
    ]
    assert len(ranges) == 1
    assert ranges[0]["kwargs"]["min_value"] == 1
    assert ranges[0]["kwargs"]["max_value"] == 7


def test_gx_status_gets_value_set(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    in_set = [
        e
        for e in suite["expectations"]
        if e["expectation_type"] == "expect_column_values_to_be_in_set"
        and e["kwargs"]["column"] == "status"
    ]
    assert len(in_set) == 1
    assert set(in_set[0]["kwargs"]["value_set"]) == {"paid", "pending", "shipped"}


def test_gx_type_expectations_map_expected_python_names(orders_report) -> None:
    suite = to_great_expectations(orders_report)
    type_by_col = {
        e["kwargs"]["column"]: e["kwargs"]["type_"]
        for e in suite["expectations"]
        if e["expectation_type"] == "expect_column_values_to_be_of_type"
    }
    assert type_by_col["id"] == "int"
    assert type_by_col["qty"] == "int"
    assert type_by_col["status"] == "str"


def test_gx_min_samples_drops_sparse_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "sparse.csv"
    # 5 rows, `optional` is almost entirely null → 1 observed value.
    csv_path.write_text(
        "id,optional\n1,\n2,\n3,\n4,\n5,hi\n",
        encoding="utf-8",
    )
    report = profile(load(csv_path), path=csv_path)
    suite = to_great_expectations(report, min_samples=3)
    covered = {
        e["kwargs"].get("column")
        for e in suite["expectations"]
        if e["expectation_type"] != "expect_table_columns_to_match_set"
    }
    assert "optional" not in covered
    assert "id" in covered


def test_gx_output_is_json_serialisable(orders_report) -> None:
    body = dumps(orders_report, "gx")
    parsed = json.loads(body)
    assert parsed["expectation_suite_name"] == "orders"


# ---------------------------------------------------------------------------
# Soda exporter
# ---------------------------------------------------------------------------


def test_soda_top_level_shape(orders_report) -> None:
    payload = to_soda(orders_report)
    assert "checks for orders" in payload
    checks = payload["checks for orders"]
    assert isinstance(checks, list)
    # First check is always the row-count sanity.
    assert list(checks[0].keys()) == ["row_count > 0"]
    # Sidecar meta block is present.
    assert payload["schema_seance"]["schema_version"] == EXPECTATIONS_SCHEMA_VERSION


def test_soda_schema_check_lists_every_column(orders_report) -> None:
    payload = to_soda(orders_report)
    schema_checks = [c for c in payload["checks for orders"] if "schema" in c]
    assert len(schema_checks) == 1
    listed = schema_checks[0]["schema"]["fail"]["when required column missing"]
    assert listed == ["id", "status", "qty", "email"]


def test_soda_skips_pii_by_default(orders_report) -> None:
    text = dumps(orders_report, "soda")
    # The schema check still names `email` (dataset shape), but per-column
    # substantive checks must not touch it.
    assert "missing_count(email)" not in text
    assert "email_type_check" not in text


def test_soda_numeric_bounds_present(orders_report) -> None:
    text = dumps(orders_report, "soda")
    assert "min(qty) >= 1" in text
    assert "max(qty) <= 7" in text


def test_soda_value_set_present_for_status(orders_report) -> None:
    text = dumps(orders_report, "soda")
    assert "valid values: [paid, pending, shipped]" in text


def test_soda_yaml_round_trips_via_pyyaml_if_available(orders_report) -> None:
    """Belt-and-braces: if PyYAML happens to be installed in the env,
    make sure our hand-rolled writer round-trips through a real parser.
    """
    yaml = pytest.importorskip("yaml")
    text = dumps(orders_report, "soda")
    parsed = yaml.safe_load(text)
    assert "checks for orders" in parsed
    assert parsed["schema_seance"]["schema_version"] == EXPECTATIONS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# dumps() input validation
# ---------------------------------------------------------------------------


def test_dumps_rejects_unknown_format(orders_report) -> None:
    with pytest.raises(ValueError, match="unknown expectations format"):
        dumps(orders_report, "yaml")


def test_dumps_respects_suite_name_override(orders_report) -> None:
    body = dumps(orders_report, "gx", suite_name="prod.orders_v2")
    parsed = json.loads(body)
    assert parsed["expectation_suite_name"] == "prod.orders_v2"


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_summon_expectations_gx(orders_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["summon", str(orders_csv), "--expectations", "gx"],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["expectation_suite_name"] == "orders"
    assert parsed["meta"]["schema_version"] == EXPECTATIONS_SCHEMA_VERSION


def test_cli_summon_expectations_soda(orders_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["summon", str(orders_csv), "--expectations", "soda"],
    )
    assert result.exit_code == 0, result.output
    assert "checks for orders" in result.stdout
    assert "row_count > 0" in result.stdout


def test_cli_summon_expectations_rejects_json_combo(orders_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        ["summon", str(orders_csv), "--expectations", "gx", "--json"],
    )
    assert result.exit_code != 0
    assert "cannot be combined" in result.output


def test_cli_summon_expectations_suite_name_override(orders_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        [
            "summon",
            str(orders_csv),
            "--expectations",
            "gx",
            "--suite-name",
            "warehouse.orders",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["expectation_suite_name"] == "warehouse.orders"


def test_cli_summon_expectations_include_pii_flag(orders_csv: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        seance_cli,
        [
            "summon",
            str(orders_csv),
            "--expectations",
            "gx",
            "--include-pii",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    per_column_cols = {
        e["kwargs"].get("column")
        for e in parsed["expectations"]
        if e["expectation_type"] != "expect_table_columns_to_match_set"
    }
    assert "email" in per_column_cols
