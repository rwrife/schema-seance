"""Backwards-compatible shim around the persona-pack registry.

Originally this module hard-coded Madame Schema's voice. As of the
persona-packs feature it delegates to :mod:`schema_seance.personas`,
which is the new home for swappable voices. The names below are kept
so existing imports (and tests) continue to work.
"""

from __future__ import annotations

from .personas import DEFAULT_PERSONA_ID, PERSONAS

_DEFAULT = PERSONAS[DEFAULT_PERSONA_ID]

GREETING_TITLE = _DEFAULT.display_name
GREETING_TAGLINE = _DEFAULT.tagline
GREETING_LINES: tuple[str, ...] = _DEFAULT.greeting_lines


def greeting_panel_body() -> str:
    """Return the default-persona greeting body as Rich markup."""
    return _DEFAULT.greeting_body()
