"""Unit tests for mcp.server remember and recall tool functions (US-008)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

_FAKE_UUID = UUID("12345678-1234-5678-1234-567812345678")
_FAKE_PROJECT = "munin"
_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _patch_project() -> None:
    """Force _project to a known value for all tests in this module."""
    import munin.mcp.server as srv

    original = srv._project
    srv._project = _FAKE_PROJECT
    yield
    srv._project = original


class TestRememberTool:
    def test_returns_id_and_project(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID):
            from munin.mcp.server import remember

            result = remember(content="test thought")

        assert result == {"id": str(_FAKE_UUID), "project": _FAKE_PROJECT}

    def test_passes_content_and_project(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID) as mock_rem:
            from munin.mcp.server import remember

            remember(content="hello")

        mock_rem.assert_called_once_with(
            "hello",
            project=_FAKE_PROJECT,
            scope=None,
            tags=None,
            metadata=None,
        )

    def test_passes_optional_fields(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID) as mock_rem:
            from munin.mcp.server import remember

            remember(content="hello", scope="session", tags=["a", "b"], metadata={"k": "v"})

        mock_rem.assert_called_once_with(
            "hello",
            project=_FAKE_PROJECT,
            scope="session",
            tags=["a", "b"],
            metadata={"k": "v"},
        )

    def test_id_is_string(self) -> None:
        with patch("munin.mcp.server.memory.remember", return_value=_FAKE_UUID):
            from munin.mcp.server import remember

            result = remember(content="x")

        assert isinstance(result["id"], str)


class TestRecallTool:
    def _make_thought(self, content: str = "match") -> object:
        from munin.core.memory import ThoughtResult

        return ThoughtResult(
            id=_FAKE_UUID,
            content=content,
            project=_FAKE_PROJECT,
            scope=None,
            tags=["tag1"],
            metadata={},
            similarity=0.9,
            created_at=_NOW,
        )

    def test_returns_structured_dict(self) -> None:
        thought = self._make_thought()
        with patch("munin.mcp.server.memory.recall", return_value=[thought]):
            from munin.mcp.server import recall

            result = recall(query="test")

        assert result["project"] == _FAKE_PROJECT
        assert result["count"] == 1
        assert len(result["results"]) == 1  # type: ignore[arg-type]

    def test_result_fields_serialized(self) -> None:
        thought = self._make_thought("a memory")
        with patch("munin.mcp.server.memory.recall", return_value=[thought]):
            from munin.mcp.server import recall

            result = recall(query="test")

        item = result["results"][0]  # type: ignore[index]
        assert item["id"] == str(_FAKE_UUID)
        assert item["content"] == "a memory"
        assert item["project"] == _FAKE_PROJECT
        assert item["scope"] is None
        assert item["tags"] == ["tag1"]
        assert item["similarity"] == 0.9
        assert item["created_at"] == _NOW.isoformat()
        assert isinstance(item["created_at"], str)

    def test_empty_results(self) -> None:
        with patch("munin.mcp.server.memory.recall", return_value=[]):
            from munin.mcp.server import recall

            result = recall(query="nothing")

        assert result["count"] == 0
        assert result["results"] == []

    def test_passes_params_to_core(self) -> None:
        with patch("munin.mcp.server.memory.recall", return_value=[]) as mock_rec:
            from munin.mcp.server import recall

            recall(query="q", scope="session", limit=5, threshold=0.7)

        mock_rec.assert_called_once_with(
            "q",
            project=_FAKE_PROJECT,
            scope="session",
            limit=5,
            threshold=0.7,
        )

    def test_multiple_results_count(self) -> None:
        thoughts = [self._make_thought(f"memory {i}") for i in range(3)]
        with patch("munin.mcp.server.memory.recall", return_value=thoughts):
            from munin.mcp.server import recall

            result = recall(query="multiple")

        assert result["count"] == 3
        assert len(result["results"]) == 3  # type: ignore[arg-type]
