"""Madame Schema's voice — a small, swappable string library.

Future personas (Skeptic, Pirate, Noir Detective…) will register here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    name: str
    greeting: str
    tagline: str


MADAME_SCHEMA = Persona(
    name="Madame Schema",
    greeting="The veil is thin tonight…",
    tagline="Place your hands on the dataset. The spirits will speak.",
)


def default_persona() -> Persona:
    return MADAME_SCHEMA
