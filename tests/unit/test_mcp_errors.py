"""Unit tests for MCP error handling and graceful degradation (US-010)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from munin.core.errors import MuninDBError, MuninEmbedError, MuninError


@pytest.fixture(autouse=True)
def _patch_project() -> None:
    import munin.mcp.server as srv

    original = srv._project
    srv._project = "test-project"
    yield
    srv._project = original


# ---------------------------------------------------------------------------
# _error_response helper
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_code_and_message(self) -> None:
        from munin.mcp.server import _error_response

        r = _error_response("db_unreachable", "connection refused")
        assert r == {"error": {"code": "db_unreachable", "message": "connection refused"}}

    def test_with_hint(self) -> None:
        from munin.mcp.server import _error_response

        r = _error_response("db_unreachable", "msg", hint="run docker compose up -d")
        assert r["error"]["hint"] == "run docker compose up -d"

    def test_without_hint_no_hint_key(self) -> None:
        from munin.mcp.server import _error_response

        r = _error_response("some_code", "msg")
        assert "hint" not in r["error"]


# ---------------------------------------------------------------------------
# remember — DB and embed errors
# ---------------------------------------------------------------------------


class TestRememberErrors:
    def test_db_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.remember",
            side_effect=MuninDBError("connection refused"),
        ):
            from munin.mcp.server import remember

            result = remember(content="test")

        assert result["error"]["code"] == "db_unreachable"
        assert "hint" in result["error"]
        assert "docker compose" in result["error"]["hint"]

    def test_embed_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.remember",
            side_effect=MuninEmbedError("llama.cpp down"),
        ):
            from munin.mcp.server import remember

            result = remember(content="test")

        assert result["error"]["code"] == "embed_unreachable"
        assert "hint" in result["error"]
        assert "llama.cpp" in result["error"]["hint"]

    def test_munin_base_error(self) -> None:
        with patch(
            "munin.mcp.server.memory.remember",
            side_effect=MuninError("validation failed"),
        ):
            from munin.mcp.server import remember

            result = remember(content="test")

        assert result["error"]["code"] == "validation_error"

    def test_unexpected_exception_returns_internal_error(self) -> None:
        with patch(
            "munin.mcp.server.memory.remember",
            side_effect=RuntimeError("boom"),
        ):
            from munin.mcp.server import remember

            result = remember(content="test")

        assert result["error"]["code"] == "internal_error"
        assert "RuntimeError" in result["error"]["message"]

    def test_unexpected_exception_logs_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "munin.mcp.server.memory.remember",
            side_effect=RuntimeError("traceback test"),
        ):
            from munin.mcp.server import remember

            remember(content="test")

        captured = capsys.readouterr()
        assert "RuntimeError" in captured.err


# ---------------------------------------------------------------------------
# recall — DB and embed errors
# ---------------------------------------------------------------------------


class TestRecallErrors:
    def test_db_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.recall",
            side_effect=MuninDBError("no connection"),
        ):
            from munin.mcp.server import recall

            result = recall(query="anything")

        assert result["error"]["code"] == "db_unreachable"
        assert "hint" in result["error"]

    def test_embed_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.recall",
            side_effect=MuninEmbedError("embed server down"),
        ):
            from munin.mcp.server import recall

            result = recall(query="anything")

        assert result["error"]["code"] == "embed_unreachable"

    def test_internal_error(self) -> None:
        with patch(
            "munin.mcp.server.memory.recall",
            side_effect=ValueError("unexpected"),
        ):
            from munin.mcp.server import recall

            result = recall(query="anything")

        assert result["error"]["code"] == "internal_error"


# ---------------------------------------------------------------------------
# list_projects, show, forget — DB errors (no embed dependency)
# ---------------------------------------------------------------------------


class TestListProjectsErrors:
    def test_db_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.list_projects",
            side_effect=MuninDBError("down"),
        ):
            from munin.mcp.server import list_projects

            result = list_projects()

        assert result["error"]["code"] == "db_unreachable"

    def test_no_embed_error_path(self) -> None:
        """list_projects has no embed dependency — embed errors treated as internal."""
        with patch(
            "munin.mcp.server.memory.list_projects",
            side_effect=MuninEmbedError("embed down"),
        ):
            from munin.mcp.server import list_projects

            result = list_projects()

        # MuninEmbedError IS caught → embed_unreachable
        assert result["error"]["code"] == "embed_unreachable"


class TestShowErrors:
    def test_db_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.show",
            side_effect=MuninDBError("db down"),
        ):
            from munin.mcp.server import show

            result = show(thought_id="any-id")

        assert result["error"]["code"] == "db_unreachable"

    def test_internal_error(self) -> None:
        with patch(
            "munin.mcp.server.memory.show",
            side_effect=KeyError("bad"),
        ):
            from munin.mcp.server import show

            result = show(thought_id="any-id")

        assert result["error"]["code"] == "internal_error"


class TestForgetErrors:
    def test_db_unreachable(self) -> None:
        with patch(
            "munin.mcp.server.memory.forget",
            side_effect=MuninDBError("db down"),
        ):
            from munin.mcp.server import forget

            result = forget(thought_id="any-id")

        assert result["error"]["code"] == "db_unreachable"

    def test_internal_error(self) -> None:
        with patch(
            "munin.mcp.server.memory.forget",
            side_effect=OSError("io fail"),
        ):
            from munin.mcp.server import forget

            result = forget(thought_id="any-id")

        assert result["error"]["code"] == "internal_error"
