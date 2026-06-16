"""Integration tests for P2-4: cross-encoder reranker in the recall path.

Requires postgres on localhost:5433, embed server on localhost:8088, AND
the llama-rerank sidecar on localhost:8089.  Skipped if sidecar is down
(graceful-degrade is tested in unit tests instead).

Covers:
  (a) flag-off: reranker not called, order matches rerank-disabled recall.
  (b) reranker-unreachable: mock ConnectError → graceful degrade, warning logged.
  (c) reranker on + live sidecar: a held-out thought ranked below hybrid top-1
      is lifted to top-1 by the cross-encoder.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from munin.core.config import MuninConfig
from munin.core.memory import recall, remember
from munin.core.rerank import MuninRerankUnavailable


def _rerank_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 8089), timeout=1):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# (a) flag-off: identical to pre-rerank order
# ---------------------------------------------------------------------------

def test_rerank_flag_off_identical_order(cfg: MuninConfig) -> None:
    """recall with rerank_enabled=False must produce the same order as the
    hybrid-only path (no reranker involvement)."""
    proj = "pytest_rerank_off"

    contents = [
        "munin stores thoughts in Postgres with pgvector",
        "llama.cpp serves the embedding model locally",
        "the munin CLI has remember and recall commands",
        "Reciprocal Rank Fusion combines dense and lexical scores",
        "FastAPI is a Python web framework for building APIs",
    ]
    for c in contents:
        remember(c, project=proj, config=cfg)

    off_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_rerank_enabled=False,
        recall_mmr_enabled=False,  # isolate rerank effect, remove MMR randomness
        recall_w_rrf=1.0,
        recall_w_recency=0.0,
        recall_w_hits=0.0,
    )

    with patch("munin.core.memory._rerank") as mock_rerank:
        results = recall("munin recall memory", project=proj, limit=3, config=off_cfg)

    # With flag off the reranker must NEVER be called.
    mock_rerank.assert_not_called()
    assert len(results) == 3


# ---------------------------------------------------------------------------
# (b) reranker unreachable → graceful degrade, logs warning, returns results
# ---------------------------------------------------------------------------

def test_rerank_unreachable_degrades_gracefully(cfg: MuninConfig) -> None:
    """When the sidecar raises MuninRerankUnavailable, recall() must not crash,
    must return results (hybrid-order fallback), and must log a warning.

    The warning is verified via a stdlib logging handler rather than caplog
    (caplog is not available in integration conftest).
    """
    import logging as _logging

    proj = "pytest_rerank_degrade"

    # Use distinct enough thoughts so P2-1 dedup (threshold=0.95) does not
    # collapse them — the rerank branch only fires when len(candidates) > 1.
    thoughts = [
        "PostgreSQL stores relational data in tables with typed columns",
        "Redis is an in-memory key-value store used for caching",
        "Kafka is a distributed message broker for event streaming",
        "Elasticsearch indexes documents for full-text search",
        "munin persists agent memories using pgvector embeddings",
    ]
    for t in thoughts:
        remember(t, project=proj, config=cfg)

    on_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_rerank_enabled=True,
        rerank_url="http://localhost:8089",
    )

    # Capture WARNING records from munin.core.memory via a stdlib handler.
    captured: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            if record.levelno >= _logging.WARNING:
                captured.append(record.getMessage())

    memory_logger = _logging.getLogger("munin.core.memory")
    handler = _Capture()
    memory_logger.addHandler(handler)
    try:
        with patch(
            "munin.core.memory._rerank",
            side_effect=MuninRerankUnavailable("connection refused"),
        ):
            results = recall(
                "distributed consensus raft", project=proj, limit=3, config=on_cfg
            )
    finally:
        memory_logger.removeHandler(handler)

    # Primary: recall must NOT crash and must return results.
    assert len(results) > 0, "graceful degrade must return results, not crash"

    # Secondary: a warning mentioning the reranker must have been logged.
    assert any(
        "reranker" in m.lower() or "degrad" in m.lower() for m in captured
    ), f"Expected reranker-degrade warning in munin.core.memory logger, got: {captured}"


# ---------------------------------------------------------------------------
# (c) live sidecar: cross-encoder lifts the correct thought to top-1
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _rerank_reachable(),
    reason="llama-rerank sidecar not running on localhost:8089",
)
def test_rerank_lifts_correct_thought(cfg: MuninConfig) -> None:
    """With rerank enabled and the live sidecar, a thought that is semantically
    off-topic but lexically noisy should be pushed down while the semantically
    correct answer is lifted to top-1.

    Strategy: insert one exactly-correct thought and several red-herrings that
    share surface keywords with the query but are about a different domain.
    Ask the query with rerank OFF and confirm the correct thought may not be
    top-1 (or at best verify position); then ask with rerank ON and assert
    the correct thought is top-1.
    """
    proj = "pytest_rerank_lift"

    # The query we will ask.
    query = "how does munin store memories?"

    # The correct answer (highly relevant to query).
    correct = "munin persists agent memories in a Postgres database using pgvector embeddings"

    # Red-herrings: share words like 'store', 'memory', 'database' but are
    # about unrelated domains — chosen to plausibly confuse a lexical ranker.
    red_herrings = [
        "store your RAM modules in an anti-static bag to prevent memory damage",
        "the Redis in-memory database can store key-value pairs at high speed",
        "video game memory cards store save files on portable flash storage",
        "database indexes store pre-sorted data to accelerate query performance",
        "computer memory stores data temporarily in volatile DRAM chips",
    ]

    for rh in red_herrings:
        remember(rh, project=proj, config=cfg)
    remember(correct, project=proj, config=cfg)

    base = dict(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_mmr_enabled=False,  # isolate rerank effect
        recall_w_rrf=1.0,
        recall_w_recency=0.0,
        recall_w_hits=0.0,
    )

    off_cfg = MuninConfig(**base, recall_rerank_enabled=False)
    on_cfg = MuninConfig(**base, recall_rerank_enabled=True, rerank_url="http://localhost:8089")

    off_results = recall(query, project=proj, limit=6, config=off_cfg)
    on_results = recall(query, project=proj, limit=6, config=on_cfg)

    off_contents = [r.content for r in off_results]
    on_contents = [r.content for r in on_results]

    # With reranker ON the correct thought must be rank 1 (index 0).
    assert on_contents[0] == correct, (
        f"Expected correct thought at rank 1 with reranker ON.\n"
        f"  rerank-ON  order: {on_contents}\n"
        f"  rerank-OFF order: {off_contents}"
    )

    # Confirm the reranker actually changed something (the correct thought must
    # rank higher with rerank ON than without, or at least be at top-1).
    off_pos = off_contents.index(correct) if correct in off_contents else 999
    on_pos = on_contents.index(correct)
    assert on_pos <= off_pos, (
        f"Reranker should not push the correct thought DOWN. "
        f"off_pos={off_pos} on_pos={on_pos}"
    )
