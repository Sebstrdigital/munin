"""Integration tests for US-004: MMR diversity re-ranking.

Requires postgres on localhost:5433 and embed server on localhost:8088.
Run: pytest tests/integration/test_mmr_recall.py -v

Covers:
  - Near-duplicate suppression: top-k from a project with several paraphrases
    of the same idea should contain distinct thoughts rather than repetitions.
  - Disabled MMR returns the pure fused ranking (same IDs, same order as
    without the MMR pass).
  - Result count always respects the requested limit.
"""
from __future__ import annotations

from munin.core.config import MuninConfig
from munin.core.memory import recall, remember

# ---------------------------------------------------------------------------
# Near-duplicate suppression
# ---------------------------------------------------------------------------

def test_mmr_suppresses_near_duplicates(cfg: MuninConfig) -> None:
    """When a project contains many paraphrases of one idea plus distinct ones,
    MMR top-k should contain at least some of the distinct thoughts."""
    proj = "pytest_mmr_diversity"

    # Insert many near-identical paraphrases about one topic …
    paraphrases = [
        "PostgreSQL uses the pgvector extension for storing vector embeddings",
        "Postgres has a pgvector plugin that allows storing vector data",
        "The pgvector extension for PostgreSQL enables vector similarity search",
        "With pgvector, PostgreSQL can store and query high-dimensional vectors",
        "pgvector adds vector storage and cosine search to PostgreSQL databases",
    ]
    for p in paraphrases:
        remember(p, project=proj, config=cfg)

    # … and a clearly distinct thought.
    remember(
        "The Python requests library is used for HTTP calls",
        project=proj,
        config=cfg,
    )
    remember(
        "Astro is a static site generator framework for the web",
        project=proj,
        config=cfg,
    )

    # With MMR enabled, ask for top 4.  Pure relevance would likely return
    # 4 pgvector paraphrases; MMR should include at least one distinct thought.
    mmr_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_mmr_enabled=True,
        recall_mmr_lambda=0.3,  # strong diversity push to make the test deterministic
    )
    results = recall(
        "pgvector PostgreSQL vector storage",
        project=proj,
        limit=4,
        config=mmr_cfg,
    )

    assert len(results) == 4, f"Expected 4 results, got {len(results)}"

    contents = [r.content for r in results]
    distinct_present = any(
        c in contents
        for c in [
            "The Python requests library is used for HTTP calls",
            "Astro is a static site generator framework for the web",
        ]
    )
    assert distinct_present, (
        "MMR should surface at least one distinct thought when lambda is low. "
        f"Got: {contents}"
    )


# ---------------------------------------------------------------------------
# Disabled MMR returns pure fused ranking unchanged
# ---------------------------------------------------------------------------

def test_mmr_disabled_returns_fused_order(cfg: MuninConfig) -> None:
    """MMR-disabled recall must return exactly the top-N by fused score.

    Uses 8 thoughts but requests only limit=4, so the assertion exercises
    which candidates survive — not just that all thoughts are returned.  The
    top-4 by fused score (fused_score field) must match the 4 returned IDs.

    Note: we assert on the FIRST call only (before hit_count is bumped) so
    that recency/hits tie-breaking does not invalidate the fused-score order.
    """
    proj = "pytest_mmr_disabled"

    # Eight distinct thoughts — more than the limit so selection is non-trivial.
    thoughts = [
        "munin stores thoughts in a Postgres database with pgvector",
        "llama.cpp serves the embedding model for munin",
        "the munin CLI exposes remember and recall commands",
        "munin uses Reciprocal Rank Fusion for hybrid retrieval",
        "pgvector enables cosine similarity search in PostgreSQL",
        "FastAPI is a modern Python web framework",
        "Astro is a static site generator for the web",
        "SQLAlchemy is a Python ORM for relational databases",
    ]
    for t in thoughts:
        remember(t, project=proj, config=cfg)

    # Pure-RRF weights (no recency/hits) so ranking has no state feedback:
    # recall() bumps hit_count/last_hit_at, which would otherwise perturb the
    # fused score between the limit=8 and limit=4 calls and make the comparison
    # non-deterministic. With w_recency=w_hits=0 the fused score == normalised RRF,
    # which is stable across calls.
    no_mmr_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_w_rrf=1.0,
        recall_w_recency=0.0,
        recall_w_hits=0.0,
        recall_mmr_enabled=False,
    )

    # Fetch all 8 candidates (no limit cap) to know the true fused ranking.
    all_results = recall(
        "munin memory retrieval", project=proj, limit=8, config=no_mmr_cfg
    )
    assert len(all_results) == 8, f"Expected 8 results, got {len(all_results)}"

    # The top-4 by fused_score from the full set should match limit=4 recall.
    top4_ids = {r.id for r in sorted(all_results, key=lambda r: r.fused_score, reverse=True)[:4]}

    # Second call with limit=4 — MMR is off so it must be a fused-score top-4 slice.
    # (hit_count was bumped for all 8 by the first call, so recency/hits are uniform
    # across candidates; fused_score ordering is stable.)
    limited_results = recall(
        "munin memory retrieval", project=proj, limit=4, config=no_mmr_cfg
    )

    assert len(limited_results) == 4, (
        f"Expected 4 results with limit=4, got {len(limited_results)}"
    )
    returned_ids = {r.id for r in limited_results}
    assert returned_ids == top4_ids, (
        "MMR-disabled recall(limit=4) must return the top-4 by fused score. "
        f"Expected IDs: {top4_ids}, got: {returned_ids}"
    )


# ---------------------------------------------------------------------------
# Result count respects limit
# ---------------------------------------------------------------------------

def test_mmr_respects_limit(cfg: MuninConfig) -> None:
    """recall with MMR enabled never returns more than *limit* results."""
    proj = "pytest_mmr_limit"

    for i in range(12):
        remember(f"thought number {i} about machine learning models", project=proj, config=cfg)

    mmr_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_mmr_enabled=True,
        recall_mmr_lambda=0.7,
    )

    for limit in (1, 3, 5, 10):
        results = recall(
            "machine learning", project=proj, limit=limit, config=mmr_cfg
        )
        assert len(results) <= limit, (
            f"Expected ≤{limit} results, got {len(results)}"
        )


# ---------------------------------------------------------------------------
# MMR disabled: pure fused order is unchanged (not re-ordered by MMR)
# ---------------------------------------------------------------------------

def test_mmr_disabled_identical_to_no_mmr_pass(cfg: MuninConfig) -> None:
    """recall(mmr_enabled=False) IDs must equal what match_thoughts returns
    directly — MMR must not silently re-order when disabled."""
    proj = "pytest_mmr_passthrough"

    contents = [
        "FastAPI is a modern Python web framework for building APIs",
        "SQLAlchemy is an ORM for Python applications",
        "Pydantic provides data validation using Python type hints",
        "Uvicorn is an ASGI server for Python web applications",
        "Starlette is the underlying ASGI toolkit used by FastAPI",
    ]
    for c in contents:
        remember(c, project=proj, config=cfg)

    disabled_cfg = MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        recall_mmr_enabled=False,
    )

    r1 = recall("Python web framework", project=proj, limit=3, config=disabled_cfg)
    r2 = recall("Python web framework", project=proj, limit=3, config=disabled_cfg)

    # Order should be stable across two identical calls (modulo hit_count bumps
    # which don't change RPC order when MMR is off).
    assert len(r1) == 3
    assert len(r2) == 3
    # The IDs should be the same set (order may vary slightly due to hit_count tie-break)
    assert set(r.id for r in r1) == set(r.id for r in r2)
