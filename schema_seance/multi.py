"""Multi-file summon: profile a directory / glob as one cross-file report.

The medium hosts a group seance — enumerate matching inputs (local or
remote), profile each with the single-file profiler, then produce a
roll-up that clusters files by schema signature, sums row counts, and
surfaces per-file failures.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .profile import ProfileReport
from .profile import profile as profile_relation
from .readers import (
    ExcelReaderError,
    RemoteAccessError,
    SQLiteTableError,
    UnsupportedFormatError,
    load,
)
from .remote import (
    REMOTE_SCHEMES,
    configure_s3,
    ensure_httpfs,
    is_remote,
)

__all__ = [
    "DEFAULT_MAX_FILES",
    "FileOutcome",
    "MultiReport",
    "SchemaCluster",
    "expand_inputs",
    "profile_many",
    "MultiInputError",
]

# Safe default cap so `seance summon ./huge-dir/` doesn't accidentally
# spend an afternoon reading a million files.
DEFAULT_MAX_FILES = 50

# Extensions the single-file readers can currently handle. We ignore
# everything else during directory enumeration so mixed folders (READMEs,
# checksums, etc.) don't count against `--max-files`.
_SUPPORTED_EXTS = frozenset(
    {
        ".csv",
        ".tsv",
        ".jsonl",
        ".ndjson",
        ".parquet",
        ".pq",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".xlsx",
        ".xlsm",
    }
)

_GLOB_MAGIC = frozenset("*?[")


class MultiInputError(ValueError):
    """Raised for user-facing enumeration errors (no matches, cap hit)."""


@dataclass(frozen=True)
class FileOutcome:
    """Per-file result inside a multi-file report."""

    path: str
    ok: bool
    report: ProfileReport | None = None
    error: str | None = None
    error_kind: str | None = None


@dataclass(frozen=True)
class SchemaCluster:
    """A group of files that share the same (column-name, dtype) signature."""

    signature: tuple[tuple[str, str], ...]
    files: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class MultiReport:
    """The Congregation — cross-file roll-up."""

    inputs: tuple[str, ...]
    files: tuple[FileOutcome, ...] = field(default_factory=tuple)
    clusters: tuple[SchemaCluster, ...] = field(default_factory=tuple)
    drifted_columns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_rows(self) -> int:
        return sum(f.report.rows for f in self.files if f.ok and f.report is not None)

    @property
    def loaded_count(self) -> int:
        return sum(1 for f in self.files if f.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for f in self.files if not f.ok)

    @property
    def failures(self) -> tuple[FileOutcome, ...]:
        return tuple(f for f in self.files if not f.ok)


def _has_glob(text: str) -> bool:
    return any(ch in _GLOB_MAGIC for ch in text)


def _split_remote_glob(uri: str) -> tuple[str, str] | None:
    """Return (base, pattern) if *uri* is a remote glob, else None.

    Splits at the first path component containing a glob magic char so
    the base can be used for prefix listing / DuckDB `glob()`.
    """
    if not is_remote(uri) or not _has_glob(uri):
        return None
    return uri, ""  # DuckDB's glob() handles it directly.


def _list_remote_glob(uri: str, *, region: str | None = None) -> list[str]:
    """Enumerate a remote glob via DuckDB's `glob()` table function."""
    con = duckdb.connect()
    ensure_httpfs(con)
    if uri.lower().startswith("s3://"):
        configure_s3(con, region=region)
    try:
        rows = con.execute("SELECT file FROM glob(?)", [uri]).fetchall()
    except duckdb.Error as exc:  # pragma: no cover - network dependent
        raise MultiInputError(f"Could not enumerate remote glob {uri!r}: {exc}") from exc
    return [str(r[0]) for r in rows]


def _apply_filters(
    paths: list[str],
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> list[str]:
    out = []
    for p in paths:
        name = p.rsplit("/", 1)[-1].rsplit(os.sep, 1)[-1]
        if include and not any(fnmatch.fnmatch(name, pat) for pat in include):
            continue
        if exclude and any(fnmatch.fnmatch(name, pat) for pat in exclude):
            continue
        out.append(p)
    return out


def _supported_ext(path: str) -> bool:
    suffix = ""
    lowered = path.lower()
    for ext in _SUPPORTED_EXTS:
        if lowered.endswith(ext):
            suffix = ext
            break
    return bool(suffix)


def expand_inputs(
    pattern: str | os.PathLike[str],
    *,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    max_files: int = DEFAULT_MAX_FILES,
    region: str | None = None,
) -> list[str]:
    """Expand *pattern* into an ordered list of file inputs.

    Accepts:
      - a single local file path
      - a local directory (recursive, only supported extensions)
      - a local glob pattern (``data/*.csv``)
      - a remote URI (``s3://…``, ``https://…``)
      - a remote glob (``s3://bucket/prefix/*.parquet``) — enumerated via
        DuckDB's ``glob()`` function

    Applies ``include`` / ``exclude`` fnmatch filters against the basename
    and caps the result at ``max_files``.
    """
    text = os.fspath(pattern)

    if is_remote(text):
        if _has_glob(text):
            candidates = _list_remote_glob(text, region=region)
        else:
            candidates = [text]
    elif _has_glob(text):
        candidates = sorted(_glob.glob(text, recursive=True))
    else:
        p = Path(text)
        if p.is_dir():
            candidates = sorted(
                str(child)
                for child in p.rglob("*")
                if child.is_file() and _supported_ext(child.name)
            )
        elif p.is_file():
            candidates = [str(p)]
        else:
            raise MultiInputError(
                f"No files match {text!r} — path does not exist and is not a glob."
            )

    # Directory / glob results should only include supported extensions.
    # For an explicit single file, trust the user (single-file summon will
    # error clearly for unsupported types).
    if len(candidates) != 1 or _has_glob(text) or (not is_remote(text) and Path(text).is_dir()):
        candidates = [c for c in candidates if _supported_ext(c)]

    candidates = _apply_filters(candidates, include=include, exclude=exclude)

    if not candidates:
        raise MultiInputError(f"No supported files matched {text!r} after include/exclude filters.")

    if max_files > 0 and len(candidates) > max_files:
        raise MultiInputError(
            f"{len(candidates)} files matched {text!r} — exceeds --max-files "
            f"cap ({max_files}). Raise the cap or tighten your pattern."
        )

    return candidates


def _signature(report: ProfileReport) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((c.name, c.dtype) for c in report.columns))


def _cluster_and_drift(
    outcomes: list[FileOutcome],
) -> tuple[tuple[SchemaCluster, ...], tuple[str, ...]]:
    signatures: dict[tuple[tuple[str, str], ...], list[str]] = {}
    for o in outcomes:
        if not o.ok or o.report is None:
            continue
        sig = _signature(o.report)
        signatures.setdefault(sig, []).append(o.path)

    clusters = tuple(
        SchemaCluster(signature=sig, files=tuple(files))
        for sig, files in sorted(signatures.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )

    # Drift: any column name that appears with more than one dtype across
    # files, OR appears in some but not all files.
    column_dtypes: dict[str, set[str]] = {}
    column_presence: Counter[str] = Counter()
    ok_files = sum(1 for o in outcomes if o.ok and o.report is not None)
    for o in outcomes:
        if not o.ok or o.report is None:
            continue
        for col in o.report.columns:
            column_dtypes.setdefault(col.name, set()).add(col.dtype)
            column_presence[col.name] += 1

    drifted = tuple(
        sorted(
            name
            for name, dtypes in column_dtypes.items()
            if len(dtypes) > 1 or column_presence[name] != ok_files
        )
    )
    return clusters, drifted


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, UnsupportedFormatError):
        return "unsupported_format"
    if isinstance(exc, RemoteAccessError):
        return "remote_error"
    if isinstance(exc, SQLiteTableError):
        return "sqlite_error"
    if isinstance(exc, ExcelReaderError):
        return "excel_error"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    return "load_error"


def profile_many(
    inputs: list[str],
    *,
    sample: int | None = None,
    table: str | None = None,
    sheet: str | int | None = None,
    region: str | None = None,
    format: str | None = None,
    timeseries: bool = True,
    top_k: int = 5,
) -> MultiReport:
    """Profile each file in *inputs* and roll up a MultiReport."""
    outcomes: list[FileOutcome] = []
    for path in inputs:
        try:
            relation = load(
                path,
                table=table,
                sheet=sheet,
                region=region,
                format=format,
            )
            report = profile_relation(
                relation,
                path=path,
                sample=sample,
                top_k=top_k,
                timeseries=timeseries,
            )
            outcomes.append(FileOutcome(path=str(path), ok=True, report=report))
        except (
            UnsupportedFormatError,
            RemoteAccessError,
            SQLiteTableError,
            ExcelReaderError,
            FileNotFoundError,
            duckdb.Error,
        ) as exc:
            outcomes.append(
                FileOutcome(
                    path=str(path),
                    ok=False,
                    error=str(exc),
                    error_kind=_classify_error(exc),
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            outcomes.append(
                FileOutcome(
                    path=str(path),
                    ok=False,
                    error=str(exc),
                    error_kind="load_error",
                )
            )

    clusters, drifted = _cluster_and_drift(outcomes)
    return MultiReport(
        inputs=tuple(inputs),
        files=tuple(outcomes),
        clusters=clusters,
        drifted_columns=drifted,
    )


def is_multi_input(text: str | os.PathLike[str]) -> bool:
    """True when *text* refers to a directory or a glob pattern.

    Used by the CLI to decide between single-file and multi-file summon
    without doing a redundant enumeration.
    """
    s = os.fspath(text)
    if _has_glob(s):
        return True
    if is_remote(s):
        return False
    try:
        return Path(s).is_dir()
    except OSError:
        return False


# Re-export scheme constants for CLI-side detection.
_ = REMOTE_SCHEMES
