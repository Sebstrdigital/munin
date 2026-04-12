"""CLI smoke test configuration.

Skips the entire module when:
- the compose stack (postgres:5433, embed:8088) is unreachable, or
- munin is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Generator

import pytest

# Point at the test stack.
os.environ.setdefault("MUNIN_DB_URL", "postgresql://munin:munin@localhost:5433/munin")
os.environ.setdefault("MUNIN_EMBED_URL", "http://localhost:8088")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_stack() -> None:
    if not _is_reachable("localhost", 5433):
        pytest.skip("postgres (localhost:5433) unreachable — start docker-compose stack first")
    if not _is_reachable("localhost", 8088):
        pytest.skip("embed server (localhost:8088) unreachable — start docker-compose stack first")
    if not shutil.which("munin"):
        pytest.skip("munin not on PATH — run `pip install -e .` first")


@pytest.fixture(scope="module")
def remembered_id() -> Generator[str, None, None]:
    """Store a thought, yield its UUID, then clean up."""
    result = subprocess.run(
        ["munin", "remember", "module-scoped fixture thought for cli smoke tests", "--project", "cli_test"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ},
    )
    assert result.returncode == 0, f"remember failed: {result.stderr}"
    thought_id = result.stdout.strip()
    yield thought_id
    # Teardown — best-effort delete.
    subprocess.run(
        ["munin", "forget", thought_id, "--yes"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ},
    )
