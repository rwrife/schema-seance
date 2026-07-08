"""Configuration file support for ``schema-seance``.

Resolution order (highest precedence first):

1. Explicit CLI flag (handled at the Click layer — not this module's job).
2. Environment variable (``SEANCE_*``; also outside this module).
3. ``./.seancerc.toml`` (project-local, discovered by walking up from CWD).
4. ``[tool.seance]`` table in the nearest ``pyproject.toml`` (same walk).
5. ``~/.config/schema-seance/config.toml`` (respects ``$XDG_CONFIG_HOME``).
6. Built-in defaults (handled by whoever consumes the resolved value).

The public entrypoints are :func:`load` (discover + parse the merged
config) and :func:`resolved_for_source_display` (a serialisable view of
the merged config *with provenance* for the ``seance config`` command).

The config surface is intentionally 1:1 with the CLI flag names (kebab
maps to snake). Unknown top-level keys warn but do not error, so old
CLIs stay forward-compatible with new configs. Unknown keys under known
nested tables (``[llm]``, ``[pii]``, ``[watch]``, ``[render]``) are hard
errors — the goal is to fail fast on typos in the tables that matter.

Secrets (e.g. LLM API keys) are never accepted from a config file and
never surfaced by this module — see :func:`load` docstring.
"""

from __future__ import annotations

import os
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CONFIG_FILENAME",
    "PYPROJECT_FILENAME",
    "PYPROJECT_TABLE",
    "ConfigError",
    "ConfigNotFoundError",
    "LoadedConfig",
    "PiiNameRule",
    "ResolvedValue",
    "discover",
    "load",
    "user_config_path",
]


CONFIG_FILENAME = ".seancerc.toml"
PYPROJECT_FILENAME = "pyproject.toml"
PYPROJECT_TABLE = "tool.seance"


# Top-level scalar keys recognised in v1. Kebab in TOML → snake here.
_TOP_LEVEL_SCALAR_KEYS: frozenset[str] = frozenset(
    {
        "persona",
        "sample",
        "fail_on_pii",
        "min_score",
        "no_timeseries",
    }
)

# Nested tables and their allowed keys. Unknown keys under these tables
# are hard errors (see :class:`ConfigError`).
_NESTED_TABLE_KEYS: dict[str, frozenset[str]] = {
    "llm": frozenset({"base_url", "model"}),
    "pii": frozenset({"name_rules"}),
    "watch": frozenset({"debounce_ms", "poll_interval"}),
    "render": frozenset({"color"}),
}

# Keys whose kebab-form users are likely to type. TOML itself allows
# either form, but we normalise so ``fail-on-pii`` and ``fail_on_pii``
# behave the same.
_KEBAB_TO_SNAKE = str.maketrans("-", "_")


class ConfigError(ValueError):
    """Raised when a config file is present but malformed or invalid.

    The message always includes the source path (and line/column when
    ``tomllib`` provides one) so the user can jump straight to the
    offending line.
    """


class ConfigNotFoundError(ConfigError):
    """Raised when ``--config PATH`` points at a non-existent file."""


@dataclass(frozen=True)
class PiiNameRule:
    """User-declared PII detector hint for a column-name pattern."""

    pattern: str
    detector: str
    confidence: str = "medium"


@dataclass(frozen=True)
class ResolvedValue:
    """One resolved config value plus where it came from."""

    value: Any
    source: str  # "cli" | "env" | path str | "default"


@dataclass(frozen=True)
class LoadedConfig:
    """Result of :func:`load` — merged config + per-key provenance.

    ``values`` is the flat merged dict (top-level scalars + one nested
    dict per known table). ``sources`` maps the same keys to the
    file/label they came from (relative path when possible, or literal
    labels like ``"env"``, ``"default"``).

    ``sources_by_path`` is the ordered list of files actually consulted
    (highest precedence first); it's useful for ``seance config`` to
    print a resolution trail even when a file contributed no value.
    """

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    sources_by_path: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def user_config_path(env: dict[str, str] | None = None) -> Path:
    """Return the user-global config path.

    Respects ``$XDG_CONFIG_HOME``; falls back to ``~/.config``.
    """
    e = env if env is not None else os.environ
    xdg = (e.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "schema-seance" / "config.toml"


def _walk_up(start: Path) -> list[Path]:
    """All directories from *start* up to and including its root."""
    start = start.resolve()
    out = [start]
    for parent in start.parents:
        out.append(parent)
    return out


def discover(
    *,
    start: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[Path]:
    """Return the ordered list of config files to consider.

    Order is highest precedence first. Files that don't exist are
    omitted. ``pyproject.toml`` is only included if it contains a
    ``[tool.seance]`` table.
    """
    cwd = (start or Path.cwd()).resolve()
    hits: list[Path] = []

    # 1. .seancerc.toml — walk up from CWD.
    for d in _walk_up(cwd):
        p = d / CONFIG_FILENAME
        if p.is_file():
            hits.append(p)
            break

    # 2. pyproject.toml with [tool.seance] — walk up from CWD.
    for d in _walk_up(cwd):
        p = d / PYPROJECT_FILENAME
        if p.is_file() and _pyproject_has_seance_table(p):
            hits.append(p)
            break

    # 3. User-global config.
    user_p = user_config_path(env=env)
    if user_p.is_file():
        hits.append(user_p)

    return hits


def _pyproject_has_seance_table(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        # We don't crash on a broken pyproject.toml just because we
        # peeked at it; someone else in the toolchain will complain.
        return False
    tool = data.get("tool")
    return isinstance(tool, dict) and "seance" in tool


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_toml_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigNotFoundError(f"Config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def _extract_seance_table(path: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Given a parsed TOML doc, pull out the seance-relevant section.

    For ``pyproject.toml`` this is ``[tool.seance]``; for anything else
    (``.seancerc.toml``, the user-global file, or an explicit
    ``--config``) the whole document *is* the seance table.
    """
    if path.name == PYPROJECT_FILENAME:
        tool = raw.get("tool")
        if not isinstance(tool, dict):
            return {}
        seance = tool.get("seance")
        if seance is None:
            return {}
        if not isinstance(seance, dict):
            raise ConfigError(
                f"{path}: [tool.seance] must be a table, got {type(seance).__name__}."
            )
        return seance
    return raw


def _normalise_keys(table: dict[str, Any]) -> dict[str, Any]:
    """Apply kebab→snake to top-level keys only.

    Nested tables are handled by the merge step so their key rules can
    diverge in the future.
    """
    return {k.translate(_KEBAB_TO_SNAKE): v for k, v in table.items()}


def _validate_scalars(
    path: Path,
    table: dict[str, Any],
) -> dict[str, Any]:
    """Type-check top-level scalar keys and drop unknown ones with a warning."""
    out: dict[str, Any] = {}
    for key, value in table.items():
        if key in _NESTED_TABLE_KEYS:
            # Nested tables are handled separately.
            continue
        if key not in _TOP_LEVEL_SCALAR_KEYS:
            warnings.warn(
                f"{path}: unknown top-level key {key!r} (ignored). "
                "Known keys: "
                + ", ".join(sorted(_TOP_LEVEL_SCALAR_KEYS | _NESTED_TABLE_KEYS.keys()))
                + ".",
                stacklevel=2,
            )
            continue
        out[key] = _coerce_scalar(path, key, value)
    return out


def _coerce_scalar(path: Path, key: str, value: Any) -> Any:
    """Light per-key type validation.

    We *don't* try to fully mirror Click's converters here — precedence
    at merge time is safer if we keep values as-is where possible and
    let the CLI do the final coercion. But obvious type mismatches (a
    list where a scalar is expected) are caught early.
    """
    if key == "persona":
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'persona' must be a string, got {type(value).__name__}.")
        return value
    if key == "sample":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{path}: 'sample' must be an integer, got {type(value).__name__}.")
        if value < 1:
            raise ConfigError(f"{path}: 'sample' must be >= 1, got {value}.")
        return value
    if key == "fail_on_pii":
        if not isinstance(value, str):
            raise ConfigError(
                f"{path}: 'fail_on_pii' must be a string, got {type(value).__name__}."
            )
        lowered = value.lower()
        if lowered not in {"low", "medium", "high"}:
            raise ConfigError(
                f"{path}: 'fail_on_pii' must be one of low|medium|high, got {value!r}."
            )
        return lowered
    if key == "min_score":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(
                f"{path}: 'min_score' must be an integer, got {type(value).__name__}."
            )
        if not (0 <= value <= 100):
            raise ConfigError(f"{path}: 'min_score' must be between 0 and 100, got {value}.")
        return value
    if key == "no_timeseries":
        if not isinstance(value, bool):
            raise ConfigError(
                f"{path}: 'no_timeseries' must be a boolean, got {type(value).__name__}."
            )
        return value
    # Should be unreachable — _validate_scalars filters unknown keys.
    return value


def _validate_nested(path: Path, table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate the ``[llm]``, ``[pii]``, ``[watch]``, ``[render]`` tables.

    Unknown keys under these tables are hard errors.
    """
    out: dict[str, dict[str, Any]] = {}
    for section, allowed in _NESTED_TABLE_KEYS.items():
        raw = table.get(section)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: [{section}] must be a table, got {type(raw).__name__}.")
        section_out: dict[str, Any] = {}
        for key, value in raw.items():
            snake = key.translate(_KEBAB_TO_SNAKE)
            if snake not in allowed:
                raise ConfigError(
                    f"{path}: unknown key [{section}].{key} — "
                    f"valid keys: {', '.join(sorted(allowed))}."
                )
            section_out[snake] = _coerce_nested(path, section, snake, value)
        out[section] = section_out
    return out


def _coerce_nested(path: Path, section: str, key: str, value: Any) -> Any:
    if section == "llm":
        if key in ("base_url", "model"):
            if not isinstance(value, str):
                raise ConfigError(
                    f"{path}: [llm].{key} must be a string, got {type(value).__name__}."
                )
            return value
    if section == "pii" and key == "name_rules":
        if not isinstance(value, list):
            raise ConfigError(
                f"{path}: [pii].name_rules must be an array of tables, got {type(value).__name__}."
            )
        rules: list[PiiNameRule] = []
        for i, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise ConfigError(
                    f"{path}: [pii].name_rules[{i}] must be a table, got {type(entry).__name__}."
                )
            pattern = entry.get("pattern")
            detector = entry.get("detector")
            confidence = entry.get("confidence", "medium")
            if not isinstance(pattern, str) or not pattern:
                raise ConfigError(
                    f"{path}: [pii].name_rules[{i}].pattern must be a non-empty string."
                )
            if not isinstance(detector, str) or not detector:
                raise ConfigError(
                    f"{path}: [pii].name_rules[{i}].detector must be a non-empty string."
                )
            if not isinstance(confidence, str) or confidence.lower() not in {
                "low",
                "medium",
                "high",
            }:
                raise ConfigError(
                    f"{path}: [pii].name_rules[{i}].confidence must be one of "
                    f"low|medium|high, got {confidence!r}."
                )
            unknown = set(entry.keys()) - {"pattern", "detector", "confidence"}
            if unknown:
                raise ConfigError(
                    f"{path}: [pii].name_rules[{i}] has unknown keys: {', '.join(sorted(unknown))}."
                )
            rules.append(
                PiiNameRule(
                    pattern=pattern,
                    detector=detector.lower(),
                    confidence=confidence.lower(),
                )
            )
        return rules
    if section == "watch":
        if key == "debounce_ms":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConfigError(
                    f"{path}: [watch].debounce_ms must be a non-negative integer, got {value!r}."
                )
            return value
        if key == "poll_interval":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ConfigError(
                    f"{path}: [watch].poll_interval must be a positive number, got {value!r}."
                )
            return float(value)
    if section == "render" and key == "color":
        if not isinstance(value, str) or value.lower() not in {"auto", "always", "never"}:
            raise ConfigError(
                f"{path}: [render].color must be one of auto|always|never, got {value!r}."
            )
        return value.lower()
    return value


# ---------------------------------------------------------------------------
# Loading + merging
# ---------------------------------------------------------------------------


def load(
    *,
    explicit: Path | None = None,
    disable: bool = False,
    start: Path | None = None,
    env: dict[str, str] | None = None,
) -> LoadedConfig:
    """Discover + parse the merged config.

    Parameters
    ----------
    explicit:
        If given, ONLY this file is consulted (``--config PATH``). It
        must exist; otherwise :class:`ConfigNotFoundError` is raised.
    disable:
        If ``True`` (``--no-config``), all discovery is skipped and an
        empty :class:`LoadedConfig` is returned. Useful for CI
        hermeticity.
    start:
        Directory to walk from for discovery. Defaults to CWD.
    env:
        Env dict override (for tests). Defaults to ``os.environ``.

    Notes
    -----
    Never accepts secrets. The ``[llm]`` table intentionally omits
    ``api_key`` — that value only ever comes from the
    ``SEANCE_LLM_API_KEY`` env var. This keeps API keys out of files
    that are easy to accidentally commit.
    """
    if disable:
        return LoadedConfig()

    if explicit is not None:
        explicit = explicit.expanduser()
        if not explicit.is_file():
            raise ConfigNotFoundError(f"Config file not found: {explicit}")
        paths = [explicit]
    else:
        paths = discover(start=start, env=env)

    return _merge_paths(paths)


def _merge_paths(paths: list[Path]) -> LoadedConfig:
    """Merge parsed configs. Earlier entries in *paths* win over later ones."""
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    # We iterate LOWEST precedence first (reversed) so higher-precedence
    # writes overwrite. This keeps the merge logic trivial and mirrors
    # how the CLI overlay works at the top layer.
    for path in reversed(paths):
        raw = _parse_toml_file(path)
        table = _extract_seance_table(path, raw)
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: root must be a TOML table.")
        table = _normalise_keys(table)
        scalars = _validate_scalars(path, table)
        nested = _validate_nested(path, table)
        label = _friendly_source(path)

        for key, value in scalars.items():
            values[key] = value
            sources[key] = label

        for section, section_values in nested.items():
            merged_section = (
                dict(values.get(section, {})) if isinstance(values.get(section), dict) else {}
            )
            for k, v in section_values.items():
                if section == "pii" and k == "name_rules":
                    # name_rules is purely additive: user rules from a
                    # higher-precedence file are appended, not replaced.
                    existing = list(merged_section.get("name_rules", []))
                    existing.extend(v)
                    merged_section["name_rules"] = existing
                else:
                    merged_section[k] = v
                sources[f"{section}.{k}"] = label
            values[section] = merged_section

    return LoadedConfig(
        values=values,
        sources=sources,
        sources_by_path=tuple(_friendly_source(p) for p in paths),
    )


def _friendly_source(path: Path) -> str:
    """Try to make paths readable relative to CWD."""
    try:
        rel = path.resolve().relative_to(Path.cwd().resolve())
        text = f"./{rel}" if not str(rel).startswith(".") else str(rel)
    except ValueError:
        # Not under CWD — collapse the home dir if possible.
        try:
            home = Path.home().resolve()
            resolved = path.resolve()
            if resolved.is_relative_to(home):
                text = "~/" + str(resolved.relative_to(home))
            else:
                text = str(resolved)
        except (OSError, ValueError):
            text = str(path)
    return text
