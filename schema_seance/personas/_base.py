"""Persona dataclass — kept in its own module to avoid circular imports.

Individual persona modules (``madame``, ``pirate``, …) import :class:`Persona`
from here. :mod:`schema_seance.personas.__init__` then aggregates them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Persona"]


@dataclass(frozen=True)
class Persona:
    """A swappable voice for schema-seance's narrator.

    All output that has personality flows through one of these. Keep
    fields short and Rich-markup-safe; the renderer is responsible for
    panel layout, never for character voice.
    """

    id: str
    display_name: str
    tagline: str
    emoji: str
    greeting_lines: tuple[str, ...]
    llm_system_prompt: str
    reading_title: str = "A Reading"
    refusal_phrase: str = "The veil refuses you."

    @property
    def panel_title(self) -> str:
        """Title to use on Rich panels — emoji + display name."""
        return f"{self.emoji} {self.display_name}"

    @property
    def reading_panel_title(self) -> str:
        """Title for the LLM reading output panel."""
        return f"{self.emoji} {self.reading_title}"

    def greeting_body(self) -> str:
        """Body text for the welcome panel as Rich markup."""
        return "\n".join(self.greeting_lines)
