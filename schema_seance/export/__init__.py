"""Exporters that turn a :class:`ProfileReport` into other tools' formats.

Currently supports Great Expectations v3 ExpectationSuite JSON and
Soda Core checks YAML.  These live behind ``seance summon --expectations
{gx,soda}`` and can also be used programmatically::

    from schema_seance.export.expectations import to_great_expectations
    suite = to_great_expectations(report, suite_name="orders")
"""

from __future__ import annotations

from .expectations import (
    EXPECTATIONS_SCHEMA_VERSION,
    dumps,
    to_great_expectations,
    to_soda,
)

__all__ = [
    "EXPECTATIONS_SCHEMA_VERSION",
    "dumps",
    "to_great_expectations",
    "to_soda",
]
