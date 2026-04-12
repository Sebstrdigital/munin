"""Unit tests for the CLI show and forget commands (US-004)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from munin.cli.main import app
from munin.core.memory import Thought

_RUNNER = CliRunner()

_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_TS_CREATED = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_TS_UPDATED = datetime(2024, 6, 2, 8, 0, 0, tzinfo=timezone.utc)


def _thought(
    *,
    row_id: uuid.UUID = _ID,
    content: str = "use pgvector for embedding storage",
    project: str = "myrepo",
    scope: str | None = "design",
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> Thought:
    return Thought(
        id=row_id,
        content=content,
        project=project,
        scope=scope,
        tags=tags or ["arch", "db"],
        metadata=metadata or {"source": "adr-001"},
        created_at=_TS_CREATED,
        updated_at=_TS_UPDATED,
    )


class TestShowHumanOutput:
    def test_shows_all_fields(self) -> None:
        t = _thought()
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert out.exit_code == 0
        assert str(_ID) in out.output
        assert "myrepo" in out.output
        assert "design" in out.output
        assert "arch" in out.output
        assert "use pgvector for embedding storage" in out.output
        assert _TS_CREATED.isoformat() in out.output
        assert _TS_UPDATED.isoformat() in out.output

    def test_full_content_untruncated(self) -> None:
        long_content = "x" * 500
        t = _thought(content=long_content)
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert long_content in out.output

    def test_none_scope_displayed(self) -> None:
        t = _thought(scope=None)
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert out.exit_code == 0
        assert "Scope:" in out.output

    def test_empty_tags(self) -> None:
        t = _thought(tags=[])
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert out.exit_code == 0
        assert "Tags:" in out.output


class TestShowJsonOutput:
    def test_json_valid_and_all_fields(self) -> None:
        t = _thought()
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["id"] == str(_ID)
        assert data["content"] == "use pgvector for embedding storage"
        assert data["project"] == "myrepo"
        assert data["scope"] == "design"
        assert data["tags"] == ["arch", "db"]
        assert data["metadata"] == {"source": "adr-001"}
        assert data["created_at"] == _TS_CREATED.isoformat()
        assert data["updated_at"] == _TS_UPDATED.isoformat()

    def test_json_no_ansi(self) -> None:
        t = _thought()
        with patch("munin.cli.main._show", return_value=t):
            out = _RUNNER.invoke(app, ["show", str(_ID), "--json"])
        assert "\x1b[" not in out.output


class TestShowErrors:
    def test_not_found_returns_none_exits_1(self) -> None:
        with patch("munin.cli.main._show", return_value=None):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert out.exit_code == 1
        assert "not found" in out.stderr

    def test_invalid_uuid_exits_1(self) -> None:
        with patch("munin.cli.main._show", side_effect=ValueError("bad uuid")):
            out = _RUNNER.invoke(app, ["show", "not-a-uuid"])
        assert out.exit_code == 1
        assert "not found" in out.stderr

    def test_infra_error_exits_2(self) -> None:
        from munin.core.errors import MuninDBError

        with patch("munin.cli.main._show", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert out.exit_code == 2
        assert "docker compose up -d" in out.stderr


class TestForgetCommand:
    def test_deletes_with_yes_flag(self) -> None:
        with patch("munin.cli.main._forget", return_value=True) as mock_forget:
            out = _RUNNER.invoke(app, ["forget", str(_ID), "--yes"])
        assert out.exit_code == 0
        assert f"Deleted {_ID}" in out.output
        mock_forget.assert_called_once_with(str(_ID))

    def test_prompts_without_yes_flag_confirms(self) -> None:
        with patch("munin.cli.main._forget", return_value=True):
            out = _RUNNER.invoke(app, ["forget", str(_ID)], input="y\n")
        assert out.exit_code == 0
        assert f"Deleted {_ID}" in out.output

    def test_prompts_without_yes_flag_aborts_on_no(self) -> None:
        with patch("munin.cli.main._forget", return_value=True) as mock_forget:
            out = _RUNNER.invoke(app, ["forget", str(_ID)], input="n\n")
        assert out.exit_code != 0
        mock_forget.assert_not_called()

    def test_short_yes_flag(self) -> None:
        with patch("munin.cli.main._forget", return_value=True):
            out = _RUNNER.invoke(app, ["forget", str(_ID), "-y"])
        assert out.exit_code == 0

    def test_not_found_exits_1(self) -> None:
        with patch("munin.cli.main._forget", return_value=False):
            out = _RUNNER.invoke(app, ["forget", str(_ID), "--yes"])
        assert out.exit_code == 1
        assert "not found" in out.stderr

    def test_invalid_uuid_exits_1(self) -> None:
        with patch("munin.cli.main._forget", side_effect=ValueError("bad uuid")):
            out = _RUNNER.invoke(app, ["forget", "bad-id", "--yes"])
        assert out.exit_code == 1
        assert "not found" in out.stderr

    def test_infra_error_exits_2(self) -> None:
        from munin.core.errors import MuninDBError

        with patch("munin.cli.main._forget", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["forget", str(_ID), "--yes"])
        assert out.exit_code == 2
        assert "docker compose up -d" in out.stderr


class TestShowAfterForget:
    def test_show_returns_not_found_after_forget(self) -> None:
        """Simulate: forget succeeds, then show returns None."""
        with patch("munin.cli.main._forget", return_value=True):
            forget_out = _RUNNER.invoke(app, ["forget", str(_ID), "--yes"])
        assert forget_out.exit_code == 0

        with patch("munin.cli.main._show", return_value=None):
            show_out = _RUNNER.invoke(app, ["show", str(_ID)])
        assert show_out.exit_code == 1
        assert "not found" in show_out.stderr
