"""MCP (Model Context Protocol) server mode — issue #10.

Exposes the core ``summon`` and ``read`` operations as MCP tools over stdio,
so agents (Claude Desktop, Cursor, Continue, etc.) can profile a data file
on demand.

This is a small, dependency-free implementation of the slice of the MCP spec
we actually need:

* JSON-RPC 2.0 framing over line-delimited JSON on stdin/stdout (one
  request/response per line). Real clients also accept LSP-style
  ``Content-Length`` framing; we accept that on input but emit line-delimited
  output, which Claude Desktop and the official ``mcp`` Python SDK both
  accept as a fallback.
* Methods: ``initialize``, ``tools/list``, ``tools/call``, plus the standard
  ``notifications/initialized`` no-op and ``ping``.
* Two tools: ``summon`` (offline profile) and ``read`` (profile + LLM
  reading, requires the usual ``SEANCE_LLM_*`` env vars).

The handler functions are exported and unit-tested directly; the
:func:`serve` entrypoint just wires stdio to :func:`handle_request`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from . import __version__
from .llm import LLMUnavailableError
from .llm import load_config as load_llm_config
from .llm import read as llm_read
from .personas import PERSONAS, UnknownPersonaError
from .personas import resolve as resolve_persona
from .pii import CONFIDENCE_BANDS
from .profile import profile as profile_relation
from .readers import SQLiteTableError, UnsupportedFormatError, load
from .render.json import report_to_dict

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "TOOLS",
    "handle_request",
    "list_tools",
    "serve",
    "tool_read",
    "tool_summon",
]

# Latest spec version we've validated against. Clients may negotiate down.
MCP_PROTOCOL_VERSION = "2025-06-18"

_SERVER_INFO = {"name": "schema-seance", "version": __version__}

# JSON-RPC error codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Tool definitions (declarative; consumed by tools/list and tools/call).
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "summon",
        "description": (
            "Profile a data file (CSV, TSV, JSONL, NDJSON, Parquet, or SQLite) "
            "and return its schema, per-column statistics, PII findings, and "
            "anomalies as JSON. Offline — no network access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the data file.",
                },
                "table": {
                    "type": "string",
                    "description": "Table name (SQLite only). Defaults to the first table.",
                },
                "sample": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Profile only the first N rows.",
                },
                "fail_on_pii": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": (
                        "Mark the response as an error when any PII finding "
                        "meets or exceeds this confidence band."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read",
        "description": (
            "Profile a data file and ask the configured LLM for a short "
            "narrative reading. Requires SEANCE_LLM_BASE_URL and "
            "SEANCE_LLM_MODEL environment variables (and SEANCE_LLM_API_KEY "
            "for hosted providers)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "table": {"type": "string"},
                "sample": {"type": "integer", "minimum": 1},
                "timeout": {
                    "type": "number",
                    "minimum": 1.0,
                    "description": "Hard timeout in seconds for the LLM call.",
                },
                "persona": {
                    "type": "string",
                    "enum": sorted(PERSONAS.keys()),
                    "description": "Narrator voice. Defaults to 'madame'.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


def list_tools() -> list[dict[str, Any]]:
    """Return the tool catalog for ``tools/list``."""
    return list(TOOLS)


# ---------------------------------------------------------------------------
# Tool implementations. Each returns a plain dict suitable for JSON
# serialization; the caller wraps it in the MCP ``content`` envelope.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ToolError(Exception):
    """Internal: a tool failed in a user-visible way (no traceback needed)."""

    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _profile_payload(
    path: str,
    *,
    table: str | None,
    sample: int | None,
):
    p = Path(path)
    if not p.exists():
        raise _ToolError(f"File not found: {path}")
    if not p.is_file():
        raise _ToolError(f"Not a regular file: {path}")
    try:
        relation = load(p, table=table)
    except UnsupportedFormatError as exc:
        raise _ToolError(f"Unsupported format: {exc}") from exc
    except SQLiteTableError as exc:
        raise _ToolError(f"SQLite table problem: {exc}") from exc
    report = profile_relation(relation, path=p, sample=sample)
    return report_to_dict(report), report


def tool_summon(args: dict[str, Any]) -> dict[str, Any]:
    """Execute the ``summon`` tool. Returns a structured payload."""
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise _ToolError("'path' is required and must be a non-empty string.")
    table = args.get("table")
    if table is not None and not isinstance(table, str):
        raise _ToolError("'table' must be a string when provided.")
    sample = args.get("sample")
    if sample is not None and (not isinstance(sample, int) or sample < 1):
        raise _ToolError("'sample' must be a positive integer when provided.")
    fail_on_pii = args.get("fail_on_pii")
    if fail_on_pii is not None and fail_on_pii not in CONFIDENCE_BANDS:
        raise _ToolError(f"'fail_on_pii' must be one of {sorted(CONFIDENCE_BANDS)}.")

    profile, report = _profile_payload(path, table=table, sample=sample)
    payload: dict[str, Any] = {"profile": profile}

    if fail_on_pii is not None:
        threshold = CONFIDENCE_BANDS[fail_on_pii]
        worst = 0.0
        worst_kind: str | None = None
        worst_col: str | None = None
        for col in report.columns:
            for finding in col.pii:
                if finding.confidence > worst:
                    worst = finding.confidence
                    worst_kind = finding.kind
                    worst_col = col.name
        if worst >= threshold:
            payload["pii_breach"] = {
                "threshold": fail_on_pii,
                "confidence": worst,
                "kind": worst_kind,
                "column": worst_col,
            }
    return payload


def tool_read(args: dict[str, Any]) -> dict[str, Any]:
    """Execute the ``read`` tool. Returns profile + LLM reading."""
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise _ToolError("'path' is required and must be a non-empty string.")
    table = args.get("table")
    if table is not None and not isinstance(table, str):
        raise _ToolError("'table' must be a string when provided.")
    sample = args.get("sample")
    if sample is not None and (not isinstance(sample, int) or sample < 1):
        raise _ToolError("'sample' must be a positive integer when provided.")
    timeout = args.get("timeout")
    if timeout is not None and (not isinstance(timeout, int | float) or timeout < 1.0):
        raise _ToolError("'timeout' must be a number >= 1.0 when provided.")
    persona_name = args.get("persona")
    if persona_name is not None and not isinstance(persona_name, str):
        raise _ToolError("'persona' must be a string when provided.")

    try:
        persona = resolve_persona(persona_name)
    except UnknownPersonaError as exc:
        raise _ToolError(str(exc)) from exc

    profile, report = _profile_payload(path, table=table, sample=sample)

    try:
        config = load_llm_config(timeout=float(timeout) if timeout is not None else None)
    except LLMUnavailableError as exc:
        raise _ToolError(f"LLM config unavailable: {exc}") from exc

    try:
        result = llm_read(report, config, persona=persona)
    except LLMUnavailableError as exc:
        raise _ToolError(f"LLM call failed: {exc}") from exc

    return {
        "profile": profile,
        "reading": {
            "text": result.text,
            "model": result.model,
            "persona": persona.id,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": result.cost_usd,
            "elapsed_seconds": result.elapsed_seconds,
        },
    }


_TOOL_HANDLERS = {
    "summon": tool_summon,
    "read": tool_read,
}


# ---------------------------------------------------------------------------
# JSON-RPC framing helpers.
# ---------------------------------------------------------------------------


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _tool_content(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    """Wrap a tool payload in the MCP ``content`` envelope."""
    text = json.dumps(payload, default=str, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
        # Newer clients consume ``structuredContent`` directly.
        "structuredContent": payload,
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns ``None`` for notifications."""
    if not isinstance(request, dict):
        return _err(None, _INVALID_REQUEST, "Request must be a JSON object.")
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}
    is_notification = "id" not in request

    if not isinstance(method, str):
        return None if is_notification else _err(req_id, _INVALID_REQUEST, "Missing method.")

    # Notifications: ignore but don't error.
    if is_notification:
        # notifications/initialized, notifications/cancelled, etc.
        return None

    if method == "initialize":
        client_proto = params.get("protocolVersion") if isinstance(params, dict) else None
        proto = client_proto if isinstance(client_proto, str) else MCP_PROTOCOL_VERSION
        return _ok(
            req_id,
            {
                "protocolVersion": proto,
                "serverInfo": _SERVER_INFO,
                "capabilities": {"tools": {"listChanged": False}},
            },
        )

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": list_tools()})

    if method == "tools/call":
        if not isinstance(params, dict):
            return _err(req_id, _INVALID_PARAMS, "params must be an object.")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _err(req_id, _INVALID_PARAMS, "'name' is required.")
        if not isinstance(arguments, dict):
            return _err(req_id, _INVALID_PARAMS, "'arguments' must be an object.")
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return _err(req_id, _METHOD_NOT_FOUND, f"Unknown tool: {name}")
        try:
            payload = handler(arguments)
        except _ToolError as exc:
            return _ok(req_id, _tool_content({"error": str(exc)}, is_error=True))
        except Exception as exc:  # pragma: no cover - defensive
            return _err(req_id, _INTERNAL_ERROR, f"Tool crashed: {exc}")
        return _ok(req_id, _tool_content(payload))

    return _err(req_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")


# ---------------------------------------------------------------------------
# stdio loop.
# ---------------------------------------------------------------------------


def _read_message(stream: IO[str]) -> str | None:
    """Read one JSON-RPC message. Supports both line-delimited and
    LSP-style ``Content-Length`` framing on input."""
    first = stream.readline()
    if not first:
        return None
    stripped = first.strip()
    if not stripped:
        # Skip blank lines (can appear between Content-Length blocks).
        return _read_message(stream)
    if stripped.lower().startswith("content-length:"):
        try:
            length = int(stripped.split(":", 1)[1].strip())
        except ValueError:
            return None
        # Consume headers up to blank line.
        while True:
            line = stream.readline()
            if not line or line.strip() == "":
                break
        return stream.read(length)
    return stripped


def serve(stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    """Run the MCP stdio loop until EOF."""
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    while True:
        raw = _read_message(inp)
        if raw is None:
            return
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = _err(None, _PARSE_ERROR, f"Parse error: {exc}")
        else:
            response = handle_request(request)
        if response is None:
            continue
        out.write(json.dumps(response) + "\n")
        out.flush()
