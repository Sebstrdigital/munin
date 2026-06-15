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

from munin.core.config import MuninConfig
from munin.core.db import get_pool
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
    """A frequently-hit AND recently-used thought must rank above a genuinely
    stale (old + never-hit) thought when recalled with the same semantic query.

    Setup (deterministic via direct SQL UPDATE — no wall-clock timing dependency):
      - 'hot' thought: inserted, then backdated to 30 days ago for created_at,
        but given hit_count=5 and last_hit_at=now() to mark it recently used.
      - 'cold' thought: inserted, then backdated 365 days for both created_at
        and last_hit_at, with hit_count=0 to make it genuinely stale.
    Both have semantically similar content about "database indexing".

    MMR is disabled for this test because we are verifying the pure fused
    multi-signal ranking from the RPC (US-003), not MMR diversity (US-004).
    With two near-duplicate thoughts and MMR active, the diversity pass would
    re-order them, masking the hit_count + recency signal under test.
    """
    hot_content = "database indexing strategy with B-tree and hash indexes"
    cold_content = "database indexing approach using B-tree and hash structures"

    # Build a no-MMR config derived from the session config.
    no_mmr_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_w_rrf=cfg.recall_w_rrf,
        recall_w_recency=cfg.recall_w_recency,
        recall_w_hits=cfg.recall_w_hits,
        recall_rrf_k=cfg.recall_rrf_k,
        recall_mmr_enabled=False,
    )

    # Insert both thoughts (embeddings computed, rows created).
    remember(hot_content, project="pytest_hybrid_rank", config=no_mmr_cfg)
    remember(cold_content, project="pytest_hybrid_rank", config=no_mmr_cfg)

    # Deterministically set signal values via direct SQL UPDATE.
    # - hot: created 30 days ago, hit 5 times, last used NOW (recently active).
    # - cold: created 365 days ago, never hit, last_hit_at also 365 days ago (truly stale).
    pool = get_pool(no_mmr_cfg)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE thoughts
                   SET created_at  = now() - interval '30 days',
                       hit_count   = 5,
                       last_hit_at = now()
                 WHERE project = 'pytest_hybrid_rank'
                   AND content = %s
                """,
                (hot_content,),
            )
            cur.execute(
                """
                UPDATE thoughts
                   SET created_at  = now() - interval '365 days',
                       hit_count   = 0,
                       last_hit_at = now() - interval '365 days'
                 WHERE project = 'pytest_hybrid_rank'
                   AND content = %s
                """,
                (cold_content,),
            )

    # Recall with a neutral query that both thoughts match equally well.
    results = recall(
        "database indexing B-tree",
        project="pytest_hybrid_rank",
        config=no_mmr_cfg,
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
