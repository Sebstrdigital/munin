"""MCP subprocess smoke test.

Spawns munin-mcp as a stdio process and exercises the JSON-RPC 2.0 protocol
directly (no async MCP client lib needed).  Verifies all 6 tools are listed,
then calls remember + recall and checks structured fields in the responses.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Generator
from typing import Any

import pytest

# ── wire helpers ─────────────────────────────────────────────────────────────

_PROTO_VERSION = "2024-11-05"
_EXPECTED_TOOLS = {"remember", "recall", "forget", "show", "list_projects", "stats"}


def _reader(proc: "subprocess.Popen[bytes]", q: "queue.Queue[str]") -> None:
    """Background thread: read stdout lines and enqueue them."""
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode().strip()
        if line:
            q.put(line)


def _send(proc: "subprocess.Popen[bytes]", msg: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def _recv(q: "queue.Queue[str]", timeout: float = 15.0) -> dict[str, Any]:
    raw = q.get(timeout=timeout)
    result: dict[str, Any] = json.loads(raw)
    return result


def _call_tool(
    proc: "subprocess.Popen[bytes]",
    q: "queue.Queue[str]",
    msg_id: int,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Send a tools/call request and return the parsed tool result dict."""
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    resp = _recv(q)
    assert "result" in resp, f"Expected 'result' in response, got: {resp}"
    content = resp["result"].get("content", [])
    assert content, f"Empty content in tools/call response for {tool_name}"
    text = content[0]["text"]
    tool_result: dict[str, Any] = json.loads(text)
    return tool_result


# ── fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mcp_session() -> Generator[
    tuple["subprocess.Popen[bytes]", "queue.Queue[str]"], None, None
]:
    """Start munin-mcp, perform MCP handshake, yield (proc, queue), then clean up."""
    proc = subprocess.Popen(
        ["munin-mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    q: queue.Queue[str] = queue.Queue()
    reader_thread = threading.Thread(target=_reader, args=(proc, q), daemon=True)
    reader_thread.start()

    # ── MCP initialization handshake ────────────────────────────────────────
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTO_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "munin-smoke-test", "version": "0.0.1"},
            },
        },
    )
    init_resp = _recv(q)
    assert "result" in init_resp, f"initialize failed: {init_resp}"

    # Confirmed-initialized notification (no response expected)
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
    )

    yield proc, q

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── tests ────────────────────────────────────────────────────────────────────


def test_tools_list(
    mcp_session: tuple["subprocess.Popen[bytes]", "queue.Queue[str]"],
) -> None:
    """All 6 tools must be present in tools/list."""
    proc, q = mcp_session
    _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    resp = _recv(q)
    assert "result" in resp, f"tools/list failed: {resp}"
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == _EXPECTED_TOOLS, f"Tool mismatch — got {names}"


def test_remember_returns_id_and_project(
    mcp_session: tuple["subprocess.Popen[bytes]", "queue.Queue[str]"],
) -> None:
    """remember() must return 'id' (UUID string) and 'project'."""
    proc, q = mcp_session
    result = _call_tool(
        proc,
        q,
        msg_id=3,
        tool_name="remember",
        arguments={"content": "munin smoke test: pgvector stores 768-dim embeddings"},
    )
    assert "error" not in result, f"remember returned error: {result}"
    assert "id" in result, f"Missing 'id' in remember response: {result}"
    assert "project" in result, f"Missing 'project' in remember response: {result}"
    assert isinstance(result["id"], str) and len(result["id"]) > 0


def test_recall_returns_results_with_fields(
    mcp_session: tuple["subprocess.Popen[bytes]", "queue.Queue[str]"],
) -> None:
    """recall() must return 'results', 'count', and 'project'; each result has required fields."""
    proc, q = mcp_session
    result = _call_tool(
        proc,
        q,
        msg_id=4,
        tool_name="recall",
        arguments={"query": "pgvector embeddings"},
    )
    assert "error" not in result, f"recall returned error: {result}"
    assert "results" in result, f"Missing 'results' in recall response: {result}"
    assert "count" in result, f"Missing 'count' in recall response: {result}"
    assert "project" in result, f"Missing 'project' in recall response: {result}"
    assert result["count"] >= 1, "Expected at least one result after remember()"

    first = result["results"][0]
    for field in ("id", "content", "project", "scope", "tags", "similarity", "created_at"):
        assert field in first, f"Missing field '{field}' in recall result: {first}"
    assert isinstance(first["similarity"], float)
    assert isinstance(first["content"], str) and len(first["content"]) > 0
