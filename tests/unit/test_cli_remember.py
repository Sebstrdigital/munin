"""Unit tests for the CLI remember command --json flag (US-006)."""

from __future__ import annotations

import json
import re
import uuid
from unittest.mock import patch

from typer.testing import CliRunner

from munin.cli.main import app
from munin.core.errors import MuninDBError, MuninEmbedError

_RUNNER = CliRunner()

_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class TestRememberJsonOutput:
    def test_json_contains_id(self) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["remember", "hello", "--project", "myrepo", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["id"] == str(_ID)

    def test_json_contains_project(self) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["remember", "hello", "--project", "myrepo", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["project"] == "myrepo"

    def test_json_no_ansi(self) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["remember", "hello", "--json"])
        assert out.exit_code == 0
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        assert not ansi.search(out.output)

    def test_json_parseable(self) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["remember", "some content", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert isinstance(data, dict)
        assert set(data.keys()) == {"id", "project"}

    def test_human_output_without_flag(self) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["remember", "hello"])
        assert out.exit_code == 0
        assert str(_ID) in out.output


class TestRememberErrors:
    def test_db_error_exits_2(self) -> None:
        with patch("munin.cli.main._remember", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["remember", "hello", "--json"])
        assert out.exit_code == 2
        assert "podman compose up -d" in out.stderr
        assert "database" in out.stderr

    def test_embed_error_exits_2(self) -> None:
        with patch("munin.cli.main._remember", side_effect=MuninEmbedError("no server")):
            out = _RUNNER.invoke(app, ["remember", "hello", "--json"])
        assert out.exit_code == 2
        assert "embed server" in out.stderr

    def test_no_content_exits_1(self) -> None:
        out = _RUNNER.invoke(app, ["remember"])
        assert out.exit_code == 1
        assert "no content" in out.stderr
