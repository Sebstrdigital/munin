"""Unit tests for core.memory.recall and core.scope.current_project."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from munin.core import scope as _scope
from munin.core.config import MuninConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_scope_cache() -> Iterator[None]:
    """Clear the lru_cache between tests so scope detection is not stale."""
    _scope._find_project.cache_clear()
    yield
    _scope._find_project.cache_clear()


@pytest.fixture()
def cfg() -> MuninConfig:
    return MuninConfig(
        db_url="postgresql://munin:munin@localhost:5433/munin",
        embed_url="http://localhost:8088",
        embed_dim=768,
        default_limit=10,
        embed_batch_size=32,
    )


def _make_row(
    *,
    row_id: uuid.UUID | None = None,
    content: str = "test content",
    project: str = "myproject",
    scope: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    similarity: float = 0.9,
    created_at: datetime | None = None,
) -> tuple[Any, ...]:
    return (
        row_id or uuid.uuid4(),
        content,
        project,
        scope,
        tags or [],
        metadata or {},
        similarity,
        created_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _mock_pool_with_rows(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[tuple[Any, ...]],
) -> MagicMock:
    """Patch get_pool to return a mock whose cursor yields the given rows."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cursor
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value.__enter__ = lambda s: conn
    pool.connection.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
    return cursor


# ---------------------------------------------------------------------------
# scope.current_project
# ---------------------------------------------------------------------------


class TestCurrentProject:
    def test_finds_git_root_in_subdir(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        subdir = repo / "src" / "lib"
        subdir.mkdir(parents=True)

        assert _scope.current_project(cwd=subdir) == "myrepo"

    def test_finds_git_root_at_cwd(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _scope.current_project(cwd=tmp_path) == tmp_path.name

    def test_returns_none_when_no_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.errors import MuninError
        from munin.core.memory import recall

        monkeypatch.chdir(tmp_path)  # isolated dir guaranteed to have no .git ancestor
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.5] * 768)
        _mock_pool_with_rows(monkeypatch, [])

        with pytest.raises(MuninError, match="project could not be determined"):
            recall("test query", config=cfg)

    def test_caches_by_resolved_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "cached_repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        result1 = _scope.current_project(cwd=repo)
        result2 = _scope.current_project(cwd=repo)
        assert result1 == result2 == "cached_repo"
        assert _scope._find_project.cache_info().hits >= 1


# ---------------------------------------------------------------------------
# recall — error cases
# ---------------------------------------------------------------------------


class TestRecallErrors:
    def test_raises_when_project_cannot_be_resolved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.errors import MuninError
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory._scope.current_project", lambda: None)
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)

        with pytest.raises(MuninError, match="project could not be determined"):
            recall("what did I decide about auth?", config=cfg)


# ---------------------------------------------------------------------------
# recall — argument passing
# ---------------------------------------------------------------------------


class TestRecallArguments:
    def test_passes_scope_limit_threshold_to_cursor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.5] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "testproject"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [])

        recall("test query", scope="design", limit=5, threshold=0.7, config=cfg)

        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert "match_thoughts" in sql
        assert params[1] == "testproject"
        assert params[2] == "design"
        assert params[3] == 5
        assert params[4] == 0.7

    def test_uses_config_default_limit_when_limit_not_passed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [])

        recall("query", config=cfg)

        _, params = cursor.execute.call_args[0]
        assert params[3] == cfg.default_limit  # 10

    def test_explicit_project_skips_scope_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        called: list[bool] = []

        def _spy() -> str:
            called.append(True)
            return "ignored"

        monkeypatch.setattr("munin.core.memory._scope.current_project", _spy)
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        cursor = _mock_pool_with_rows(monkeypatch, [])

        recall("query", project="explicit-proj", config=cfg)

        assert not called, "current_project should not be called when project= is provided"
        _, params = cursor.execute.call_args[0]
        assert params[1] == "explicit-proj"

    def test_scope_none_passes_none_to_cursor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [])

        recall("query", config=cfg)  # no scope

        _, params = cursor.execute.call_args[0]
        assert params[2] is None


# ---------------------------------------------------------------------------
# recall — ThoughtResult mapping
# ---------------------------------------------------------------------------


class TestRecallMapping:
    def test_maps_row_to_thought_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import ThoughtResult, recall

        row_id = uuid.uuid4()
        created = datetime(2024, 6, 1, tzinfo=timezone.utc)
        row = _make_row(
            row_id=row_id,
            content="auth decision content",
            project="myrepo",
            scope="design",
            tags=["auth", "security"],
            metadata={"source": "slack"},
            similarity=0.87,
            created_at=created,
        )

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "myrepo"
        )
        _mock_pool_with_rows(monkeypatch, [row])

        results = recall("what about auth?", config=cfg)

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ThoughtResult)
        assert r.id == row_id
        assert r.content == "auth decision content"
        assert r.project == "myrepo"
        assert r.scope == "design"
        assert r.tags == ["auth", "security"]
        assert r.metadata == {"source": "slack"}
        assert r.similarity == pytest.approx(0.87)
        assert r.created_at == created

    def test_empty_tags_and_metadata_become_empty_collections(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        row = _make_row(tags=None, metadata=None)
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "myproject"
        )
        _mock_pool_with_rows(monkeypatch, [row])

        results = recall("query", config=cfg)

        assert results[0].tags == []
        assert results[0].metadata == {}

    def test_returns_empty_list_when_no_rows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        _mock_pool_with_rows(monkeypatch, [])

        results = recall("query", config=cfg)
        assert results == []
