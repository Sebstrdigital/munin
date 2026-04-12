"""Unit tests for the CLI import command — markdown folder format (US-002)."""

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


def _md(tmp_path: Path, name: str, body: str, frontmatter: str = "") -> Path:
    p = tmp_path / name
    if frontmatter:
        p.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    else:
        p.write_text(body, encoding="utf-8")
    return p


class TestImportMarkdown:
    def test_imports_md_file_without_frontmatter(self, tmp_path: Path) -> None:
        _md(tmp_path, "note.md", "hello world")
        with (
            patch("munin.cli.main._remember", return_value=_ID) as mock_rem,
            patch("munin.cli.main._current_project", return_value="auto-proj"),
        ):
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        assert mock_rem.call_count == 1
        assert "Imported: 1" in out.output

    def test_frontmatter_populates_fields(self, tmp_path: Path) -> None:
        _md(
            tmp_path,
            "note.md",
            "content here",
            "project: myproj\nscope: api\ntags:\n  - a\n  - b\nmetadata:\n  k: v",
        )
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        kw = mock_rem.call_args.kwargs
        assert kw["project"] == "myproj"
        assert kw["scope"] == "api"
        assert kw["tags"] == ["a", "b"]
        assert kw["metadata"] == {"k": "v"}

    def test_no_frontmatter_uses_current_project(self, tmp_path: Path) -> None:
        _md(tmp_path, "note.md", "plain content")
        with (
            patch("munin.cli.main._remember", return_value=_ID) as mock_rem,
            patch("munin.cli.main._current_project", return_value="inferred"),
        ):
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        assert mock_rem.call_args.kwargs.get("project") == "inferred"

    def test_empty_content_skipped(self, tmp_path: Path) -> None:
        _md(tmp_path, "empty.md", "   \n  ")
        _md(tmp_path, "good.md", "real content")
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        assert mock_rem.call_count == 1
        assert "Imported: 1" in out.output
        assert "Skipped: 1" in out.output

    def test_multiple_files_all_imported(self, tmp_path: Path) -> None:
        for i in range(3):
            _md(tmp_path, f"note{i}.md", f"content {i}")
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        assert mock_rem.call_count == 3
        assert "Imported: 3" in out.output

    def test_json_output_flag(self, tmp_path: Path) -> None:
        _md(tmp_path, "note.md", "hello")
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(tmp_path), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data == {"imported": 1, "skipped": 0, "failed": 0}

    def test_failed_row_increments_failed(self, tmp_path: Path) -> None:
        _md(tmp_path, "good.md", "good content")
        _md(tmp_path, "bad.md", "bad content")

        def side_effect(content: str, **_: object) -> uuid.UUID:
            if content == "bad content":
                raise RuntimeError("boom")
            return _ID

        with patch("munin.cli.main._remember", side_effect=side_effect):
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 0
        assert "Imported: 1" in out.output
        assert "Failed: 1" in out.output

    def test_empty_folder_exits_1(self, tmp_path: Path) -> None:
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert out.exit_code == 1

    def test_idempotent_reimport(self, tmp_path: Path) -> None:
        """Re-running import calls remember for each file each time (upsert handled by core)."""
        _md(tmp_path, "note.md", "hello")
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            _RUNNER.invoke(app, ["import", str(tmp_path)])
            _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert mock_rem.call_count == 2

    def test_format_flag_forces_markdown_on_file(self, tmp_path: Path) -> None:
        """--format markdown on a non-dir path still tries markdown import."""
        d = tmp_path / "notes"
        d.mkdir()
        _md(d, "x.md", "content")
        with patch("munin.cli.main._remember", return_value=_ID):
            out = _RUNNER.invoke(app, ["import", str(d), "--format", "markdown"])
        assert out.exit_code == 0
        assert "Imported: 1" in out.output

    def test_non_md_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "note.txt").write_text("ignored", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        _md(tmp_path, "real.md", "real content")
        with patch("munin.cli.main._remember", return_value=_ID) as mock_rem:
            out = _RUNNER.invoke(app, ["import", str(tmp_path)])
        assert mock_rem.call_count == 1
        assert "Imported: 1" in out.output
