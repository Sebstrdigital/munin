"""Unit tests for the CLI import command (US-001)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from munin.cli.main import app

_RUNNER = CliRunner()
_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _write_jsonl(tmp_path: Path, lines: list[dict]) -> Path:  # type: ignore[type-arg]
    p = tmp_path / "thoughts.jsonl"
    p.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return p


class TestImportJsonl:
    def test_imports_valid_rows(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "hello"}, {"content": "world"}])
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert mock_rem.call_count == 2
        assert "Imported: 2" in out.output

    def test_summary_counts(self, tmp_path: Path) -> None:
        p = _write_jsonl(
            tmp_path,
            [
                {"content": "good"},
                {},  # missing content → skipped
                {"content": "also good"},
            ],
        )
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert "Imported: 2" in out.output
        assert "Skipped: 1" in out.output
        assert "Failed: 0" in out.output

    def test_json_output_flag(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "hi"}])
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(p), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data == {"imported": 1, "skipped": 0, "failed": 0}

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "thoughts.jsonl"
        p.write_text('{"content": "a"}\n\n{"content": "b"}\n', encoding="utf-8")
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert mock_rem.call_count == 2

    def test_missing_content_skipped_with_warning(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"project": "foo"}, {"content": "ok"}])
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert "Skipped: 1" in out.output
        assert "content" in out.stderr

    def test_failed_row_increments_failed(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "good"}, {"content": "bad"}])

        def side_effect(content: str, **_: object) -> uuid.UUID:
            if content == "bad":
                raise RuntimeError("boom")
            return _ID

        with patch("munin.cli.main._remember", side_effect=side_effect):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert "Imported: 1" in out.output
        assert "Failed: 1" in out.output

    def test_all_failed_exits_1(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "x"}])
        with patch("munin.cli.main._remember", side_effect=RuntimeError("boom")):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 1

    def test_all_skipped_exits_1(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"no_content": "x"}])
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 1

    def test_project_fallback_to_current(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "hi"}])
        with (
            patch("munin.cli.main._remember", return_value=_ID) as mock_rem,
            patch("munin.cli.main._current_project", return_value="auto-proj"),
        ):
            _RUNNER.invoke(app, ["import", str(p)])
        _call_kwargs = mock_rem.call_args
        assert _call_kwargs.kwargs.get("project") == "auto-proj"

    def test_project_from_row_used(self, tmp_path: Path) -> None:
        p = _write_jsonl(tmp_path, [{"content": "hi", "project": "row-proj"}])
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            _RUNNER.invoke(app, ["import", str(p)])
        assert mock_rem.call_args.kwargs.get("project") == "row-proj"

    def test_passes_scope_tags_metadata(self, tmp_path: Path) -> None:
        p = _write_jsonl(
            tmp_path,
            [{"content": "hi", "scope": "api", "tags": ["a", "b"], "metadata": {"k": "v"}}],
        )
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            _RUNNER.invoke(app, ["import", str(p)])
        kw = mock_rem.call_args.kwargs
        assert kw["scope"] == "api"
        assert kw["tags"] == ["a", "b"]
        assert kw["metadata"] == {"k": "v"}

    def test_markdown_empty_folder_exits_1(self, tmp_path: Path) -> None:
        d = tmp_path / "notes"
        d.mkdir()
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(d)])
        assert out.exit_code == 1

    def test_file_not_found_exits_nonzero(self, tmp_path: Path) -> None:
        out = _RUNNER.invoke(app, ["import", str(tmp_path / "missing.jsonl")])
        assert out.exit_code != 0

    def test_invalid_json_line_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text('{"content": "ok"}\nNOT_JSON\n', encoding="utf-8")
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(p)])
        assert out.exit_code == 0
        assert "Imported: 1" in out.output
        assert "Skipped: 1" in out.output
