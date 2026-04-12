"""Integration test configuration.

Sets MUNIN_DB_URL and MUNIN_EMBED_URL before munin imports, then skips the
entire module if either component is unreachable.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Generator

import pytest

# ── env setup (must happen before any munin import) ──────────────────────────
os.environ["MUNIN_DB_URL"] = "postgresql://munin:munin@localhost:5433/munin"
os.environ["MUNIN_EMBED_URL"] = "http://localhost:8088"


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


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

from munin.core.config import MuninConfig, load  # noqa: E402
from munin.core.db import get_pool  # noqa: E402


@pytest.fixture(scope="session")
def cfg() -> MuninConfig:
    """Load config from env vars set above."""
    return load()


@pytest.fixture(scope="session", autouse=True)
def open_pool(cfg: MuninConfig) -> Generator[None, None, None]:
    """Open the connection pool once for the entire test session."""
    pool = get_pool(cfg)
    pool.open(wait=True)
    yield
    pool.close()


@pytest.fixture(autouse=True)
def truncate_thoughts(cfg: MuninConfig) -> Generator[None, None, None]:
    """Truncate thoughts table before each test for deterministic runs."""
    pool = get_pool(cfg)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE thoughts")
    yield
