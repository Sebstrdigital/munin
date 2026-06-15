"""Recall interface for vector-similarity thought retrieval."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from munin.core import scope as _scope
from munin.core.config import MuninConfig, load
from munin.core.db import get_pool
from munin.core.embed import embed
from munin.core.errors import MuninError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MMR helper
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mmr_rerank(
    candidates: list[ThoughtResult],
    embeddings: dict[UUID, list[float]],
    *,
    lambda_: float,
    k: int,
) -> list[ThoughtResult]:
    """Maximal Marginal Relevance re-ranking over *candidates*.

    Selects *k* items iteratively.  Each step picks the candidate that maximises:
        lambda_ * relevance_score  -  (1 - lambda_) * max_similarity_to_selected

    relevance_score is taken from ThoughtResult.fused_score — the multi-signal
    weighted blend of RRF rank, recency, and hit_count returned by the
    match_thoughts() RPC (column `score`).  Using the fused score ensures MMR
    trades diversity against the same ranking signal that determines candidate
    order, not just raw cosine.  Pairwise similarity for the diversity term is
    computed as cosine distance over the raw embedding vectors.

    Args:
        candidates: Fused-ranked candidates (ordered by descending fused score).
        embeddings: Map of thought id → raw embedding vector (768-dim).
        lambda_:    Trade-off weight — 1.0 = pure relevance, 0.0 = pure diversity.
        k:          Number of results to select (≤ len(candidates)).

    Returns:
        Up to *k* ThoughtResults in MMR order.
    """
    if not candidates or k <= 0:
        return []

    # Normalise fused relevance scores to [0, 1] so they are on the same scale
    # as cosine similarity used for the diversity term.
    max_score = max(c.fused_score for c in candidates) or 1.0
    rel: dict[UUID, float] = {c.id: c.fused_score / max_score for c in candidates}

    remaining = list(candidates)
    selected: list[ThoughtResult] = []

    while remaining and len(selected) < k:
        if not selected:
            # Bootstrap: pick the highest-relevance candidate first.
            best = max(remaining, key=lambda c: rel[c.id])
        else:
            # For each remaining candidate compute MMR score.
            selected_embs = [embeddings[s.id] for s in selected if s.id in embeddings]

            def mmr_score(c: ThoughtResult) -> float:
                emb = embeddings.get(c.id)
                if emb is None or not selected_embs:
                    return lambda_ * rel[c.id]
                max_sim = max(_cosine(emb, se) for se in selected_embs)
                return lambda_ * rel[c.id] - (1.0 - lambda_) * max_sim

            best = max(remaining, key=mmr_score)

        selected.append(best)
        remaining.remove(best)

    return selected


@dataclass
class ThoughtResult:
    """A single recalled thought with its similarity score.

    Attributes:
        similarity:  Raw cosine similarity between the query embedding and the
                     thought embedding.  Preserved for backward-compatible display
                     and CLI output.
        fused_score: Multi-signal final score from match_thoughts() — weighted
                     blend of RRF rank fusion, recency (last_hit_at), and
                     hit_count.  This is the authoritative ranking signal; MMR
                     re-ranking uses this value as its relevance term.
    """

    id: UUID
    content: str
    project: str
    scope: str | None
    tags: list[str]
    metadata: dict[str, Any]
    similarity: float
    fused_score: float
    created_at: datetime


def recall(
    query: str,
    *,
    project: str | None = None,
    scope: str | None = None,
    limit: int | None = None,
    threshold: float = 0.0,
    config: MuninConfig | None = None,
) -> list[ThoughtResult]:
    """Return thoughts most similar to query, filtered by project and optional scope.

    Uses hybrid Reciprocal Rank Fusion (RRF) combining a dense vector leg and a
    lexical full-text leg, then applies multi-signal re-ranking that weighs
    fused relevance, recency (last_hit_at / created_at), and hit_count.

    When recall_mmr_enabled is True (default), a Maximal Marginal Relevance pass
    re-orders the fused candidates to balance relevance against diversity.  The
    trade-off is controlled by recall_mmr_lambda (default 0.7 — higher means more
    relevance, lower means more diversity).  To get a large enough candidate pool
    for MMR, the RPC is asked for match_limit * 5 rows; MMR then selects the final
    match_limit.  When MMR is disabled the pure fused ranking is returned unchanged.

    Config fields read (see MuninConfig):
        recall_w_rrf        — weight for fused RRF relevance signal (default 0.7)
        recall_w_recency    — weight for recency signal (default 0.2)
        recall_w_hits       — weight for normalised hit_count (default 0.1)
        recall_rrf_k        — RRF k constant (default 60)
        recall_mmr_enabled  — enable MMR re-ranking (default True)
        recall_mmr_lambda   — MMR relevance/diversity trade-off (default 0.7)

    Args:
        query: Natural-language query to embed (dense leg) and search (lexical leg).
        project: Project name to filter by. Resolved from git root if not provided.
        scope: Optional scope label to further restrict results.
        limit: Maximum number of results. Defaults to config.default_limit.
        threshold: Minimum cosine similarity for the dense leg (0.0–1.0).
        config: Optional config override; uses load() if not provided.

    Returns:
        List of ThoughtResult (length ≤ limit), ordered by MMR score when MMR is
        enabled, or by descending hybrid score when MMR is disabled.

    Raises:
        MuninError: If project cannot be determined.
    """
    cfg = config if config is not None else load()

    resolved_project = project or _scope.current_project()
    if resolved_project is None:
        raise MuninError(
            "project could not be determined; pass project= or run from inside a git repo"
        )

    match_limit = limit if limit is not None else cfg.default_limit

    # When MMR is enabled we need a larger candidate pool so the diversity pass
    # has meaningful choices.  We ask the RPC for match_limit * 5 candidates
    # (mirrors the internal candidate_factor=5 logic in the SQL function).
    # The final slice back to match_limit happens after MMR.
    mmr_enabled = cfg.recall_mmr_enabled
    rpc_limit = match_limit * 5 if mmr_enabled else match_limit

    logger.debug(
        "recall: project=%s query_len=%d limit=%d mmr=%s",
        resolved_project, len(query), match_limit, mmr_enabled,
    )
    vec = embed(query, config=cfg)
    # DR-003: fixed-precision formatting avoids repr() emitting 'nan'/'inf'.
    vec_str = "[" + ",".join(f"{v:.8g}" for v in vec) + "]"

    pool = get_pool(cfg)
    pool.open(wait=True)

    candidates: list[ThoughtResult] = []
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # Set HNSW query-time parameters for this transaction.
            # SET LOCAL is scoped to the current transaction and is the correct
            # mechanism for per-query ef_search / iterative_scan tuning (pgvector 0.8).
            # These cannot be set inside the STABLE match_thoughts() function itself.
            cur.execute("SET LOCAL hnsw.ef_search = 100")
            cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")

            cur.execute(
                "SELECT id, content, project, scope, tags, metadata,"
                " similarity, created_at, updated_at, score"
                " FROM match_thoughts("
                "   %s::vector,"   # query_embedding
                "   %s,"           # query_text (lexical leg)
                "   %s,"           # p_project
                "   %s,"           # p_scope
                "   %s,"           # match_limit (rpc_limit — enlarged for MMR)
                "   %s,"           # similarity_threshold
                "   %s,"           # rrf_k
                "   5,"            # candidate_factor (fixed at 5)
                "   %s,"           # w_rrf
                "   %s,"           # w_recency
                "   %s"            # w_hits
                ")",
                (
                    vec_str,
                    query,
                    resolved_project,
                    scope,
                    rpc_limit,
                    threshold,
                    cfg.recall_rrf_k,
                    cfg.recall_w_rrf,
                    cfg.recall_w_recency,
                    cfg.recall_w_hits,
                ),
            )
            for row in cur.fetchall():
                candidates.append(
                    ThoughtResult(
                        id=row[0],
                        content=row[1],
                        project=row[2],
                        scope=row[3],
                        tags=list(row[4]) if row[4] else [],
                        metadata=dict(row[5]) if row[5] else {},
                        similarity=float(row[6]),
                        fused_score=float(row[9]),
                        created_at=row[7],
                    )
                )

            if mmr_enabled and len(candidates) > 1:
                # Fetch raw embeddings for the candidate set so MMR can compute
                # pairwise cosine similarity.  A single query by UUID array is
                # cheap relative to the full HNSW scan already performed above.
                candidate_ids = [c.id for c in candidates]
                cur.execute(
                    "SELECT id, embedding::text FROM thoughts WHERE id = ANY(%s)",
                    (candidate_ids,),
                )
                embeddings: dict[UUID, list[float]] = {}
                for emb_row in cur.fetchall():
                    eid = UUID(str(emb_row[0]))
                    # Postgres returns vector as a string like "[0.1,0.2,...]"
                    raw = str(emb_row[1]).strip("[]")
                    embeddings[eid] = [float(x) for x in raw.split(",")]

                logger.debug(
                    "recall: MMR over %d candidates → selecting %d (lambda=%.2f)",
                    len(candidates), match_limit, cfg.recall_mmr_lambda,
                )
                results = _mmr_rerank(
                    candidates,
                    embeddings,
                    lambda_=cfg.recall_mmr_lambda,
                    k=match_limit,
                )
            else:
                # MMR disabled or single candidate: return pure fused ranking.
                results = candidates[:match_limit]

            # Bump hit counters only for thoughts actually returned to the caller.
            if results:
                hit_ids = [r.id for r in results]
                cur.execute(
                    "UPDATE thoughts"
                    " SET hit_count = hit_count + 1, last_hit_at = now()"
                    " WHERE id = ANY(%s)",
                    (hit_ids,),
                )
                logger.debug("recall: bumped hit_count for %d thoughts", len(hit_ids))

    return results


@dataclass
class Thought:
    """Full thought row — no similarity score. Returned by show()."""

    id: UUID
    content: str
    project: str
    scope: str | None
    tags: list[str]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def list_projects(
    *, config: MuninConfig | None = None
) -> list[tuple[str, int]]:
    """Return (project, thought_count) for every project, ordered by name."""
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project, COUNT(*) FROM thoughts"
                " GROUP BY project ORDER BY project"
            )
            rows: list[tuple[str, int]] = [
                (str(r[0]), int(r[1])) for r in cur.fetchall()
            ]
    return rows


def show(
    thought_id: UUID | str, *, config: MuninConfig | None = None
) -> Thought | None:
    """Return the full Thought for thought_id, or None if not found."""
    uid = (
        thought_id if isinstance(thought_id, UUID) else UUID(str(thought_id))
    )
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, project, scope, tags, metadata, created_at, updated_at"
                " FROM thoughts WHERE id = %s",
                (uid,),
            )
            row: Any = cur.fetchone()
    if row is None:
        return None
    return Thought(
        id=UUID(str(row[0])),
        content=str(row[1]),
        project=str(row[2]),
        scope=str(row[3]) if row[3] is not None else None,
        tags=list(row[4]),
        metadata=dict(row[5]),
        created_at=row[6],
        updated_at=row[7],
    )


def remember(
    content: str,
    *,
    project: str | None = None,
    scope: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    config: MuninConfig | None = None,
) -> UUID:
    """Store a thought, auto-detecting the current git project if needed.

    Args:
        content: The thought content to store.
        project: Project name. Resolved from git root if not provided.
        scope: Optional scope label.
        tags: Optional list of string tags. Defaults to [].
        metadata: Optional JSON-serialisable metadata dict. Defaults to {}.
        config: Optional config override; uses load() if not provided.

    Returns:
        UUID of the inserted (or upserted) thought row.

    Raises:
        MuninError: If project cannot be determined.
    """
    cfg = config if config is not None else load()

    resolved_project = project or _scope.current_project()
    if resolved_project is None:
        raise MuninError(
            "project could not be determined; pass project= or run from inside a git repo"
        )

    resolved_tags: list[str] = tags if tags is not None else []
    resolved_metadata: dict[str, Any] = metadata if metadata is not None else {}

    logger.info("remember: project=%s content_len=%d", resolved_project, len(content))
    vec = embed(content, config=cfg)
    # DR-003: fixed-precision formatting avoids repr() emitting 'nan'/'inf'.
    embedding_str = "[" + ",".join(f"{v:.8g}" for v in vec) + "]"

    pool = get_pool(cfg)
    pool.open(wait=True)

    # P2-1: Semantic near-duplicate detection — ANN-check before insert.
    # When enabled, query the top-1 in-project neighbour and skip if cosine
    # similarity >= threshold.  Flag OFF restores prior insert-always behaviour.
    if cfg.remember_dedup_enabled:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, 1 - (embedding <=> %s::vector) AS cosine"
                    " FROM thoughts"
                    " WHERE project = %s"
                    " ORDER BY embedding <=> %s::vector"
                    " LIMIT 1",
                    (embedding_str, resolved_project, embedding_str),
                )
                dup_row: Any = cur.fetchone()

        if dup_row is not None:
            dup_id = UUID(str(dup_row[0]))
            cosine = float(dup_row[1])
            if cosine >= cfg.remember_dedup_threshold:
                logger.info(
                    "remember: dedup skip — new thought is near-duplicate of %s"
                    " (cosine=%.4f >= threshold=%.4f); project=%s",
                    dup_id, cosine, cfg.remember_dedup_threshold, resolved_project,
                )
                return dup_id

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT upsert_thought(%s, %s::vector, %s, %s, %s, %s::jsonb)",
                (
                    content,
                    embedding_str,
                    resolved_project,
                    scope,
                    resolved_tags,
                    json.dumps(resolved_metadata),
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise MuninError("upsert_thought returned no row")
    return UUID(str(row[0]))


def forget(
    thought_id: UUID | str, *, config: MuninConfig | None = None
) -> bool:
    """Hard-delete a thought. Returns True if deleted, False if not found."""
    uid = (
        thought_id if isinstance(thought_id, UUID) else UUID(str(thought_id))
    )
    pool = get_pool(config)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM thoughts WHERE id = %s RETURNING id", (uid,)
            )
            row: Any = cur.fetchone()
    return row is not None
