"""Unit tests for CLI projects and stats commands (US-005)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from munin.cli.main import app
from munin.core.errors import MuninDBError, MuninEmbedError

_RUNNER = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


class TestProjectsHumanOutput:
    def test_shows_table_with_results(self) -> None:
        rows = [("alpha", 3), ("beta", 7)]
        with patch("munin.cli.main._list_projects", return_value=rows):
            out = _RUNNER.invoke(app, ["projects"])
        assert out.exit_code == 0
        assert "alpha" in out.output
        assert "beta" in out.output
        assert "3" in out.output
        assert "7" in out.output

    def test_empty_table(self) -> None:
        with patch("munin.cli.main._list_projects", return_value=[]):
            out = _RUNNER.invoke(app, ["projects"])
        assert out.exit_code == 0

    def test_columns_present(self) -> None:
        rows = [("myrepo", 1)]
        with patch("munin.cli.main._list_projects", return_value=rows):
            out = _RUNNER.invoke(app, ["projects"])
        assert "Project" in out.output
        assert "Count" in out.output


class TestProjectsJsonOutput:
    def test_json_valid_array(self) -> None:
        rows = [("alpha", 3), ("beta", 7)]
        with patch("munin.cli.main._list_projects", return_value=rows):
            out = _RUNNER.invoke(app, ["projects", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_json_fields(self) -> None:
        rows = [("alpha", 3)]
        with patch("munin.cli.main._list_projects", return_value=rows):
            out = _RUNNER.invoke(app, ["projects", "--json"])
        item = json.loads(out.output)[0]
        assert item["project"] == "alpha"
        assert item["count"] == 3

    def test_json_empty(self) -> None:
        with patch("munin.cli.main._list_projects", return_value=[]):
            out = _RUNNER.invoke(app, ["projects", "--json"])
        assert json.loads(out.output) == []


class TestProjectsErrors:
    def test_db_error_exits_2(self) -> None:
        with patch("munin.cli.main._list_projects", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["projects"])
        assert out.exit_code == 2
        assert "check that docker services are running" in out.stderr

    def test_embed_error_exits_2(self) -> None:
        with patch("munin.cli.main._list_projects", side_effect=MuninEmbedError("no embed")):
            out = _RUNNER.invoke(app, ["projects"])
        assert out.exit_code == 2


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def _mock_pool(total_thoughts: int = 5, db_size: int = 32768) -> MagicMock:
    """Build a mock pool whose cursor returns predictable rows."""
    cur = MagicMock()
    cur.fetchone.side_effect = [(total_thoughts,), (db_size,)]

    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=conn_cm)
    conn_cm.__exit__ = MagicMock(return_value=False)
    conn_cm.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn_cm.cursor.return_value.__exit__ = MagicMock(return_value=False)

    pool = MagicMock()
    pool.connection.return_value = conn_cm
    return pool


class TestStatsHumanOutput:
    def test_shows_all_fields(self) -> None:
        pool = _mock_pool(total_thoughts=42, db_size=65536)
        with (
            patch("munin.cli.main._list_projects", return_value=[("p1", 10), ("p2", 32)]),
            patch("munin.cli.main._get_pool", return_value=pool),
            patch("munin.cli.main._load_config") as mock_cfg,
            patch("munin.cli.main._embed", return_value=[0.1]),
        ):
            mock_cfg.return_value.embed_url = "http://localhost:8088"
            out = _RUNNER.invoke(app, ["stats"])

        assert out.exit_code == 0
        assert "42" in out.output
        assert "2" in out.output  # total_projects
        assert "65,536" in out.output  # db_size formatted
        assert "http://localhost:8088" in out.output
        assert "yes" in out.output

    def test_embed_unreachable_shows_no(self) -> None:
        pool = _mock_pool()
        with (
            patch("munin.cli.main._list_projects", return_value=[]),
            patch("munin.cli.main._get_pool", return_value=pool),
            patch("munin.cli.main._load_config") as mock_cfg,
            patch("munin.cli.main._embed", side_effect=MuninEmbedError("down")),
        ):
            mock_cfg.return_value.embed_url = "http://localhost:8088"
            out = _RUNNER.invoke(app, ["stats"])

        assert out.exit_code == 0
        assert "no" in out.output


class TestStatsJsonOutput:
    def test_json_valid_object(self) -> None:
        pool = _mock_pool(total_thoughts=10, db_size=8192)
        with (
            patch("munin.cli.main._list_projects", return_value=[("r1", 10)]),
            patch("munin.cli.main._get_pool", return_value=pool),
            patch("munin.cli.main._load_config") as mock_cfg,
            patch("munin.cli.main._embed", return_value=[0.1]),
        ):
            mock_cfg.return_value.embed_url = "http://localhost:8088"
            out = _RUNNER.invoke(app, ["stats", "--json"])

        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["total_thoughts"] == 10
        assert data["total_projects"] == 1
        assert data["db_size_bytes"] == 8192
        assert data["embed_url"] == "http://localhost:8088"
        assert data["embed_reachable"] is True

    def test_json_embed_unreachable(self) -> None:
        pool = _mock_pool()
        with (
            patch("munin.cli.main._list_projects", return_value=[]),
            patch("munin.cli.main._get_pool", return_value=pool),
            patch("munin.cli.main._load_config") as mock_cfg,
            patch("munin.cli.main._embed", side_effect=MuninEmbedError("down")),
        ):
            mock_cfg.return_value.embed_url = "http://localhost:8088"
            out = _RUNNER.invoke(app, ["stats", "--json"])

        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["embed_reachable"] is False


class TestStatsErrors:
    def test_db_error_exits_2(self) -> None:
        with patch("munin.cli.main._list_projects", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["stats"])
        assert out.exit_code == 2
        assert "check that docker services are running" in out.stderr

    def test_db_error_no_traceback(self) -> None:
        with patch("munin.cli.main._list_projects", side_effect=MuninDBError("bad")):
            out = _RUNNER.invoke(app, ["stats"])
        assert "Traceback" not in out.output
        assert "Traceback" not in out.stderr
