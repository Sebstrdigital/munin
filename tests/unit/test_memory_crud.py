"""Unit tests for US-010: list_projects, show, forget."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from munin.core.memory import Thought, forget, list_projects, show

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

_FULL_ROW: tuple[Any, ...] = (
    _ID,
    "some content",
    "my-project",
    "main",
    ["tag1", "tag2"],
    {"k": "v"},
    _TS,
    _TS,
)


def _make_pool(
    fetchall: list[Any] | None = None,
    fetchone: Any = None,
) -> MagicMock:
    """Return a fake pool whose connection/cursor chain is pre-programmed."""
    fake_cur: MagicMock = MagicMock()
    if fetchall is not None:
        fake_cur.fetchall.return_value = fetchall
    fake_cur.fetchone.return_value = fetchone

    fake_conn: MagicMock = MagicMock()
    fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cur)
    fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    fake_pool: MagicMock = MagicMock()
    fake_pool.connection.return_value.__enter__ = MagicMock(
        return_value=fake_conn
    )
    fake_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

    return fake_pool


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def test_list_projects_returns_tuples(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_projects() returns a list of (project, count) tuples."""
    pool = _make_pool(fetchall=[("proj_a", 3), ("proj_b", 1)])
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    result = list_projects()

    assert result == [("proj_a", 3), ("proj_b", 1)]


def test_list_projects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_projects() returns empty list when no thoughts exist."""
    pool = _make_pool(fetchall=[])
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    assert list_projects() == []


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_returns_thought_for_valid_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """show() returns a populated Thought dataclass for a matching row."""
    pool = _make_pool(fetchone=_FULL_ROW)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    thought = show(_ID)

    assert thought is not None
    assert thought.id == _ID
    assert thought.content == "some content"
    assert thought.project == "my-project"
    assert thought.scope == "main"
    assert thought.tags == ["tag1", "tag2"]
    assert thought.metadata == {"k": "v"}
    assert thought.created_at == _TS
    assert thought.updated_at == _TS


def test_show_returns_none_for_missing_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """show() returns None when fetchone returns nothing."""
    pool = _make_pool(fetchone=None)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    assert show(_ID) is None


def test_show_accepts_string_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """show() coerces a string argument to UUID without error."""
    pool = _make_pool(fetchone=_FULL_ROW)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    thought = show(str(_ID))
    assert thought is not None
    assert thought.id == _ID


def test_show_scope_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """show() sets scope=None when the column value is NULL."""
    row = (_ID, "c", "p", None, [], {}, _TS, _TS)
    pool = _make_pool(fetchone=row)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    thought = show(_ID)
    assert thought is not None
    assert thought.scope is None


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_returns_true_on_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """forget() returns True when RETURNING id yields a row."""
    pool = _make_pool(fetchone=(_ID,))
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    assert forget(_ID) is True


def test_forget_returns_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """forget() returns False when RETURNING id yields nothing."""
    pool = _make_pool(fetchone=None)
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    assert forget(_ID) is False


def test_forget_accepts_string_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """forget() coerces a string argument to UUID without error."""
    pool = _make_pool(fetchone=(_ID,))
    monkeypatch.setattr("munin.core.memory.get_pool", lambda cfg=None: pool)

    assert forget(str(_ID)) is True
