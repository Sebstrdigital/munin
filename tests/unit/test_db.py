"""Unit tests for core.db — singleton pool behavior without real connections."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from munin.core.config import MuninConfig
from munin.core.db import _pools, get_pool


@pytest.fixture(autouse=True)
def clear_pool_cache() -> Iterator[None]:
    """Ensure pool cache is clean before and after each test."""
    _pools.clear()
    yield
    _pools.clear()


@pytest.fixture()
def cfg() -> MuninConfig:
    return MuninConfig(
        db_url="postgresql://munin:munin@localhost:5433/munin",
        embed_url="http://localhost:8088",
        embed_dim=768,
        default_limit=10,
        embed_batch_size=32,
    )


def test_get_pool_returns_connection_pool(cfg: MuninConfig) -> None:
    """get_pool() returns a ConnectionPool instance."""
    import psycopg_pool

    with patch.object(psycopg_pool.ConnectionPool, "__init__", return_value=None):
        pool = get_pool(cfg)
    assert isinstance(pool, psycopg_pool.ConnectionPool)


def test_get_pool_singleton(cfg: MuninConfig) -> None:
    """Calling get_pool() twice returns the exact same object."""
    import psycopg_pool

    with patch.object(psycopg_pool.ConnectionPool, "__init__", return_value=None):
        pool_a = get_pool(cfg)
        pool_b = get_pool(cfg)

    assert pool_a is pool_b


def test_get_pool_different_urls_different_instances() -> None:
    """Different db_url values produce different pool instances."""
    import psycopg_pool

    cfg_a = MuninConfig(
        db_url="postgresql://a:a@localhost:5433/db_a",
        embed_url="http://localhost:8088",
        embed_dim=768,
        default_limit=10,
        embed_batch_size=32,
    )
    cfg_b = MuninConfig(
        db_url="postgresql://b:b@localhost:5433/db_b",
        embed_url="http://localhost:8088",
        embed_dim=768,
        default_limit=10,
        embed_batch_size=32,
    )

    with patch.object(psycopg_pool.ConnectionPool, "__init__", return_value=None):
        pool_a = get_pool(cfg_a)
        pool_b = get_pool(cfg_b)

    assert pool_a is not pool_b


def test_get_pool_constructor_called_once(cfg: MuninConfig) -> None:
    """ConnectionPool constructor is invoked exactly once per url."""
    import psycopg_pool

    with patch.object(
        psycopg_pool.ConnectionPool, "__init__", return_value=None
    ) as mock_init:
        get_pool(cfg)
        get_pool(cfg)
        get_pool(cfg)

    mock_init.assert_called_once()
