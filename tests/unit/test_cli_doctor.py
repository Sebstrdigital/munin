"""Unit tests for the CLI doctor command (US-003)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from munin.cli.main import app
from munin.core.config import MuninConfig
from munin.core.errors import MuninDBError, MuninEmbedError

_RUNNER = CliRunner()

_CFG = MuninConfig(
    db_url="postgresql://munin:munin@localhost:5433/munin",
    embed_url="http://localhost:8088",
    embed_dim=768,
    default_limit=10,
    embed_batch_size=32,
)


def _make_pool(*, reachable: bool = True, has_schema: bool = True, has_functions: bool = True) -> MagicMock:
    """Build a pool mock for the given scenario."""
    pool = MagicMock()
    pool.closed = True

    if not reachable:
        pool.open.side_effect = MuninDBError("connection refused")
        return pool

    cur = pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (1,) if has_schema else None
    if has_functions:
        cur.fetchall.return_value = [("match_thoughts",), ("upsert_thought",)]
    else:
        cur.fetchall.return_value = []

    return pool


class TestDoctorAllPass:
    def test_exit_0_when_all_pass(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 0

    def test_output_has_only_pass_marks(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert "✓" in out.output
        assert "✗" not in out.output

    def test_all_seven_checks_listed(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        for name in (
            "config_loaded",
            "db_reachable",
            "schema_present",
            "functions_present",
            "embed_reachable",
            "embed_dim_matches",
            "log_dir_writable",
        ):
            assert name in out.output

    def test_json_all_passed_true(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["all_passed"] is True
        assert len(data["checks"]) == 7
        assert all(c["passed"] for c in data["checks"])


class TestDoctorFailures:
    def test_exit_1_when_config_fails(self) -> None:
        with (
            patch("munin.cli.main._load_config", side_effect=Exception("bad config")),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1

    def test_config_fail_shows_hint(self) -> None:
        with (
            patch("munin.cli.main._load_config", side_effect=Exception("bad config")),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert "✗" in out.output
        assert "config.toml" in out.output

    def test_db_unreachable_shows_hint(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool(reachable=False)),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1
        assert "podman compose up -d" in out.output

    def test_embed_unreachable_shows_hint(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", side_effect=MuninEmbedError("no server")),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1

    def test_embed_dim_mismatch_fails(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 512),  # wrong dim
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1
        assert "✗" in out.output

    def test_schema_missing_fails(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool(has_schema=False)),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1

    def test_functions_missing_fails(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool(has_functions=False)),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor"])
        assert out.exit_code == 1


class TestDoctorJsonStructure:
    def test_json_has_required_keys(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor", "--json"])
        data = json.loads(out.output)
        assert set(data.keys()) == {"checks", "all_passed"}

    def test_json_check_has_name_and_passed(self) -> None:
        with (
            patch("munin.cli.main._load_config", return_value=_CFG),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor", "--json"])
        data = json.loads(out.output)
        for check in data["checks"]:
            assert "name" in check
            assert "passed" in check

    def test_json_all_passed_false_when_fail(self) -> None:
        with (
            patch("munin.cli.main._load_config", side_effect=Exception("bad config")),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor", "--json"])
        data = json.loads(out.output)
        assert data["all_passed"] is False
        config_check = next(c for c in data["checks"] if c["name"] == "config_loaded")
        assert config_check["passed"] is False

    def test_json_exit_1_when_any_fail(self) -> None:
        with (
            patch("munin.cli.main._load_config", side_effect=Exception("bad config")),
            patch("munin.cli.main._get_pool", return_value=_make_pool()),
            patch("munin.cli.main._embed", return_value=[0.1] * 768),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            out = _RUNNER.invoke(app, ["doctor", "--json"])
        assert out.exit_code == 1
