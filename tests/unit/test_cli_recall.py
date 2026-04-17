"""Unit tests for the CLI recall command (US-003)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from munin.cli.main import app
from munin.core.memory import ThoughtResult

_RUNNER = CliRunner()

_ID1 = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_ID2 = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _result(
    *,
    row_id: uuid.UUID = _ID1,
    content: str = "auth decision was JWT",
    project: str = "myrepo",
    scope: str | None = None,
    tags: list[str] | None = None,
    similarity: float = 0.95,
) -> ThoughtResult:
    return ThoughtResult(
        id=row_id,
        content=content,
        project=project,
        scope=scope,
        tags=tags or [],
        metadata={},
        similarity=similarity,
        created_at=_TS,
    )


class TestRecallHumanOutput:
    def test_shows_table_with_result(self) -> None:
        results = [_result()]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "auth decision"])
        assert out.exit_code == 0
        assert "0.950" in out.output
        assert "auth decision was JWT" in out.output
        assert "myrepo" in out.output

    def test_project_scope_combined(self) -> None:
        results = [_result(scope="design")]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "query"])
        assert "myrepo/design" in out.output

    def test_content_truncated_to_120(self) -> None:
        long_content = "x" * 130
        results = [_result(content=long_content)]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "query"])
        assert "\u2026" in out.output
        # The displayed content should be truncated (120 chars + ellipsis)
        assert "x" * 121 not in out.output

    def test_empty_results_prints_table_no_rows(self) -> None:
        with patch("munin.cli.main._recall", return_value=[]):
            out = _RUNNER.invoke(app, ["recall", "nothing"])
        assert out.exit_code == 0

    def test_passes_all_filters(self) -> None:
        with patch("munin.cli.main._recall", return_value=[]) as mock_recall:
            _RUNNER.invoke(
                app,
                [
                    "recall", "query",
                    "--project", "P1",
                    "--scope", "design",
                    "--limit", "5",
                    "--threshold", "0.4",
                ],
            )
        mock_recall.assert_called_once_with(
            "query", project="P1", scope="design", limit=5, threshold=0.4
        )


class TestRecallJsonOutput:
    def test_json_mode_outputs_valid_json(self) -> None:
        results = [_result()]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "auth", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_json_fields_present(self) -> None:
        results = [_result(tags=["auth", "jwt"])]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "auth", "--json"])
        item = json.loads(out.output)[0]
        assert item["id"] == str(_ID1)
        assert item["content"] == "auth decision was JWT"
        assert item["project"] == "myrepo"
        assert item["scope"] is None
        assert item["tags"] == ["auth", "jwt"]
        assert item["similarity"] == pytest.approx(0.95)
        assert item["created_at"] == _TS.isoformat()

    def test_json_mode_no_ansi(self) -> None:
        results = [_result()]
        with patch("munin.cli.main._recall", return_value=results):
            out = _RUNNER.invoke(app, ["recall", "auth", "--json"])
        assert "\x1b[" not in out.output

    def test_json_empty_results(self) -> None:
        with patch("munin.cli.main._recall", return_value=[]):
            out = _RUNNER.invoke(app, ["recall", "nothing", "--json"])
        assert json.loads(out.output) == []


class TestRecallErrors:
    def test_infra_error_exits_2(self) -> None:
        from munin.core.errors import MuninDBError

        with patch("munin.cli.main._recall", side_effect=MuninDBError("conn failed")):
            out = _RUNNER.invoke(app, ["recall", "query"])
        assert out.exit_code == 2
        assert "podman compose up -d" in out.stderr

    def test_validation_error_exits_1(self) -> None:
        from munin.core.errors import MuninError

        with patch("munin.cli.main._recall", side_effect=MuninError("bad input")):
            out = _RUNNER.invoke(app, ["recall", "query"])
        assert out.exit_code == 1
        assert "bad input" in out.stderr
