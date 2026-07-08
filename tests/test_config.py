"""Tests for the ``.seancerc.toml`` config layer."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.config import (
    CONFIG_FILENAME,
    ConfigError,
    ConfigNotFoundError,
    PiiNameRule,
    discover,
    load,
    user_config_path,
)
from schema_seance.pii import CONFIDENCE_BANDS, detect_column

# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discover_walks_up_for_seancerc(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)
    hits = discover(start=sub, env={})
    assert hits == [tmp_path / CONFIG_FILENAME]


def test_discover_finds_pyproject_when_no_seancerc(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.seance]\npersona = "pirate"\n'
    )
    hits = discover(start=tmp_path, env={})
    assert hits == [tmp_path / "pyproject.toml"]


def test_discover_ignores_pyproject_without_tool_seance(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    hits = discover(start=tmp_path, env={})
    assert hits == []


def test_discover_returns_both_when_both_exist(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    (tmp_path / "pyproject.toml").write_text('[tool.seance]\npersona = "pirate"\n')
    hits = discover(start=tmp_path, env={})
    # seancerc first (higher precedence), then pyproject.
    assert hits[0].name == CONFIG_FILENAME
    assert hits[1].name == "pyproject.toml"


def test_user_config_path_respects_xdg(tmp_path: Path) -> None:
    p = user_config_path(env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert p == tmp_path / "schema-seance" / "config.toml"


def test_user_config_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = user_config_path(env={})
    assert p == Path.home() / ".config" / "schema-seance" / "config.toml"


# ---------------------------------------------------------------------------
# load + parse
# ---------------------------------------------------------------------------


def test_load_disable_returns_empty(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    lc = load(disable=True, start=tmp_path)
    assert lc.values == {}
    assert lc.sources == {}
    assert lc.sources_by_path == ()


def test_load_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "custom.toml"
    p.write_text('persona = "pirate"\nsample = 42\n')
    lc = load(explicit=p, start=tmp_path)
    assert lc.values["persona"] == "pirate"
    assert lc.values["sample"] == 42


def test_load_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError):
        load(explicit=tmp_path / "nope.toml")


def test_load_seancerc_walk(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\nsample = 200\n')
    sub = tmp_path / "deep" / "sub"
    sub.mkdir(parents=True)
    lc = load(start=sub)
    assert lc.values["persona"] == "noir"
    assert lc.values["sample"] == 200


def test_pyproject_table_extraction(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.seance]\npersona = "pirate"\n'
    )
    lc = load(start=tmp_path)
    assert lc.values["persona"] == "pirate"
    assert "pyproject.toml" in lc.sources["persona"]


def test_kebab_keys_normalise(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        'fail-on-pii = "high"\nno-timeseries = true\nmin-score = 55\n'
    )
    lc = load(start=tmp_path)
    assert lc.values["fail_on_pii"] == "high"
    assert lc.values["no_timeseries"] is True
    assert lc.values["min_score"] == 55


def test_precedence_seancerc_wins_over_pyproject_and_user(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    (tmp_path / "pyproject.toml").write_text('[tool.seance]\npersona = "pirate"\n')
    # user-global goes elsewhere; simulate via XDG_CONFIG_HOME.
    user_home = tmp_path / "xdg"
    (user_home / "schema-seance").mkdir(parents=True)
    (user_home / "schema-seance" / "config.toml").write_text('persona = "shakespeare"\n')
    lc = load(start=tmp_path, env={"XDG_CONFIG_HOME": str(user_home)})
    assert lc.values["persona"] == "noir"
    # But pyproject's other keys should still surface if not overridden.


def test_lower_priority_fills_in_missing_key(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    (tmp_path / "pyproject.toml").write_text("[tool.seance]\nsample = 999\n")
    lc = load(start=tmp_path)
    assert lc.values["persona"] == "noir"
    assert lc.values["sample"] == 999
    assert lc.sources["sample"].endswith("pyproject.toml")
    assert lc.sources["persona"].endswith(CONFIG_FILENAME)


def test_invalid_toml_reports_path(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("this is = not = valid = toml\n")
    with pytest.raises(ConfigError) as excinfo:
        load(start=tmp_path)
    assert str(tmp_path) in str(excinfo.value)


def test_unknown_top_level_key_warns(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('future_feature = "hi"\n')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        lc = load(start=tmp_path)
    assert any("future_feature" in str(w.message) for w in caught)
    assert "future_feature" not in lc.values


def test_unknown_nested_key_is_hard_error(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('[llm]\noops = "typo"\n')
    with pytest.raises(ConfigError) as excinfo:
        load(start=tmp_path)
    assert "[llm].oops" in str(excinfo.value)


def test_sample_must_be_positive(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("sample = 0\n")
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_fail_on_pii_must_be_a_valid_band(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('fail_on_pii = "extreme"\n')
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_min_score_range(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("min_score = 250\n")
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_pii_name_rules_parse(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        "[pii]\n"
        "name_rules = [\n"
        '  { pattern = "*_ssn", detector = "ssn", confidence = "high" },\n'
        '  { pattern = "email_*", detector = "email" },\n'
        "]\n"
    )
    lc = load(start=tmp_path)
    rules = lc.values["pii"]["name_rules"]
    assert len(rules) == 2
    assert isinstance(rules[0], PiiNameRule)
    assert rules[0].pattern == "*_ssn"
    assert rules[0].detector == "ssn"
    assert rules[0].confidence == "high"
    assert rules[1].confidence == "medium"  # default


def test_pii_name_rules_merge_additively(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        '[pii]\nname_rules = [{ pattern = "top_*", detector = "email" }]\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.seance.pii]\nname_rules = [{ pattern = "bot_*", detector = "phone" }]\n'
    )
    lc = load(start=tmp_path)
    rules = lc.values["pii"]["name_rules"]
    patterns = {r.pattern for r in rules}
    assert patterns == {"top_*", "bot_*"}


def test_pii_name_rules_reject_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        '[pii]\nname_rules = [{ pattern = "*_x", detector = "ssn", oops = 1 }]\n'
    )
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_watch_debounce_ms_type(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("[watch]\ndebounce_ms = -5\n")
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_watch_poll_interval_positive(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("[watch]\npoll_interval = 0\n")
    with pytest.raises(ConfigError):
        load(start=tmp_path)


def test_render_color_choice(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('[render]\ncolor = "rainbow"\n')
    with pytest.raises(ConfigError):
        load(start=tmp_path)


# ---------------------------------------------------------------------------
# pii name-rules integration
# ---------------------------------------------------------------------------


def test_detect_column_applies_name_rules() -> None:
    rules = [PiiNameRule(pattern="user_*", detector="ssn", confidence="high")]
    findings = detect_column("user_id", "BIGINT", ["1", "2", "3"], name_rules=rules)
    assert any(f.kind == "ssn" for f in findings)
    ssn = next(f for f in findings if f.kind == "ssn")
    assert ssn.confidence >= CONFIDENCE_BANDS["high"]


def test_detect_column_rule_does_not_suppress_builtin() -> None:
    rules = [PiiNameRule(pattern="anything*", detector="email", confidence="low")]
    findings = detect_column(
        "anything_here",
        "VARCHAR",
        ["a@b.com", "c@d.com", "e@f.com"],
        name_rules=rules,
    )
    email = next(f for f in findings if f.kind == "email")
    # Built-in email detector on 3/3 real emails scores near 1.0; the
    # user's "low" rule must not drag it down.
    assert email.confidence >= CONFIDENCE_BANDS["medium"]


def test_detect_column_no_rules_backward_compatible() -> None:
    # Positional-only args still work — no name_rules kwarg.
    findings = detect_column("email", "VARCHAR", ["a@b.com"])
    assert any(f.kind == "email" for f in findings)


# ---------------------------------------------------------------------------
# CLI precedence + `seance config`
# ---------------------------------------------------------------------------


def _write_csv(path: Path) -> Path:
    csv = path / "ghosts.csv"
    csv.write_text("id,name\n1,alice\n2,bob\n")
    return csv


def test_cli_uses_config_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "pirate"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    # No subcommand => greeting panel prints and shows the persona.
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert "Cap'n Schema" in result.output


def test_cli_flag_overrides_config_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "pirate"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    result = CliRunner().invoke(main, ["--persona", "madame"])
    assert result.exit_code == 0, result.output
    assert "Madame Schema" in result.output
    assert "Cap'n" not in result.output


def test_cli_env_overrides_config_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "pirate"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEANCE_PERSONA", "noir")
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert "Detective Schema" in result.output
    assert "Cap'n" not in result.output


def test_cli_no_config_disables_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "pirate"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    result = CliRunner().invoke(main, ["--no-config"])
    assert result.exit_code == 0, result.output
    # Default persona is Madame, not pirate.
    assert "Cap'n Schema" not in result.output
    assert "Madame Schema" in result.output


def test_cli_explicit_config_skips_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "pirate"\n')
    forced = tmp_path / "forced.toml"
    forced.write_text('persona = "noir"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    result = CliRunner().invoke(main, ["--config", str(forced)])
    assert result.exit_code == 0, result.output
    assert "Detective Schema" in result.output
    assert "Cap'n" not in result.output


def test_cli_summon_uses_config_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("sample = 500\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    csv = _write_csv(tmp_path)
    result = CliRunner().invoke(main, ["summon", str(csv)])
    assert result.exit_code == 0, result.output
    # "Sampled first 500 rows" only prints when --sample is applied.
    assert "500" in result.output


def test_cli_invalid_config_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('[llm]\noops = "typo"\n')
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code != 0
    assert "unknown key" in result.output.lower()


def test_config_command_shows_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\nsample = 200\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 0, result.output
    assert "persona" in result.output
    assert CONFIG_FILENAME in result.output


def test_config_command_masks_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('persona = "noir"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEANCE_LLM_API_KEY", "sk-very-secret")
    result = CliRunner().invoke(main, ["config", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["llm.api_key"]["value"] == "***"
    assert "sk-very-secret" not in result.output


def test_config_command_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        'persona = "noir"\nsample = 200\n[pii]\n'
        'name_rules = [{ pattern = "*_ssn", detector = "ssn", confidence = "high" }]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    result = CliRunner().invoke(main, ["config", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["persona"]["value"] == "noir"
    assert data["sample"]["value"] == 200
    rules_entry = data["pii.name_rules"]
    assert rules_entry["value"][0]["pattern"] == "*_ssn"


def test_config_command_no_files_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SEANCE_PERSONA", raising=False)
    monkeypatch.delenv("SEANCE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SEANCE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SEANCE_LLM_MODEL", raising=False)
    result = CliRunner().invoke(main, ["config", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {}
