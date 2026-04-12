"""MCP smoke test configuration.

Skips the entire module when:
- the compose stack (postgres:5433, embed:8088) is unreachable, or
- munin-mcp is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import socket

import pytest

# ── env setup (must happen before any munin import) ──────────────────────────
os.environ.setdefault("MUNIN_DB_URL", "postgresql://munin:munin@localhost:5433/munin")
os.environ.setdefault("MUNIN_EMBED_URL", "http://localhost:8088")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


if shutil.which("munin-mcp") is None:
    pytest.skip(
        "munin-mcp not found on PATH — run `pip install -e .` first",
        allow_module_level=True,
    )

if not _reachable("localhost", 5433):
    pytest.skip(
        "postgres (localhost:5433) is unreachable — start docker-compose stack first",
        allow_module_level=True,
    )

if not _reachable("localhost", 8088):
    pytest.skip(
        "embed server (localhost:8088) is unreachable — start docker-compose stack first",
        allow_module_level=True,
    )
