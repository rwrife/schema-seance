"""Tests for the MCP stdio server (issue #10).

We never spawn a real subprocess: ``handle_request`` is exercised directly,
and ``serve`` is driven against in-memory ``StringIO`` streams.
"""

from __future__ import annotations

import io
import json
from unittest import mock

import pytest

from schema_seance import llm, mcp
from schema_seance.llm import LLMResult


def _csv(tmp_path):
    p = tmp_path / "people.csv"
    p.write_text(
        "id,email,age\n1,alice@example.com,30\n2,bob@example.com,42\n3,carol@example.com,29\n"
    )
    return p


# ---------------------------------------------------------------------------
# Protocol basics
# ---------------------------------------------------------------------------


def test_initialize_returns_server_info_and_protocol_version():
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "schema-seance"
    assert "tools" in result["capabilities"]


def test_initialize_echoes_client_protocol_version():
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_ping_returns_empty_result():
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": "p", "method": "ping"})
    assert resp == {"jsonrpc": "2.0", "id": "p", "result": {}}


def test_unknown_method_returns_method_not_found():
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert resp["error"]["code"] == -32601


def test_notifications_get_no_response():
    # No "id" => notification per JSON-RPC 2.0.
    resp = mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


def test_tools_list_exposes_summon_and_read():
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"summon", "read"}
    summon = next(t for t in resp["result"]["tools"] if t["name"] == "summon")
    assert summon["inputSchema"]["required"] == ["path"]
    assert "path" in summon["inputSchema"]["properties"]


# ---------------------------------------------------------------------------
# summon tool
# ---------------------------------------------------------------------------


def test_summon_returns_profile_for_csv(tmp_path):
    csv = _csv(tmp_path)
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "summon", "arguments": {"path": str(csv)}},
        }
    )
    result = resp["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert "profile" in structured
    assert structured["profile"]["rows"] == 3
    cols = {c["name"] for c in structured["profile"]["columns"]}
    assert {"id", "email", "age"} <= cols
    # The text content is parseable JSON matching the structured payload.
    text_payload = json.loads(result["content"][0]["text"])
    assert text_payload == structured


def test_summon_missing_path_is_tool_error(tmp_path):
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "summon", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "path" in resp["result"]["structuredContent"]["error"]


def test_summon_nonexistent_file_is_tool_error(tmp_path):
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "summon",
                "arguments": {"path": str(tmp_path / "ghost.csv")},
            },
        }
    )
    assert resp["result"]["isError"] is True
    assert "not found" in resp["result"]["structuredContent"]["error"].lower()


def test_summon_fail_on_pii_flags_breach(tmp_path):
    csv = _csv(tmp_path)
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "summon",
                "arguments": {"path": str(csv), "fail_on_pii": "low"},
            },
        }
    )
    structured = resp["result"]["structuredContent"]
    assert "pii_breach" in structured
    assert structured["pii_breach"]["column"] == "email"


def test_summon_rejects_invalid_fail_on_pii(tmp_path):
    csv = _csv(tmp_path)
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "summon",
                "arguments": {"path": str(csv), "fail_on_pii": "extreme"},
            },
        }
    )
    assert resp["result"]["isError"] is True


def test_summon_unknown_tool_is_method_not_found():
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "exorcise", "arguments": {}},
        }
    )
    assert resp["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# read tool (LLM path; network is mocked at the llm.read seam)
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("SEANCE_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("SEANCE_LLM_API_KEY", "sk-fake")
    monkeypatch.setenv("SEANCE_LLM_MODEL", "gpt-4o-mini")


def test_read_returns_profile_and_reading(tmp_path, llm_env):
    csv = _csv(tmp_path)
    fake = LLMResult(
        text="The spirits whisper of emails.",
        model="gpt-4o-mini",
        prompt_tokens=120,
        completion_tokens=40,
        total_tokens=160,
        cost_usd=0.0001,
        elapsed_seconds=0.42,
    )
    with mock.patch.object(mcp, "llm_read", return_value=fake) as patched:
        resp = mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "read",
                    "arguments": {"path": str(csv), "persona": "skeptic"},
                },
            }
        )
        assert patched.called
        passed_persona = patched.call_args.kwargs["persona"]
        assert passed_persona.id == "skeptic"

    result = resp["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["reading"]["text"] == "The spirits whisper of emails."
    assert structured["reading"]["model"] == "gpt-4o-mini"
    assert structured["reading"]["persona"] == "skeptic"
    assert structured["profile"]["rows"] == 3


def test_read_without_llm_env_returns_tool_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SEANCE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SEANCE_LLM_MODEL", raising=False)
    csv = _csv(tmp_path)
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read", "arguments": {"path": str(csv)}},
        }
    )
    assert resp["result"]["isError"] is True
    err = resp["result"]["structuredContent"]["error"].lower()
    assert "llm" in err


def test_read_propagates_llm_failure_as_tool_error(tmp_path, llm_env):
    csv = _csv(tmp_path)
    with mock.patch.object(mcp, "llm_read", side_effect=llm.LLMUnavailableError("provider 503")):
        resp = mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "read", "arguments": {"path": str(csv)}},
            }
        )
    assert resp["result"]["isError"] is True
    assert "provider 503" in resp["result"]["structuredContent"]["error"]


def test_read_unknown_persona_is_tool_error(tmp_path, llm_env):
    csv = _csv(tmp_path)
    resp = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {"path": str(csv), "persona": "ghost"},
            },
        }
    )
    assert resp["result"]["isError"] is True


# ---------------------------------------------------------------------------
# stdio loop
# ---------------------------------------------------------------------------


def test_serve_handles_line_delimited_messages(tmp_path):
    csv = _csv(tmp_path)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "summon", "arguments": {"path": str(csv)}},
        },
    ]
    stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    stdout = io.StringIO()
    mcp.serve(stdin=stdin, stdout=stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    # Notification produces no response; the other 3 do.
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["id"] == 1
    assert parsed[1]["id"] == 2
    assert parsed[2]["id"] == 3
    assert parsed[2]["result"]["isError"] is False


def test_serve_handles_content_length_framing(tmp_path):
    req = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    body = json.dumps(req)
    framed = f"Content-Length: {len(body)}\r\n\r\n{body}"
    stdin = io.StringIO(framed)
    stdout = io.StringIO()
    mcp.serve(stdin=stdin, stdout=stdout)
    parsed = json.loads(stdout.getvalue().strip())
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_serve_returns_parse_error_for_garbage():
    stdin = io.StringIO("this is not json\n")
    stdout = io.StringIO()
    mcp.serve(stdin=stdin, stdout=stdout)
    parsed = json.loads(stdout.getvalue().strip())
    assert parsed["error"]["code"] == -32700


def test_cli_mcp_subcommand_runs_serve():
    from click.testing import CliRunner

    from schema_seance.cli import main

    runner = CliRunner()
    # Empty stdin -> immediate EOF -> graceful exit.
    res = runner.invoke(main, ["mcp"], input="")
    assert res.exit_code == 0, res.output
