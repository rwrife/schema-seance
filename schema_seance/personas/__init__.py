"""Persona packs — swappable voices for the narrator.

Each persona is a pure string-template pack: no logic, no I/O, no side
effects. Add a new persona by dropping a module under this package and
registering it in :data:`PERSONAS`.

Resolution order, highest precedence first:

1. Explicit name passed to :func:`resolve` (e.g. from a ``--persona`` CLI flag).
2. ``SEANCE_PERSONA`` environment variable.
3. :data:`DEFAULT_PERSONA_ID` (``"madame"``), which preserves prior behaviour.

The :class:`Persona` dataclass is the entire contract — render layers,
the CLI, and the LLM module read from it and never hard-code voice text.
"""

from __future__ import annotations

import os

from . import corporate, madame, noir, pirate, shakespeare, skeptic
from ._base import Persona

__all__ = [
    "DEFAULT_PERSONA_ID",
    "PERSONAS",
    "Persona",
    "UnknownPersonaError",
    "available_ids",
    "get",
    "resolve",
]

DEFAULT_PERSONA_ID = "madame"


class UnknownPersonaError(ValueError):
    """Raised when the requested persona id is not registered."""


PERSONAS: dict[str, Persona] = {
    madame.PERSONA.id: madame.PERSONA,
    skeptic.PERSONA.id: skeptic.PERSONA,
    pirate.PERSONA.id: pirate.PERSONA,
    noir.PERSONA.id: noir.PERSONA,
    corporate.PERSONA.id: corporate.PERSONA,
    shakespeare.PERSONA.id: shakespeare.PERSONA,
}


def available_ids() -> tuple[str, ...]:
    """Sorted tuple of registered persona ids, default first."""
    others = sorted(pid for pid in PERSONAS if pid != DEFAULT_PERSONA_ID)
    return (DEFAULT_PERSONA_ID, *others)


def get(persona_id: str) -> Persona:
    """Look up a persona by id (case-insensitive).

    Raises :class:`UnknownPersonaError` if not registered.
    """
    key = (persona_id or "").strip().lower()
    if not key:
        raise UnknownPersonaError("Persona id is empty.")
    try:
        return PERSONAS[key]
    except KeyError as exc:
        choices = ", ".join(available_ids())
        raise UnknownPersonaError(
            f"Unknown persona {persona_id!r}. Choose one of: {choices}."
        ) from exc


def resolve(
    explicit: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Persona:
    """Resolve a :class:`Persona` from CLI flag, env, or the default.

    ``explicit`` wins; otherwise we consult ``SEANCE_PERSONA``; otherwise
    we fall back to :data:`DEFAULT_PERSONA_ID`.
    """
    if explicit:
        return get(explicit)
    e = env if env is not None else os.environ
    env_choice = (e.get("SEANCE_PERSONA") or "").strip()
    if env_choice:
        return get(env_choice)
    return PERSONAS[DEFAULT_PERSONA_ID]
