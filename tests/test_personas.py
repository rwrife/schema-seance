"""Tests for the persona-pack registry and the CLI's --persona flag."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.personas import (
    DEFAULT_PERSONA_ID,
    PERSONAS,
    Persona,
    UnknownPersonaError,
    available_ids,
    get,
    resolve,
)

# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


EXPECTED_IDS = {"madame", "skeptic", "pirate", "noir", "corporate", "shakespeare"}


def test_registry_has_expected_personas() -> None:
    assert set(PERSONAS) == EXPECTED_IDS


def test_default_is_madame() -> None:
    assert DEFAULT_PERSONA_ID == "madame"
    assert available_ids()[0] == "madame"


def test_available_ids_are_sorted_after_default() -> None:
    ids = available_ids()
    assert ids[0] == DEFAULT_PERSONA_ID
    assert list(ids[1:]) == sorted(ids[1:])


def test_each_persona_has_required_fields() -> None:
    for pid, persona in PERSONAS.items():
        assert isinstance(persona, Persona)
        assert persona.id == pid
        assert persona.display_name
        assert persona.tagline
        assert persona.emoji
        assert persona.greeting_lines
        assert "EXACTLY three" in persona.llm_system_prompt
        # System prompts must reference all three required sections
        for keyword in ("paragraphs", "PII", "next"):
            assert keyword in persona.llm_system_prompt, f"{pid} system prompt missing '{keyword}'"


def test_panel_titles_include_emoji_and_name() -> None:
    p = PERSONAS["pirate"]
    assert p.panel_title == f"{p.emoji} {p.display_name}"
    assert p.reading_panel_title.endswith(p.reading_title)


# ---------------------------------------------------------------------------
# get / resolve
# ---------------------------------------------------------------------------


def test_get_is_case_insensitive() -> None:
    assert get("PIRATE") is PERSONAS["pirate"]
    assert get("  Noir  ") is PERSONAS["noir"]


def test_get_unknown_raises_with_choices() -> None:
    with pytest.raises(UnknownPersonaError) as excinfo:
        get("bogus")
    msg = str(excinfo.value)
    assert "bogus" in msg
    for pid in EXPECTED_IDS:
        assert pid in msg


def test_resolve_explicit_wins() -> None:
    p = resolve("skeptic", env={"SEANCE_PERSONA": "pirate"})
    assert p.id == "skeptic"


def test_resolve_env_when_no_explicit() -> None:
    p = resolve(None, env={"SEANCE_PERSONA": "noir"})
    assert p.id == "noir"


def test_resolve_default_when_nothing_set() -> None:
    p = resolve(None, env={})
    assert p.id == DEFAULT_PERSONA_ID


def test_resolve_blank_env_falls_through() -> None:
    p = resolve(None, env={"SEANCE_PERSONA": "   "})
    assert p.id == DEFAULT_PERSONA_ID


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_personas_command_lists_all() -> None:
    result = CliRunner().invoke(main, ["personas"])
    assert result.exit_code == 0, result.output
    for pid in EXPECTED_IDS:
        assert pid in result.output


def test_persona_flag_changes_greeting() -> None:
    result = CliRunner().invoke(main, ["--persona", "pirate"])
    assert result.exit_code == 0, result.output
    assert "Cap'n Schema" in result.output
    assert "Madame Schema" not in result.output


def test_persona_env_changes_greeting(monkeypatch) -> None:
    monkeypatch.setenv("SEANCE_PERSONA", "noir")
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert "Detective Schema" in result.output


def test_persona_flag_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("SEANCE_PERSONA", "noir")
    result = CliRunner().invoke(main, ["--persona", "shakespeare"])
    assert result.exit_code == 0, result.output
    assert "Bard of Schema" in result.output
    assert "Detective" not in result.output


def test_persona_flag_rejects_unknown() -> None:
    result = CliRunner().invoke(main, ["--persona", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in result.output.lower() or "invalid" in result.output.lower()


def test_summon_uses_persona_refusal_phrase(tmp_path) -> None:
    csv = tmp_path / "leaky.csv"
    csv.write_text("id,email\n1,alice@example.com\n2,bob@example.com\n")
    result = CliRunner().invoke(
        main,
        ["--persona", "skeptic", "summon", str(csv), "--fail-on-pii", "low"],
    )
    assert result.exit_code == 3, result.output
    # Skeptic's refusal phrase, not the default Madame Schema one
    assert "evidence does not warrant" in result.output


# ---------------------------------------------------------------------------
# LLM wiring
# ---------------------------------------------------------------------------


def test_build_messages_uses_persona_system_prompt(tmp_path) -> None:
    from schema_seance.llm import build_messages
    from schema_seance.profile import profile as profile_relation
    from schema_seance.readers import load

    csv = tmp_path / "x.csv"
    csv.write_text("id,name\n1,Alice\n2,Bob\n")
    report = profile_relation(load(csv), path=csv)

    pirate_msgs = build_messages(report, persona=PERSONAS["pirate"])
    assert "Cap'n Schema" in pirate_msgs[0]["content"]

    # Default (no persona arg) stays as Madame Schema for back-compat
    default_msgs = build_messages(report)
    assert "Madame Schema" in default_msgs[0]["content"]
