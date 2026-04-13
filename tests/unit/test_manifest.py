"""Unit tests for munin.core.manifest."""

from pathlib import Path

import pytest

from munin.core.errors import MuninError
from munin.core.manifest import load_sources


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_valid_sources_parsed(tmp_path: Path) -> None:
    """Valid source entries are parsed into SourceConfig objects."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "my-project"
scope = "backend"
tags = ["docs"]
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 1
    assert sources[0].path == docs_dir
    assert sources[0].globs == ["**/*.md"]
    assert sources[0].project == "my-project"
    assert sources[0].scope == "backend"
    assert sources[0].tags == ["docs"]


def test_multiple_sources(tmp_path: Path) -> None:
    """Multiple source entries are all parsed."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "docs"

[[source]]
path = "{code_dir}"
globs = ["**/*.py"]
project = "code"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 2
    assert sources[0].project == "docs"
    assert sources[1].project == "code"


def test_missing_path_logs_warning_and_skips(tmp_path: Path, caplog) -> None:
    """Source entry without path is skipped with warning."""
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        """
[[source]]
globs = ["**/*.md"]
project = "test"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 0
    assert "missing 'path'" in caplog.text


def test_nonexistent_path_logs_warning_and_skips(tmp_path: Path, caplog) -> None:
    """Source path that doesn't exist is skipped with warning."""
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        """
[[source]]
path = "/nonexistent/path"
globs = ["**/*.md"]
project = "test"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 0
    assert "does not exist" in caplog.text


def test_no_globs_defaults_to_markdown(tmp_path: Path) -> None:
    """Source without globs defaults to ['**/*.md']."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
project = "test"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 1
    assert sources[0].globs == ["**/*.md"]


def test_no_project_logs_warning_and_skips(tmp_path: Path, caplog) -> None:
    """Source without project logs a warning and is skipped."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 0
    assert "missing required 'project' field" in caplog.text


def test_missing_manifest_raises_munin_error(tmp_path: Path) -> None:
    """Non-existent manifest raises MuninError with helpful message."""
    nonexistent = tmp_path / "nonexistent.toml"

    with pytest.raises(MuninError, match="Manifest not found"):
        load_sources(sources_path=nonexistent)


def test_optional_scope_and_tags(tmp_path: Path) -> None:
    """Scope and tags are optional and default to None/empty list."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "test"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert len(sources) == 1
    assert sources[0].scope is None
    assert sources[0].tags == []


def test_single_glob_converts_to_list(tmp_path: Path) -> None:
    """Single glob string is converted to a list."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    manifest = tmp_path / "sources.toml"
    _write_toml(
        manifest,
        f"""
[[source]]
path = "{docs_dir}"
globs = "**/*.md"
project = "test"
""",
    )

    sources = load_sources(sources_path=manifest)

    assert sources[0].globs == ["**/*.md"]
