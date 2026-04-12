"""Project detection from git root."""

from __future__ import annotations

import functools
from pathlib import Path


def current_project(cwd: Path | None = None) -> str | None:
    """Walk up from cwd looking for .git; return the containing directory name.

    Returns None if no git root is found.
    """
    root = (cwd or Path.cwd()).resolve()
    return _find_project(root)


@functools.lru_cache(maxsize=None)
def _find_project(resolved_dir: Path) -> str | None:
    """Cached project lookup keyed on resolved absolute path."""
    for parent in [resolved_dir, *resolved_dir.parents]:
        if (parent / ".git").exists():
            return parent.name
    return None
