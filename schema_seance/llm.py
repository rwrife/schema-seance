"""Optional LLM "reading" of a profile — M5.

Madame Schema, given a finished :class:`ProfileReport`, can ask an
OpenAI-compatible chat API to produce a 3-paragraph reading of the data.

The transport is deliberately tiny: we POST to ``{base_url}/chat/completions``
with the standard OpenAI request shape. That covers OpenAI itself, Ollama
(``http://localhost:11434/v1``), vLLM, LM Studio, llama.cpp's server, and
most other compatible providers.

Network is **strictly opt-in**: nothing in this module runs unless
:func:`read` is called explicitly from the ``seance read`` command.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as _urlerror
from urllib import request as _urlrequest

from .personas import DEFAULT_PERSONA_ID, PERSONAS, Persona
from .profile import ProfileReport
from .render.json import report_to_dict

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LLMConfig",
    "LLMResult",
    "LLMUnavailableError",
    "build_messages",
    "estimate_cost_usd",
    "load_config",
    "read",
]

DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_PROFILE_BYTES = 24_000  # keep prompts tractable on small local models


def _system_prompt(persona: Persona | None) -> str:
    """Return the persona's LLM system prompt, defaulting to Madame Schema."""
    if persona is None:
        persona = PERSONAS[DEFAULT_PERSONA_ID]
    return persona.llm_system_prompt


# Backwards-compatible default for callers that imported the constant.
_SYSTEM_PROMPT = PERSONAS[DEFAULT_PERSONA_ID].llm_system_prompt

# Rough USD/1k-token rates for a couple of common OpenAI models. Anything not
# in this table is treated as free (local) and we just print token counts.
_PRICE_TABLE_USD_PER_1K: dict[str, tuple[float, float]] = {
    # model: (prompt, completion)
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
}


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM call cannot be made or fails after retries."""


@dataclass(frozen=True)
class LLMConfig:
    """Resolved LLM provider configuration.

    All three fields come from environment variables by default; see
    :func:`load_config`.
    """

    base_url: str
    api_key: str | None
    model: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


@dataclass(frozen=True)
class LLMResult:
    """Outcome of a successful LLM reading."""

    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    elapsed_seconds: float


def load_config(
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> LLMConfig:
    """Resolve provider config from env vars.

    Recognised:
      * ``SEANCE_LLM_BASE_URL`` — required (e.g. ``https://api.openai.com/v1``)
      * ``SEANCE_LLM_API_KEY`` — optional (local providers often skip it)
      * ``SEANCE_LLM_MODEL`` — required
    """
    e = env if env is not None else os.environ
    base_url = e.get("SEANCE_LLM_BASE_URL", "").strip()
    api_key = (e.get("SEANCE_LLM_API_KEY") or "").strip() or None
    model = e.get("SEANCE_LLM_MODEL", "").strip()
    if not base_url:
        raise LLMUnavailableError(
            "SEANCE_LLM_BASE_URL is not set. Point it at an OpenAI-compatible "
            "endpoint, e.g. https://api.openai.com/v1 or http://localhost:11434/v1."
        )
    if not model:
        raise LLMUnavailableError(
            "SEANCE_LLM_MODEL is not set. Choose a model id available on your provider."
        )
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout,
    )


def _trim_profile_for_prompt(report: ProfileReport) -> str:
    """Serialize the profile, trimming heavy fields if it gets too large."""
    data = report_to_dict(report)
    text = json.dumps(data, default=str)
    if len(text) <= _MAX_PROFILE_BYTES:
        return text
    # Trim per-column ``top`` lists and ``sample`` values, which tend to dominate.
    for col in data.get("columns", []):
        if isinstance(col.get("top"), list):
            col["top"] = col["top"][:3]
        if isinstance(col.get("sample"), list):
            col["sample"] = col["sample"][:3]
    text = json.dumps(data, default=str)
    if len(text) <= _MAX_PROFILE_BYTES:
        return text
    # Last resort: drop ``top`` entirely.
    for col in data.get("columns", []):
        col.pop("top", None)
    return json.dumps(data, default=str)[:_MAX_PROFILE_BYTES]


def build_messages(report: ProfileReport, persona: Persona | None = None) -> list[dict[str, str]]:
    """Build the chat messages for a reading.

    Exposed so tests (and other callers) can inspect the prompt without
    making a network call. ``persona`` defaults to Madame Schema for
    backwards compatibility.
    """
    profile_json = _trim_profile_for_prompt(report)
    user = (
        "Here is the profile JSON for the dataset. Read it and respond with "
        "your three paragraphs as instructed.\n\n"
        f"```json\n{profile_json}\n```"
    )
    return [
        {"role": "system", "content": _system_prompt(persona)},
        {"role": "user", "content": user},
    ]


def estimate_cost_usd(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> float | None:
    """Best-effort USD cost estimate for known OpenAI models. Local = ``None``."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    rates = _PRICE_TABLE_USD_PER_1K.get(model)
    if rates is None:
        # Allow a loose match (e.g. dated snapshots) — first prefix wins.
        for known, known_rates in _PRICE_TABLE_USD_PER_1K.items():
            if model.startswith(known):
                rates = known_rates
                break
    if rates is None:
        return None
    p_in, p_out = rates
    return round((prompt_tokens / 1000.0) * p_in + (completion_tokens / 1000.0) * p_out, 6)


def _post_chat(config: LLMConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.6,
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    req = _urlrequest.Request(config.endpoint, data=body, headers=headers, method="POST")
    try:
        with _urlrequest.urlopen(req, timeout=config.timeout) as resp:  # noqa: S310
            raw = resp.read()
    except _urlerror.HTTPError as exc:  # pragma: no cover - exercised via mock
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001
            detail = ""
        raise LLMUnavailableError(
            f"Provider returned HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except _urlerror.URLError as exc:  # pragma: no cover - exercised via mock
        raise LLMUnavailableError(f"Could not reach provider: {exc.reason}") from exc
    except TimeoutError as exc:  # pragma: no cover
        raise LLMUnavailableError(f"Provider timed out after {config.timeout:.0f}s.") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LLMUnavailableError("Provider returned non-JSON response.") from exc


def _extract_text(response: dict[str, Any]) -> str:
    try:
        choice = response["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailableError("Provider response had no choices.") from exc
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        # Some providers return a list of content parts.
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailableError("Provider returned an empty completion.")
    return content.strip()


def read(
    report: ProfileReport,
    config: LLMConfig,
    *,
    persona: Persona | None = None,
) -> LLMResult:
    """Call the LLM and return a structured result. Raises on any failure."""
    messages = build_messages(report, persona=persona)
    started = time.monotonic()
    response = _post_chat(config, messages)
    elapsed = time.monotonic() - started

    text = _extract_text(response)
    usage = response.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    cost = estimate_cost_usd(config.model, prompt_tokens, completion_tokens)

    return LLMResult(
        text=text,
        model=config.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost,
        elapsed_seconds=elapsed,
    )
