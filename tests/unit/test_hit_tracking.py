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
        db_url="postgresql://munin:munin@localhost:5433/munin_test",
        embed_url="http://localhost:8088",
        embed_dim=768,
        default_limit=10,
        embed_batch_size=32,
        # Hybrid ranking weights (US-003 defaults)
        recall_w_rrf=0.7,
        recall_w_recency=0.2,
        recall_w_hits=0.1,
        recall_rrf_k=60,
        # MMR disabled in unit tests — MMR behaviour is covered by integration tests.
        # Disabling here keeps the mock cursor call-count predictable (no extra
        # embedding-fetch execute call).
        recall_mmr_enabled=False,
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
    updated_at: datetime | None = None,
    fused_score: float = 0.9,
) -> tuple[Any, ...]:
    ts = created_at or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (
        row_id or uuid.uuid4(),
        content,
        project,
        scope,
        tags or [],
        metadata or {},
        similarity,
        ts,
        updated_at or ts,
        fused_score,
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

        # cursor.execute is called four times:
        #   [0] SET LOCAL hnsw.ef_search
        #   [1] SET LOCAL hnsw.iterative_scan
        #   [2] SELECT ... FROM match_thoughts(...)
        #   [3] UPDATE thoughts SET hit_count ...
        assert cursor.execute.call_count == 4
        update_call = cursor.execute.call_args_list[3]
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

        # UPDATE is the 4th call (index 3): [SET LOCAL ef, SET LOCAL iter, SELECT, UPDATE]
        update_call = cursor.execute.call_args_list[3]
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

        # Three execute calls (2x SET LOCAL + SELECT); no UPDATE when empty results.
        assert cursor.execute.call_count == 3

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

        # UPDATE is the 4th call (index 3): [SET LOCAL ef, SET LOCAL iter, SELECT, UPDATE]
        update_sql: str = cursor.execute.call_args_list[3][0][0]
        assert "superseded_by" not in update_sql
