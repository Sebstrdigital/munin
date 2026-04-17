"""Unit tests for US-003: hit_count / last_hit_at bump on recall."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from munin.core import scope as _scope
from munin.core.config import MuninConfig


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_scope_cache() -> Iterator[None]:
    """Clear lru_cache between tests so scope detection is not stale."""
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
    """Patch get_pool with a mock pool whose cursor yields *rows* on fetchall."""
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
# Tests
# ---------------------------------------------------------------------------


class TestHitCountBump:
    def test_recall_issues_update_for_returned_thoughts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        """recall() must UPDATE hit_count+1 / last_hit_at for all returned rows."""
        from munin.core.memory import recall

        row_id = uuid.uuid4()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [_make_row(row_id=row_id)])

        recall("query", config=cfg)

        # cursor.execute is called twice: once for SELECT, once for UPDATE
        assert cursor.execute.call_count == 2
        update_call = cursor.execute.call_args_list[1]
        sql: str = update_call[0][0]
        params: tuple[Any, ...] = update_call[0][1]

        assert "hit_count" in sql
        assert "last_hit_at" in sql
        assert "UPDATE thoughts" in sql
        # The list of ids passed to ANY(%s) must contain our row_id
        assert row_id in params[0]

    def test_recall_bumps_all_returned_ids(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        """UPDATE must include all ids returned, not just the first."""
        from munin.core.memory import recall

        ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        rows = [_make_row(row_id=i) for i in ids]

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, rows)

        recall("query", config=cfg)

        update_call = cursor.execute.call_args_list[1]
        passed_ids: list[uuid.UUID] = update_call[0][1][0]
        assert set(passed_ids) == set(ids)

    def test_recall_no_update_when_no_results(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        """recall() must NOT issue an UPDATE when match_thoughts returns nothing."""
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [])

        recall("query", config=cfg)

        # Only one execute call (the SELECT); no UPDATE issued.
        assert cursor.execute.call_count == 1

    def test_new_thought_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        """remember() row is inserted with hit_count=0 and last_hit_at=NULL
        (enforced by DB defaults in migration 005; this test verifies that
        the INSERT statement does not override those columns)."""
        from munin.core.memory import remember

        inserted_sql: list[str] = []

        cursor = MagicMock()
        cursor.fetchone.return_value = (uuid.uuid4(),)

        def _capture_execute(sql: str, params: Any = None) -> None:
            inserted_sql.append(sql)

        cursor.execute.side_effect = _capture_execute

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cursor
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        pool = MagicMock()
        pool.connection.return_value.__enter__ = lambda s: conn
        pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)

        remember("some content", config=cfg)

        # The INSERT goes through upsert_thought RPC; hit_count / last_hit_at
        # should NOT appear in the call (defaults are DB-side).
        assert len(inserted_sql) == 1
        assert "hit_count" not in inserted_sql[0]
        assert "last_hit_at" not in inserted_sql[0]

    def test_superseded_by_column_not_set_by_recall(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: MuninConfig,
    ) -> None:
        """The UPDATE issued by recall() must not touch superseded_by."""
        from munin.core.memory import recall

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: [0.1] * 768)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "proj"
        )
        cursor = _mock_pool_with_rows(monkeypatch, [_make_row()])

        recall("query", config=cfg)

        update_sql: str = cursor.execute.call_args_list[1][0][0]
        assert "superseded_by" not in update_sql
