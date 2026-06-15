"""Integration tests for US-003: hybrid RRF + multi-signal ranking.

Requires postgres on localhost:5433 and embed server on localhost:8088.
Run: pytest tests/integration/test_hybrid_recall.py -v

Covers:
  - Exact-identifier recall via the lexical leg (even when vector similarity
    alone would miss it).
  - Recency + hit_count tie-breaking: a frequently-hit recent thought ranks
    above an equally-similar stale never-hit thought.
  - Project isolation: no thought from another project leaks into results.
"""
from __future__ import annotations

import time

import psycopg

from munin.core.config import MuninConfig
from munin.core.memory import recall, remember


# ---------------------------------------------------------------------------
# Lexical leg: exact identifier recall
# ---------------------------------------------------------------------------


def test_exact_identifier_recall_via_lexical_leg(cfg: MuninConfig) -> None:
    """A thought containing a rare identifier is recalled when that identifier
    is used as the query, even if the vector similarity alone might not rank
    it first.

    We insert a thought with a unique token and a distracting thought whose
    content is semantically closer to common "vector search" vocabulary.
    The unique token must appear in the results.
    """
    unique_token = "XJZKP9_UNIQUE_IDENTIFIER_FOR_LEXICAL_TEST"
    remember(
        f"configuration key {unique_token} controls the cache expiry policy",
        project="pytest_hybrid",
        config=cfg,
    )
    remember(
        "vector similarity search using pgvector and cosine distance embeddings",
        project="pytest_hybrid",
        config=cfg,
    )

    results = recall(unique_token, project="pytest_hybrid", config=cfg, limit=10)

    contents = [r.content for r in results]
    assert any(unique_token in c for c in contents), (
        f"Thought with {unique_token!r} not found in results: {contents}"
    )


# ---------------------------------------------------------------------------
# Multi-signal: recency + hit_count tie-breaking
# ---------------------------------------------------------------------------


def test_hit_count_recent_thought_ranks_above_stale(cfg: MuninConfig) -> None:
    """A frequently-hit recent thought must rank above an equally-similar
    stale never-hit thought when recalled with the same semantic query.

    Setup:
      - 'hot' thought: inserted, then hit several times (hit_count > 0, recent last_hit_at)
      - 'cold' thought: inserted, never recalled (hit_count = 0, no last_hit_at)
    Both have semantically similar content about "database indexing".
    """
    hot_content = "database indexing strategy with B-tree and hash indexes"
    cold_content = "database indexing approach using B-tree and hash structures"

    # Insert hot thought and recall it a few times to raise hit_count.
    remember(hot_content, project="pytest_hybrid_rank", config=cfg)
    # Bump hit count by recalling the hot thought multiple times.
    for _ in range(3):
        recall(hot_content, project="pytest_hybrid_rank", config=cfg, limit=5)

    # Insert cold thought after — no further recalls.
    remember(cold_content, project="pytest_hybrid_rank", config=cfg)

    # Now recall with a neutral query that both thoughts match equally well.
    results = recall(
        "database indexing B-tree",
        project="pytest_hybrid_rank",
        config=cfg,
        limit=10,
    )

    assert len(results) >= 2, f"Expected at least 2 results, got {len(results)}"

    # Find positions of hot and cold thoughts.
    positions: dict[str, int] = {}
    for idx, r in enumerate(results):
        if hot_content in r.content:
            positions["hot"] = idx
        elif cold_content in r.content:
            positions["cold"] = idx

    assert "hot" in positions, f"hot thought not found in results: {[r.content for r in results]}"
    assert "cold" in positions, f"cold thought not found in results: {[r.content for r in results]}"

    assert positions["hot"] < positions["cold"], (
        f"Expected hot thought (pos {positions['hot']}) to rank above "
        f"cold thought (pos {positions['cold']})"
    )


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------


def test_project_isolation_no_cross_project_leak(cfg: MuninConfig) -> None:
    """Hybrid recall must not return thoughts from a different project."""
    remember(
        "shared vocabulary: vector search database embeddings",
        project="pytest_hybrid_proj_a",
        config=cfg,
    )
    remember(
        "shared vocabulary: vector search database embeddings rival entry",
        project="pytest_hybrid_proj_b",
        config=cfg,
    )

    results = recall(
        "vector search database embeddings",
        project="pytest_hybrid_proj_a",
        config=cfg,
        limit=10,
    )

    returned_projects = {r.project for r in results}
    assert "pytest_hybrid_proj_b" not in returned_projects, (
        f"Cross-project leak detected. Projects in results: {returned_projects}"
    )
    assert "pytest_hybrid_proj_a" in returned_projects


# ---------------------------------------------------------------------------
# Scope filtering preserved
# ---------------------------------------------------------------------------


def test_scope_filter_preserved(cfg: MuninConfig) -> None:
    """Hybrid recall with scope= must exclude thoughts from other scopes."""
    remember(
        "architecture decision: use HNSW index for approximate nearest neighbour",
        project="pytest_hybrid_scope",
        scope="architecture",
        config=cfg,
    )
    remember(
        "todo: benchmark HNSW index performance and tune ef_search parameter",
        project="pytest_hybrid_scope",
        scope="todo",
        config=cfg,
    )

    results = recall(
        "HNSW index",
        project="pytest_hybrid_scope",
        scope="architecture",
        config=cfg,
        limit=10,
    )

    assert all(r.scope == "architecture" for r in results), (
        f"Scope leak: {[r.scope for r in results]}"
    )
    assert len(results) >= 1
