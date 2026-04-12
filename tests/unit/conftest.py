"""Unit test configuration.

Stubs out heavy runtime dependencies (psycopg_pool, httpx) that are not
available in a minimal test environment.  Integration tests use real deps.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub_psycopg_pool() -> None:
    """Insert a minimal psycopg_pool stub into sys.modules if absent."""
    if "psycopg_pool" in sys.modules:
        return

    stub = types.ModuleType("psycopg_pool")

    class ConnectionPool:
        def __init__(self, **kwargs: object) -> None:
            pass

    stub.ConnectionPool = ConnectionPool  # type: ignore[attr-defined]
    sys.modules["psycopg_pool"] = stub


_stub_psycopg_pool()
