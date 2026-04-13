"""Unit tests for core.ingest."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from munin.core.ingest import IngestResult, _relativize, ingest


def test_relativize_returns_relative_path() -> None:
    """Relativize returns path relative to root when subpath."""
    root = Path("/home/user/project")
    path = Path("/home/user/project/docs/readme.md")
    result = _relativize(path, root)
    assert result == "docs/readme.md"


def test_relativize_returns_full_path_when_not_subpath() -> None:
    """Relativize returns full path when not under root."""
    root = Path("/home/user/project")
    path = Path("/home/other/docs/readme.md")
    result = _relativize(path, root)
    assert result == "/home/other/docs/readme.md"


def test_ingest_dry_run_no_db_calls(tmp_path: Path) -> None:
    """Dry run does not call database."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "test.md").write_text("# Hello\n\nContent here.")

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "test-project"
scope = "backend"
tags = ["docs"]
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(
                path=docs_dir,
                globs=["**/*.md"],
                project="test-project",
                scope="backend",
                tags=["docs"],
            )
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert result.files_scanned == 1
        assert result.chunks_stored == 1
        assert result.chunks_skipped == 0
        assert result.failures == 0


def test_ingest_skips_non_file_paths(tmp_path: Path) -> None:
    """Ignores directories in glob matches."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*"]
project = "test"
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*"], project="test", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert result.files_scanned == 0


def test_ingest_returns_result_object(tmp_path: Path) -> None:
    """Returns IngestResult with all fields populated."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    manifest = tmp_path / "sources.toml"
    manifest.write_text(f"""
[[source]]
path = "{docs_dir}"
globs = ["**/*.md"]
project = "test"
""")

    with patch("munin.core.ingest._load_sources") as mock_load:
        mock_load.return_value = [
            MagicMock(path=docs_dir, globs=["**/*.md"], project="test", scope=None, tags=[])
        ]

        result = ingest(sources_path=manifest, dry_run=True)

        assert isinstance(result, IngestResult)
        assert result.files_scanned == 0
        assert result.chunks_stored == 0
        assert result.chunks_skipped == 0
        assert result.failures == 0
