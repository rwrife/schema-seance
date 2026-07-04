"""Turn a :class:`ProfileReport` into a starter expectation suite.

Two flavours are supported:

* **Great Expectations** — emits a v3 ``ExpectationSuite`` JSON document.
* **Soda Core** — emits a ``checks.yml`` fragment.

Design notes
------------

* Everything here is pure: input is a :class:`ProfileReport`, output is a
  plain ``dict`` (or a serialised string via :func:`dumps`). No I/O, no
  network, no logging.
* No new required dependencies. Great Expectations output uses ``json``;
  Soda output uses a tiny hand-rolled YAML writer scoped to the shape we
  emit — pulling in ``PyYAML`` just to write a checks file felt gratuitous.
* Columns whose PII confidence sits at or above ``medium`` are skipped by
  default. Flip ``include_pii=True`` to opt back in.
* Columns with fewer than ``min_samples`` observed non-null values get
  skipped (default ``0``). Useful on huge files profiled with ``--sample``.
* ``expect_column_values_to_be_in_set`` fires only when a column's distinct
  count is small enough (``<= max_distinct_for_set``, default 20) *and*
  the profile's top-K captured every distinct value. Otherwise we'd be
  asserting membership against a partial set — worse than not asserting.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..pii import CONFIDENCE_BANDS
from ..profile import ColumnProfile, ProfileReport

__all__ = [
    "EXPECTATIONS_SCHEMA_VERSION",
    "ExportOptions",
    "SUPPORTED_FORMATS",
    "dumps",
    "suite_name_from_path",
    "to_great_expectations",
    "to_soda",
]

EXPECTATIONS_SCHEMA_VERSION = 1
_MAX_DISTINCT_FOR_SET = 20
_PII_SKIP_THRESHOLD = CONFIDENCE_BANDS["medium"]

GX = "gx"
SODA = "soda"
SUPPORTED_FORMATS: tuple[str, ...] = (GX, SODA)


@dataclass(frozen=True)
class ExportOptions:
    """Knobs that both exporters honour."""

    suite_name: str | None = None
    include_pii: bool = False
    min_samples: int = 0
    max_distinct_for_set: int = _MAX_DISTINCT_FOR_SET


# ---------------------------------------------------------------------------
# Suite name + eligibility helpers
# ---------------------------------------------------------------------------

_SUITE_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def suite_name_from_path(path: Path | str | None) -> str:
    """Derive a stable suite name from a file path.

    ``/data/orders.csv`` -> ``orders``. Falls back to ``seance_suite``
    when *path* is None or gives us an empty stem.
    """
    if path is None:
        return "seance_suite"
    p = path if isinstance(path, Path) else Path(str(path))
    stem = p.stem or p.name or "seance_suite"
    cleaned = _SUITE_SAFE.sub("_", stem).strip("_")
    return cleaned or "seance_suite"


def _pii_should_skip(col: ColumnProfile, *, include_pii: bool) -> bool:
    if include_pii:
        return False
    return any(f.confidence >= _PII_SKIP_THRESHOLD for f in col.pii)


def _observed_non_null(col: ColumnProfile, rows: int) -> int:
    if rows <= 0:
        return 0
    # null_pct is a rounded percentage; reconstruct as best we can.
    return max(0, round(rows * (1.0 - (col.null_pct / 100.0))))


def _eligible_columns(report: ProfileReport, opts: ExportOptions) -> list[ColumnProfile]:
    out: list[ColumnProfile] = []
    for col in report.columns:
        if _pii_should_skip(col, include_pii=opts.include_pii):
            continue
        if _observed_non_null(col, report.rows) < opts.min_samples:
            continue
        out.append(col)
    return out


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------


def _gx_type_name(dtype: str) -> str | None:
    """Map a DuckDB dtype to a Great-Expectations ``type_`` argument."""
    upper = dtype.upper()
    if upper == "BOOLEAN":
        return "bool"
    if upper in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    }:
        return "int"
    if upper in {"FLOAT", "DOUBLE", "REAL"} or upper.startswith(("DECIMAL", "NUMERIC")):
        return "float"
    if upper == "DATE":
        return "date"
    if upper.startswith(("TIMESTAMP", "DATETIME")):
        return "datetime"
    if upper == "TIME":
        return "time"
    if upper in {"VARCHAR", "TEXT", "STRING"} or upper.startswith(("VARCHAR", "CHAR")):
        return "str"
    if upper == "UUID":
        return "str"
    if upper == "BLOB":
        return "bytes"
    return None


def _soda_type_name(dtype: str) -> str | None:
    gx = _gx_type_name(dtype)
    if gx is None:
        return None
    return {
        "int": "integer",
        "float": "decimal",
        "bool": "boolean",
        "str": "text",
        "date": "date",
        "datetime": "timestamp",
        "time": "time",
        "bytes": "text",
    }.get(gx, "text")


def _is_numeric(dtype: str) -> bool:
    return _gx_type_name(dtype) in {"int", "float"}


def _is_stringy(dtype: str) -> bool:
    return _gx_type_name(dtype) == "str"


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Decimal):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, _dt.datetime | _dt.date | _dt.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    return str(value)


def _covers_all_distinct(col: ColumnProfile) -> bool:
    if col.distinct <= 0:
        return False
    return len(col.top) >= col.distinct


def _distinct_set(col: ColumnProfile) -> list[Any] | None:
    if not col.top or not _covers_all_distinct(col):
        return None
    seen: list[Any] = []
    hashable_seen: set[Any] = set()
    for t in col.top:
        v = _jsonable(t.value)
        if v is None:
            continue
        try:
            if v in hashable_seen:
                continue
            hashable_seen.add(v)
        except TypeError:
            if v in seen:
                continue
        seen.append(v)
    return seen or None


# ---------------------------------------------------------------------------
# Great Expectations exporter
# ---------------------------------------------------------------------------


def to_great_expectations(
    report: ProfileReport,
    *,
    suite_name: str | None = None,
    include_pii: bool = False,
    min_samples: int = 0,
    max_distinct_for_set: int = _MAX_DISTINCT_FOR_SET,
) -> dict[str, Any]:
    """Build a v3 ``ExpectationSuite`` payload from *report*."""
    opts = ExportOptions(
        suite_name=suite_name,
        include_pii=include_pii,
        min_samples=min_samples,
        max_distinct_for_set=max_distinct_for_set,
    )
    name = opts.suite_name or suite_name_from_path(report.path)
    columns = _eligible_columns(report, opts)

    expectations: list[dict[str, Any]] = []

    # 1. Table-level column set.
    if report.columns:
        expectations.append(
            {
                "expectation_type": "expect_table_columns_to_match_set",
                "kwargs": {"column_set": [c.name for c in report.columns]},
                "meta": {"source": "schema-seance", "level": "table"},
            }
        )

    # 2. Per-column expectations, in stable column order.
    for col in columns:
        col_meta = {"source": "schema-seance", "column": col.name}

        expectations.append(
            {
                "expectation_type": "expect_column_to_exist",
                "kwargs": {"column": col.name},
                "meta": dict(col_meta),
            }
        )

        gx_type = _gx_type_name(col.dtype)
        if gx_type is not None:
            expectations.append(
                {
                    "expectation_type": "expect_column_values_to_be_of_type",
                    "kwargs": {"column": col.name, "type_": gx_type},
                    "meta": {**col_meta, "duckdb_dtype": col.dtype},
                }
            )

        if col.null_pct == 0.0 and report.rows > 0:
            expectations.append(
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": col.name},
                    "meta": dict(col_meta),
                }
            )

        if _is_numeric(col.dtype) and col.min is not None and col.max is not None:
            lo = _jsonable(col.min)
            hi = _jsonable(col.max)
            if lo is not None and hi is not None:
                expectations.append(
                    {
                        "expectation_type": "expect_column_values_to_be_between",
                        "kwargs": {
                            "column": col.name,
                            "min_value": lo,
                            "max_value": hi,
                        },
                        "meta": dict(col_meta),
                    }
                )

        if _is_stringy(col.dtype):
            lengths = [len(t.value) for t in col.top if isinstance(t.value, str)]
            if lengths:
                expectations.append(
                    {
                        "expectation_type": "expect_column_value_lengths_to_be_between",
                        "kwargs": {
                            "column": col.name,
                            "min_value": min(lengths),
                            "max_value": max(lengths),
                        },
                        "meta": {**col_meta, "source_hint": "top_k_lengths"},
                    }
                )

        if 0 < col.distinct <= opts.max_distinct_for_set:
            values = _distinct_set(col)
            if values is not None:
                expectations.append(
                    {
                        "expectation_type": "expect_column_values_to_be_in_set",
                        "kwargs": {"column": col.name, "value_set": values},
                        "meta": dict(col_meta),
                    }
                )

    return {
        "expectation_suite_name": name,
        "meta": {
            "generated_by": "schema-seance",
            "schema_version": EXPECTATIONS_SCHEMA_VERSION,
            "source_path": str(report.path) if report.path else None,
            "rows_profiled": report.rows,
            "sampled": report.sampled,
            "include_pii": include_pii,
            "min_samples": min_samples,
        },
        "expectations": expectations,
        "data_asset_type": None,
        "ge_cloud_id": None,
    }


# ---------------------------------------------------------------------------
# Soda exporter
# ---------------------------------------------------------------------------


def to_soda(
    report: ProfileReport,
    *,
    suite_name: str | None = None,
    include_pii: bool = False,
    min_samples: int = 0,
    max_distinct_for_set: int = _MAX_DISTINCT_FOR_SET,
) -> dict[str, Any]:
    """Build a Soda Core ``checks.yml``-shaped payload."""
    opts = ExportOptions(
        suite_name=suite_name,
        include_pii=include_pii,
        min_samples=min_samples,
        max_distinct_for_set=max_distinct_for_set,
    )
    name = opts.suite_name or suite_name_from_path(report.path)
    columns = _eligible_columns(report, opts)

    checks: list[dict[str, Any]] = []

    # Table-level row-count sanity.
    checks.append({"row_count > 0": None})

    # Schema check: every declared column must be present.
    if report.columns:
        checks.append(
            {
                "schema": {
                    "name": "expected columns present",
                    "fail": {
                        "when required column missing": [c.name for c in report.columns],
                    },
                }
            }
        )

    for col in columns:
        # Not-null checks first for readability.
        if col.null_pct == 0.0 and report.rows > 0:
            checks.append({f"missing_count({col.name}) = 0": None})

        # Numeric range bounds.
        if _is_numeric(col.dtype) and col.min is not None and col.max is not None:
            lo = _jsonable(col.min)
            hi = _jsonable(col.max)
            if lo is not None and hi is not None:
                checks.append({f"min({col.name}) >= {lo}": None})
                checks.append({f"max({col.name}) <= {hi}": None})

        # Value-set constraint for low-cardinality columns.
        if 0 < col.distinct <= opts.max_distinct_for_set:
            values = _distinct_set(col)
            if values is not None:
                checks.append(
                    {
                        f"invalid_count({col.name}) = 0": {
                            "name": f"{col.name} in known value set",
                            "valid values": values,
                        }
                    }
                )

        # Dtype hint via Soda's `valid format` block.
        soda_type = _soda_type_name(col.dtype)
        if soda_type is not None:
            checks.append(
                {
                    f"invalid_count({col.name}) = 0 as {col.name}_type_check": {
                        "name": f"{col.name} matches type {soda_type}",
                        "valid format": soda_type,
                    }
                }
            )

    return {
        f"checks for {name}": checks,
        # Sidecar meta block — Soda ignores unknown top-level keys.
        "schema_seance": {
            "schema_version": EXPECTATIONS_SCHEMA_VERSION,
            "source_path": str(report.path) if report.path else None,
            "rows_profiled": report.rows,
            "sampled": report.sampled,
            "include_pii": include_pii,
            "min_samples": min_samples,
        },
    }


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def dumps(
    report: ProfileReport,
    format: str,
    *,
    suite_name: str | None = None,
    include_pii: bool = False,
    min_samples: int = 0,
    max_distinct_for_set: int = _MAX_DISTINCT_FOR_SET,
) -> str:
    """Serialise *report* to a GX JSON string or a Soda YAML string.

    Raises :class:`ValueError` for unknown *format*.
    """
    fmt = format.lower().strip()
    if fmt == GX:
        payload = to_great_expectations(
            report,
            suite_name=suite_name,
            include_pii=include_pii,
            min_samples=min_samples,
            max_distinct_for_set=max_distinct_for_set,
        )
        return json.dumps(payload, indent=2, sort_keys=False)
    if fmt == SODA:
        payload = to_soda(
            report,
            suite_name=suite_name,
            include_pii=include_pii,
            min_samples=min_samples,
            max_distinct_for_set=max_distinct_for_set,
        )
        return _yaml_dumps(payload)
    raise ValueError(f"unknown expectations format {format!r}; expected one of {SUPPORTED_FORMATS}")


# ---------------------------------------------------------------------------
# Small hand-rolled YAML writer.
#
# Only handles the shapes emitted by :func:`to_soda`: nested mappings,
# lists of mappings (or lists of scalars, emitted as inline flow), and
# scalars. Deterministic key order; no anchors, aliases, or block/folded
# scalars.
# ---------------------------------------------------------------------------

_YAML_SAFE_STR = re.compile(r"^[A-Za-z0-9_./+()= @%<>!,-]+$")
_YAML_RESERVED = {"null", "true", "false", "yes", "no", "on", "off", "~"}
_YAML_BAD_PREFIX = ("-", "?", ":", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`", "#")


def _yaml_needs_quotes(text: str) -> bool:
    if text == "":
        return True
    if text.lower() in _YAML_RESERVED:
        return True
    if not _YAML_SAFE_STR.match(text):
        return True
    if text.startswith(_YAML_BAD_PREFIX):
        return True
    return False


def _yaml_str(text: str) -> str:
    if not _yaml_needs_quotes(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "null"
        return repr(value)
    return _yaml_str(str(value))


def _yaml_key(key: Any) -> str:
    return _yaml_str(str(key))


def _yaml_dumps(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise TypeError("top-level YAML payload must be a mapping")
    lines: list[str] = []
    _yaml_emit_mapping(payload, 0, lines)
    return "\n".join(lines) + "\n"


def _yaml_emit_mapping(mapping: dict[str, Any], indent: int, lines: list[str]) -> None:
    pad = " " * indent
    for raw_key, value in mapping.items():
        key = _yaml_key(raw_key)
        if isinstance(value, dict):
            if not value:
                lines.append(f"{pad}{key}: {{}}")
                continue
            lines.append(f"{pad}{key}:")
            _yaml_emit_mapping(value, indent + 2, lines)
        elif isinstance(value, list | tuple):
            _yaml_emit_key_with_list(key, list(value), indent, lines)
        else:
            lines.append(f"{pad}{key}: {_yaml_scalar(value)}")


def _yaml_emit_key_with_list(key: str, seq: list[Any], indent: int, lines: list[str]) -> None:
    pad = " " * indent
    if not seq:
        lines.append(f"{pad}{key}: []")
        return
    if all(not isinstance(x, dict | list | tuple) for x in seq):
        joined = ", ".join(_yaml_scalar(x) for x in seq)
        lines.append(f"{pad}{key}: [{joined}]")
        return
    lines.append(f"{pad}{key}:")
    _yaml_emit_block_sequence(seq, indent + 2, lines)


def _yaml_emit_block_sequence(seq: list[Any], indent: int, lines: list[str]) -> None:
    pad = " " * indent
    for item in seq:
        if isinstance(item, dict):
            if not item:
                lines.append(f"{pad}- {{}}")
                continue
            keys = list(item.keys())
            first_key = _yaml_key(keys[0])
            first_val = item[keys[0]]
            if isinstance(first_val, dict) and first_val:
                lines.append(f"{pad}- {first_key}:")
                _yaml_emit_mapping(first_val, indent + 4, lines)
            elif isinstance(first_val, list | tuple) and first_val:
                if all(not isinstance(x, dict | list | tuple) for x in first_val):
                    joined = ", ".join(_yaml_scalar(x) for x in first_val)
                    lines.append(f"{pad}- {first_key}: [{joined}]")
                else:
                    lines.append(f"{pad}- {first_key}:")
                    _yaml_emit_block_sequence(list(first_val), indent + 4, lines)
            else:
                lines.append(f"{pad}- {first_key}: {_yaml_scalar(first_val)}")
            extra_pad = " " * (indent + 2)
            for k in keys[1:]:
                v = item[k]
                sub_key = _yaml_key(k)
                if isinstance(v, dict) and v:
                    lines.append(f"{extra_pad}{sub_key}:")
                    _yaml_emit_mapping(v, indent + 4, lines)
                elif isinstance(v, list | tuple):
                    _yaml_emit_key_with_list(sub_key, list(v), indent + 2, lines)
                else:
                    lines.append(f"{extra_pad}{sub_key}: {_yaml_scalar(v)}")
        else:
            lines.append(f"{pad}- {_yaml_scalar(item)}")
