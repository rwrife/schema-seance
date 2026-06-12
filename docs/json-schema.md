# `seance summon --json` schema

Stable, versioned output. The current version is exposed at runtime as
`schema_seance.profile.PROFILE_SCHEMA_VERSION` and is bumped whenever the
shape below changes in a backwards-incompatible way.

## Top-level

```jsonc
{
  "schema_version": 1,
  "file": {
    "path": "data.csv",        // string | null
    "size_bytes": 12345,        // int | null
    "encoding": "utf-8"        // string | null
  },
  "rows": 1000,                  // int — rows actually profiled (post-sample)
  "cols": 7,                     // int
  "sampled": false,              // bool — true if --sample was applied
  "sample_size": null,           // int | null — value passed to --sample
  "columns": [ /* ColumnProfile */ ]
}
```

## ColumnProfile

```jsonc
{
  "name": "email",              // string
  "dtype": "VARCHAR",           // DuckDB type string
  "null_pct": 33.33,             // float, 0..100, two decimals
  "distinct": 2,                 // int
  "sample": "alice@example.com", // any JSON-friendly scalar | null
  "min": "alice@example.com",   // any JSON-friendly scalar | null
  "max": "carol@example.com",   // any JSON-friendly scalar | null
  "mean": null,                  // float | null (numeric columns only)
  "stddev": null,                // float | null (numeric columns only)
  "top": [
    { "value": "alice@example.com", "count": 1 },
    { "value": "carol@example.com", "count": 1 }
  ]
}
```

### Type coercion

- `datetime`/`date`/`time` values are emitted as ISO-8601 strings.
- `Decimal` is emitted as a JSON number.
- `bytes` is decoded as UTF-8 when possible, otherwise hex.
- Nested types (struct/list/map) currently emit `null` for `min`/`max` and
  may emit `[]` for `top` when DuckDB can't aggregate them.
- `NaN` / `Infinity` are coerced to `null` to stay valid JSON.

## Stability contract

- Field names and the overall structure are stable for a given
  `schema_version`.
- New optional fields may be added without bumping the version; existing
  fields will not be removed or renamed inside the same version.
- Breaking changes (removing/renaming fields, changing semantics) bump
  `schema_version`.
