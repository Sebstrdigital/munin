"""Unit tests for core.memory.remember (US-008, P2-1)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from munin.core.config import MuninConfig
from munin.core.errors import MuninError
from munin.core.memory import remember

_FAKE_UUID = UUID("12345678-1234-5678-1234-567812345678")
_FAKE_VEC = [0.1] * 768

# Config with all lifecycle features disabled — used by tests that don't exercise
# the dedup or supersession path, asserting the old single-query insert behaviour.
_CFG_NO_DEDUP = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=False,
    remember_supersede_enabled=False,
)

# Config with dedup enabled at a low threshold (0.5) for forcing a skip in tests.
_CFG_DEDUP_LOW = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=True,
    remember_dedup_threshold=0.5,
)

# Config with dedup enabled at a very high threshold (0.9999) so normal calls pass through.
_CFG_DEDUP_HIGH = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=True,
    remember_dedup_threshold=0.9999,
)


def _make_pool_mock(return_uuid: UUID = _FAKE_UUID) -> MagicMock:
    """Build a mock pool that returns return_uuid from fetchone."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = (return_uuid,)

    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur

    pool = MagicMock()
    pool.connection.return_value = conn
    return pool


class TestRemember:
    def test_auto_project_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auto-detected project from git root is used when project= omitted."""
        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        result = remember("hello world", config=_CFG_NO_DEDUP)

        assert result == _FAKE_UUID
        # Verify the SQL call used "munin" as project
        call_args = pool.connection().__enter__().cursor().__enter__().execute.call_args
        assert call_args[0][1][2] == "munin"

    def test_explicit_project_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit project= takes precedence over auto-detection."""
        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        result = remember("hello world", project="other", config=_CFG_NO_DEDUP)

        assert result == _FAKE_UUID
        call_args = pool.connection().__enter__().cursor().__enter__().execute.call_args
        assert call_args[0][1][2] == "other"

    def test_no_git_root_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raises MuninError when no git root found and no explicit project."""
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: None
        )

        with pytest.raises(MuninError, match="project could not be determined"):
            remember("hello world", config=_CFG_NO_DEDUP)

    def test_returns_uuid_from_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns the UUID produced by the DB cursor's fetchone."""
        custom_uuid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        pool = _make_pool_mock(return_uuid=custom_uuid)
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        result = remember("something", project="proj", config=_CFG_NO_DEDUP)

        assert result == custom_uuid

    def test_dedup_same_args_called_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling remember twice with identical content calls upsert_thought twice.

        Real dedup is enforced at the SQL level (US-011 integration tests).
        This test verifies both calls pass identical args — confirming the
        upsert path is invoked rather than a plain INSERT.
        Dedup is disabled so the pure upsert path is exercised unchanged.
        """
        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        r1 = remember("same content", project="proj", config=_CFG_NO_DEDUP)
        r2 = remember("same content", project="proj", config=_CFG_NO_DEDUP)

        assert r1 == _FAKE_UUID
        assert r2 == _FAKE_UUID

        cur = pool.connection().__enter__().cursor().__enter__()
        assert cur.execute.call_count == 2
        args1 = cur.execute.call_args_list[0][0][1]
        args2 = cur.execute.call_args_list[1][0][1]
        # content and project identical
        assert args1[0] == args2[0]  # content
        assert args1[2] == args2[2]  # project


class TestRememberDedup:
    """P2-1: Semantic near-duplicate detection on write."""

    def _make_dedup_pool_mock(
        self,
        dup_id: UUID,
        cosine: float,
        insert_uuid: UUID = _FAKE_UUID,
    ) -> MagicMock:
        """Pool mock where the first fetchone returns (dup_id, cosine) and
        the second returns (insert_uuid,) — simulating the two-query dedup path."""
        # Each connection() call returns a fresh connection context manager.
        # We build two separate cursor mocks: one for the dedup SELECT and one
        # for the upsert SELECT, wired via side_effect on connection().
        dedup_cur = MagicMock()
        dedup_cur.__enter__ = lambda s: s
        dedup_cur.__exit__ = MagicMock(return_value=False)
        dedup_cur.fetchone.return_value = (dup_id, cosine)

        dedup_conn = MagicMock()
        dedup_conn.__enter__ = lambda s: s
        dedup_conn.__exit__ = MagicMock(return_value=False)
        dedup_conn.cursor.return_value = dedup_cur

        insert_cur = MagicMock()
        insert_cur.__enter__ = lambda s: s
        insert_cur.__exit__ = MagicMock(return_value=False)
        insert_cur.fetchone.return_value = (insert_uuid,)

        insert_conn = MagicMock()
        insert_conn.__enter__ = lambda s: s
        insert_conn.__exit__ = MagicMock(return_value=False)
        insert_conn.cursor.return_value = insert_cur

        pool = MagicMock()
        pool.connection.side_effect = [dedup_conn, insert_conn]
        return pool

    def test_near_dup_skips_insert_and_returns_existing_id(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Re-remembering a near-duplicate creates NO new row; returns existing id."""
        existing_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        pool = self._make_dedup_pool_mock(dup_id=existing_id, cosine=0.97)

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        with caplog.at_level(logging.INFO, logger="munin.core.memory"):
            result = remember(
                "Munin uses pgvector for storage",
                project="test-proj",
                config=_CFG_DEDUP_LOW,
            )

        # Returns the existing row id, not a new one.
        assert result == existing_id
        # Only one connection was opened (dedup SELECT) — upsert_thought was NOT called.
        assert pool.connection.call_count == 1
        # Log message records both ids and "dedup skip".
        assert str(existing_id) in caplog.text
        assert "dedup skip" in caplog.text

    def test_dissimilar_thought_inserts_normally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuinely dissimilar thought (cosine < threshold) still inserts."""
        existing_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        new_id = UUID("11111111-2222-3333-4444-555555555555")
        # cosine=0.30 is well below any sensible threshold.
        pool = self._make_dedup_pool_mock(
            dup_id=existing_id, cosine=0.30, insert_uuid=new_id
        )

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        result = remember(
            "The weather in Stockholm is cold",
            project="test-proj",
            config=_CFG_DEDUP_LOW,
        )

        # A new row was inserted; the new id is returned.
        assert result == new_id
        # Both the dedup SELECT and the upsert were executed (two connection() calls).
        assert pool.connection.call_count == 2

    def test_dedup_flag_off_restores_prior_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With remember_dedup_enabled=False the dedup SELECT is never issued."""
        pool = _make_pool_mock()

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        result = remember(
            "Some thought",
            project="test-proj",
            config=_CFG_NO_DEDUP,
        )

        assert result == _FAKE_UUID
        # Exactly one execute call — the upsert_thought — no dedup SELECT.
        cur = pool.connection().__enter__().cursor().__enter__()
        assert cur.execute.call_count == 1
        sql = cur.execute.call_args_list[0][0][0]
        assert "upsert_thought" in sql

    def test_no_existing_thoughts_inserts_normally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no in-project thoughts exist, dedup SELECT returns None; insert proceeds."""
        new_id = UUID("cccccccc-dddd-eeee-ffff-000000000000")

        # First connection: dedup SELECT returns None (empty project).
        dedup_cur = MagicMock()
        dedup_cur.__enter__ = lambda s: s
        dedup_cur.__exit__ = MagicMock(return_value=False)
        dedup_cur.fetchone.return_value = None

        dedup_conn = MagicMock()
        dedup_conn.__enter__ = lambda s: s
        dedup_conn.__exit__ = MagicMock(return_value=False)
        dedup_conn.cursor.return_value = dedup_cur

        # Second connection: upsert returns new_id.
        insert_cur = MagicMock()
        insert_cur.__enter__ = lambda s: s
        insert_cur.__exit__ = MagicMock(return_value=False)
        insert_cur.fetchone.return_value = (new_id,)

        insert_conn = MagicMock()
        insert_conn.__enter__ = lambda s: s
        insert_conn.__exit__ = MagicMock(return_value=False)
        insert_conn.cursor.return_value = insert_cur

        pool = MagicMock()
        pool.connection.side_effect = [dedup_conn, insert_conn]

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        result = remember(
            "Brand new thought with no neighbours",
            project="test-proj",
            config=_CFG_DEDUP_LOW,
        )

        assert result == new_id
        assert pool.connection.call_count == 2


# ---------------------------------------------------------------------------
# Configs for supersession unit tests
# ---------------------------------------------------------------------------

_OLD_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_NEW_ID = UUID("11111111-2222-3333-4444-555555555555")

# Supersession ON, threshold low (0.3) so cosine=0.60 falls in range.
# Dedup threshold high (0.9999) so we never accidentally hit the dedup path.
_CFG_SUPERSEDE_ON = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=True,
    remember_dedup_threshold=0.9999,
    remember_supersede_enabled=True,
    remember_supersede_threshold=0.30,
)

# Supersession OFF (dedup also off) — insert-always baseline.
_CFG_SUPERSEDE_OFF = MuninConfig(
    db_url="postgresql://x:x@localhost:5433/x",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
    remember_dedup_enabled=False,
    remember_supersede_enabled=False,
)


def _make_supersede_pool_mock(
    neighbour_id: UUID,
    cosine: float,
    new_id: UUID = _NEW_ID,
) -> MagicMock:
    """Pool mock for the supersession happy path.

    Connection order:
      1. ANN SELECT → (neighbour_id, cosine)
      2. upsert_thought → (new_id,)
      3. UPDATE thoughts SET superseded_by — no fetchone needed
    """
    ann_cur = MagicMock()
    ann_cur.__enter__ = lambda s: s
    ann_cur.__exit__ = MagicMock(return_value=False)
    ann_cur.fetchone.return_value = (neighbour_id, cosine)
    ann_conn = MagicMock()
    ann_conn.__enter__ = lambda s: s
    ann_conn.__exit__ = MagicMock(return_value=False)
    ann_conn.cursor.return_value = ann_cur

    upsert_cur = MagicMock()
    upsert_cur.__enter__ = lambda s: s
    upsert_cur.__exit__ = MagicMock(return_value=False)
    upsert_cur.fetchone.return_value = (new_id,)
    upsert_conn = MagicMock()
    upsert_conn.__enter__ = lambda s: s
    upsert_conn.__exit__ = MagicMock(return_value=False)
    upsert_conn.cursor.return_value = upsert_cur

    update_cur = MagicMock()
    update_cur.__enter__ = lambda s: s
    update_cur.__exit__ = MagicMock(return_value=False)
    update_conn = MagicMock()
    update_conn.__enter__ = lambda s: s
    update_conn.__exit__ = MagicMock(return_value=False)
    update_conn.cursor.return_value = update_cur

    pool = MagicMock()
    pool.connection.side_effect = [ann_conn, upsert_conn, update_conn]
    return pool


class TestRememberSupersession:
    """P2-2: Supersession / conflict handling unit tests."""

    def test_supersession_fires_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When cosine is in [supersede_threshold, dedup_threshold), the old row
        is retired and the new id is returned."""
        pool = _make_supersede_pool_mock(neighbour_id=_OLD_ID, cosine=0.60)

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        with caplog.at_level(logging.INFO, logger="munin.core.memory"):
            result = remember(
                "We now use SQLite instead of PostgreSQL",
                project="test-proj",
                config=_CFG_SUPERSEDE_ON,
            )

        # New id returned.
        assert result == _NEW_ID
        # Three connections: ANN, upsert, UPDATE.
        assert pool.connection.call_count == 3
        # Log records supersession.
        assert "superseded" in caplog.text
        assert str(_OLD_ID) in caplog.text
        assert str(_NEW_ID) in caplog.text

    def test_supersession_update_sql_correct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UPDATE issued to retire the old row sets superseded_by = new_id."""
        # Build the mock connections first so we can inspect update_conn after the call.
        ann_cur = MagicMock()
        ann_cur.__enter__ = lambda s: s
        ann_cur.__exit__ = MagicMock(return_value=False)
        ann_cur.fetchone.return_value = (_OLD_ID, 0.60)
        ann_conn = MagicMock()
        ann_conn.__enter__ = lambda s: s
        ann_conn.__exit__ = MagicMock(return_value=False)
        ann_conn.cursor.return_value = ann_cur

        upsert_cur = MagicMock()
        upsert_cur.__enter__ = lambda s: s
        upsert_cur.__exit__ = MagicMock(return_value=False)
        upsert_cur.fetchone.return_value = (_NEW_ID,)
        upsert_conn = MagicMock()
        upsert_conn.__enter__ = lambda s: s
        upsert_conn.__exit__ = MagicMock(return_value=False)
        upsert_conn.cursor.return_value = upsert_cur

        update_cur = MagicMock()
        update_cur.__enter__ = lambda s: s
        update_cur.__exit__ = MagicMock(return_value=False)
        update_conn = MagicMock()
        update_conn.__enter__ = lambda s: s
        update_conn.__exit__ = MagicMock(return_value=False)
        update_conn.cursor.return_value = update_cur

        pool = MagicMock()
        pool.connection.side_effect = [ann_conn, upsert_conn, update_conn]

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        remember(
            "Updated decision text",
            project="test-proj",
            config=_CFG_SUPERSEDE_ON,
        )

        # Third connection is the UPDATE; verify SQL and params.
        assert pool.connection.call_count == 3
        update_sql, update_params = update_cur.execute.call_args[0]
        assert "superseded_by" in update_sql
        assert _NEW_ID in update_params
        assert _OLD_ID in update_params

    def test_supersession_flag_off_no_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With remember_supersede_enabled=False, no UPDATE is issued."""
        # ANN + upsert only (2 connections).
        ann_cur = MagicMock()
        ann_cur.__enter__ = lambda s: s
        ann_cur.__exit__ = MagicMock(return_value=False)
        ann_cur.fetchone.return_value = None  # no neighbour → no supersession possible
        ann_conn = MagicMock()
        ann_conn.__enter__ = lambda s: s
        ann_conn.__exit__ = MagicMock(return_value=False)
        ann_conn.cursor.return_value = ann_cur

        upsert_cur = MagicMock()
        upsert_cur.__enter__ = lambda s: s
        upsert_cur.__exit__ = MagicMock(return_value=False)
        upsert_cur.fetchone.return_value = (_NEW_ID,)
        upsert_conn = MagicMock()
        upsert_conn.__enter__ = lambda s: s
        upsert_conn.__exit__ = MagicMock(return_value=False)
        upsert_conn.cursor.return_value = upsert_cur

        pool = MagicMock()
        pool.connection.side_effect = [upsert_conn]  # only 1 connection needed

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        result = remember(
            "No supersession here",
            project="test-proj",
            config=_CFG_SUPERSEDE_OFF,
        )

        assert result == _NEW_ID
        # Only the upsert connection — no ANN, no UPDATE.
        assert pool.connection.call_count == 1

    def test_below_supersede_threshold_no_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cosine below supersede_threshold → genuinely new thought, no UPDATE."""
        # cosine=0.10 is below the 0.30 threshold in _CFG_SUPERSEDE_ON.
        ann_cur = MagicMock()
        ann_cur.__enter__ = lambda s: s
        ann_cur.__exit__ = MagicMock(return_value=False)
        ann_cur.fetchone.return_value = (_OLD_ID, 0.10)
        ann_conn = MagicMock()
        ann_conn.__enter__ = lambda s: s
        ann_conn.__exit__ = MagicMock(return_value=False)
        ann_conn.cursor.return_value = ann_cur

        upsert_cur = MagicMock()
        upsert_cur.__enter__ = lambda s: s
        upsert_cur.__exit__ = MagicMock(return_value=False)
        upsert_cur.fetchone.return_value = (_NEW_ID,)
        upsert_conn = MagicMock()
        upsert_conn.__enter__ = lambda s: s
        upsert_conn.__exit__ = MagicMock(return_value=False)
        upsert_conn.cursor.return_value = upsert_cur

        pool = MagicMock()
        pool.connection.side_effect = [ann_conn, upsert_conn]

        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "test-proj"
        )

        result = remember(
            "Completely unrelated thought",
            project="test-proj",
            config=_CFG_SUPERSEDE_ON,
        )

        assert result == _NEW_ID
        # ANN + upsert only — no UPDATE (cosine too low).
        assert pool.connection.call_count == 2
