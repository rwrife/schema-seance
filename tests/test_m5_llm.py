"""Tests for the M5 LLM reading path.

Network is never actually hit: we monkeypatch the urlopen call inside
``schema_seance.llm`` so tests run hermetically.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest import mock

import pytest
from click.testing import CliRunner

from schema_seance import llm
from schema_seance.cli import main
from schema_seance.llm import (
    LLMConfig,
    LLMUnavailableError,
    build_messages,
    estimate_cost_usd,
    load_config,
)
from schema_seance.profile import profile as profile_relation
from schema_seance.readers import load

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_report(tmp_path):
    csv = tmp_path / "people.csv"
    csv.write_text(
        "id,email,age\n1,alice@example.com,30\n2,bob@example.com,42\n3,carol@example.com,29\n"
    )
    rel = load(csv)
    return profile_relation(rel, path=csv)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:  # noqa: D401
        return None


def _patch_urlopen(monkeypatch, payload: dict[str, Any], capture: dict[str, Any] | None = None):
    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        if capture is not None:
            capture["url"] = req.full_url
            capture["timeout"] = timeout
            capture["headers"] = dict(req.header_items())
            capture["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(payload)

    monkeypatch.setattr(llm._urlrequest, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_load_config_requires_base_url() -> None:
    with pytest.raises(LLMUnavailableError, match="SEANCE_LLM_BASE_URL"):
        load_config(env={"SEANCE_LLM_MODEL": "gpt-4o-mini"})


def test_load_config_requires_model() -> None:
    with pytest.raises(LLMUnavailableError, match="SEANCE_LLM_MODEL"):
        load_config(env={"SEANCE_LLM_BASE_URL": "https://api.openai.com/v1"})


def test_load_config_happy_path() -> None:
    cfg = load_config(
        env={
            "SEANCE_LLM_BASE_URL": "https://api.openai.com/v1",
            "SEANCE_LLM_API_KEY": "sk-test",
            "SEANCE_LLM_MODEL": "gpt-4o-mini",
        }
    )
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_key == "sk-test"
    assert cfg.endpoint == "https://api.openai.com/v1/chat/completions"


def test_endpoint_handles_trailing_slash_and_existing_path() -> None:
    cfg = LLMConfig(base_url="http://localhost:11434/v1/", api_key=None, model="llama3")
    assert cfg.endpoint == "http://localhost:11434/v1/chat/completions"
    cfg2 = LLMConfig(base_url="https://x.example/v1/chat/completions", api_key=None, model="m")
    assert cfg2.endpoint == "https://x.example/v1/chat/completions"


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------


def test_estimate_cost_known_model() -> None:
    cost = estimate_cost_usd("gpt-4o-mini", 1000, 500)
    # (1 * 0.00015) + (0.5 * 0.0006) = 0.00045
    assert cost == pytest.approx(0.00045, rel=1e-6)


def test_estimate_cost_unknown_model_is_none() -> None:
    assert estimate_cost_usd("llama3:8b", 100, 100) is None


def test_estimate_cost_with_missing_usage_is_none() -> None:
    assert estimate_cost_usd("gpt-4o-mini", None, 50) is None


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def test_build_messages_includes_profile_and_persona(tmp_path) -> None:
    report = _make_report(tmp_path)
    messages = build_messages(report)
    assert messages[0]["role"] == "system"
    assert "Madame Schema" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "email" in messages[1]["content"]


# ---------------------------------------------------------------------------
# llm.read
# ---------------------------------------------------------------------------


def test_read_success(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)
    captured: dict[str, Any] = {}
    _patch_urlopen(
        monkeypatch,
        {
            "choices": [{"message": {"content": "Para 1.\n\nPara 2.\n\nPara 3."}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 120, "total_tokens": 920},
        },
        capture=captured,
    )
    cfg = LLMConfig(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        timeout=5.0,
    )
    result = llm.read(report, cfg)
    assert "Para 1." in result.text
    assert result.prompt_tokens == 800
    assert result.completion_tokens == 120
    assert result.total_tokens == 920
    assert result.cost_usd is not None and result.cost_usd > 0
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-4o-mini"


def test_read_no_api_key_omits_auth_header(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)
    captured: dict[str, Any] = {}
    _patch_urlopen(
        monkeypatch,
        {
            "choices": [{"message": {"content": "ok ok ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        capture=captured,
    )
    cfg = LLMConfig(base_url="http://localhost:11434/v1", api_key=None, model="llama3")
    result = llm.read(report, cfg)
    assert result.text == "ok ok ok"
    # urllib lowercases header names in header_items().
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert "authorization" not in headers_lower
    # Local model -> no cost estimate.
    assert result.cost_usd is None


def test_read_handles_list_content_parts(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)
    _patch_urlopen(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Hello "},
                            {"type": "text", "text": "spirits."},
                        ]
                    }
                }
            ]
        },
    )
    cfg = LLMConfig(base_url="http://x/v1", api_key=None, model="m")
    result = llm.read(report, cfg)
    assert result.text == "Hello spirits."


def test_read_empty_completion_raises(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)
    _patch_urlopen(monkeypatch, {"choices": [{"message": {"content": "   "}}]})
    cfg = LLMConfig(base_url="http://x/v1", api_key=None, model="m")
    with pytest.raises(LLMUnavailableError):
        llm.read(report, cfg)


def test_read_http_error_is_wrapped(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)

    def boom(req, timeout=None):  # noqa: ANN001
        raise llm._urlerror.HTTPError(
            req.full_url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b"nope")
        )

    monkeypatch.setattr(llm._urlrequest, "urlopen", boom)
    cfg = LLMConfig(base_url="http://x/v1", api_key=None, model="m")
    with pytest.raises(LLMUnavailableError, match="503"):
        llm.read(report, cfg)


def test_read_network_error_is_wrapped(monkeypatch, tmp_path) -> None:
    report = _make_report(tmp_path)

    def boom(req, timeout=None):  # noqa: ANN001
        raise llm._urlerror.URLError("unreachable")

    monkeypatch.setattr(llm._urlrequest, "urlopen", boom)
    cfg = LLMConfig(base_url="http://x/v1", api_key=None, model="m")
    with pytest.raises(LLMUnavailableError, match="unreachable"):
        llm.read(report, cfg)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_read_command_without_env_exits_4(tmp_path, monkeypatch) -> None:
    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n")
    monkeypatch.delenv("SEANCE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SEANCE_LLM_MODEL", raising=False)
    result = CliRunner().invoke(main, ["read", str(csv)])
    assert result.exit_code == 4, result.output
    assert "SEANCE_LLM_BASE_URL" in result.output


def test_read_command_happy_path(tmp_path, monkeypatch) -> None:
    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    monkeypatch.setenv("SEANCE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("SEANCE_LLM_MODEL", "llama3")
    monkeypatch.delenv("SEANCE_LLM_API_KEY", raising=False)

    fake = mock.MagicMock(
        return_value=_FakeResponse(
            {
                "choices": [{"message": {"content": "First.\n\nSecond.\n\nThird."}}],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 25,
                    "total_tokens": 75,
                },
            }
        )
    )
    monkeypatch.setattr(llm._urlrequest, "urlopen", fake)

    result = CliRunner().invoke(main, ["read", str(csv), "--no-show-profile"])
    assert result.exit_code == 0, result.output
    assert "First." in result.output
    assert "tokens=75" in result.output
    assert "llama3" in result.output


def test_no_network_unless_read_invoked(tmp_path, monkeypatch) -> None:
    """`summon` must NEVER reach for the LLM module's transport."""
    csv = tmp_path / "x.csv"
    csv.write_text("a,b\n1,2\n")

    def explode(*a, **kw):  # noqa: ANN001
        raise AssertionError("summon must not touch the network")

    monkeypatch.setattr(llm._urlrequest, "urlopen", explode)
    result = CliRunner().invoke(main, ["summon", str(csv)])
    assert result.exit_code == 0, result.output
