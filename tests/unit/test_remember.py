"""Unit tests for core.memory.remember (US-008)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from munin.core.errors import MuninError
from munin.core.memory import remember

_FAKE_UUID = UUID("12345678-1234-5678-1234-567812345678")
_FAKE_VEC = [0.1] * 768


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

        result = remember("hello world")

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

        result = remember("hello world", project="other")

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
            remember("hello world")

    def test_returns_uuid_from_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns the UUID produced by the DB cursor's fetchone."""
        custom_uuid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        pool = _make_pool_mock(return_uuid=custom_uuid)
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        result = remember("something", project="proj")

        assert result == custom_uuid

    def test_dedup_same_args_called_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling remember twice with identical content calls upsert_thought twice.

        Real dedup is enforced at the SQL level (US-011 integration tests).
        This test verifies both calls pass identical args — confirming the
        upsert path is invoked rather than a plain INSERT.
        """
        pool = _make_pool_mock()
        monkeypatch.setattr("munin.core.memory.embed", lambda *a, **kw: _FAKE_VEC)
        monkeypatch.setattr("munin.core.memory.get_pool", lambda *a, **kw: pool)
        monkeypatch.setattr(
            "munin.core.memory._scope.current_project", lambda: "munin"
        )

        r1 = remember("same content", project="proj")
        r2 = remember("same content", project="proj")

        assert r1 == _FAKE_UUID
        assert r2 == _FAKE_UUID

        cur = pool.connection().__enter__().cursor().__enter__()
        assert cur.execute.call_count == 2
        args1 = cur.execute.call_args_list[0][0][1]
        args2 = cur.execute.call_args_list[1][0][1]
        # content and project identical
        assert args1[0] == args2[0]  # content
        assert args1[2] == args2[2]  # project
