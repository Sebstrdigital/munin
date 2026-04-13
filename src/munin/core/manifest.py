"""Manifest parsing for sources.toml."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from munin.core.errors import MuninError

_DEFAULT_SOURCES_PATH = Path.home() / ".config" / "munin" / "sources.toml"

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """Configuration for a knowledge source."""

    path: Path
    globs: list[str]
    project: str
    scope: str | None
    tags: list[str]


def load_sources(sources_path: Path | None = None) -> list[SourceConfig]:
    """Load and validate source configurations from a sources.toml manifest.

    Args:
        sources_path: Path to sources.toml. Defaults to ~/.config/munin/sources.toml

    Returns:
        List of validated SourceConfig objects

    Raises:
        MuninError: If the manifest file does not exist
    """
    path = sources_path if sources_path is not None else _DEFAULT_SOURCES_PATH

    if not path.exists():
        example = """# Example ~/.config/munin/sources.toml
[[source]]
path = "/path/to/your/docs"
globs = ["**/*.md", "**/*.py"]
project = "my-project"
scope = "backend"  # optional
tags = ["docs", "code"]  # optional
"""
        raise MuninError(f"Manifest not found at {path}. Expected format:\n{example}")

    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    sources = data.get("source", [])
    configs: list[SourceConfig] = []

    for source in sources:
        raw_path = source.get("path")
        if not raw_path:
            logger.warning("Source entry missing 'path', skipping")
            continue

        source_path = Path(raw_path)

        if not source_path.exists():
            logger.warning(f"Source path does not exist: {source_path}, skipping")
            continue

        globs = source.get("globs", [])
        if not globs:
            logger.warning(f"No globs defined for source {source_path}, skipping")
            continue

        project = source.get("project")
        if not project:
            logger.warning(f"No project defined for source {source_path}, skipping")
            continue

        config = SourceConfig(
            path=source_path,
            globs=globs if isinstance(globs, list) else [globs],
            project=project,
            scope=source.get("scope"),
            tags=source.get("tags", []),
        )
        configs.append(config)

    return configs
