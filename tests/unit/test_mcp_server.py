"""Unit tests for mcp.server tool functions (US-008, US-009)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
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


class TestListProjectsTool:
    def test_returns_projects_list(self) -> None:
        with patch(
            "munin.mcp.server.memory.list_projects",
            return_value=[("alpha", 3), ("beta", 7)],
        ):
            from munin.mcp.server import list_projects

            result = list_projects()

        assert result == {
            "projects": [
                {"project": "alpha", "count": 3},
                {"project": "beta", "count": 7},
            ]
        }

    def test_empty_returns_empty_list(self) -> None:
        with patch("munin.mcp.server.memory.list_projects", return_value=[]):
            from munin.mcp.server import list_projects

            result = list_projects()

        assert result == {"projects": []}


class TestShowTool:
    def _make_thought(self) -> object:
        from munin.core.memory import Thought

        return Thought(
            id=_FAKE_UUID,
            content="stored thought",
            project=_FAKE_PROJECT,
            scope="session",
            tags=["x"],
            metadata={"k": "v"},
            created_at=_NOW,
            updated_at=_NOW,
        )

    def test_returns_full_thought(self) -> None:
        thought = self._make_thought()
        with patch("munin.mcp.server.memory.show", return_value=thought):
            from munin.mcp.server import show

            result = show(thought_id=str(_FAKE_UUID))

        assert result["id"] == str(_FAKE_UUID)
        assert result["content"] == "stored thought"
        assert result["project"] == _FAKE_PROJECT
        assert result["scope"] == "session"
        assert result["tags"] == ["x"]
        assert result["metadata"] == {"k": "v"}
        assert result["created_at"] == _NOW.isoformat()
        assert result["updated_at"] == _NOW.isoformat()

    def test_not_found_returns_error(self) -> None:
        with patch("munin.mcp.server.memory.show", return_value=None):
            from munin.mcp.server import show

            result = show(thought_id="nonexistent-id")

        assert "error" in result
        assert result["error"]["code"] == "not_found"  # type: ignore[index]


class TestForgetTool:
    def test_deleted_returns_true_and_id(self) -> None:
        with patch("munin.mcp.server.memory.forget", return_value=True):
            from munin.mcp.server import forget

            result = forget(thought_id=str(_FAKE_UUID))

        assert result == {"deleted": True, "id": str(_FAKE_UUID)}

    def test_not_found_returns_error(self) -> None:
        with patch("munin.mcp.server.memory.forget", return_value=False):
            from munin.mcp.server import forget

            result = forget(thought_id="missing-id")

        assert "error" in result
        assert result["error"]["code"] == "not_found"  # type: ignore[index]


class TestStatsTool:
    def _make_pool_mock(self, count: int = 5) -> MagicMock:
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = (count,)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur

        pool = MagicMock()
        pool.connection.return_value = conn
        return pool

    def test_db_reachable_returns_counts(self) -> None:
        pool = self._make_pool_mock(count=5)
        with (
            patch("munin.mcp.server._load_config", return_value=MagicMock()),
            patch("munin.mcp.server._get_pool", return_value=pool),
            patch("munin.mcp.server._embed"),
            patch(
                "munin.mcp.server.memory.list_projects",
                return_value=[("p1", 3), ("p2", 2)],
            ),
        ):
            from munin.mcp.server import stats

            result = stats()

        assert result["db_reachable"] is True
        assert result["embed_server_reachable"] is True
        assert result["total_thoughts"] == 5
        assert result["total_projects"] == 2

    def test_db_unreachable(self) -> None:
        with (
            patch("munin.mcp.server._load_config", return_value=MagicMock()),
            patch("munin.mcp.server._get_pool", side_effect=Exception("conn failed")),
            patch("munin.mcp.server._embed"),
        ):
            from munin.mcp.server import stats

            result = stats()

        assert result["db_reachable"] is False
        assert result["total_thoughts"] == 0
        assert result["total_projects"] == 0

    def test_embed_unreachable(self) -> None:
        from munin.core.errors import MuninEmbedError

        pool = self._make_pool_mock(count=2)
        with (
            patch("munin.mcp.server._load_config", return_value=MagicMock()),
            patch("munin.mcp.server._get_pool", return_value=pool),
            patch("munin.mcp.server._embed", side_effect=MuninEmbedError("unreachable")),
            patch("munin.mcp.server.memory.list_projects", return_value=[("p", 2)]),
        ):
            from munin.mcp.server import stats

            result = stats()

        assert result["embed_server_reachable"] is False
        assert result["db_reachable"] is True
