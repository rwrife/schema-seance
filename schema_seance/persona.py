"""Madame Schema's voice — a small bank of in-character strings.

Kept deliberately tiny for M1. Future milestones add persona packs
(see PLAN.md §8). All output passes through here so swapping voices later
is a one-file change.
"""

from __future__ import annotations

GREETING_TITLE = "Madame Schema"
GREETING_TAGLINE = "Medium of Messy Data"

GREETING_LINES: tuple[str, ...] = (
    "The parlor is dim. The candle gutters. Place your dataset on the velvet.",
    "I sense… columns. Yes. Many columns. Some of them are lying to you.",
    "Summon a file with [bold]seance summon <path>[/bold] and we shall begin.",
)


def greeting_panel_body() -> str:
    """Return the body text for the welcome panel as Rich markup."""
    return "\n".join(GREETING_LINES)
