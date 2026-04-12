"""Database connection pool singleton."""

from __future__ import annotations

import logging
from typing import Any

import psycopg_pool

from munin.core.config import MuninConfig, load
from munin.core.errors import MuninDBError

logger = logging.getLogger(__name__)

_pools: dict[str, psycopg_pool.ConnectionPool[Any]] = {}


def get_pool(config: MuninConfig | None = None) -> psycopg_pool.ConnectionPool[Any]:
    """Return the process-level ConnectionPool for the given config's db_url.

    Calling this function twice with the same db_url returns the same instance.
    The pool is created lazily on first call and reused on subsequent calls.
    """
    cfg = config if config is not None else load()
    url = cfg.db_url

    if url not in _pools:
        logger.debug("db: creating connection pool for %s", url)
        try:
            _pools[url] = psycopg_pool.ConnectionPool(
                conninfo=url,
                min_size=1,
                max_size=4,
                open=False,
            )
        except Exception as exc:
            raise MuninDBError(f"Failed to create connection pool for {url}: {exc}") from exc

    return _pools[url]
